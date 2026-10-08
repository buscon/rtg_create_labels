#!/usr/bin/env bash
# Deploy the website from the git clone to the web folder, without git files.
#
# Usage on the server (via SSH), from anywhere:
#     ~/rtg_create_labels/deploy.sh ~/httpdocs/rgt            # git pull, then copy
#     ~/rtg_create_labels/deploy.sh ~/httpdocs/rgt --no-pull  # copy the current commit only
#
# What it does:
#   1. git pull (fast-forward only) in the clone
#   2. exports the tracked files of public/ from the current commit (git archive)
#      into the web folder. No .git folder, README, tests or scripts are copied.
#   3. runs private/setup.php (checks configuration, creates missing tables)
#
# Files that exist only on the server are never touched, because they are not
# in git: private/db_credentials.ini, private/interviewer_token.hash,
# private/dumps/, audio files, models/, static/vendor/.
# Tracked files ARE overwritten, including private/config.ini: change the
# configuration in the repository, commit, push, and deploy again.
# Files deleted from the repository are not deleted from the web folder.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
  echo "usage: $0 /path/to/web/folder [--no-pull]" >&2
  exit 1
fi
mkdir -p "$TARGET"
TARGET="$(cd "$TARGET" && pwd)"

# safety: the web folder must not be inside the repository (or the other way round)
case "$TARGET/" in "$REPO/"*) echo "error: the web folder must be outside the repository" >&2; exit 1;; esac
case "$REPO/" in "$TARGET/"*) echo "error: the repository must not be inside the web folder (the .git folder would be public)" >&2; exit 1;; esac

if [[ "${2:-}" != "--no-pull" ]]; then
  git -C "$REPO" pull --ff-only
fi

COMMIT="$(git -C "$REPO" rev-parse --short HEAD)"
if [[ -f "$TARGET/private_path.php" ]]; then
  # private folder kept outside the web folder (see INSTALL.md, step 6)
  PRIV="$(php -r 'echo require $argv[1];' "$TARGET/private_path.php")"
  mkdir -p "$PRIV"
  git -C "$REPO" archive --format=tar HEAD:public | tar -x -C "$TARGET" --exclude='private' --exclude='private/*'
  git -C "$REPO" archive --format=tar HEAD:public/private | tar -x -C "$PRIV"
  echo "deployed commit $COMMIT to $TARGET (private files to $PRIV)"
else
  PRIV="$TARGET/private"
  git -C "$REPO" archive --format=tar HEAD:public | tar -x -C "$TARGET"
  echo "deployed commit $COMMIT to $TARGET"
fi

php "$PRIV/setup.php" --public-dir "$TARGET"
