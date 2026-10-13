"""Step 4: check the binaural renderings and put them into the website.

    python stimuli/04_finalize.py --rendered PATH/TO/BINAURAL [--bits 16] [--dry-run]

Input: a folder with one stereo WAV/FLAC per exported file, with the same names
(clip_01.wav, practice_1.wav, ...), rendered from stimuli/work/export/foa/.

The script
  1. checks: every exported file has a rendering; stereo; 48 kHz; same length;
  2. checks that the renderer kept the level differences: the change in level from
     the ambisonic file to the binaural file must be about the same for all files
     (warning if a file deviates by more than 1.5 dB, which means the renderer
     normalised the files individually);
  3. applies one common gain so that the loudest peak is at -1 dBFS (the relative
     levels stay unchanged);
  4. writes public/audio/<name>.flac and updates private/data/elements.csv
     (clips) and practice_clips in private/config.ini;
  5. writes public/credits.html from stimuli/ATTRIBUTION.md (linked on the end page).

Upload the new files in public/audio/ to the server afterwards (they are not in git);
commit elements.csv, config.ini and credits.html as usual.
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import HERE, WORK, leq_dbfs, read_csv, write_csv  # noqa: E402

REPO = HERE.parent


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rendered", type=Path, required=True, help="folder with the binaural files")
    ap.add_argument("--bits", type=int, choices=(16, 24), default=16, help="FLAC bit depth (default 16)")
    ap.add_argument("--peak", type=float, default=-1.0, help="peak of the loudest file in dBFS (default -1)")
    ap.add_argument("--dry-run", action="store_true", help="only check, write nothing")
    args = ap.parse_args()

    report = read_csv(WORK / "export" / "export_report.csv")
    files = {}
    problems = []
    for r in report:
        cands = [p for ext in (".wav", ".flac") if (p := args.rendered / f"{r['name']}{ext}").exists()]
        if not cands:
            problems.append(f"missing rendering for {r['name']}")
            continue
        x, fs = sf.read(cands[0], always_2d=True)
        if x.shape[1] != 2:
            problems.append(f"{cands[0].name}: {x.shape[1]} channels, expected 2")
        if fs != 48000:
            problems.append(f"{cands[0].name}: {fs} Hz, expected 48000")
        files[r["name"]] = (r, x, fs)
    lengths = {n: len(x) for n, (_, x, _) in files.items()}
    if lengths and max(lengths.values()) - min(lengths.values()) > 48000 * 0.05:
        problems.append(f"lengths differ: {lengths}")
    if problems:
        sys.exit("problems:\n  " + "\n  ".join(problems))

    # level consistency: binaural level (mean of both ears) minus ambisonic W level
    deltas = {}
    for n, (r, x, fs) in files.items():
        binaural = 10 * np.log10(np.mean([10 ** (leq_dbfs(x[:, c], fs) / 10) for c in (0, 1)]))
        deltas[n] = binaural - float(r["w_level_dbfs_out"])
    med = float(np.median(list(deltas.values())))
    print("level change ambisonic W -> binaural (should be about the same for all files):")
    for n, d in sorted(deltas.items()):
        flag = "  <-- differs, check the renderer settings" if abs(d - med) > 1.5 else ""
        print(f"  {n:<12} {d:+6.1f} dB{flag}")

    peak = max(np.abs(x).max() for _, x, _ in files.values())
    gain = 10 ** (args.peak / 20) / peak
    print(f"common gain: {20 * np.log10(gain):+.1f} dB (loudest peak -> {args.peak} dBFS)")
    if args.dry_run:
        return

    audio = REPO / "public" / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    for n, (r, x, fs) in files.items():
        sf.write(audio / f"{n}.flac", (x * gain).astype(np.float32), fs, subtype=f"PCM_{args.bits}")
    print(f"written: {len(files)} files to {audio}")

    clips = sorted((r for r, _, _ in files.values() if r["use"] == "clip"), key=lambda r: r["name"])
    practice = sorted((r for r, _, _ in files.values() if r["use"] == "practice"), key=lambda r: r["name"])
    write_csv(REPO / "private" / "data" / "elements.csv",
              [{"element_id": i, "filename": f"{r['name']}.flac", "category": r["category"]}
               for i, r in enumerate(clips, start=1)])
    print(f"private/data/elements.csv: {len(clips)} clips")

    cfg = REPO / "private" / "config.ini"
    text = cfg.read_text(encoding="utf-8")
    new = " | ".join(f"{r['name']}.flac" for r in practice)
    text, n_sub = re.subn(r'(?m)^(practice_clips\s*=\s*)"[^"]*"', lambda m: f'{m.group(1)}"{new}"', text)
    if practice and n_sub == 1:
        cfg.write_text(text, encoding="utf-8")
        print(f"private/config.ini: practice_clips = \"{new}\"")
    if len(practice) != 3:
        print(f"note: {len(practice)} practice clips; the practice triad needs exactly 3")
    if len(clips) != 10:
        print(f"note: {len(clips)} clips; n_clips in config.ini and the triad set expect 10")

    attr = (HERE / "ATTRIBUTION.md").read_text(encoding="utf-8")
    items = "".join(f"<li>{html.escape(line[2:]).replace('**', '')}</li>" for line in attr.splitlines() if line.startswith("- "))
    (REPO / "public" / "credits.html").write_text(f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>Sound credits</title><link rel="stylesheet" href="static/style.css">
<main><div class="panel"><h1>Sound recordings / Tonaufnahmen</h1>
<p>The sounds in this study are excerpts of the following recordings, published under the
Creative Commons Attribution 4.0 licence. They were shortened, filtered, level-adjusted and rendered to binaural audio.</p>
<ul>{items}</ul>
<p><a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a></p></div></main></html>
""", encoding="utf-8")
    print("public/credits.html written")


if __name__ == "__main__":
    main()
