<?php
// Set-up and check script. Run on the server via SSH:
//     php /path/to/private/setup.php --public-dir /path/to/httpdocs/rgt
//         check requirements, validate config, create/update the database tables
//     ... --set-token   additionally create a new interviewer password
//     ... --add-code "NOTE" MAX_USES [VALID_UNTIL]   create a group code for an invitation link
//     ... --set-code CODE MAX_USES [VALID_UNTIL|-]  change a code (MAX_USES 0 = unlimited)
//     ... --list-codes  show the group codes and how often each was used
//     ... --reset-data  delete all sessions and answers (test data), e.g. before a new clip set;
//                       asks for confirmation; make a backup first (php private/backup.php)
// deploy.sh runs this automatically. Without --public-dir, the web folder of the
// last deploy (private/web_dir.txt) is checked, or else the public/ folder next
// to this folder (repository layout, local testing).
//
// The script can also be run locally with the PHP command line.

declare(strict_types=1);

if (PHP_SAPI !== 'cli') {
    http_response_code(403);
    exit("Run this script from the command line.\n");
}

$privateDir = __DIR__;
$publicDir = dirname(__DIR__) . '/public';
// deploy.sh remembers the web folder of the server in web_dir.txt (not in git)
if (is_file(__DIR__ . '/web_dir.txt') && is_dir($d = trim((string)file_get_contents(__DIR__ . '/web_dir.txt')))) $publicDir = $d;
$i = array_search('--public-dir', $argv, true);
if ($i !== false && isset($argv[$i + 1])) $publicDir = rtrim($argv[$i + 1], '/');
$ok = true;

function line(string $status, string $msg): void { echo str_pad("[$status]", 8) . $msg . PHP_EOL; }

// 1. PHP version and extensions
if (version_compare(PHP_VERSION, '8.1.0', '<')) { line('FAIL', 'PHP 8.1 or newer is required, found ' . PHP_VERSION); $ok = false; }
else line('ok', 'PHP ' . PHP_VERSION);
foreach (['mbstring', 'json'] as $ext) {
    if (extension_loaded($ext)) line('ok', "extension $ext");
    else { line('FAIL', "PHP extension $ext is missing (enable it in the hosting control panel)"); $ok = false; }
}
if (!extension_loaded('intl')) line('note', 'extension intl not loaded (optional: Unicode normalisation of labels)');
if (!$ok) exit(1);

require $privateDir . '/lib/config.php';
require $privateDir . '/lib/core.php';

// 2. configuration
try {
    $cfg = rgt_load_config($privateDir);
    line('ok', 'config.ini valid (version ' . $cfg['study']['config_version'] . ')');
} catch (ConfigError $e) {
    line('FAIL', 'config.ini: ' . $e->getMessage());
    exit(1);
}

// 3. audio files
line('ok', "web folder checked: $publicDir");
$audio = $publicDir . '/audio';
$missing = [];
foreach (rgt_elements($cfg) as $e) if (!is_file("$audio/{$e['filename']}")) $missing[] = $e['filename'];
if ($cfg['triads']['practice_triad']) foreach ($cfg['stimuli']['practice_clips'] as $p) if (!is_file("$audio/$p")) $missing[] = $p;
if ($missing) { line('WARN', 'audio files missing in audio/: ' . implode(', ', $missing)); }
else line('ok', 'all clips found in audio/');
// Firefox does not play FLAC files with a tag (e.g. ID3) in front of the FLAC data
$bad = [];
foreach (glob("$audio/*.flac") ?: [] as $f) {
    if (file_get_contents($f, false, null, 0, 4) !== 'fLaC') $bad[] = basename($f);
    elseif ((fileperms($f) & 0004) === 0) $bad[] = basename($f) . ' (not readable by the web server: chmod 644)';
}
if ($bad) line('WARN', 'audio files that browsers may not play: ' . implode(', ', $bad)
    . ' (upload again with scripts/upload_server_files.sh)');
if ($cfg['headphone_check']['enabled']) {
    if (is_file("$privateDir/headphone_manifest.csv")) line('ok', 'headphone-check answer key found');
    else line('WARN', 'private/headphone_manifest.csv missing (run scripts/make_headphone_stimuli.py and upload it '
        . 'to the private folder, the sound files to audio/headphone/)');
}
if ($cfg['similarity']['backend'] === 'embedding') {
    $model = $publicDir . '/models/' . $cfg['similarity']['embedding_model'];
    if (is_file("$model/onnx/model_quantized.onnx")) line('ok', 'embedding model found');
    else line('WARN', "embedding model missing in models/{$cfg['similarity']['embedding_model']} "
        . '(run scripts/fetch_assets.py and upload); participants will fall back to text similarity');
    if (is_file($publicDir . '/static/vendor/transformers.min.js')) line('ok', 'transformers.js found');
    else line('WARN', 'static/vendor/transformers.min.js missing (run scripts/fetch_assets.py and upload)');
}

// 4. database
$driver = $cfg['server']['db_driver'];
$ext = $driver === 'mysql' ? 'pdo_mysql' : 'pdo_sqlite';
if (!extension_loaded($ext)) {
    line('FAIL', "PHP extension $ext is missing (needed for db_driver = \"$driver\")");
    exit(1);
}
line('ok', "extension $ext");
if (in_array('--reset-data', $argv, true)) {
    // delete ALL data (test sessions) so that a new clip set can be used; asks for confirmation
    echo "This deletes ALL sessions, answers and clips from the database. Make a backup first\n"
        . "(php private/backup.php). Type DELETE to continue: ";
    $answer = trim((string)fgets(STDIN));
    if ($answer !== 'DELETE') { line('note', 'nothing deleted'); exit(1); }
    $pdo = rgt_db($cfg);
    try { $pdo->exec('UPDATE constructs SET nearest_construct = NULL'); } catch (Throwable $e) {}
    foreach (['ratings', 'play_events', 'interviewer_judgements', 'responses', 'constructs', 'triads',
              'participants', 'elements', 'session_starts'] as $table) {
        try { $n = $pdo->exec("DELETE FROM $table"); line('ok', "deleted $n rows from $table"); }
        catch (Throwable $e) { line('note', "$table: " . $e->getMessage()); }
    }
}
try {
    rgt_init_db($cfg);
    if ($driver === 'sqlite') {
        $db = rgt_path($cfg, $cfg['server']['db_path']);
        @chmod($db, 0600);
        line('ok', "database ready: $db");
    } else {
        $cr = parse_ini_file(rgt_path($cfg, $cfg['server']['db_credentials']), false, INI_SCANNER_RAW);
        @chmod(rgt_path($cfg, $cfg['server']['db_credentials']), 0600);
        line('ok', "database ready: MySQL database '{$cr['dbname']}' on {$cr['host']}");
    }
} catch (Throwable $e) {
    line('FAIL', 'database: ' . $e->getMessage());
    exit(1);
}

// 5. group codes for the invitation links (design file 14)
$codesFile = rgt_path($cfg, $cfg['access']['codes_file']);
$i = array_search('--set-code', $argv, true);
if ($i !== false) {
    // change the maximum (0 = unlimited) and optionally the end date ("-" removes it) of an existing code
    $want = rgt_normalise_code($argv[$i + 1] ?? '');
    $maxArg = $argv[$i + 2] ?? '';
    $until = $argv[$i + 3] ?? null;
    if ($want === '' || !preg_match('/^\d+$/', $maxArg) || ($until !== null && $until !== '-' && !preg_match('/^\d{4}-\d{2}-\d{2}$/', $until))) {
        line('FAIL', 'usage: --set-code CODE MAX_USES [VALID_UNTIL as YYYY-MM-DD, or - for none]   (MAX_USES 0 = unlimited)');
        exit(1);
    }
    $rows = is_file($codesFile) ? rgt_read_csv($codesFile) : [];
    $found = false;
    foreach ($rows as &$r) {
        if (rgt_normalise_code((string)$r['code']) === $want) {
            $r['max_uses'] = (int)$maxArg;
            if ($until !== null) $r['valid_until'] = $until === '-' ? '' : $until;
            $found = true;
        }
    }
    unset($r);
    if (!$found) { line('FAIL', "code $want not found"); exit(1); }
    $h = fopen($codesFile, 'w');
    fputcsv($h, ['code', 'max_uses', 'valid_until', 'note'], ',', '"', '');
    foreach ($rows as $r) fputcsv($h, [$r['code'], $r['max_uses'], $r['valid_until'] ?? '', $r['note'] ?? ''], ',', '"', '');
    fclose($h);
    line('ok', "code $want changed");
}
$i = array_search('--add-code', $argv, true);
if ($i !== false) {
    $note = $argv[$i + 1] ?? '';
    $maxArg = $argv[$i + 2] ?? '';
    $max = (int)$maxArg;
    $until = $argv[$i + 3] ?? '';
    if ($note === '' || !preg_match('/^\d+$/', $maxArg) || ($until !== '' && !preg_match('/^\d{4}-\d{2}-\d{2}$/', $until))) {
        line('FAIL', 'usage: --add-code "NOTE" MAX_USES [VALID_UNTIL as YYYY-MM-DD]   (MAX_USES 0 = unlimited)');
        exit(1);
    }
    try {
        $pretty = rgt_add_access_code($cfg, $note, $max, $until);
    } catch (InvalidArgumentException $e) {
        line('FAIL', $e->getMessage());
        exit(1);
    }
    line('ok', "new code $pretty for \"$note\" (" . ($max > 0 ? "max. $max sessions" : 'unlimited sessions')
        . ($until ? ", until $until" : '') . ')');
    $site = $cfg['access']['site_url'] ?: 'https://<your site>/rgt/';
    echo "        invitation link: " . rtrim($site, '/') . "/?c=$pretty\n";
}
if ($cfg['access']['require_code']) {
    $codes = rgt_access_codes($cfg);
    if (!$codes) line('WARN', "no group codes yet: online sessions cannot start (php private/setup.php --add-code \"NOTE\" MAX_USES)");
    else line('ok', count($codes) . ' group code(s) in ' . $cfg['access']['codes_file']);
    if (in_array('--list-codes', $argv, true)) {
        $uses = rgt_code_uses(rgt_db($cfg));
        foreach ($codes as $c => $r) {
            $u = $uses[$c] ?? ['n' => 0, 'done' => 0];
            printf("        %s-%s  used %d of %s (finished %d)%s  %s\n", substr($c, 0, 4), substr($c, 4), $u['n'],
                (int)$r['max_uses'] > 0 ? (string)(int)$r['max_uses'] : 'unlimited', $u['done'], ($r['valid_until'] ?? '') !== '' ? ", until {$r['valid_until']}" : '', $r['note'] ?? '');
        }
    }
} else {
    line('note', 'group codes are switched off ([access] require_code = false): anyone with the link can start');
}

// 6. interviewer password
$tokenFile = $privateDir . '/interviewer_token.hash';
if (in_array('--set-token', $argv, true)) {
    $token = rtrim(strtr(base64_encode(random_bytes(12)), '+/', '-_'), '=');
    file_put_contents($tokenFile, password_hash($token, PASSWORD_DEFAULT));
    @chmod($tokenFile, 0600);
    line('ok', 'new interviewer password: ' . $token);
    echo "        Keep it safe. Only a hash is stored on the server.\n";
} elseif (!is_file($tokenFile)) {
    line('note', 'no interviewer password yet (php private/setup.php --set-token)');
}

// 7. the private folder must not be inside the web folder
$realPriv = realpath($privateDir) ?: $privateDir;
$realPub = realpath($publicDir) ?: $publicDir;
if (str_starts_with($realPriv . '/', $realPub . '/')) {
    line('WARN', 'the private folder is inside the web folder; it is then protected only by .htaccess');
} else {
    line('ok', 'private folder is outside the web folder');
}
