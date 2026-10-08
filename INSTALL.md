# Installation on netcup Webhosting 2000 (marcellolussana.net/rgt)

The tool needs PHP and a MySQL database (MySQL 8.4 at netcup; MariaDB also works), which the web hosting provides. Python is not needed on the server.

The setup has three places:

| Place | What | How it is updated |
|---|---|---|
| GitHub repository | code and configuration | commit and push from your computer |
| Clone on the server, **outside** the web folder (e.g. `~/rtg_create_labels`) | copy of the repository | `git pull` (done by `deploy.sh`) |
| Web folder `~/httpdocs/rgt` | what visitors see; no git files | `deploy.sh` copies `public/` from the clone |

Files with secrets or large data are never in git. They are put on the server once by hand (step 3).

**The repository is public.** Never commit passwords, the database access file, participant data or exports. `.gitignore` excludes them; check `git status` before each commit.

## 1. First commit (on your computer)

```bash
cd rtg_create_labels
git status            # db_credentials.ini, audio, models and db/ must NOT be listed
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

If your clone is currently inside `httpdocs`, move it out. Inside the web folder, the `.git` folder and the README would be publicly readable. (`.htaccess` blocks `.git` as a precaution, and `deploy.sh` refuses to run in that layout.)

## 3. Put the server-only files in place (once)

These are not in git. Upload them with SFTP or the file manager:

| File | Where on the server | How to create it |
|---|---|---|
| `db_credentials.ini` | `~/httpdocs/rgt/private/` | copy `db_credentials.example.ini` and fill in the database access data |
| clips and practice clips | `~/httpdocs/rgt/audio/` | your recordings, file names as in `private/data/elements.csv` and `config.ini` |
| headphone-check stimuli | `~/httpdocs/rgt/audio/headphone/` | `python scripts/make_headphone_stimuli.py` on your computer |
| model and library (~160 MB) | `~/httpdocs/rgt/models/` and `~/httpdocs/rgt/static/vendor/` | `python scripts/fetch_assets.py` on your computer |

The database host given by netcup (10.35.x.x) is an internal address. It is reachable only from the web hosting itself, not from your computer, so the connection can only be tested on the server.

## 4. Deploy

```bash
bash ~/rtg_create_labels/deploy.sh ~/httpdocs/rgt
```

The script:

1. runs `git pull` in the clone;
2. copies the files of `public/` from the current commit into the web folder (using `git archive`, so no `.git`, README, tests or scripts);
3. runs `private/setup.php`, which checks PHP, the configuration, the audio files, the model and the database connection, and creates missing tables.

The server-only files from step 3 are not touched, because they are not in git.

The first time, also create the interviewer password:

```bash
php ~/httpdocs/rgt/private/setup.php --set-token
```

Write the password down; only a hash is stored on the server.

**Every later update:** commit and push on your computer, then run the deploy command again.

`private/config.ini` is in git, so each deploy overwrites it. Change parameters in the repository, not on the server. Files deleted from the repository are not deleted from the web folder; remove them by hand if needed.

## 5. PHP settings

In the netcup Webhosting Control Panel, set the PHP version for the domain to **8.1 or newer** (8.3 recommended). The extensions `pdo_mysql` and `mbstring` are needed; `setup.php` reports if one is missing.

## 6. Check the protection

Open these addresses in a browser. All must show **403 Forbidden** (or 404):

- https://marcellolussana.net/rgt/private/config.ini
- https://marcellolussana.net/rgt/private/db_credentials.ini
- https://marcellolussana.net/rgt/audio/headphone/manifest.csv

If any of them downloads instead, `.htaccess` files are not active. Then keep the private folder outside the web folder:

1. Move `~/httpdocs/rgt/private` to e.g. `~/rgt_private` (keep `db_credentials.ini` in it).
2. Find its absolute path: `cd ~/rgt_private && pwd`.
3. Create `~/httpdocs/rgt/private_path.php` containing `<?php return '/absolute/path/to/rgt_private';`
4. Run the deploy command again. `deploy.sh` detects `private_path.php` and from then on copies the private files to that folder.

## 7. Test

- https://marcellolussana.net/rgt/ (participants)
- https://marcellolussana.net/rgt/?mode=interviewer (interviewer)
- https://marcellolussana.net/rgt/?invited=in_person or `?invited=online` (records how a participant was invited)
- https://marcellolussana.net/rgt/api.php?r=health (status)

Make sure HTTPS is active for the domain (Let's Encrypt certificate in the control panel).

## 8. Backups and data download

```bash
php ~/httpdocs/rgt/private/backup.php    # writes private/dumps/dump-YYYYMMDD-HHMMSS/ (one CSV per table)
```

Download the dump folder with SFTP and convert it on your computer with `python scripts/import_dump.py` (see README). If the hosting offers cron jobs, schedule the backup daily. Delete old dumps from the server regularly. A database export from the control panel (phpMyAdmin) can be used in addition.

## Before real data collection

- Replace all `[PLACEHOLDER]` texts in `public/static/i18n.js` with the approved participant information.
- Check the data protection set-up with the university's data protection officer (hosting at netcup; clarify whether a data processing agreement with netcup is needed).
- Set all `TEST` values in `config.ini` after the test run and set `config_version = "1.0"`.
- Restrict database access to local connections and change the database password (update `db_credentials.ini`).
- Delete test sessions: drop all tables (e.g. in phpMyAdmin) and run `php ~/httpdocs/rgt/private/setup.php` again.
