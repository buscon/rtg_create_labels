# rtg_create_labels

Web tool for the repertory grid study on restorative soundscapes (postdoc project, Computational Humanities, University of Bamberg).

Participants listen to three sound clips at a time (a *triad*), choose the two that are alike, and describe in their own words what the two share (similarity pole) and how the third differs (contrast pole). The tool stops a session automatically when a participant no longer produces new constructs. All answers are stored in a MySQL database (on the web hosting; MariaDB also works) or SQLite (for local testing).

The design decisions are documented in the folder `rgt-tool-design/` next to this repository (files 00–12).

## How it works

| Part | Technology | Where it runs |
|---|---|---|
| Participant interface | HTML, CSS, JavaScript (no build step) | Browser |
| Repetition check (sentence embeddings) | transformers.js + multilingual MiniLM model, served from this site | Browser |
| API and stopping rule | PHP 8.1+ (`public/api.php`) | Web hosting |
| Database | MySQL 8.4 on the web hosting (MariaDB also works); SQLite for local testing (`db_driver` in `config.ini`) | Web hosting |
| Preparation, export, analysis | Python scripts (`scripts/`) | Researcher's computer |

Session flow: consent → short questions → volume setting → headphone check → instructions → practice triad → up to 17 triads → end. Interviewer-run sessions use the same flow, opened with `?mode=interviewer`; after each answer the interviewer records their own judgement (see design file 11).

## Folder structure

```
public/                     everything that is uploaded to the web hosting (→ marcellolussana.net/rgt)
  index.html, api.php, .htaccess
  static/                   app.js, i18n.js (all interface texts), embedder.js, style.css
  static/vendor/            transformers.js + ONNX runtime        (downloaded, not in git)
  models/                   embedding model                         (downloaded, not in git)
  audio/                    study clips, practice clips, headphone/  (not in git)
  private/                  blocked from the web by .htaccess
    config.ini              ALL adjustable parameters
    data/elements.csv       the 10 clips (file name, category)
    data/triad_set.csv      the 17 triads covering all clip pairs
    db_credentials.example.ini  template for the database access data (copy to db_credentials.ini, not in git)
    schema.mysql.sql, schema.sqlite.sql, lib/   database schema and PHP code
    setup.php               check + create tables + interviewer password
    backup.php              CSV dump of all tables (backup and data download)
    db/, dumps/             SQLite file (local testing) and dumps   (not in git)
scripts/                    Python tools for the researcher
tests/                      automated tests of the API
deploy.sh                   copies public/ from the server clone to the web folder (see INSTALL.md)
```

## Changing parameters

Edit `public/private/config.ini` (on the server, e.g. with the file manager or SFTP). Changes apply to the next session that starts; no restart is needed. Each session stores a copy of the configuration it used. Increase `config_version` whenever you change a value, and set it to `1.0` before real data collection starts.

Interface texts (consent, instructions, buttons) are in `public/static/i18n.js`. Texts marked `[PLACEHOLDER]` must be replaced with the approved study information.

## Quick start on your own computer

Requirements: PHP 8.1+ with `pdo_sqlite` and `mbstring`, Python 3.10+. For local testing set `db_driver = "sqlite"` in `public/private/config.ini` (the default `"mysql"` is for the server).

```bash
pip install -r scripts/requirements.txt
python scripts/make_placeholder_audio.py      # test sounds (replace with real clips later)
python scripts/make_headphone_stimuli.py      # headphone-check stimuli
python scripts/fetch_assets.py                # transformers.js + model (~160 MB)
php public/private/setup.php --set-token      # database + interviewer password
php -S 127.0.0.1:8000 -t public               # then open http://127.0.0.1:8000
```

Interviewer mode: `http://127.0.0.1:8000/?mode=interviewer`. English: add `?lang=en`.

Run the tests: `pytest tests/` (SQLite). To test against MySQL/MariaDB as well, set `RGT_TEST_MYSQL=host:port:user:password:dbname` (a test database; it is emptied). Tested with MySQL 8.0 and MariaDB 10.11.

Deployment to netcup (git clone on the server + `deploy.sh`): see [INSTALL.md](INSTALL.md).

## Data

1. On the server: `php private/backup.php` writes a CSV dump of all tables to `private/dumps/`.
2. Download the dump folder (SFTP) and convert it: `python scripts/import_dump.py path/to/dump-… --out data/rgt.sqlite`
3. `python scripts/export.py --db data/rgt.sqlite --out export/` writes analysis tables and one text file per participant with their constructs.
4. `python scripts/simulate_stopping.py --db data/rgt.sqlite --threshold 0.80` re-runs the stopping rule with other values (design file 12).

## Known limitations

- **Download size for participants.** With the embedding backend, each participant's browser loads about 160 MB once (model, tokenizer, WebAssembly runtime). If the model is not ready after `embedding_wait_s` seconds, the server falls back to text similarity for that answer. Setting `backend = "text"` removes the download but makes the check purely character-based.
- **Similarity scores need calibration.** In a first test, short antonym pairs on different topics (e.g. *natürlich – künstlich* vs. *weit und offen – eng*) scored around 0.8, while pairs a person would call the same distinction (*ruhig – hektisch* vs. *still – laut und unruhig*) scored about 0.7. At the default threshold of 0.85 the check mainly catches near-identical labels. The threshold, and possibly the model, must be set in the test run with the interviewer judgements (design file 04).
- **Headphone check.** The Huggins-pitch stimuli are generated with approximate parameters. Check them against Milne et al. (2021) before use.
- **Rating stage.** Not implemented yet; the database table `ratings` is prepared.
