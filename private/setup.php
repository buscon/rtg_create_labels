<?php
// Set-up and check script. Run on the server via SSH:
//     php /path/to/private/setup.php --public-dir /path/to/httpdocs/rgt
//         check requirements, validate config, create/update the database tables
//     ... --set-token   additionally create a new interviewer password
// deploy.sh runs this automatically. Without --public-dir, the public/ folder
// next to this folder is checked (repository layout, local testing).
//
// The script can also be run locally with the PHP command line.

declare(strict_types=1);

if (PHP_SAPI !== 'cli') {
    http_response_code(403);
    exit("Run this script from the command line.\n");
}

$privateDir = __DIR__;
$publicDir = dirname(__DIR__) . '/public';
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
$audio = $publicDir . '/audio';
$missing = [];
foreach (rgt_elements($cfg) as $e) if (!is_file("$audio/{$e['filename']}")) $missing[] = $e['filename'];
if ($cfg['triads']['practice_triad']) foreach ($cfg['stimuli']['practice_clips'] as $p) if (!is_file("$audio/$p")) $missing[] = $p;
if ($missing) { line('WARN', 'audio files missing in audio/: ' . implode(', ', $missing)); }
else line('ok', 'all clips found in audio/');
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

// 5. interviewer password
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

// 6. the private folder must not be inside the web folder
$realPriv = realpath($privateDir) ?: $privateDir;
$realPub = realpath($publicDir) ?: $publicDir;
if (str_starts_with($realPriv . '/', $realPub . '/')) {
    line('WARN', 'the private folder is inside the web folder; it is then protected only by .htaccess');
} else {
    line('ok', 'private folder is outside the web folder');
}
