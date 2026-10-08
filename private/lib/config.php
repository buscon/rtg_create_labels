<?php
// Load and validate config.ini. If a value is impossible, the API refuses to
// run and names the problem, so that a typo cannot silently change sessions.

declare(strict_types=1);

final class ConfigError extends RuntimeException {}

const RGT_LIST_KEYS = [
    'study' => ['languages'],
    'stimuli' => ['practice_clips'],
    'interviewer' => ['followup_de', 'followup_en'],
];

function rgt_split_list(string $v): array {
    return array_values(array_filter(array_map('trim', explode('|', $v)), fn($x) => $x !== ''));
}

function rgt_load_config(string $privateDir, bool $checkFiles = true): array {
    $file = $privateDir . '/config.ini';
    if (!is_file($file)) {
        throw new ConfigError("configuration file not found: $file");
    }
    $raw = parse_ini_file($file, true, INI_SCANNER_TYPED);
    if ($raw === false) {
        throw new ConfigError('config.ini could not be parsed (check quotes and brackets)');
    }
    foreach (RGT_LIST_KEYS as $section => $keys) {
        foreach ($keys as $k) {
            if (isset($raw[$section][$k])) {
                $raw[$section][$k] = rgt_split_list((string)$raw[$section][$k]);
            }
        }
    }
    $raw['_private_dir'] = $privateDir;
    rgt_validate_config($raw, $checkFiles);
    return $raw;
}

function rgt_path(array $cfg, string $rel): string {
    return ($rel !== '' && $rel[0] === '/') ? $rel : $cfg['_private_dir'] . '/' . $rel;
}

function rgt_read_csv(string $file): array {
    $rows = [];
    if (($h = fopen($file, 'r')) === false) {
        throw new ConfigError("cannot read $file");
    }
    $head = fgetcsv($h, null, ',', '"', '');
    while (($r = fgetcsv($h, null, ',', '"', '')) !== false) {
        if ($r === [null]) continue;
        $rows[] = array_combine($head, $r);
    }
    fclose($h);
    return $rows;
}

function rgt_elements(array $cfg): array {
    return array_map(fn($r) => [
        'element_id' => (int)$r['element_id'],
        'filename' => $r['filename'],
        'category' => ($r['category'] ?? '') !== '' ? $r['category'] : null,
    ], rgt_read_csv(rgt_path($cfg, $cfg['stimuli']['elements_file'])));
}

function rgt_triad_set(array $cfg): array {
    return array_map(fn($r) => [(int)$r['triad'], (int)$r['clip_a'], (int)$r['clip_b'], (int)$r['clip_c']],
        rgt_read_csv(rgt_path($cfg, $cfg['triads']['set_file'])));
}

function rgt_validate_config(array $c, bool $checkFiles): void {
    $required = [
        'study' => ['config_version', 'languages', 'default_language'],
        'stimuli' => ['elements_file', 'n_clips', 'clip_duration_s', 'practice_clips', 'require_full_play'],
        'triads' => ['set_file', 'max_triads', 'randomise_clip_mapping', 'randomise_order',
                     'randomise_positions', 'practice_triad'],
        'labels' => ['min_words', 'max_chars'],
        'stopping' => ['min_triads', 'consecutive_not_new', 'time_limit_min', 'no_difference_counts_as_not_new'],
        'similarity' => ['backend', 'threshold_embedding', 'threshold_text', 'embedding_model',
                         'embedding_wait_s', 'compare_both_orientations'],
        'headphone_check' => ['enabled', 'n_trials', 'pass_min_correct'],
        'device' => ['block_mobile'],
        'interviewer' => ['enabled'],
        'server' => ['db_driver'],
    ];
    $e = [];
    foreach ($required as $s => $keys) {
        if (!isset($c[$s])) { $e[] = "missing section [$s]"; continue; }
        foreach ($keys as $k) {
            if (!array_key_exists($k, $c[$s])) $e[] = "missing value [$s] $k";
        }
    }
    if ($e) throw new ConfigError(implode('; ', $e));

    if (!in_array($c['study']['default_language'], $c['study']['languages'], true))
        $e[] = 'default_language must be one of languages';
    foreach ($c['study']['languages'] as $l) {
        if (!isset($c['prompt']["text_$l"])) $e[] = "[prompt] text_$l missing";
    }
    foreach (['threshold_embedding', 'threshold_text'] as $k) {
        $v = $c['similarity'][$k];
        if (!is_numeric($v) || $v <= 0 || $v > 1) $e[] = "[similarity] $k must be between 0 and 1";
    }
    if (!in_array($c['similarity']['backend'], ['embedding', 'text'], true))
        $e[] = "[similarity] backend must be \"embedding\" or \"text\"";
    $st = $c['stopping'];
    if ($st['min_triads'] < 1) $e[] = 'min_triads must be at least 1';
    if ($st['min_triads'] > $c['triads']['max_triads']) $e[] = 'min_triads must not be larger than max_triads';
    if ($st['consecutive_not_new'] < 1) $e[] = 'consecutive_not_new must be at least 1';
    if ($st['time_limit_min'] <= 0) $e[] = 'time_limit_min must be positive';
    if ($c['labels']['max_chars'] < 5) $e[] = 'max_chars is too small';
    $hp = $c['headphone_check'];
    if ($hp['enabled'] && ($hp['pass_min_correct'] < 1 || $hp['pass_min_correct'] > $hp['n_trials']))
        $e[] = 'pass_min_correct must be between 1 and n_trials';
    if ($c['triads']['practice_triad'] && count($c['stimuli']['practice_clips']) !== 3)
        $e[] = 'practice_clips must list exactly 3 files';

    $drv = $c['server']['db_driver'];
    if (!in_array($drv, ['mysql', 'sqlite'], true)) $e[] = '[server] db_driver must be "mysql" or "sqlite"';
    if ($drv === 'sqlite' && empty($c['server']['db_path'])) $e[] = '[server] db_path missing';
    if ($drv === 'mysql' && empty($c['server']['db_credentials'])) $e[] = '[server] db_credentials missing';

    if ($checkFiles && !$e) {
        foreach (['elements_file' => $c['stimuli']['elements_file'], 'set_file' => $c['triads']['set_file']] as $k => $p) {
            if (!is_file(rgt_path($c, $p))) $e[] = "file not found: $p";
        }
        if (!$e) {
            $elements = rgt_elements($c);
            $triads = rgt_triad_set($c);
            if (count($elements) !== (int)$c['stimuli']['n_clips'])
                $e[] = 'elements_file has ' . count($elements) . ' clips, n_clips is ' . $c['stimuli']['n_clips'];
            foreach ($triads as $t) {
                foreach (array_slice($t, 1) as $n) {
                    if ($n < 1 || $n > $c['stimuli']['n_clips']) { $e[] = 'triad set refers to clip numbers outside 1..n_clips'; break 2; }
                }
            }
            if ($c['triads']['max_triads'] > count($triads))
                $e[] = "max_triads ({$c['triads']['max_triads']}) exceeds the " . count($triads) . ' triads in set_file';
        }
    }
    if ($e) throw new ConfigError(implode('; ', $e));
}

function rgt_public_config(array $c): array {
    $langs = $c['study']['languages'];
    $prompt = $follow = [];
    foreach ($langs as $l) {
        $prompt[$l] = $c['prompt']["text_$l"];
        $follow[$l] = $c['interviewer']["followup_$l"] ?? [];
    }
    return [
        'languages' => $langs,
        'default_language' => $c['study']['default_language'],
        'clip_duration_s' => $c['stimuli']['clip_duration_s'],
        'require_full_play' => (bool)$c['stimuli']['require_full_play'],
        'allow_replay' => (bool)($c['stimuli']['allow_replay'] ?? true),
        'prompt' => $prompt,
        'labels' => ['min_words' => $c['labels']['min_words'], 'max_chars' => $c['labels']['max_chars']],
        'headphone_check' => ['enabled' => (bool)$c['headphone_check']['enabled'],
                              'n_trials' => $c['headphone_check']['n_trials']],
        'block_mobile' => (bool)$c['device']['block_mobile'],
        'similarity' => ['backend' => $c['similarity']['backend'],
                         'model' => $c['similarity']['embedding_model'],
                         'wait_s' => $c['similarity']['embedding_wait_s']],
        'followup_questions' => $follow,
        // the first practice clip is used to set the volume
        'calibration_url' => $c['stimuli']['practice_clips']
            ? 'audio/' . rawurlencode($c['stimuli']['practice_clips'][0]) : null,
    ];
}
