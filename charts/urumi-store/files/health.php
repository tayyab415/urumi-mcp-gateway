<?php
// Do not load WordPress: its installation wizard and redirects must stay usable.
header('Content-Type: application/json');
header('Cache-Control: no-store');
if (($_GET['check'] ?? '') === 'live') {
    echo '{"status":"ok"}';
    exit;
}
mysqli_report(MYSQLI_REPORT_OFF);
$db = mysqli_init();
$db->options(MYSQLI_OPT_CONNECT_TIMEOUT, 3);
$ready = @$db->real_connect(
    getenv('WORDPRESS_DB_HOST'),
    getenv('WORDPRESS_DB_USER'),
    getenv('WORDPRESS_DB_PASSWORD'),
    getenv('WORDPRESS_DB_NAME'),
    3306
);
if ($ready) {
    $ready = (bool) $db->query('SELECT 1');
    $db->close();
}
http_response_code($ready ? 200 : 503);
echo $ready ? '{"status":"ok"}' : '{"status":"unavailable"}';
