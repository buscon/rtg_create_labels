<?php
// Database, triad planning, stopping rule and repetition check.

declare(strict_types=1);

function rgt_now(): string {
    return (new DateTimeImmutable('now', new DateTimeZone('UTC')))->format('Y-m-d\TH:i:s.vP');
}

// ------------------------------------------------------------------ database
// Two drivers: "mysql" (MariaDB/MySQL, used on the web hosting) and "sqlite"
// (local testing). The SQL used below works on both.

function rgt_db(array $cfg): PDO {
    $opts = [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_TIMEOUT => 10,
    ];
    if ($cfg['server']['db_driver'] === 'mysql') {
        $file = rgt_path($cfg, $cfg['server']['db_credentials']);
        if (!is_file($file)) {
            throw new RuntimeException("database access data not found: $file (copy db_credentials.example.ini)");
        }
        $cr = parse_ini_file($file, false, INI_SCANNER_RAW);
        foreach (['host', 'dbname', 'user', 'password'] as $k) {
            if (!isset($cr[$k])) throw new RuntimeException("db_credentials.ini: '$k' missing");
        }
        $dsn = sprintf('mysql:host=%s;port=%d;dbname=%s;charset=utf8mb4', $cr['host'], (int)($cr['port'] ?? 3306), $cr['dbname']);
        $pdo = new PDO($dsn, $cr['user'], $cr['password'], $opts + [PDO::ATTR_EMULATE_PREPARES => false]);
        $pdo->exec("SET time_zone = '+00:00'");
        return $pdo;
    }
    $path = rgt_path($cfg, $cfg['server']['db_path']);
    $dir = dirname($path);
    if (!is_dir($dir) && !mkdir($dir, 0700, true)) {
        throw new RuntimeException("cannot create database folder $dir");
    }
    $pdo = new PDO('sqlite:' . $path, null, null, $opts);
    $pdo->exec('PRAGMA journal_mode = WAL');
    $pdo->exec('PRAGMA foreign_keys = ON');
    $pdo->exec('PRAGMA busy_timeout = 10000');
    return $pdo;
}

function rgt_is_sqlite(PDO $pdo): bool {
    return $pdo->getAttribute(PDO::ATTR_DRIVER_NAME) === 'sqlite';
}

/** Start a write transaction. SQLite takes the write lock at once to avoid lock upgrades. */
function rgt_begin(PDO $pdo): void {
    if (rgt_is_sqlite($pdo)) $pdo->exec('BEGIN IMMEDIATE');
    else $pdo->beginTransaction();
}

function rgt_commit(PDO $pdo): void {
    if (rgt_is_sqlite($pdo)) $pdo->exec('COMMIT');
    else $pdo->commit();
}

function rgt_rollback(PDO $pdo): void {
    try {
        if (rgt_is_sqlite($pdo)) $pdo->exec('ROLLBACK');
        elseif ($pdo->inTransaction()) $pdo->rollBack();
    } catch (Throwable $e) { /* nothing to roll back */ }
}

function rgt_ensure_column(PDO $pdo, string $table, string $column, string $type): void {
    if (rgt_is_sqlite($pdo)) {
        $cols = array_column($pdo->query("PRAGMA table_info($table)")->fetchAll(), 'name');
    } else {
        $cols = $pdo->query("SHOW COLUMNS FROM $table")->fetchAll(PDO::FETCH_COLUMN);
    }
    if (!in_array($column, $cols, true)) {
        $pdo->exec("ALTER TABLE $table ADD COLUMN $column $type");
    }
}

/** Create tables and register the clips from the elements file. Safe to run repeatedly. */
function rgt_init_db(array $cfg): void {
    $pdo = rgt_db($cfg);
    $schema = file_get_contents($cfg['_private_dir'] . '/schema.' . (rgt_is_sqlite($pdo) ? 'sqlite' : 'mysql') . '.sql');
    $schema = preg_replace('/--[^\n]*/', '', $schema);
    foreach (array_filter(array_map('trim', explode(';', $schema))) as $stmt) {
        $pdo->exec($stmt);
    }
    // columns added after the first version: add them to existing databases
    rgt_ensure_column($pdo, 'responses', 'language', rgt_is_sqlite($pdo) ? 'TEXT' : 'VARCHAR(10) NULL');
    rgt_ensure_column($pdo, 'participants', 'access_code', rgt_is_sqlite($pdo) ? 'TEXT' : 'VARCHAR(50) NULL');
    rgt_ensure_column($pdo, 'responses', 'hints_shown', rgt_is_sqlite($pdo) ? 'TEXT' : 'VARCHAR(500) NULL');
    foreach (['age_group' => 'VARCHAR(10) NULL', 'gender' => 'VARCHAR(10) NULL', 'first_language' => 'VARCHAR(10) NULL',
              'first_language_other' => 'VARCHAR(100) NULL', 'sound_expertise' => 'VARCHAR(15) NULL',
              'residence' => 'VARCHAR(10) NULL'] as $col => $type) {
        rgt_ensure_column($pdo, 'participants', $col, rgt_is_sqlite($pdo) ? 'TEXT' : $type);
    }
    rgt_ensure_column($pdo, 'participants', 'listening_device', rgt_is_sqlite($pdo) ? 'TEXT' : 'VARCHAR(20) NULL');
    rgt_ensure_column($pdo, 'participants', 'listening_environment', rgt_is_sqlite($pdo) ? 'TEXT' : 'VARCHAR(20) NULL');

    rgt_begin($pdo);
    try {
        $get = $pdo->prepare('SELECT filename FROM elements WHERE element_id = ?');
        // the clip set may still change while no session has been started
        $started = (int)$pdo->query('SELECT COUNT(*) FROM participants')->fetchColumn() > 0;
        foreach (rgt_elements($cfg) as $e) {
            $get->execute([$e['element_id']]);
            $row = $get->fetch();
            $get->closeCursor();
            if ($row === false) {
                $pdo->prepare('INSERT INTO elements (element_id, filename, category, is_practice) VALUES (?,?,?,0)')
                    ->execute([$e['element_id'], $e['filename'], $e['category']]);
            } elseif ($row['filename'] !== $e['filename']) {
                if ($started) {
                    throw new RuntimeException("element {$e['element_id']} is '{$row['filename']}' in the database but "
                        . "'{$e['filename']}' in the elements file, and sessions already exist. "
                        . "Use a new (empty) database for a new clip set (setup.php --reset-data).");
                }
                $pdo->prepare('UPDATE elements SET filename = ?, category = ? WHERE element_id = ?')
                    ->execute([$e['filename'], $e['category'], $e['element_id']]);
            } else {
                $pdo->prepare('UPDATE elements SET category = ? WHERE element_id = ?')
                    ->execute([$e['category'], $e['element_id']]);
            }
        }
        // clips that are no longer in the elements file (e.g. 10 -> 8 clips)
        $ids = array_map(fn($e) => (int)$e['element_id'], rgt_elements($cfg));
        $stale = array_values(array_filter(
            array_map('intval', $pdo->query('SELECT element_id FROM elements WHERE is_practice = 0')->fetchAll(PDO::FETCH_COLUMN)),
            fn($id) => !in_array($id, $ids, true)));
        if ($stale) {
            if ($started) {
                throw new RuntimeException('the database contains clips (element_id ' . implode(', ', $stale)
                    . ') that are not in the elements file, and sessions already exist. '
                    . 'Use a new (empty) database for a new clip set (setup.php --reset-data).');
            }
            $pdo->prepare('DELETE FROM elements WHERE is_practice = 0 AND element_id IN ('
                . implode(',', array_fill(0, count($stale), '?')) . ')')->execute($stale);
        }
        if (!$started) {
            // practice clips that are no longer configured
            $keep = $cfg['stimuli']['practice_clips'];
            $old = $pdo->query('SELECT element_id, filename FROM elements WHERE is_practice = 1')->fetchAll();
            $del = $pdo->prepare('DELETE FROM elements WHERE element_id = ?');
            foreach ($old as $row) if (!in_array($row['filename'], $keep, true)) $del->execute([$row['element_id']]);
        }
        // practice clips get ids from 1001 on, so they never take the id of a clip added later
        $find = $pdo->prepare('SELECT 1 FROM elements WHERE filename = ?');
        $taken = $pdo->prepare('SELECT 1 FROM elements WHERE element_id = ?');
        $next = 1001;
        foreach ($cfg['stimuli']['practice_clips'] as $name) {
            $find->execute([$name]);
            $exists = $find->fetch();
            $find->closeCursor();
            if (!$exists) {
                while (true) {
                    $taken->execute([$next]);
                    $used = $taken->fetch();
                    $taken->closeCursor();
                    if (!$used) break;
                    $next++;
                }
                $pdo->prepare('INSERT INTO elements (element_id, filename, is_practice) VALUES (?, ?, 1)')->execute([$next, $name]);
                $next++;
            }
        }
        rgt_commit($pdo);
    } catch (Throwable $e) {
        rgt_rollback($pdo);
        throw $e;
    }
}

// ------------------------------------------------------------------ triads (design file 03)

/**
 * Plan the triads for one participant. The real clips are randomly assigned to
 * the numbers 1..n of the covering set, then the triad order and the positions
 * within each triad are shuffled.
 * Returns [mapping (set number => element_id), plan (list of order_index, set_number, elements)].
 */
function rgt_plan_triads(array $triadSet, array $elementIds, ?array $practiceIds, int $maxTriads,
                         bool $randMapping = true, bool $randOrder = true, bool $randPositions = true): array {
    sort($elementIds);
    if ($randMapping) shuffle($elementIds);
    $mapping = [];
    foreach ($elementIds as $i => $id) $mapping[$i + 1] = $id;

    if ($randOrder) shuffle($triadSet);
    $triadSet = array_slice($triadSet, 0, $maxTriads);

    $plan = [];
    if ($practiceIds) {
        $p = $practiceIds;
        if ($randPositions) shuffle($p);
        $plan[] = ['order_index' => 0, 'set_number' => null, 'elements' => $p];
    }
    foreach ($triadSet as $i => [$setNo, $a, $b, $c]) {
        $els = [$mapping[$a], $mapping[$b], $mapping[$c]];
        if ($randPositions) shuffle($els);
        $plan[] = ['order_index' => $i + 1, 'set_number' => $setNo, 'elements' => $els];
    }
    return [$mapping, $plan];
}

// ------------------------------------------------------------------ stopping rule (design file 04)

function rgt_trailing_not_new(array $history): int {
    $n = 0;
    for ($i = count($history) - 1; $i >= 0; $i--) {
        if ($history[$i]) break;
        $n++;
    }
    return $n;
}

/** $history: one bool per completed real triad (true = new construct). Returns a reason or null. */
function rgt_stop_reason(array $history, float $elapsedMin, int $minTriads, int $consecutive,
                         int $maxTriads, float $timeLimitMin): ?string {
    $n = count($history);
    if ($n >= $maxTriads) return 'max_triads';
    if ($elapsedMin >= $timeLimitMin) return 'time_limit';
    if ($n >= $minTriads && rgt_trailing_not_new($history) >= $consecutive) return 'saturation';
    return null;
}

// ------------------------------------------------------------------ repetition check (design file 04)

function rgt_normalise(string $s): string {
    if (class_exists('Normalizer')) {           // intl extension, if installed
        $s = Normalizer::normalize($s, Normalizer::FORM_KC) ?: $s;
    }
    $s = mb_strtolower($s, 'UTF-8');
    $s = preg_replace('/[^\p{L}\p{N}\s]/u', ' ', $s);
    return trim(preg_replace('/\s+/u', ' ', $s));
}

function rgt_text_sim(string $a, string $b): float {
    if ($a === '' && $b === '') return 1.0;
    similar_text($a, $b, $pct);
    return $pct / 100;
}

function rgt_cosine(array $a, array $b): float {
    $dot = $na = $nb = 0.0;
    $n = min(count($a), count($b));
    for ($i = 0; $i < $n; $i++) {
        $dot += $a[$i] * $b[$i];
        $na += $a[$i] * $a[$i];
        $nb += $b[$i] * $b[$i];
    }
    return ($na > 0 && $nb > 0) ? $dot / (sqrt($na) * sqrt($nb)) : 0.0;
}

/**
 * Compare a new construct with the participant's earlier constructs.
 * Poles are compared pairwise, in both orientations if configured:
 *   same:    mean(sim(s1, s2), sim(c1, c2))
 *   swapped: mean(sim(s1, c2), sim(c1, s2))
 * Embeddings are used when both constructs have them; otherwise text similarity.
 *
 * $new: ['s' => string, 'c' => string, 'es' => ?array, 'ec' => ?array]
 * $earlier: list of same shape plus 'id'
 * Returns ['is_new' => bool, 'nearest' => ?int, 'score' => ?float, 'backend' => ?string]
 */
function rgt_check_repetition(array $new, array $earlier, array $cfg): array {
    if (!$earlier) return ['is_new' => true, 'nearest' => null, 'score' => null, 'backend' => null];
    $sc = $cfg['similarity'];
    $both = (bool)$sc['compare_both_orientations'];
    $s1 = rgt_normalise($new['s']);
    $c1 = rgt_normalise($new['c']);
    $useEmb = $sc['backend'] === 'embedding';

    $best = null;
    foreach ($earlier as $old) {
        $s2 = rgt_normalise($old['s']);
        $c2 = rgt_normalise($old['c']);
        if (($s1 === $s2 && $c1 === $c2) || ($both && $s1 === $c2 && $c1 === $s2)) {
            return ['is_new' => false, 'nearest' => $old['id'], 'score' => 1.0, 'backend' => 'identical_text'];
        }
        if ($useEmb && $new['es'] && $new['ec'] && $old['es'] && $old['ec']) {
            $backend = 'embedding';
            $same = (rgt_cosine($new['es'], $old['es']) + rgt_cosine($new['ec'], $old['ec'])) / 2;
            $swap = (rgt_cosine($new['es'], $old['ec']) + rgt_cosine($new['ec'], $old['es'])) / 2;
        } else {
            $backend = 'text';
            $same = (rgt_text_sim($s1, $s2) + rgt_text_sim($c1, $c2)) / 2;
            $swap = (rgt_text_sim($s1, $c2) + rgt_text_sim($c1, $s2)) / 2;
        }
        $score = $both ? max($same, $swap) : $same;
        $threshold = $backend === 'embedding' ? $sc['threshold_embedding'] : $sc['threshold_text'];
        // compare relative to the threshold, so text and embedding scores can be mixed
        $margin = $score - $threshold;
        if ($best === null || $margin > $best['margin']) {
            $best = ['margin' => $margin, 'nearest' => $old['id'], 'score' => round($score, 4), 'backend' => $backend];
        }
    }
    return ['is_new' => $best['margin'] < 0, 'nearest' => $best['nearest'],
            'score' => $best['score'], 'backend' => $best['backend']];
}

// ------------------------------------------------------------------ access protection (design file 14)

/** Codes are compared without case, spaces and dashes: "k7m2-qx9p" = "K7M2QX9P". */
function rgt_normalise_code(string $code): string {
    return strtoupper((string)preg_replace('/[^A-Za-z0-9]/', '', $code));
}

/** The group codes from private/access_codes.csv (code, max_uses, valid_until, note), keyed by normalised code. */
function rgt_access_codes(array $cfg): array {
    $file = rgt_path($cfg, $cfg['access']['codes_file']);
    if (!is_file($file)) return [];
    $codes = [];
    foreach (rgt_read_csv($file) as $r) {
        $c = rgt_normalise_code((string)($r['code'] ?? ''));
        if ($c !== '') $codes[$c] = $r;
    }
    return $codes;
}

/** 'ok' | 'missing' | 'invalid' | 'expired' | 'used_up' */
function rgt_code_status(PDO $pdo, array $cfg, string $code): string {
    if (!$cfg['access']['require_code']) return 'ok';
    $code = rgt_normalise_code($code);
    if ($code === '') return 'missing';
    $row = rgt_access_codes($cfg)[$code] ?? null;
    if ($row === null) return 'invalid';
    $until = trim((string)($row['valid_until'] ?? ''));
    if ($until !== '' && gmdate('Y-m-d') > $until) return 'expired';
    $max = (int)($row['max_uses'] ?? 0);
    if ($max > 0) {
        $st = $pdo->prepare('SELECT COUNT(*) FROM participants WHERE access_code = ?');
        $st->execute([$code]);
        if ((int)$st->fetchColumn() >= $max) return 'used_up';
    }
    return 'ok';
}

function rgt_code_uses(PDO $pdo): array {
    $rows = $pdo->query('SELECT access_code, COUNT(*) AS n, SUM(CASE WHEN finished_at IS NOT NULL THEN 1 ELSE 0 END) AS done
        FROM participants WHERE access_code IS NOT NULL GROUP BY access_code')->fetchAll();
    $out = [];
    foreach ($rows as $r) $out[$r['access_code']] = ['n' => (int)$r['n'], 'done' => (int)$r['done']];
    return $out;
}

/** Salted hash of the client IP; the salt is a random key in the private folder (not in git). */
function rgt_ip_hash(array $cfg): string {
    $file = $cfg['_private_dir'] . '/ip_salt.key';
    $salt = is_file($file) ? trim((string)file_get_contents($file)) : '';
    if ($salt === '') {
        $salt = bin2hex(random_bytes(32));
        if (@file_put_contents($file, $salt) !== false) @chmod($file, 0600);
    }
    return hash_hmac('sha256', (string)($_SERVER['REMOTE_ADDR'] ?? ''), $salt);
}

/** True if this IP may start another session; old entries (> 1 hour) are deleted on the way. */
function rgt_ip_allowed(PDO $pdo, array $cfg): bool {
    $max = (int)$cfg['access']['max_sessions_per_ip_hour'];
    if ($max <= 0) return true;
    $now = time();
    $pdo->prepare('DELETE FROM session_starts WHERE started_unix < ?')->execute([$now - 3600]);
    $st = $pdo->prepare('SELECT COUNT(*) FROM session_starts WHERE ip_hash = ?');
    $st->execute([rgt_ip_hash($cfg)]);
    return (int)$st->fetchColumn() < $max;
}

function rgt_ip_record(PDO $pdo, array $cfg): void {
    if ((int)$cfg['access']['max_sessions_per_ip_hour'] <= 0) return;
    $pdo->prepare('INSERT INTO session_starts (ip_hash, started_unix) VALUES (?, ?)')->execute([rgt_ip_hash($cfg), time()]);
}

/** Seconds since the epoch of a timestamp written by rgt_now(). */
function rgt_ts(string $ts): float {
    return (float)(new DateTimeImmutable($ts))->format('U.u');
}

/**
 * Was every clip of the triad played to the end? With min_play_fraction > 0, "ended" counts only
 * if it arrived at least that share of the clip duration after the first "play" of the clip
 * (server time), so a script cannot skip the listening.
 */
function rgt_clips_fully_played(PDO $pdo, array $cfg, array $triad, array $elementIds): bool {
    $min = (float)$cfg['access']['min_play_fraction'] * (float)$cfg['stimuli']['clip_duration_s'];
    $st = $pdo->prepare('SELECT element_id, event, logged_at FROM play_events WHERE triad_id = ? ORDER BY event_id');
    $st->execute([$triad['triad_id']]);
    $first = $ended = [];
    foreach ($st->fetchAll() as $r) {
        $id = (int)$r['element_id'];
        if ($r['event'] === 'play' && !isset($first[$id])) $first[$id] = rgt_ts($r['logged_at']);
        if ($r['event'] === 'ended') $ended[$id] = rgt_ts($r['logged_at']);
    }
    foreach ($elementIds as $id) {
        $id = (int)$id;
        if (!isset($ended[$id])) return false;
        if ($min > 0 && (!isset($first[$id]) || $ended[$id] - $first[$id] < $min)) return false;
    }
    return true;
}
