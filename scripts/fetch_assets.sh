#!/usr/bin/env bash
# Download the files for the repetition check (transformers.js and the embedding model,
# about 150 MB) DIRECTLY ON THE SERVER into the web folder, instead of uploading them.
# Same files as scripts/fetch_assets.py.
#
#     bash scripts/fetch_assets.sh [WEB_FOLDER]
#
# WEB_FOLDER defaults to the folder set for deploy.sh (git config rgt.webdir).
set -euo pipefail

WEB="${1:-$(git -C "$(dirname "$0")/.." config rgt.webdir || true)}"
[ -n "$WEB" ] || { echo "usage: bash scripts/fetch_assets.sh WEB_FOLDER"; exit 1; }
[ -d "$WEB" ] || { echo "web folder not found: $WEB"; exit 1; }

VERSION="3.8.1"
CDN="https://cdn.jsdelivr.net/npm/@huggingface/transformers@${VERSION}/dist"
MODEL="$(sed -n 's/^embedding_model *= *"\([^"]*\)".*/\1/p' "$(dirname "$0")/../private/config.ini")"
[ -n "$MODEL" ] || MODEL="Xenova/paraphrase-multilingual-MiniLM-L12-v2"

get() {  # get URL DEST
    if [ -s "$2" ]; then echo "exists   $2"; return; fi
    mkdir -p "$(dirname "$2")"
    echo "download $1"
    if command -v curl >/dev/null; then curl -fL --retry 5 -# -o "$2.part" "$1"
    else wget -q --tries=5 -O "$2.part" "$1"; fi
    mv "$2.part" "$2"
}

for f in transformers.min.js ort-wasm-simd-threaded.jsep.mjs ort-wasm-simd-threaded.jsep.wasm; do
    get "$CDN/$f" "$WEB/static/vendor/$f"
done
for f in config.json tokenizer.json tokenizer_config.json special_tokens_map.json onnx/model_quantized.onnx; do
    get "https://huggingface.co/$MODEL/resolve/main/$f" "$WEB/models/$MODEL/$f" || {
        [ "$f" = special_tokens_map.json ] && echo "skipped  $f" || exit 1; }
done
echo "done: $WEB/static/vendor and $WEB/models/$MODEL"
