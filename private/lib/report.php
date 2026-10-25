<?php
// Analysis report (design: report section of README). Used by
//   private/report.php  (command line: writes an HTML file)
//   public/report.php   (web page behind the interviewer password, built fresh on every visit)

declare(strict_types=1);

const RGT_R_SCRIPT_UA = '/python|curl|wget|headless|bot\b|crawler|spider|selenium|playwright|puppeteer|phantom|httpclient|go-http|java\/|okhttp|node-fetch|axios/i';
const RGT_R_GENERIC = ['good', 'bad', 'nice', 'not nice', 'ok', 'okay', 'yes', 'no', 'a', 'b', 'c', 'x', 'y', 'gut', 'schlecht',
                       'schön', 'nicht schön', 'ja', 'nein', 'same', 'different', 'gleich', 'anders', 'test', 'asdf'];

// ------------------------------------------------------------------ helpers

function rgt_r_rows(PDO $pdo, string $table): array {
    return array_map(fn($r) => array_map(fn($v) => $v === null ? null : (string)$v, $r),
                     $pdo->query("SELECT * FROM $table")->fetchAll(PDO::FETCH_ASSOC));
}
function rgt_r_ts(?string $s): ?DateTimeImmutable {
    if ($s === null || $s === '') return null;
    try { return new DateTimeImmutable(str_replace(' ', 'T', $s)); } catch (Exception $e) { return null; }
}
function rgt_r_secs(DateTimeImmutable $a, DateTimeImmutable $b): float {
    return (float)$b->format('U.u') - (float)$a->format('U.u');
}
function rgt_r_median(array $a): ?float {
    if (!$a) return null;
    sort($a);
    $n = count($a);
    return $n % 2 ? (float)$a[intdiv($n, 2)] : ($a[$n / 2 - 1] + $a[$n / 2]) / 2;
}
function rgt_r_low(string $s): string { return mb_strtolower(trim($s)); }
function rgt_r_short(string $pid): string { return str_starts_with($pid, 'P-') ? substr($pid, 2, 8) : substr($pid, 0, 8); }
function rgt_r_gibberish(?string $s): bool {
    if ($s === null || $s === '') return false;
    $t = rgt_r_low($s);
    if (mb_strlen($t) < 3 || in_array($t, RGT_R_GENERIC, true) || preg_match('/^(.)\1+$/u', $t)) return true;
    $letters = preg_replace('/[^a-zäöüß]/u', '', $t);
    return mb_strlen($letters) >= 4 && !preg_match('/[aeiouyäöü]/u', $letters);
}
function rgt_r_browser(string $ua): string {
    if ($ua === '') return '–';
    foreach (['Edge' => 'Edg/', 'Firefox' => 'Firefox/', 'Chrome' => 'Chrome/', 'Safari' => 'Safari/'] as $name => $pat) {
        if (str_contains($ua, $pat)) {
            $os = str_contains($ua, 'Mac OS') ? 'macOS' : (str_contains($ua, 'Windows') ? 'Windows' : (str_contains($ua, 'Linux') ? 'Linux' : ''));
            return trim("$name $os");
        }
    }
    return substr($ua, 0, 30);
}

/** The whole report page as HTML, built from the live database. */
function rgt_report_html(array $cfg, PDO $pdo): string {
    // ------------------------------------------------------------------ data

    $groups = [];
    foreach (rgt_access_codes($cfg) as $code => $r) $groups[$code] = trim((string)($r['note'] ?? '')) ?: $code;

    $recordings = [];
    $clipsCsv = dirname(__DIR__, 2) . '/stimuli/isd_clips.csv';
    if (is_file($clipsCsv)) foreach (rgt_read_csv($clipsCsv) as $r) $recordings[$r['file']] = $r;

    $clips = [];
    foreach (rgt_r_rows($pdo, 'elements') as $e) {
        if ($e['is_practice'] === '1') continue;
        $name = str_replace('.flac', '', $e['filename']);
        if (isset($recordings[$e['filename']])) $name .= " · {$recordings[$e['filename']]['GroupID']} ({$recordings[$e['filename']]['LocationID']})";
        if (($e['category'] ?? '') !== '') $name .= " · {$e['category']}";
        $clips[(int)$e['element_id']] = $name;
    }
    ksort($clips);

    $participants = rgt_r_rows($pdo, 'participants');
    $triads = [];
    foreach (rgt_r_rows($pdo, 'triads') as $t) $triads[$t['triad_id']] = $t;
    $constructs = [];
    foreach (rgt_r_rows($pdo, 'constructs') as $c) $constructs[$c['construct_id']] = $c;
    $respByP = [];
    foreach (rgt_r_rows($pdo, 'responses') as $r) {
        if (isset($triads[$r['triad_id']])) $respByP[$triads[$r['triad_id']]['participant_id']][] = $r;
    }
    $plays = [];
    foreach (rgt_r_rows($pdo, 'play_events') as $e) $plays[$e['triad_id']][] = $e;

    // label pairs per participant, to find identical answers across sessions
    $labelset = [];
    foreach ($constructs as $c) {
        if ($c['is_practice'] === '1') continue;
        $labelset[$c['participant_id']][rgt_r_low($c['similarity_pole']) . "\x1f" . rgt_r_low($c['contrast_pole'])] = true;
    }

    // ------------------------------------------------------------------ analysis

    $people = $answerRows = [];
    foreach ($participants as $p) {
        $pid = $p['participant_id'];
        $code = rgt_normalise_code((string)($p['access_code'] ?? ''));
        $group = $groups[$code] ?? ($p['access_code'] ?? ($p['mode'] === 'interviewer' ? 'interviewer' : 'no code'));
        $rs = $respByP[$pid] ?? [];
        usort($rs, fn($a, $b) => strcmp($a['submitted_at'], $b['submitted_at']));
        $real = array_values(array_filter($rs, fn($r) => (int)$triads[$r['triad_id']]['order_index'] >= 1));
        $rts = array_values(array_map(fn($r) => (int)$r['rt_ms'] / 1000, array_filter($real, fn($r) => $r['rt_ms'] !== null)));
        $snap = json_decode((string)$p['config_snapshot'], true);
        $clipS = (float)($snap['stimuli']['clip_duration_s'] ?? 15);

        $listen = [];
        $replays = 0;
        foreach ($real as $r) {
            $evs = $plays[$r['triad_id']] ?? [];
            $replays += max(0, count(array_filter($evs, fn($e) => $e['event'] === 'play')) - 3);
            // listening time by the server clock: first "play" to last "ended"
            $st = array_filter(array_map(fn($e) => $e['event'] === 'play' ? rgt_r_ts($e['logged_at']) : null, $evs));
            $en = array_filter(array_map(fn($e) => $e['event'] === 'ended' ? rgt_r_ts($e['logged_at']) : null, $evs));
            if ($st && $en) $listen[] = rgt_r_secs(min($st), max($en));
        }
        $started = rgt_r_ts($p['started_at']);
        $finished = rgt_r_ts($p['finished_at'] ?? null);
        $end = $finished ?? ($rs ? rgt_r_ts(end($rs)['submitted_at']) : null);
        $dur = ($started && $end) ? rgt_r_secs($started, $end) / 60 : null;
        $cons = [];
        foreach ($real as $r) if ($r['construct_id'] !== null && isset($constructs[$r['construct_id']])) $cons[] = $constructs[$r['construct_id']];
        $langs = array_unique(array_filter(array_map(fn($r) => $r['language'] ?? null, $rs)));

        // plausibility checks
        $flags = [];
        $minRt = 3 * $clipS;
        $medRt = rgt_r_median($rts);
        $fast = count(array_filter($rts, fn($x) => $x < $minRt));
        if ($medRt !== null && $medRt < $minRt + 15) $flags[] = sprintf('median answer time %.0f s (listening alone takes %.0f s)', $medRt, $minRt);
        elseif ($fast) $flags[] = sprintf('%d answer(s) faster than the three clips last (%.0f s)', $fast, $minRt);
        $medListen = rgt_r_median($listen);
        if ($medListen !== null && $medListen < 0.9 * $clipS) $flags[] = sprintf('server-side listening per comparison only %.0f s', $medListen);
        $ua = (string)($p['user_agent'] ?? '');
        if ($ua === '') $flags[] = 'no browser string';
        elseif (preg_match(RGT_R_SCRIPT_UA, $ua)) $flags[] = 'browser string of a script or automated browser';
        $texts = [];
        foreach ($cons as $c) { $texts[] = $c['similarity_pole']; $texts[] = $c['contrast_pole']; }
        if ($texts) {
            $gen = count(array_filter($texts, fn($t) => in_array(rgt_r_low($t), RGT_R_GENERIC, true)));
            if ($gen / count($texts) >= 0.5) $flags[] = "$gen of " . count($texts) . ' labels generic (good/bad, …)';
            $pairs = array_count_values(array_map(fn($c) => rgt_r_low($c['similarity_pole']) . "\x1f" . rgt_r_low($c['contrast_pole']), $cons));
            $rep = array_sum(array_map(fn($n) => $n - 1, $pairs));
            if ($rep >= 2) $flags[] = "$rep labels repeated word for word";
            $meanLen = array_sum(array_map('mb_strlen', $texts)) / count($texts);
            if ($meanLen > 45) $flags[] = sprintf('very long labels (mean %.0f characters)', $meanLen);
            $sentences = count(array_filter($texts, fn($t) => count(preg_split('/\s+/u', trim($t))) >= 8));
            if ($sentences >= 2 && $meanLen <= 45) $flags[] = "$sentences labels are whole sentences";
        }
        // identical answers in another session; short common pairs ("calm - loud") are expected to
        // recur between real people, so only distinctive pairs (at least 25 characters together) count
        $mine = array_filter(array_keys($labelset[$pid] ?? []), fn($k) => mb_strlen($k) - 1 >= 25);
        if (count($mine) >= 2) {
            foreach ($labelset as $other => $theirs) {
                if ($other === $pid) continue;
                $same = count(array_filter($mine, fn($k) => isset($theirs[$k])));
                if ($same >= 2) { $flags[] = "$same distinctive labels identical to session " . rgt_r_short($other); break; }
            }
        }
        if (rgt_r_gibberish($p['headphones_model'] ?? null)) $flags[] = 'device model looks random (“' . $p['headphones_model'] . '”)';
        if ($dur !== null && $finished && count($real) >= 6 && $dur < 6 * $minRt / 60 + 5)
            $flags[] = sprintf('finished %d comparisons in %.0f min', count($real), $dur);
        $demo = array_map(fn($k) => $p[$k] ?? null, ['age_group', 'gender', 'first_language', 'sound_expertise', 'residence']);
        if (!array_filter($demo, fn($d) => $d !== null && $d !== 'na')) $flags[] = 'no demographic answer given (weak sign)';

        $short = rgt_r_short($pid);
        $people[] = [
            'pid' => $pid, 'short' => $short, 'group' => $group, 'code' => $code,
            'mode' => $p['mode'], 'language' => $p['language'], 'lang_switch' => count($langs) > 1,
            'started' => $p['started_at'], 'finished' => $p['finished_at'] ?? null, 'stop' => $p['stop_reason'] ?? null,
            'n_answers' => count($real), 'n_constructs' => count($cons),
            'n_new' => count(array_filter($cons, fn($c) => $c['auto_is_new'] === '1')),
            'n_nodiff' => count(array_filter($real, fn($r) => $r['no_difference'] === '1')),
            'hints' => count(array_filter($real, fn($r) => !in_array($r['hints_shown'] ?? null, [null, '', '[]'], true))),
            'dur_min' => $dur !== null ? round($dur, 1) : null,
            'rt_median' => $medRt !== null ? (int)round($medRt) : null,
            'rt_min' => $rts ? (int)round(min($rts)) : null,
            'listen_median' => $medListen !== null ? (int)round($medListen) : null,
            'replays' => $replays,
            'device' => $p['listening_device'] ?? null, 'environment' => $p['listening_environment'] ?? null,
            'model' => $p['headphones_model'] ?? null, 'hearing' => $p['hearing_problems'] ?? null,
            'age' => $p['age_group'] ?? null, 'gender' => $p['gender'] ?? null,
            'first_language' => $p['first_language'] ?? null, 'first_language_other' => $p['first_language_other'] ?? null,
            'expertise' => $p['sound_expertise'] ?? null, 'residence' => $p['residence'] ?? null,
            'ua' => $ua, 'browser' => rgt_r_browser($ua), 'config' => $p['config_version'],
            'excluded' => $p['excluded'] === '1', 'flags' => $flags,
        ];
        foreach ($real as $r) {
            $t = $triads[$r['triad_id']];
            $c = $r['construct_id'] !== null ? ($constructs[$r['construct_id']] ?? null) : null;
            $answerRows[] = [
                'pid' => $pid, 'short' => $short, 'group' => $group, 'n' => (int)$t['order_index'],
                'shown' => [(int)$t['pos_a'], (int)$t['pos_b'], (int)$t['pos_c']],
                'pair' => array_values(array_map('intval', array_filter([$r['pair_a'], $r['pair_b']], fn($x) => $x !== null))),
                'odd' => $r['odd_one'] !== null ? (int)$r['odd_one'] : null,
                'nodiff' => $r['no_difference'] === '1',
                'sim' => $c['similarity_pole'] ?? null, 'con' => $c['contrast_pole'] ?? null,
                'new' => ($c && $c['auto_is_new'] !== null) ? $c['auto_is_new'] === '1' : null,
                'score' => ($c && $c['similarity_score'] !== null) ? round((float)$c['similarity_score'], 2) : null,
                'rt' => $r['rt_ms'] !== null ? (int)round((int)$r['rt_ms'] / 1000) : null,
                'lang' => $r['language'] ?? null,
            ];
        }
    }

    // ------------------------------------------------------------------ page

    $data = ['people' => $people, 'rows' => $answerRows, 'clips' => (object)array_combine(array_map('strval', array_keys($clips)), $clips),
             'dump' => 'database, ' . gmdate('Y-m-d H:i') . ' UTC', 'made' => gmdate('Y-m-d H:i') . ' UTC'];
    $json = json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_HEX_TAG | JSON_INVALID_UTF8_SUBSTITUTE);
    $page = str_replace('__DATA__', $json, file_get_contents(dirname(__DIR__) . '/report_template.html'));

    return $page;
}
