<?php
// Export / back up all tables as CSV files. Works with MariaDB/MySQL and SQLite.
//     php private/backup.php
// Writes private/dumps/dump-YYYYMMDD-HHMMSS/<table>.csv (NULL is written as \N).
// Download the folder with SFTP and convert it for the analysis scripts:
//     python scripts/import_dump.py path/to/dump-YYYYMMDD-HHMMSS
// Can also be run as a daily cron job. Delete old dumps from the server regularly.

declare(strict_types=1);
if (PHP_SAPI !== 'cli') { http_response_code(403); exit; }

require __DIR__ . '/lib/config.php';
require __DIR__ . '/lib/core.php';

const TABLES = ['elements', 'participants', 'triads', 'constructs', 'responses',
                'interviewer_judgements', 'play_events', 'ratings'];

$cfg = rgt_load_config(__DIR__, false);
$pdo = rgt_db($cfg);
$dir = __DIR__ . '/dumps/dump-' . gmdate('Ymd-His');
if (!mkdir($dir, 0700, true)) exit("cannot create $dir\n");

foreach (TABLES as $table) {
    $st = $pdo->query("SELECT * FROM $table");
    $fh = fopen("$dir/$table.csv", 'w');
    $n = 0;
    $header = null;
    while ($row = $st->fetch(PDO::FETCH_ASSOC)) {
        if ($header === null) { $header = array_keys($row); fputcsv($fh, $header, ',', '"', ''); }
        fputcsv($fh, array_map(fn($v) => $v === null ? '\N' : (string)$v, $row), ',', '"', '');
        $n++;
    }
    if ($header === null) {   // empty table: still write the column names
        $cols = rgt_is_sqlite($pdo)
            ? array_column($pdo->query("PRAGMA table_info($table)")->fetchAll(), 'name')
            : $pdo->query("SHOW COLUMNS FROM $table")->fetchAll(PDO::FETCH_COLUMN);
        fputcsv($fh, $cols, ',', '"', '');
    }
    fclose($fh);
    echo str_pad($table, 24) . "$n rows\n";
}
echo "dump written: $dir\n";
