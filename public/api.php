<?php
// JSON API of the repertory grid elicitation tool.
// All requests go to api.php?r=<route>. No URL rewriting is needed.

declare(strict_types=1);

// Location of the private folder (configuration, database, code).
// Default: the "private" folder next to this file, protected by .htaccess.
// To keep it outside the web root, create private_path.php next to this file:
//     <?php return '/absolute/path/to/private';
$privateDir = is_file(__DIR__ . '/private_path.php') ? require __DIR__ . '/private_path.php' : __DIR__ . '/private';

require $privateDir . '/lib/config.php';
require $privateDir . '/lib/core.php';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');

function out(array $data, int $status = 200): never {
    http_response_code($status);
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
    exit;
}

function fail(int $status, string $msg): never {
    out(['detail' => $msg], $status);
}

function body(): array {
    $raw = file_get_contents('php://input');
    $data = json_decode($raw ?: '{}', true);
    if (!is_array($data)) fail(400, 'invalid JSON');
    return $data;
}

function str_or_null($v, int $max): ?string {
    if ($v === null) return null;
    $v = trim((string)$v);
    return $v === '' ? null : mb_substr($v, 0, $max);
}

function interviewer_token_ok(array $cfg): bool {
    $given = $_SERVER['HTTP_X_INTERVIEWER_TOKEN'] ?? '';
    $file = $cfg['_private_dir'] . '/interviewer_token.hash';
    if ($given === '' || !is_file($file)) return false;
    return password_verify($given, trim(file_get_contents($file)));
}

try {
    $cfg = rgt_load_config($privateDir);
    $pdo = rgt_db($cfg);
    $pdo->query('SELECT 1 FROM participants LIMIT 1');   // fails if setup.php has not been run
} catch (ConfigError $e) {
    error_log('[rgt] configuration error: ' . $e->getMessage());
    fail(500, 'configuration error');
} catch (Throwable $e) {
    error_log('[rgt] start-up error: ' . $e->getMessage());
    fail(500, 'server not set up (run php private/setup.php)');
}

const POS = ['A' => 'pos_a', 'B' => 'pos_b', 'C' => 'pos_c'];

function participant(PDO $pdo, string $pid, bool $lock = false): array {
    // FOR UPDATE serialises concurrent answers of the same participant (MariaDB/MySQL)
    $sql = 'SELECT * FROM participants WHERE participant_id = ?' . ($lock && !rgt_is_sqlite($pdo) ? ' FOR UPDATE' : '');
    $st = $pdo->prepare($sql);
    $st->execute([$pid]);
    $p = $st->fetch();
    if (!$p) fail(404, 'unknown participant');
    return $p;
}

function triad(PDO $pdo, string $pid, int $tid): array {
    $st = $pdo->prepare('SELECT * FROM triads WHERE triad_id = ? AND participant_id = ?');
    $st->execute([$tid, $pid]);
    $t = $st->fetch();
    if (!$t) fail(404, 'unknown triad');
    return $t;
}

function finish(PDO $pdo, string $pid, string $reason): void {
    $pdo->prepare('UPDATE participants SET finished_at = ?, stop_reason = ? WHERE participant_id = ? AND finished_at IS NULL')
        ->execute([rgt_now(), $reason, $pid]);
}

function history(PDO $pdo, string $pid, array $cfg): array {
    $st = $pdo->prepare('SELECT r.no_difference, c.auto_is_new FROM responses r JOIN triads t ON t.triad_id = r.triad_id
        LEFT JOIN constructs c ON c.construct_id = r.construct_id
        WHERE t.participant_id = ? AND t.order_index >= 1 ORDER BY t.order_index');
    $st->execute([$pid]);
    $h = [];
    foreach ($st->fetchAll() as $r) {
        if ($r['no_difference']) {
            if ($cfg['stopping']['no_difference_counts_as_not_new']) $h[] = false;
        } else {
            $h[] = (bool)$r['auto_is_new'];
        }
    }
    return $h;
}

function elapsed_min(array $p): float {
    if (!$p['triads_started_at']) return 0.0;
    $start = new DateTimeImmutable($p['triads_started_at']);
    return (microtime(true) - (float)$start->format('U.u')) / 60;
}

function earlier_constructs(PDO $pdo, string $pid, ?int $exclude = null): array {
    $st = $pdo->prepare('SELECT construct_id, similarity_pole, contrast_pole, emb_similarity, emb_contrast
        FROM constructs WHERE participant_id = ? AND is_practice = 0 AND (? IS NULL OR construct_id <> ?) ORDER BY construct_id');
    $st->execute([$pid, $exclude, $exclude]);
    return $st->fetchAll();
}

function next_triad(PDO $pdo, array $p): array {
    if ($p['finished_at']) return ['done' => true, 'stop_reason' => $p['stop_reason']];
    $st = $pdo->prepare('SELECT t.* FROM triads t LEFT JOIN responses r ON r.triad_id = t.triad_id
        WHERE t.participant_id = ? AND r.response_id IS NULL ORDER BY t.order_index LIMIT 1');
    $st->execute([$p['participant_id']]);
    $t = $st->fetch();
    if (!$t) {
        finish($pdo, $p['participant_id'], 'max_triads');
        return ['done' => true, 'stop_reason' => 'max_triads'];
    }
    $now = rgt_now();
    $pdo->prepare('UPDATE triads SET presented_at = COALESCE(presented_at, ?) WHERE triad_id = ?')->execute([$now, $t['triad_id']]);
    if ($t['order_index'] >= 1) {
        $pdo->prepare('UPDATE participants SET triads_started_at = COALESCE(triads_started_at, ?) WHERE participant_id = ?')
            ->execute([$now, $p['participant_id']]);
    }
    $n = $pdo->prepare('SELECT COUNT(*) FROM responses r JOIN triads t ON t.triad_id = r.triad_id
        WHERE t.participant_id = ? AND t.order_index >= 1');
    $n->execute([$p['participant_id']]);
    $fn = $pdo->prepare('SELECT filename FROM elements WHERE element_id = ?');
    $clips = [];
    foreach (POS as $pos => $col) {
        $fn->execute([$t[$col]]);
        $clips[] = ['position' => $pos, 'url' => 'audio/' . rawurlencode($fn->fetchColumn())];
    }
    return ['done' => false, 'triad' => [
        'triad_id' => (int)$t['triad_id'], 'is_practice' => (int)$t['order_index'] === 0,
        'n_done' => (int)$n->fetchColumn(), 'clips' => $clips,
    ]];
}

function vector($v, int $max = 2048): ?array {
    if (!is_array($v) || !$v || count($v) > $max) return null;
    foreach ($v as $x) if (!is_int($x) && !is_float($x)) return null;
    return array_map('floatval', $v);
}

// ------------------------------------------------------------------ routes

$route = $_GET['r'] ?? '';
$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';

switch ("$method $route") {

case 'GET config':
    out(rgt_public_config($cfg));

case 'GET health':
    out(['ok' => true, 'config_version' => $cfg['study']['config_version'],
         'similarity_backend' => $cfg['similarity']['backend']]);

case 'POST session':
    $b = body();
    $lang = (string)($b['language'] ?? '');
    if (!in_array($lang, $cfg['study']['languages'], true)) fail(400, 'unsupported language');
    $invited = in_array($b['invited_mode'] ?? null, ['online', 'in_person'], true) ? $b['invited_mode'] : null;
    $mode = 'online';
    if (isset($_SERVER['HTTP_X_INTERVIEWER_TOKEN'])) {
        if (!$cfg['interviewer']['enabled'] || !interviewer_token_ok($cfg)) fail(403, 'invalid interviewer password');
        $mode = 'interviewer';
    }
    rgt_begin($pdo);
    try {
        $pid = 'P-' . rtrim(strtr(base64_encode(random_bytes(9)), '+/', '-_'), '=');
        $ids = $pdo->query('SELECT element_id FROM elements WHERE is_practice = 0 ORDER BY element_id')->fetchAll(PDO::FETCH_COLUMN);
        $practice = null;
        if ($cfg['triads']['practice_triad']) {
            $q = $pdo->prepare('SELECT element_id FROM elements WHERE filename = ?');
            $practice = [];
            foreach ($cfg['stimuli']['practice_clips'] as $name) { $q->execute([$name]); $practice[] = (int)$q->fetchColumn(); }
        }
        $t = $cfg['triads'];
        [$mapping, $plan] = rgt_plan_triads(rgt_triad_set($cfg), array_map('intval', $ids), $practice,
            (int)$t['max_triads'], (bool)$t['randomise_clip_mapping'], (bool)$t['randomise_order'], (bool)$t['randomise_positions']);

        $hpSeq = null; $hpOut = [];
        if ($cfg['headphone_check']['enabled']) {
            $manifest = __DIR__ . '/audio/headphone/manifest.csv';
            if (!is_file($manifest)) throw new RuntimeException('headphone stimuli missing (audio/headphone/manifest.csv)');
            $stimuli = rgt_read_csv($manifest);
            $hpSeq = [];
            for ($i = 0; $i < $cfg['headphone_check']['n_trials']; $i++) {
                $s = $stimuli[random_int(0, count($stimuli) - 1)];
                $hpSeq[] = ['file' => $s['filename'], 'target' => (int)$s['target']];
                $hpOut[] = ['trial' => $i + 1, 'url' => 'audio/headphone/' . rawurlencode($s['filename'])];
            }
        }
        $snapshot = $cfg; unset($snapshot['_private_dir']);
        $pdo->prepare('INSERT INTO participants (participant_id, mode, invited_mode, language, started_at, headphone_sequence,
            headphones_model, hearing_problems, config_version, config_snapshot, clip_mapping, user_agent)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)')->execute([
            $pid, $mode, $invited, $lang, rgt_now(), $hpSeq ? json_encode($hpSeq) : null,
            str_or_null($b['headphones_model'] ?? null, 200), str_or_null($b['hearing_problems'] ?? null, 200),
            (string)$cfg['study']['config_version'], json_encode($snapshot, JSON_UNESCAPED_UNICODE),
            json_encode($mapping), str_or_null($_SERVER['HTTP_USER_AGENT'] ?? null, 500)]);
        $ins = $pdo->prepare('INSERT INTO triads (participant_id, order_index, set_number, pos_a, pos_b, pos_c) VALUES (?,?,?,?,?,?)');
        foreach ($plan as $row) {
            $ins->execute([$pid, $row['order_index'], $row['set_number'], ...$row['elements']]);
        }
        rgt_commit($pdo);
    } catch (Throwable $e) {
        rgt_rollback($pdo);
        error_log('[rgt] session: ' . $e->getMessage());
        fail(500, 'could not create session');
    }
    out(['participant_id' => $pid, 'mode' => $mode, 'headphone_trials' => $hpOut]);

case 'POST headphone':
    $pid = (string)($_GET['pid'] ?? '');
    $p = participant($pdo, $pid);
    if (!$cfg['headphone_check']['enabled']) out(['passed' => true]);
    if ($p['headphone_passed'] !== null) out(['passed' => (bool)$p['headphone_passed']]);
    $answers = body()['answers'] ?? null;
    $seq = json_decode((string)$p['headphone_sequence'], true) ?: [];
    if (!is_array($answers) || count($answers) !== count($seq)) fail(400, 'wrong number of answers');
    $score = 0;
    foreach ($seq as $i => $s) $score += ((int)$answers[$i] === $s['target']) ? 1 : 0;
    $passed = $score >= $cfg['headphone_check']['pass_min_correct'];
    $pdo->prepare('UPDATE participants SET headphone_answers = ?, headphone_score = ?, headphone_passed = ? WHERE participant_id = ?')
        ->execute([json_encode(array_map('intval', $answers)), $score, (int)$passed, $pid]);
    if (!$passed) finish($pdo, $pid, 'headphone_check');
    out(['passed' => $passed]);

case 'GET state':
    $p = participant($pdo, (string)($_GET['pid'] ?? ''));
    out(['mode' => $p['mode'], 'language' => $p['language'],
         'headphone_passed' => $p['headphone_passed'] === null ? null : (bool)$p['headphone_passed'],
         'finished' => $p['finished_at'] !== null, 'stop_reason' => $p['stop_reason']]);

case 'GET next':
    $p = participant($pdo, (string)($_GET['pid'] ?? ''));
    if ($cfg['headphone_check']['enabled'] && !$p['headphone_passed'] && !$p['finished_at']) fail(409, 'headphone check not passed');
    out(next_triad($pdo, $p));

case 'POST play':
    $b = body();
    $t = triad($pdo, (string)($b['participant_id'] ?? ''), (int)($b['triad_id'] ?? 0));
    $pos = $b['position'] ?? '';
    $ev = $b['event'] ?? '';
    if (!isset(POS[$pos]) || !in_array($ev, ['play', 'pause', 'ended'], true)) fail(400, 'invalid event');
    $pdo->prepare('INSERT INTO play_events (triad_id, element_id, event, at_ms, logged_at) VALUES (?,?,?,?,?)')
        ->execute([$t['triad_id'], $t[POS[$pos]], $ev, isset($b['at_ms']) ? (int)$b['at_ms'] : null, rgt_now()]);
    out(['ok' => true]);

case 'POST response':
    $b = body();
    $lab = $cfg['labels'];
    $pid = (string)($b['participant_id'] ?? '');
    rgt_begin($pdo);
    try {
        $p = participant($pdo, $pid, true);
        if ($p['finished_at']) throw new DomainException('409|session already finished');
        $t = triad($pdo, $pid, (int)($b['triad_id'] ?? 0));
        $chk = $pdo->prepare('SELECT 1 FROM responses WHERE triad_id = ?');
        $chk->execute([$t['triad_id']]);
        if ($chk->fetch()) throw new DomainException('409|triad already answered');
        if ($cfg['stimuli']['require_full_play']) {
            $st = $pdo->prepare("SELECT DISTINCT element_id FROM play_events WHERE triad_id = ? AND event = 'ended'");
            $st->execute([$t['triad_id']]);
            $ended = array_map('intval', $st->fetchAll(PDO::FETCH_COLUMN));
            foreach (POS as $col) if (!in_array((int)$t[$col], $ended, true)) throw new DomainException('400|all clips must be played to the end');
        }
        $isPractice = (int)$t['order_index'] === 0;
        $noDiff = !empty($b['no_difference']);
        $cid = null; $pairIds = [null, null]; $odd = null;
        if (!$noDiff) {
            $pair = $b['pair'] ?? null;
            if (!is_array($pair) || count($pair) !== 2 || count(array_unique($pair)) !== 2 || array_diff($pair, array_keys(POS)))
                throw new DomainException('400|choose exactly two clips');
            $sPole = trim((string)($b['similarity_pole'] ?? ''));
            $cPole = trim((string)($b['contrast_pole'] ?? ''));
            foreach ([$sPole, $cPole] as $pole) {
                $words = preg_split('/\s+/u', $pole, -1, PREG_SPLIT_NO_EMPTY);
                if (count($words) < $lab['min_words'] || mb_strlen($pole) > $lab['max_chars'])
                    throw new DomainException('400|label length out of range');
            }
            $pairIds = [$t[POS[$pair[0]]], $t[POS[$pair[1]]]];
            $odd = $t[POS[array_values(array_diff(array_keys(POS), $pair))[0]]];
            $es = vector($b['embedding']['similarity'] ?? null);
            $ec = vector($b['embedding']['contrast'] ?? null);
            $auto = $nearest = $score = $backend = null;
            if (!$isPractice) {
                $earlier = array_map(fn($r) => [
                    'id' => (int)$r['construct_id'], 's' => $r['similarity_pole'], 'c' => $r['contrast_pole'],
                    'es' => $r['emb_similarity'] ? json_decode($r['emb_similarity'], true) : null,
                    'ec' => $r['emb_contrast'] ? json_decode($r['emb_contrast'], true) : null,
                ], earlier_constructs($pdo, $pid));
                $m = rgt_check_repetition(['s' => $sPole, 'c' => $cPole, 'es' => $es, 'ec' => $ec], $earlier, $cfg);
                [$auto, $nearest, $score, $backend] = [(int)$m['is_new'], $m['nearest'], $m['score'], $m['backend']];
            }
            $pdo->prepare('INSERT INTO constructs (participant_id, triad_id, is_practice, similarity_pole, contrast_pole,
                auto_is_new, nearest_construct, similarity_score, similarity_backend, emb_similarity, emb_contrast, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)')->execute([
                $pid, $t['triad_id'], (int)$isPractice, $sPole, $cPole, $auto, $nearest, $score, $backend,
                $es ? json_encode($es) : null, $ec ? json_encode($ec) : null, rgt_now()]);
            $cid = (int)$pdo->lastInsertId();
        }
        $pdo->prepare('INSERT INTO responses (triad_id, pair_a, pair_b, odd_one, no_difference, construct_id, rt_ms, submitted_at)
            VALUES (?,?,?,?,?,?,?,?)')->execute([$t['triad_id'], $pairIds[0], $pairIds[1], $odd, (int)$noDiff, $cid,
            isset($b['rt_ms']) ? (int)$b['rt_ms'] : null, rgt_now()]);
        $reason = null;
        if (!$isPractice) {
            $st = $cfg['stopping'];
            $reason = rgt_stop_reason(history($pdo, $pid, $cfg), elapsed_min($p), (int)$st['min_triads'],
                (int)$st['consecutive_not_new'], (int)$cfg['triads']['max_triads'], (float)$st['time_limit_min']);
            if ($reason) finish($pdo, $pid, $reason);
        }
        rgt_commit($pdo);
    } catch (DomainException $e) {
        rgt_rollback($pdo);
        [$code, $msg] = explode('|', $e->getMessage(), 2);
        fail((int)$code, $msg);
    } catch (Throwable $e) {
        rgt_rollback($pdo);
        error_log('[rgt] response: ' . $e->getMessage());
        fail(500, 'could not save the answer');
    }
    $result = ['construct_id' => $cid, 'done' => $reason !== null, 'stop_reason' => $reason];
    if ($p['mode'] === 'interviewer' && $cid && !$isPractice) {
        // The interviewer sees the earlier constructs, but NOT the automatic judgement.
        $result['earlier_constructs'] = array_map(fn($r) => ['construct_id' => (int)$r['construct_id'],
            'similarity_pole' => $r['similarity_pole'], 'contrast_pole' => $r['contrast_pole']],
            earlier_constructs($pdo, $pid, $cid));
    }
    out($result);

case 'POST judgement':
    if (!interviewer_token_ok($cfg)) fail(403, 'invalid interviewer password');
    $b = body();
    $cid = (int)($b['construct_id'] ?? 0);
    $st = $pdo->prepare('SELECT participant_id FROM constructs WHERE construct_id = ?');
    $st->execute([$cid]);
    if ($st->fetchColumn() !== ($b['participant_id'] ?? null)) fail(404, 'unknown construct');
    $new = !empty($b['judged_new']);
    $pdo->prepare('REPLACE INTO interviewer_judgements (construct_id, judged_new, same_as,
        revised_similarity_pole, revised_contrast_pole, note, judged_at) VALUES (?,?,?,?,?,?,?)')->execute([
        $cid, (int)$new, $new ? null : (isset($b['same_as']) ? (int)$b['same_as'] : null),
        str_or_null($b['revised_similarity_pole'] ?? null, 300), str_or_null($b['revised_contrast_pole'] ?? null, 300),
        str_or_null($b['note'] ?? null, 2000), rgt_now()]);
    out(['ok' => true]);

default:
    fail(404, 'unknown route');
}
