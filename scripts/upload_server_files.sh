#!/usr/bin/env bash
# Upload the files that are not in git from your computer to the server:
#   public/audio/*.flac            -> <web folder>/audio/
#   public/audio/headphone/*.wav   -> <web folder>/audio/headphone/
#   private/headphone_manifest.csv -> <clone>/private/
#
#     bash scripts/upload_server_files.sh SSH_LOGIN [WEB_FOLDER] [CLONE_FOLDER]
#
# SSH_LOGIN is what you use with ssh, e.g. hosting123456@hosting123456.example.netcup.net
# Defaults: WEB_FOLDER=/marcellolussana.net/httpdocs/rgt  CLONE_FOLDER=/rtg_create_labels
# (paths as seen when logged in via SSH on netcup, where the webspace is "/").
set -euo pipefail
LOGIN="${1:?usage: bash scripts/upload_server_files.sh SSH_LOGIN [WEB_FOLDER] [CLONE_FOLDER]}"
WEB="${2:-/marcellolussana.net/httpdocs/rgt}"
CLONE="${3:-/rtg_create_labels}"
cd "$(dirname "$0")/.."

ls public/audio/*.flac >/dev/null || { echo "no clips in public/audio (run stimuli/isd_finalize.py)"; exit 1; }
ssh "$LOGIN" "mkdir -p '$WEB/audio/headphone'"
scp public/audio/*.flac "$LOGIN:$WEB/audio/"
if [ -f private/headphone_manifest.csv ]; then
    scp public/audio/headphone/*.wav "$LOGIN:$WEB/audio/headphone/"
    scp private/headphone_manifest.csv "$LOGIN:$CLONE/private/"
else
    echo "note: private/headphone_manifest.csv missing; run scripts/make_headphone_stimuli.py first"
fi
# the web server must be able to read the files (scp keeps the local permissions, often rw-------)
ssh "$LOGIN" "chmod 755 '$WEB/audio' '$WEB/audio/headphone'; chmod 644 '$WEB'/audio/*.flac; chmod 644 '$WEB'/audio/headphone/*.wav 2>/dev/null; true"
echo "uploaded. Check on the server: php $CLONE/private/setup.php --public-dir $WEB"
