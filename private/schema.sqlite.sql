-- SQLite schema of the repertory grid elicitation tool (design file 06).

CREATE TABLE IF NOT EXISTS elements (
    element_id    INTEGER PRIMARY KEY,
    filename      TEXT NOT NULL UNIQUE,
    category      TEXT,               -- never sent to the browser
    is_practice   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS participants (
    participant_id     TEXT PRIMARY KEY,
    mode               TEXT NOT NULL,      -- 'online' | 'interviewer'
    invited_mode       TEXT,               -- from the invitation link (?invited=in_person|online)
    language           TEXT NOT NULL,      -- language at the start; see responses.language for later switches
    started_at         TEXT NOT NULL,
    triads_started_at  TEXT,               -- first real triad shown; the time limit counts from here
    finished_at        TEXT,
    stop_reason        TEXT,               -- saturation | max_triads | time_limit | headphone_check | NULL = not finished
    headphone_sequence TEXT,               -- JSON: files and correct answers
    headphone_answers  TEXT,
    headphone_score    INTEGER,
    headphone_passed   INTEGER,
    headphones_model   TEXT,               -- model of the headphones or loudspeakers
    listening_device   TEXT,               -- headphones | speakers
    listening_environment TEXT,            -- quiet | some_noise | noisy
    age_group          TEXT,                -- 18-24 | 25-34 | 35-44 | 45-54 | 55-64 | 65+ | na
    gender             TEXT,                -- female | male | diverse | na
    first_language     TEXT,                -- de | en | other | na
    first_language_other TEXT,
    sound_expertise    TEXT,                -- none | hobby | professional | na
    residence          TEXT,                -- city | town | rural | na
    hearing_problems   TEXT,
    config_version     TEXT NOT NULL,
    config_snapshot    TEXT NOT NULL,      -- JSON copy of the active configuration
    clip_mapping       TEXT NOT NULL,      -- JSON: set number -> element_id
    user_agent         TEXT,
    access_code        TEXT,               -- group code from the invitation link (design file 14)
    excluded           INTEGER NOT NULL DEFAULT 0,
    exclusion_reason   TEXT
);

CREATE TABLE IF NOT EXISTS triads (
    triad_id       INTEGER PRIMARY KEY,
    participant_id TEXT NOT NULL REFERENCES participants(participant_id),
    order_index    INTEGER NOT NULL,      -- 0 = practice, 1..max_triads
    set_number     INTEGER,               -- row in the triad set; NULL for practice
    pos_a          INTEGER NOT NULL REFERENCES elements(element_id),
    pos_b          INTEGER NOT NULL REFERENCES elements(element_id),
    pos_c          INTEGER NOT NULL REFERENCES elements(element_id),
    presented_at   TEXT,
    UNIQUE (participant_id, order_index)
);

CREATE TABLE IF NOT EXISTS constructs (
    construct_id       INTEGER PRIMARY KEY,
    participant_id     TEXT NOT NULL REFERENCES participants(participant_id),
    triad_id           INTEGER NOT NULL REFERENCES triads(triad_id),
    is_practice        INTEGER NOT NULL DEFAULT 0,
    similarity_pole    TEXT NOT NULL,
    contrast_pole      TEXT NOT NULL,
    auto_is_new        INTEGER,            -- 1 new, 0 repetition, NULL for practice
    nearest_construct  INTEGER REFERENCES constructs(construct_id),
    similarity_score   REAL,
    similarity_backend TEXT,               -- 'embedding' | 'text' | NULL (first construct)
    emb_similarity     TEXT,               -- JSON embedding vector of the similarity pole
    emb_contrast       TEXT,               -- JSON embedding vector of the contrast pole
    created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS responses (
    response_id    INTEGER PRIMARY KEY,
    triad_id       INTEGER NOT NULL UNIQUE REFERENCES triads(triad_id),
    pair_a         INTEGER REFERENCES elements(element_id),
    pair_b         INTEGER REFERENCES elements(element_id),
    odd_one        INTEGER REFERENCES elements(element_id),
    no_difference  INTEGER NOT NULL DEFAULT 0,
    construct_id   INTEGER REFERENCES constructs(construct_id),
    rt_ms          INTEGER,
    language       TEXT,                       -- interface language when the answer was given,
    hints_shown    TEXT,                       -- JSON list of the thinking prompts opened (design file 07)
    submitted_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS interviewer_judgements (
    construct_id            INTEGER PRIMARY KEY REFERENCES constructs(construct_id),
    judged_new              INTEGER NOT NULL,
    same_as                 INTEGER REFERENCES constructs(construct_id),
    revised_similarity_pole TEXT,
    revised_contrast_pole   TEXT,
    note                    TEXT,
    judged_at               TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS play_events (
    event_id    INTEGER PRIMARY KEY,
    triad_id    INTEGER NOT NULL REFERENCES triads(triad_id),
    element_id  INTEGER NOT NULL REFERENCES elements(element_id),
    event       TEXT NOT NULL,     -- 'play' | 'pause' | 'ended'
    at_ms       INTEGER,           -- ms since the triad was shown
    logged_at   TEXT NOT NULL
);

-- Second step (later): ratings of every clip on every construct
CREATE TABLE IF NOT EXISTS ratings (
    participant_id TEXT NOT NULL REFERENCES participants(participant_id),
    construct_id   INTEGER NOT NULL REFERENCES constructs(construct_id),
    element_id     INTEGER NOT NULL REFERENCES elements(element_id),
    value          INTEGER NOT NULL,
    rated_at       TEXT NOT NULL,
    PRIMARY KEY (participant_id, construct_id, element_id)
);

-- Rate limit for new sessions (design file 14): salted IP hashes, kept for one hour only
CREATE TABLE IF NOT EXISTS session_starts (
    ip_hash      TEXT NOT NULL,
    started_unix INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_starts ON session_starts(ip_hash, started_unix);

CREATE INDEX IF NOT EXISTS idx_triads_participant ON triads(participant_id, order_index);
CREATE INDEX IF NOT EXISTS idx_constructs_participant ON constructs(participant_id);
CREATE INDEX IF NOT EXISTS idx_play_events_triad ON play_events(triad_id);
