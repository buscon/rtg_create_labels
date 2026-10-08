"""Download the files the browser needs for the repetition check.

    python scripts/fetch_assets.py

Downloads (about 140 MB in total) into public/:
  static/vendor/   transformers.js and the ONNX runtime (WebAssembly), pinned version
  models/<name>/   the quantised multilingual sentence-embedding model

Everything is served from your own webspace, so participants' browsers do not
contact third-party servers. These files are not stored in git (.gitignore);
run this script once and upload the two folders with the rest of public/.
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rgtconf import PUBLIC, load_config  # noqa: E402

TRANSFORMERS_VERSION = "3.8.1"
CDN = f"https://cdn.jsdelivr.net/npm/@huggingface/transformers@{TRANSFORMERS_VERSION}/dist/"
VENDOR_FILES = ["transformers.min.js", "ort-wasm-simd-threaded.jsep.mjs", "ort-wasm-simd-threaded.jsep.wasm"]
MODEL_FILES = ["config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
               "onnx/model_quantized.onnx"]


def download(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"exists   {dest.relative_to(PUBLIC)}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"download {url}")
    with urllib.request.urlopen(url) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    tmp.rename(dest)
    print(f"         -> {dest.relative_to(PUBLIC)} ({dest.stat().st_size / 1e6:.1f} MB)")


def main() -> None:
    cfg = load_config()
    model = cfg["similarity"]["embedding_model"]
    for name in VENDOR_FILES:
        download(CDN + name, PUBLIC / "static" / "vendor" / name)
    for name in MODEL_FILES:
        try:
            download(f"https://huggingface.co/{model}/resolve/main/{name}", PUBLIC / "models" / model / name)
        except Exception as exc:  # special_tokens_map.json is optional for some models
            if name == "special_tokens_map.json":
                print(f"skipped  {name} ({exc})")
            else:
                raise
    print("done")


if __name__ == "__main__":
    main()
