-- MySQL / MariaDB schema of the repertory grid elicitation tool (design file 06).
-- Tested with MySQL 8.0 and MariaDB 10.11. Same tables and columns as schema.sqlite.sql. Timestamps are ISO 8601 strings (UTC).

CREATE TABLE IF NOT EXISTS elements (
    element_id    INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    filename      VARCHAR(255) NOT NULL UNIQUE,
    category      VARCHAR(100) NULL,          -- never sent to the browser
    is_practice   TINYINT NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS participants (
    participant_id     VARCHAR(32) NOT NULL PRIMARY KEY,
    mode               VARCHAR(20) NOT NULL,       -- 'online' | 'interviewer'
    invited_mode       VARCHAR(20) NULL,
    language           VARCHAR(10) NOT NULL,
    started_at         VARCHAR(40) NOT NULL,
    triads_started_at  VARCHAR(40) NULL,
    finished_at        VARCHAR(40) NULL,
    stop_reason        VARCHAR(30) NULL,
    headphone_sequence TEXT NULL,
    headphone_answers  TEXT NULL,
    headphone_score    INT NULL,
    headphone_passed   TINYINT NULL,
    headphones_model   VARCHAR(200) NULL,
    hearing_problems   VARCHAR(200) NULL,
    config_version     VARCHAR(50) NOT NULL,
    config_snapshot    MEDIUMTEXT NOT NULL,
    clip_mapping       TEXT NOT NULL,
    user_agent         VARCHAR(500) NULL,
    excluded           TINYINT NOT NULL DEFAULT 0,
    exclusion_reason   VARCHAR(500) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS triads (
    triad_id       INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    participant_id VARCHAR(32) NOT NULL,
    order_index    INT NOT NULL,
    set_number     INT NULL,
    pos_a          INT NOT NULL,
    pos_b          INT NOT NULL,
    pos_c          INT NOT NULL,
    presented_at   VARCHAR(40) NULL,
    UNIQUE KEY uq_triad_order (participant_id, order_index),
    FOREIGN KEY (participant_id) REFERENCES participants(participant_id),
    FOREIGN KEY (pos_a) REFERENCES elements(element_id),
    FOREIGN KEY (pos_b) REFERENCES elements(element_id),
    FOREIGN KEY (pos_c) REFERENCES elements(element_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS constructs (
    construct_id       INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    participant_id     VARCHAR(32) NOT NULL,
    triad_id           INT NOT NULL,
    is_practice        TINYINT NOT NULL DEFAULT 0,
    similarity_pole    VARCHAR(500) NOT NULL,
    contrast_pole      VARCHAR(500) NOT NULL,
    auto_is_new        TINYINT NULL,
    nearest_construct  INT NULL,
    similarity_score   DOUBLE NULL,
    similarity_backend VARCHAR(20) NULL,
    emb_similarity     MEDIUMTEXT NULL,
    emb_contrast       MEDIUMTEXT NULL,
    created_at         VARCHAR(40) NOT NULL,
    KEY idx_constructs_participant (participant_id),
    FOREIGN KEY (participant_id) REFERENCES participants(participant_id),
    FOREIGN KEY (triad_id) REFERENCES triads(triad_id),
    FOREIGN KEY (nearest_construct) REFERENCES constructs(construct_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS responses (
    response_id    INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    triad_id       INT NOT NULL UNIQUE,
    pair_a         INT NULL,
    pair_b         INT NULL,
    odd_one        INT NULL,
    no_difference  TINYINT NOT NULL DEFAULT 0,
    construct_id   INT NULL,
    rt_ms          INT NULL,
    submitted_at   VARCHAR(40) NOT NULL,
    FOREIGN KEY (triad_id) REFERENCES triads(triad_id),
    FOREIGN KEY (construct_id) REFERENCES constructs(construct_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS interviewer_judgements (
    construct_id            INT NOT NULL PRIMARY KEY,
    judged_new              TINYINT NOT NULL,
    same_as                 INT NULL,
    revised_similarity_pole VARCHAR(300) NULL,
    revised_contrast_pole   VARCHAR(300) NULL,
    note                    TEXT NULL,
    judged_at               VARCHAR(40) NOT NULL,
    FOREIGN KEY (construct_id) REFERENCES constructs(construct_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS play_events (
    event_id    INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    triad_id    INT NOT NULL,
    element_id  INT NOT NULL,
    event       VARCHAR(10) NOT NULL,
    at_ms       INT NULL,
    logged_at   VARCHAR(40) NOT NULL,
    KEY idx_play_events_triad (triad_id),
    FOREIGN KEY (triad_id) REFERENCES triads(triad_id),
    FOREIGN KEY (element_id) REFERENCES elements(element_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ratings (
    participant_id VARCHAR(32) NOT NULL,
    construct_id   INT NOT NULL,
    element_id     INT NOT NULL,
    value          INT NOT NULL,
    rated_at       VARCHAR(40) NOT NULL,
    PRIMARY KEY (participant_id, construct_id, element_id),
    FOREIGN KEY (participant_id) REFERENCES participants(participant_id),
    FOREIGN KEY (construct_id) REFERENCES constructs(construct_id),
    FOREIGN KEY (element_id) REFERENCES elements(element_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
