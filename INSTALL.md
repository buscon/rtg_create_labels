# Installation on netcup Webhosting 2000 (marcellolussana.net/rgt)

The tool needs PHP and a MySQL database (MySQL 8.4 at netcup; MariaDB also works), which the web hosting provides. Python is not needed on the server.

## Overview

| Place | Content | Reachable from the internet |
|---|---|---|
| GitHub repository | code and configuration | yes (public repository) |
| `~/rtg_create_labels` on the server | git clone; its `private/` folder holds configuration, database access data, PHP code and backups | **no** |
| `~/marcellolussana.net/httpdocs/rgt` | copy of `public/`: page, scripts, audio, model | yes |

After every `git pull` in the clone, a git hook runs `deploy.sh`, which copies `public/` into the web folder. The private part is used directly from the clone, which is outside the web folder, so it cannot be reached from the internet.

Files with secrets or large data are never in git. They are put on the server once by hand (step 4).

**The repository is public.** Never commit passwords, the database access file, participant data or exports. `.gitignore` excludes them; check `git status` before each commit.

## 1. Commit and push (on your computer)

```bash
cd rtg_create_labels
git status            # db_credentials.ini, headphone_manifest.csv, audio, models and db/ must NOT be listed
git add -A
git commit -m "describe the change"
git push
```

## 2. Clone on the server, outside the web folder

Via SSH:

```bash
cd ~
git clone https://github.com/buscon/rtg_create_labels.git
```

The clone must not be inside `httpdocs`, otherwise `private/` and `.git` would be public. `deploy.sh` refuses to run in that layout.

## 3. Activate automatic deployment (once)

```bash
cd ~/rtg_create_labels
git config core.hooksPath hooks
git config rgt.webdir "$HOME/marcellolussana.net/httpdocs/rgt"
bash deploy.sh
```

The first two lines are stored only in this clone (not in GitHub): they switch on the hook in `hooks/post-merge` and set the web folder. The last line deploys for the first time. The check at the end will report missing files; they come in the next step.

Only activate the hook on the server, not on your own computer.

## 4. Put the server-only files in place (once)

These are not in git. Upload them with SFTP or the file manager:

| File | Where on the server | How to create it |
|---|---|---|
| `db_credentials.ini` | `~/rtg_create_labels/private/` | copy `db_credentials.example.ini` and fill in the database access data |
| `headphone_manifest.csv` | `~/rtg_create_labels/private/` | `python scripts/make_headphone_stimuli.py` on your computer (written to `private/`) |
| headphone-check sounds | `~/marcellolussana.net/httpdocs/rgt/audio/headphone/` | same script (written to `public/audio/headphone/`) |
| clips and practice clips | `~/marcellolussana.net/httpdocs/rgt/audio/` | your recordings, file names as in `private/data/elements.csv` and `config.ini` |
| model and library (~160 MB) | `~/marcellolussana.net/httpdocs/rgt/models/` and `~/marcellolussana.net/httpdocs/rgt/static/vendor/` | `python scripts/fetch_assets.py` on your computer |

These files are ignored by git, so `git pull` never changes or deletes them. **Do not run `git clean -x` in the clone on the server**: it deletes ignored files, including the database access data and the backups.

The headphone sounds and their answer key belong together: if you run `make_headphone_stimuli.py` again, upload both again.

The database host given by netcup (10.35.x.x) is an internal address. It is reachable only from the web hosting itself, not from your computer, so the connection can only be tested on the server.

Then run the check again and create the interviewer password:

```bash
php ~/rtg_create_labels/private/setup.php --public-dir ~/marcellolussana.net/httpdocs/rgt --set-token
```

All lines should show `[ok]`. Write the password down; only a hash is stored on the server.

## 5. Updates

Commit and push on your computer, then on the server:

```bash
cd ~/rtg_create_labels && git pull
```

That is all. When the pull brings in new commits, the hook runs `deploy.sh` automatically. It copies the tracked files of `public/` into the web folder with `git archive` (no `.git`, README, tests or scripts), writes `private_path.php` (tells `api.php` where the private folder is), and runs `setup.php`, which checks PHP, the configuration and the database connection and creates missing tables. The output appears directly after the pull.

If the pull says `Already up to date`, the hook does not run. To deploy anyway (e.g. after uploading files), run `bash ~/rtg_create_labels/deploy.sh`.

`private/config.ini` comes from git: change parameters on your computer, commit, push and pull. Do not edit it in the clone on the server, or `git pull` will stop with a conflict. Files deleted from the repository are not deleted from the web folder; remove them by hand if needed.

Note: with the hook active, whatever is pushed to the repository runs on the server at the next pull. Only you can push to it, but keep this in mind if you ever add collaborators.

## 6. PHP settings

In the netcup Webhosting Control Panel, set the PHP version for the domain to **8.1 or newer** (8.3 recommended). The extensions `pdo_mysql` and `mbstring` are needed; `setup.php` reports if one is missing.

**Check that the web server's PHP may read the clone.** Some hostings restrict PHP to certain folders (`open_basedir`); the command-line check does not show this. Open https://marcellolussana.net/rgt/api.php?r=health: it must show `{"ok":true,...}`. If it shows an error instead, allow the folder `~/rtg_create_labels` in the PHP settings of the control panel, or ask netcup support which folders outside `httpdocs` PHP may read.

## 7. Test

- https://marcellolussana.net/rgt/ (participants)
- https://marcellolussana.net/rgt/?mode=interviewer (interviewer)
- https://marcellolussana.net/rgt/?invited=in_person or `?invited=online` (records how a participant was invited)
- https://marcellolussana.net/rgt/api.php?r=health (status)

Make sure HTTPS is active for the domain (Let's Encrypt certificate in the control panel).

## 8. Backups and data download

```bash
php ~/rtg_create_labels/private/backup.php    # writes private/dumps/dump-YYYYMMDD-HHMMSS/ (one CSV per table)
```

Download the dump folder with SFTP and convert it on your computer with `python scripts/import_dump.py` (see README). If the hosting offers cron jobs, schedule the backup daily. Delete old dumps from the server regularly. A database export from the control panel (phpMyAdmin) can be used in addition.

## Before real data collection

- Replace all `[PLACEHOLDER]` texts in `public/static/i18n.js` with the approved participant information.
- Check the data protection set-up with the university's data protection officer (hosting at netcup; clarify whether a data processing agreement with netcup is needed).
- Set all `TEST` values in `config.ini` after the test run and set `config_version = "1.0"`.
- Restrict database access to local connections and change the database password (update `db_credentials.ini`).
- Delete test sessions: drop all tables (e.g. in phpMyAdmin) and run `php ~/rtg_create_labels/private/setup.php --public-dir ~/marcellolussana.net/httpdocs/rgt` again.
