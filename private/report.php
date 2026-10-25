<?php
// Analysis report of the live database as one self-contained HTML file.
//     php private/report.php [--out FILE]
// Default output: private/dumps/report-YYYYMMDD-HHMMSS.html (not reachable from the web).
// The same page is available in the browser at <study URL>/report.php (interviewer password),
// built fresh on every visit. It contains participant data: keep downloaded copies private.

declare(strict_types=1);
if (PHP_SAPI !== 'cli') { http_response_code(403); exit; }

require __DIR__ . '/lib/config.php';
require __DIR__ . '/lib/core.php';
require __DIR__ . '/lib/report.php';

$out = null;
$i = array_search('--out', $argv, true);
if ($i !== false) $out = $argv[$i + 1] ?? null;

$cfg = rgt_load_config(__DIR__, false);
$page = rgt_report_html($cfg, rgt_db($cfg));
if ($out === null) {
    $dir = __DIR__ . '/dumps';
    if (!is_dir($dir)) mkdir($dir, 0700, true);
    $out = "$dir/report-" . gmdate('Ymd-His') . '.html';
}
file_put_contents($out, $page);
@chmod($out, 0600);
echo "written: $out\n";
