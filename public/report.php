<?php
// Analysis report in the browser: <study URL>/report.php
// Protected by the interviewer password (php private/setup.php --set-token). After logging in, the page
// is built fresh from the live database on every visit (reload = current data). The login lasts
// until the browser is closed, or at most 2 hours. ?logout ends it.
// Logged in, the page can also create new group codes (form under the group table); they are
// appended to private/access_codes.csv, exactly like  php private/setup.php --add-code.
// The page shows participant data: open it only on your own devices.

declare(strict_types=1);

$privateDir = is_file(__DIR__ . '/private_path.php') ? require __DIR__ . '/private_path.php' : dirname(__DIR__) . '/private';
require $privateDir . '/lib/config.php';
require $privateDir . '/lib/core.php';
require $privateDir . '/lib/report.php';

header('Cache-Control: no-store');
header('X-Robots-Tag: noindex, nofollow');
header('X-Frame-Options: DENY');
header('Referrer-Policy: no-referrer');

const REPORT_LOGIN_S = 7200;

session_name('rgt_report');
session_set_cookie_params(['lifetime' => 0, 'path' => rtrim(dirname($_SERVER['SCRIPT_NAME']), '/') . '/',
                           'secure' => !empty($_SERVER['HTTPS']), 'httponly' => true, 'samesite' => 'Strict']);
session_start();

function login_page(string $msg = ''): never {
    header('Content-Type: text/html; charset=utf-8');
    $m = $msg ? '<p style="color:#a33b2f">' . htmlspecialchars($msg) . '</p>' : '';
    echo <<<HTML
<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>RGT analysis – login</title>
<style>body{font:15px system-ui,sans-serif;background:#f6f5f1;color:#1f2421;display:grid;place-items:center;min-height:90vh;margin:0}
@media (prefers-color-scheme:dark){body{background:#161917;color:#e6e8e5}input{background:#1f2321;color:#e6e8e5}}
form{display:grid;gap:10px;width:min(320px,90vw)}input,button{font:inherit;padding:8px 10px;border-radius:8px;border:1px solid #ccc}
button{background:#2e6b4f;color:#fff;border:0;cursor:pointer}</style></head>
<body><form method="post"><h1 style="font-size:20px;margin:0">Analysis</h1>$m
<input type="password" name="password" placeholder="Interviewer password" autocomplete="current-password" autofocus required>
<button>Show report</button></form></body></html>
HTML;
    exit;
}

try {
    $cfg = rgt_load_config($privateDir);
} catch (Throwable $e) {
    http_response_code(500);
    exit('configuration error');
}

if (isset($_GET['logout'])) {
    $_SESSION = [];
    session_destroy();
    login_page('Logged out.');
}

$loggedIn = ($_SESSION['report_until'] ?? 0) >= time();

// new group (only when logged in, with the form token of this session)
if ($_SERVER['REQUEST_METHOD'] === 'POST' && ($_POST['action'] ?? '') === 'add_group') {
    if (!$loggedIn) login_page();
    if (!hash_equals((string)($_SESSION['csrf'] ?? ''), (string)($_POST['csrf'] ?? ''))) {
        $_SESSION['flash'] = ['err', 'The form has expired. Please try again.'];
    } else {
        try {
            $name = (string)($_POST['name'] ?? '');
            $code = rgt_add_access_code($cfg, $name, (int)($_POST['max'] ?? 0), trim((string)($_POST['until'] ?? '')));
            $link = rtrim((string)$cfg['access']['site_url'], '/') . "/?c=$code";
            $_SESSION['flash'] = ['ok', "New group “" . trim($name) . "”: code $code, link $link"];
            error_log("[rgt] report.php: new group code $code");
        } catch (Throwable $e) {
            $_SESSION['flash'] = ['err', 'No group created: ' . $e->getMessage()];
        }
    }
    header('Location: ' . strtok($_SERVER['REQUEST_URI'], '?') . '#t-codes', true, 303);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $hashFile = $cfg['_private_dir'] . '/interviewer_token.hash';
    $given = (string)($_POST['password'] ?? '');
    if ($given !== '' && is_file($hashFile) && password_verify($given, trim((string)file_get_contents($hashFile)))) {
        session_regenerate_id(true);
        $_SESSION['report_until'] = time() + REPORT_LOGIN_S;
        $_SESSION['csrf'] = bin2hex(random_bytes(16));
        header('Location: ' . strtok($_SERVER['REQUEST_URI'], '?'), true, 303);   // reload with GET
        exit;
    }
    sleep(2);                                                                    // slows down guessing
    error_log('[rgt] failed login to report.php');
    login_page('Wrong password.');
}

if (!$loggedIn) login_page();

try {
    $page = rgt_report_html($cfg, rgt_db($cfg));
} catch (Throwable $e) {
    error_log('[rgt] report: ' . $e->getMessage());
    http_response_code(500);
    exit('The report could not be built (see the PHP error log).');
}
// form for a new group; the default end date is the most common one of the existing codes
$untils = array_filter(array_map(fn($r) => trim((string)($r['valid_until'] ?? '')), rgt_access_codes($cfg)));
$defUntil = $untils ? array_search(max(array_count_values($untils)), array_count_values($untils), true) : '';
$flash = $_SESSION['flash'] ?? null;
unset($_SESSION['flash']);
$h = fn($s) => htmlspecialchars((string)$s, ENT_QUOTES);
$admin = ($flash ? '<p class="notice' . ($flash[0] === 'err' ? ' err' : '') . '">' . $h($flash[1]) . '</p>' : '')
    . '<form method="post" class="addgroup"><input type="hidden" name="action" value="add_group">'
    . '<input type="hidden" name="csrf" value="' . $h($_SESSION['csrf'] ?? '') . '">'
    . '<label>New group<input name="name" required maxlength="60" placeholder="e.g. Seminar WS26"></label>'
    . '<label>Max. sessions (0 = unlimited)<input name="max" type="number" min="0" max="100000" value="100" style="width:110px"></label>'
    . '<label>Valid until (empty = no end)<input name="until" type="date" value="' . $h($defUntil) . '"></label>'
    . '<button>Create link</button></form>';
$page = str_replace('<!--ADMIN-->', $admin, $page);

header('Content-Type: text/html; charset=utf-8');
// small bar with reload / logout on top of the report
echo str_replace('<header>', '<header><div style="float:right;font-size:13px"><a href="report.php">reload</a> · '
    . '<a href="report.php?logout">log out</a></div>', $page);
