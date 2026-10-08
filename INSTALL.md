# Installation on netcup Webhosting 2000 (marcellolussana.net/rgt)

The tool needs PHP and a MySQL database (MySQL 8.4 at netcup; MariaDB also works), which the web hosting provides. Python is not needed on the server.

## Overview

There are four places:

| Place | Content | Reachable from the internet | Updated by |
|---|---|---|---|
| GitHub repository | code and configuration | yes (public repository) | commit and push from your computer |
| `~/rtg_create_labels` on the server | clone of the repository | no | `git pull` (done by `deploy.sh`) |
| `~/httpdocs/rgt` | contents of `public/`: page, scripts, audio, model | **yes** | `deploy.sh` |
| `~/rgt_private` | contents of `private/`: configuration, database access, PHP code, backups | no | `deploy.sh` |

Only the web folder `~/httpdocs/rgt` can be reached from the internet. Configuration, database access data, the headphone-check answer key and backups are outside it. (The folder names `rgt_private` and `rtg_create_labels` are suggestions; use any folder outside `httpdocs`.)

Files with secrets or large data are never in git. They are put on the server once by hand (step 3).

**The repository is public.** Never commit passwords, the database access file, participant data or exports. `.gitignore` excludes them; check `git status` before each commit.

## 1. First commit (on your computer)

```bash
cd rtg_create_labels
git status            # db_credentials.ini, headphone_manifest.csv, audio, models and db/ must NOT be listed
git add -A
git commit -m "Repertory grid elicitation tool"
git push
```

## 2. Clone on the server, outside the web folder

Via SSH:

```bash
cd ~
git clone https://github.com/buscon/rtg_create_labels.git
```

If your clone is currently inside `httpdocs`, move it out; inside the web folder, the `.git` folder would be publicly readable. `deploy.sh` refuses to run in that layout.

## 3. Deploy for the first time

```bash
bash ~/rtg_create_labels/deploy.sh ~/httpdocs/rgt ~/rgt_private
```

This creates both folders and copies the code. The check at the end will report missing files; they come in the next step.

## 4. Put the server-only files in place (once)

These are not in git. Upload them with SFTP or the file manager:

| File | Where on the server | How to create it |
|---|---|---|
| `db_credentials.ini` | `~/rgt_private/` | copy `db_credentials.example.ini` and fill in the database access data |
| `headphone_manifest.csv` | `~/rgt_private/` | `python scripts/make_headphone_stimuli.py` on your computer (written to `private/`) |
| headphone-check sounds | `~/httpdocs/rgt/audio/headphone/` | same script (written to `public/audio/headphone/`) |
| clips and practice clips | `~/httpdocs/rgt/audio/` | your recordings, file names as in `private/data/elements.csv` and `config.ini` |
| model and library (~160 MB) | `~/httpdocs/rgt/models/` and `~/httpdocs/rgt/static/vendor/` | `python scripts/fetch_assets.py` on your computer |

The headphone sounds and their answer key belong together: if you run `make_headphone_stimuli.py` again, upload both again.

The database host given by netcup (10.35.x.x) is an internal address. It is reachable only from the web hosting itself, not from your computer, so the connection can only be tested on the server.

Then run the check again and create the interviewer password:

```bash
php ~/rgt_private/setup.php --public-dir ~/httpdocs/rgt --set-token
```

All lines should show `[ok]`. Write the password down; only a hash is stored on the server.

## 5. Updates

On your computer: commit and push. On the server:

```bash
bash ~/rtg_create_labels/deploy.sh ~/httpdocs/rgt ~/rgt_private
```

The script pulls the latest commit, copies the tracked files of `public/` and `private/` with `git archive` (no `.git`, README, tests or scripts), writes `~/httpdocs/rgt/private_path.php` (tells `api.php` where the private folder is), and runs `setup.php`, which checks PHP, the configuration and the database connection and creates missing tables. The server-only files from step 4 are not touched.

`private/config.ini` is in git, so each deploy overwrites it. Change parameters in the repository, not on the server. Files deleted from the repository are not deleted on the server; remove them by hand if needed.

## 6. PHP settings

In the netcup Webhosting Control Panel, set the PHP version for the domain to **8.1 or newer** (8.3 recommended). The extensions `pdo_mysql` and `mbstring` are needed; `setup.php` reports if one is missing.

**Check that the web server's PHP may read `~/rgt_private`.** Some hostings restrict PHP to certain folders (`open_basedir`). The command-line check in step 4 does not show this. Open https://marcellolussana.net/rgt/api.php?r=health: it must show `{"ok":true,...}`. If it shows an error instead, add the private folder to the allowed paths in the PHP settings of the control panel, or ask netcup support which folders outside `httpdocs` PHP may read.

## 7. Test

- https://marcellolussana.net/rgt/ (participants)
- https://marcellolussana.net/rgt/?mode=interviewer (interviewer)
- https://marcellolussana.net/rgt/?invited=in_person or `?invited=online` (records how a participant was invited)
- https://marcellolussana.net/rgt/api.php?r=health (status)
- https://marcellolussana.net/rgt/private_path.php must show an empty page (PHP runs it; the path is not printed)

Make sure HTTPS is active for the domain (Let's Encrypt certificate in the control panel).

## 8. Backups and data download

```bash
php ~/rgt_private/backup.php    # writes ~/rgt_private/dumps/dump-YYYYMMDD-HHMMSS/ (one CSV per table)
```

Download the dump folder with SFTP and convert it on your computer with `python scripts/import_dump.py` (see README). If the hosting offers cron jobs, schedule the backup daily. Delete old dumps from the server regularly. A database export from the control panel (phpMyAdmin) can be used in addition.

## Before real data collection

- Replace all `[PLACEHOLDER]` texts in `public/static/i18n.js` with the approved participant information.
- Check the data protection set-up with the university's data protection officer (hosting at netcup; clarify whether a data processing agreement with netcup is needed).
- Set all `TEST` values in `config.ini` after the test run and set `config_version = "1.0"`.
- Restrict database access to local connections and change the database password (update `db_credentials.ini`).
- Delete test sessions: drop all tables (e.g. in phpMyAdmin) and run `php ~/rgt_private/setup.php --public-dir ~/httpdocs/rgt` again.
