"""Put the chosen ISD recordings into the website.

    python stimuli/isd_finalize.py [--length 30] [--fade 0.5] [--bits 16] [--dry-run]

Input: stimuli/isd_selection.csv (GroupID, label, use = clip | practice, ...).
The recordings are taken from stimuli/raw/isd/ (downloaded with isd_shortlist.py;
missing ones are downloaded here).

For every recording the script
  1. corrects the file level to the LAeq published in the ISD data if the two differ by
     more than 3 dB (Groningen files are exported on a different scale);
  2. chooses the 30-s excerpt whose level is closest to the level of the whole recording
     and whose first and last second are close to the median level (so the clip does not
     start or end in the middle of a loud event); steps of 0.5 s;
  3. applies 0.5-s raised-cosine fades;
  4. resamples to 48 kHz where needed (ISD has 44.1- and 48-kHz files).
Then ONE common gain is applied to all files, so that the loudest peak is at -1 dBFS:
the real level differences between the places are kept.

Output
  public/audio/clip_01.flac ... (clips, in the order of isd_selection.csv)
  public/audio/practice_1.flac ... (practice clips)
  private/data/elements.csv     element_id, filename, category (= the PAQ label)
  private/config.ini            n_clips and practice_clips updated
  stimuli/isd_clips.csv         which excerpt of which recording became which file
  stimuli/ATTRIBUTION.md, public/credits.html   CC BY 4.0 attribution

The audio files are not in git: upload public/audio/ to the server (see INSTALL.md).
"""
from __future__ import annotations

import argparse
import html
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf
from scipy import signal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import HERE, leq_dbfs, a_weighting  # noqa: E402
import isd_shortlist as isd  # noqa: E402

REPO = HERE.parent
P_REF = 2e-5


def short_levels(x: np.ndarray, fs: int, win: float = 0.125) -> np.ndarray:
    """A-weighted short-term levels (dB re full scale) of the left channel."""
    y = signal.sosfilt(a_weighting(fs), x[:, 0])
    n = int(win * fs)
    m = len(y) // n
    return 10 * np.log10(np.mean(y[: m * n].reshape(m, n) ** 2, axis=1) + 1e-20)


def best_start(x: np.ndarray, fs: int, length: float, step: float = 0.5) -> float:
    lv = short_levels(x, fs)
    per_s = 8                                           # 0.125-s frames per second
    total = 10 * math.log10(np.mean(10 ** (lv / 10)))
    med = float(np.median(lv))
    n_win = int(length * per_s)
    best, best_score = 0.0, float("inf")
    for start in np.arange(0, len(x) / fs - length + 1e-9, step):
        i = int(start * per_s)
        seg = lv[i: i + n_win]
        if len(seg) < n_win:
            break
        leq = 10 * math.log10(np.mean(10 ** (seg / 10)))
        head = 10 * math.log10(np.mean(10 ** (seg[:per_s] / 10)))
        tail = 10 * math.log10(np.mean(10 ** (seg[-per_s:] / 10)))
        score = abs(leq - total) + 0.5 * (abs(head - med) + abs(tail - med))
        if score < best_score:
            best, best_score = float(start), score
    return best


def fade(x: np.ndarray, fs: int, seconds: float) -> np.ndarray:
    n = int(seconds * fs)
    if n == 0:
        return x
    ramp = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, n))
    x = x.copy()
    x[:n] *= ramp[:, None]
    x[-n:] *= ramp[::-1][:, None]
    return x


def write_flac(dest: Path, y: np.ndarray, fs: int, bits: int) -> None:
    """FLAC files written by libsndfile (soundfile) are not decoded by Firefox
    (NS_ERROR_DOM_MEDIA_METADATA_ERR); FLAC encoded by ffmpeg plays in all browsers.
    So the audio is written as WAV first and encoded with ffmpeg (lossless)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        sys.exit("ffmpeg is needed to write browser-compatible FLAC files (macOS: brew install ffmpeg)")
    tmp = dest.with_suffix(".tmp.wav")
    sf.write(tmp, y, fs, subtype=f"PCM_{bits}")
    fmt = "s16" if bits == 16 else "s32"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(tmp), "-c:a", "flac", "-sample_fmt", fmt,
                    "-map_metadata", "-1", str(dest)], check=True)
    tmp.unlink()


def set_ini(text: str, section: str, key: str, value: str) -> str:
    pattern = rf"(\[{section}\][^\[]*?^{key}\s*=\s*)[^;\n]*?(\s*(;|$))"
    new, n = re.subn(pattern, lambda m: m.group(1) + value + m.group(2), text, count=1, flags=re.M | re.S)
    if n != 1:
        sys.exit(f"config.ini: {section}.{key} not found")
    return new


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--length", type=float, default=30.0)
    ap.add_argument("--fade", type=float, default=0.5)
    ap.add_argument("--bits", type=int, choices=(16, 24), default=16)
    ap.add_argument("--samplerate", type=int, default=48000, help="output sample rate (default 48000)")
    ap.add_argument("--peak", type=float, default=-1.0, help="peak of the loudest file in dBFS (default -1)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    sel = pd.read_csv(HERE / "isd_selection.csv")
    if not {"GroupID", "use", "label", "LocationID", "zip", "LAeq"} <= set(sel.columns):
        sys.exit("isd_selection.csv needs the columns GroupID, label, use, LocationID, zip, LAeq")
    clips = sel[sel.use == "clip"].reset_index(drop=True)
    practice = sel[sel.use == "practice"].reset_index(drop=True)

    # download what is missing
    zips: dict[str, isd.RemoteZip] = {}
    for r in sel.itertuples():
        dest = isd.RAWDIR / r.LocationID / f"{r.GroupID}.wav"
        if dest.exists():
            continue
        if r.zip not in zips:
            zips[r.zip] = isd.RemoteZip(isd.BASE + r.zip, retries=10, cache=isd.RAWDIR / f".index_{r.zip}.json")
        member = next(n for n in zips[r.zip].names()
                      if n.rsplit("/", 1)[-1].split(".")[0] == r.GroupID and n.lower().endswith(".wav")
                      and "__MACOSX" not in n)
        zips[r.zip].extract(member, dest, label=f"{r.GroupID}.wav")

    names = {r.GroupID: f"clip_{i + 1:02d}.flac" for i, r in enumerate(clips.itertuples())}
    names.update({r.GroupID: f"practice_{i + 1}.flac" for i, r in enumerate(practice.itertuples())})

    out, info = {}, []
    for r in sel.itertuples():
        x, fs = sf.read(isd.RAWDIR / r.LocationID / f"{r.GroupID}.wav", always_2d=True)
        if fs != args.samplerate:                     # ISD has 44.1 and 48 kHz files: use one rate
            x = signal.resample_poly(x, args.samplerate, fs, axis=0)
            fs = args.samplerate
        lf = leq_dbfs(x[:, 0], fs) + 20 * math.log10(1 / P_REF)          # file in pascal -> dB SPL
        corr = r.LAeq - lf if abs(r.LAeq - lf) > isd.LEVEL_TOLERANCE else 0.0
        x = x * 10 ** (corr / 20)
        if len(x) / fs < args.length:
            sys.exit(f"{r.GroupID} is only {len(x) / fs:.1f} s long")
        start = best_start(x, fs, args.length)
        y = fade(x[int(start * fs): int(start * fs) + int(args.length * fs)], fs, args.fade)
        out[r.GroupID] = (y, fs)
        info.append({"file": names[r.GroupID], "GroupID": r.GroupID, "label": r.label, "use": r.use,
                     "LocationID": r.LocationID, "city": getattr(r, "city", ""),
                     "start_s": start, "end_s": start + args.length,
                     "duration_recording_s": round(len(x) / fs, 1),
                     "laeq_recording_db": round(lf + corr, 1),
                     "laeq_excerpt_db": round(leq_dbfs(y[:, 0], fs) + 20 * math.log10(1 / P_REF), 1),
                     "level_corrected_db": round(corr, 1), "samplerate": fs})

    peak = max(np.abs(y).max() for y, _ in out.values())
    gain = 10 ** (args.peak / 20) / peak
    gain_db = 20 * math.log10(gain)
    for row in info:
        # playback level in dBFS = real level (dB SPL) + offset
        row["dbfs_minus_dbspl"] = round(gain_db + 20 * math.log10(P_REF), 1)
    df = pd.DataFrame(info)
    print(df[["file", "GroupID", "label", "start_s", "laeq_recording_db", "laeq_excerpt_db",
              "level_corrected_db"]].to_string(index=False))
    print(f"common gain: {gain_db:+.1f} dB re 1 Pa (loudest peak -> {args.peak} dBFS); "
          f"playback level in dBFS = dB SPL {gain_db + 20 * math.log10(P_REF):+.1f}")
    if args.dry_run:
        return

    audio = REPO / "public" / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    for gid, (y, fs) in out.items():
        write_flac(audio / names[gid], (y * gain).astype(np.float32), fs, args.bits)
    df.to_csv(HERE / "isd_clips.csv", index=False)

    elements = pd.DataFrame({"element_id": range(1, len(clips) + 1),
                             "filename": [names[g] for g in clips.GroupID],
                             "category": clips.label.str.replace(",", ";")})
    elements.to_csv(REPO / "private" / "data" / "elements.csv", index=False)

    cfg = REPO / "private" / "config.ini"
    text = cfg.read_text(encoding="utf-8")
    text = set_ini(text, "stimuli", "n_clips", str(len(clips)))
    text = set_ini(text, "stimuli", "practice_clips", '"' + " | ".join(names[g] for g in practice.GroupID) + '"')
    cfg.write_text(text, encoding="utf-8")

    lines = [f"- {isd.CREDIT}. Recordings used (30-s excerpts, level-adjusted, faded in and out):"]
    for row in info:
        lines.append(f"  - {row['GroupID']} ({row['LocationID']}, {row['city']}), "
                     f"{row['start_s']:.1f}-{row['end_s']:.1f} s -> {row['file']}")
    (HERE / "ATTRIBUTION.md").write_text("# Sound sources\n\n" + "\n".join(lines) + "\n", encoding="utf-8")
    items = "".join(f"<li>{html.escape(r['GroupID'])} ({html.escape(r['LocationID'])}, {html.escape(str(r['city']))})</li>"
                    for r in info)
    (REPO / "public" / "credits.html").write_text(f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>Sound credits</title><link rel="stylesheet" href="static/style.css">
<main><div class="panel"><h1>Sound recordings / Tonaufnahmen</h1>
<p>The sounds in this study are 30-second excerpts of recordings from the International Soundscape Database
(ISD v1.0; Mitchell, Oberman, Aletta, Erfanian, Kachlicka, Lionello, Kang et al.),
<a href="https://doi.org/10.5281/zenodo.10672568">doi:10.5281/zenodo.10672568</a>,
published under the <a href="https://creativecommons.org/licenses/by/4.0/">Creative Commons Attribution 4.0</a> licence.
They were shortened, faded in and out and level-adjusted.</p>
<p>Die Klänge dieser Studie sind 30-Sekunden-Ausschnitte aus Aufnahmen der International Soundscape Database
(ISD v1.0; Mitchell, Oberman, Aletta, Erfanian, Kachlicka, Lionello, Kang et al.),
<a href="https://doi.org/10.5281/zenodo.10672568">doi:10.5281/zenodo.10672568</a>,
veröffentlicht unter der Lizenz <a href="https://creativecommons.org/licenses/by/4.0/deed.de">Creative Commons Namensnennung 4.0</a>. Sie wurden gekürzt, ein- und ausgeblendet und im Pegel angepasst.</p>
<ul>{items}</ul></div></main></html>
""", encoding="utf-8")
    print(f"written: {len(out)} files to {audio}, private/data/elements.csv ({len(clips)} clips), "
          f"config.ini (n_clips, practice_clips), stimuli/isd_clips.csv, ATTRIBUTION.md, public/credits.html")
    if len(practice) != 3:
        print(f"note: {len(practice)} practice clips; the practice triad needs exactly 3")


if __name__ == "__main__":
    main()
