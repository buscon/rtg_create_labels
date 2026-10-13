"""Step 2b (optional): pre-select a shortlist of candidates to listen to first.

    python stimuli/02b_shortlist.py [--per-class Woodland=6 Park=6 Beach=4 usotw=8] [--max-rise 15] [--max-lf 10]

Reads stimuli/work/candidates.csv (from 02_candidates.py) and the raw recordings.
The choice is made from the indicators only - it cannot replace listening.

  1. Adds one indicator: bird_band, the balance between 2-8 kHz (birds, insects,
     also hiss) and 0.2-1.5 kHz (traffic, voices, surf), on the W channel after a
     200-Hz high-pass. -1 = only low band, +1 = only high band. Unlike ndsi in
     candidates.csv, it is not dominated by wind and rumble below 200 Hz.
  2. Flags excerpts with a dominating single event (max_rise_db > --max-rise) or
     low-frequency noise in the directional channels (lf_dipole_excess > --max-lf).
  3. Keeps one excerpt per recording: among the unflagged excerpts, the one most
     typical for that recording (closest to the recording's median indicators).
  4. Within each class, picks recordings that are as different from each other as
     possible (farthest-point sampling on level, level spread, brightness,
     bird_band), so the shortlist covers the range of each class.

Output: stimuli/work/shortlist.csv and stimuli/work/shortlist.html (with players).
Recordings whose excerpts are all flagged are listed at the end as fallbacks.
"""
from __future__ import annotations

import argparse
import html
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import WORK, parse_candidate, read_csv, source_path, write_csv  # noqa: E402

DEFAULT_QUOTA = {"Woodland": 6, "Park": 6, "Beach": 4, "usotw": 8}
OTHER_EIGENSCAPE = 3
FEATURES = ["laeq_est", "l10_l90", "log_centroid", "bird_band"]


def bird_band(x: np.ndarray, fs: int) -> float:
    w = signal.sosfilt(signal.butter(4, 200, "highpass", fs=fs, output="sos"), x[:, 0])
    f, p = signal.welch(w, fs, nperseg=4096)
    low = p[(f >= 200) & (f < 1500)].sum()
    high = p[(f >= 2000) & (f < 8000)].sum()
    return float((high - low) / (high + low + 1e-30))


def group_of(r: dict) -> str:
    return "usotw" if r["dataset"] == "usotw" else r["class"]


def describe(r: dict) -> str:
    tags = []
    lv = r["laeq_est"]
    if lv is not None:
        tags.append("quiet" if lv < 45 else "moderate" if lv < 60 else "loud")
    s = r["l10_l90"]
    tags.append("steady" if s < 4 else "eventful" if s > 8 else "some variation")
    b = r["bird_band"]
    tags.append("high band strong (birds/insects?)" if b > 0.2 else
                "low band strong (traffic/voices/surf?)" if b < -0.5 else "balanced spectrum")
    return ", ".join(tags)


def zscores(rows: list[dict]) -> np.ndarray:
    m = np.array([[r[f] if r[f] is not None else np.nan for f in FEATURES] for r in rows], float)
    mu, sd = np.nanmean(m, axis=0), np.nanstd(m, axis=0)
    sd[sd == 0] = 1
    z = (m - mu) / sd
    return np.nan_to_num(z)


def farthest_points(rows: list[dict], k: int) -> list[dict]:
    if len(rows) <= k:
        return rows
    z = zscores(rows)
    chosen = [int(np.argmin(np.linalg.norm(z - np.median(z, axis=0), axis=1)))]   # start with the most typical
    while len(chosen) < k:
        d = np.min(np.linalg.norm(z[:, None, :] - z[chosen][None, :, :], axis=2), axis=1)
        d[chosen] = -1
        chosen.append(int(np.argmax(d)))
    return [rows[i] for i in chosen]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--per-class", nargs="*", default=[], metavar="GROUP=N",
                    help="recordings per group; groups: usotw or an EigenScape class (default Woodland=6 Park=6 Beach=4 usotw=8, other classes 3)")
    ap.add_argument("--max-rise", type=float, default=15.0, help="flag excerpts with max_rise_db above this (default 15)")
    ap.add_argument("--max-lf", type=float, default=10.0, help="flag excerpts with lf_dipole_excess above this (default 10)")
    args = ap.parse_args()
    quota = dict(DEFAULT_QUOTA)
    for item in args.per_class:
        g, n = item.split("=")
        quota[g] = int(n)

    rows = read_csv(WORK / "candidates.csv")
    if not rows:
        sys.exit("no candidates - run 02_candidates.py first")
    print(f"computing bird_band for {len(rows)} excerpts ...")
    cache: dict[tuple, float] = {}
    for i, r in enumerate(rows):
        ds, src, start = parse_candidate(r["candidate_id"])
        path = source_path(ds, src)
        info = sf.info(path)
        length = 30 * info.samplerate
        x, fs = sf.read(path, start=start * info.samplerate, frames=length, always_2d=True)
        r["bird_band"] = round(bird_band(x, fs), 3)
        for k in ("laeq_est", "l10_l90", "max_rise_db", "centroid_hz", "lf_dipole_excess"):
            r[k] = float(r[k]) if r[k] not in ("", None) else None
        r["log_centroid"] = math.log(r["centroid_hz"]) if r["centroid_hz"] else None
        flags = []
        if r["max_rise_db"] is not None and r["max_rise_db"] > args.max_rise:
            flags.append(f"sudden event ({r['max_rise_db']:.0f} dB rise)")
        if r["lf_dipole_excess"] is not None and r["lf_dipole_excess"] > args.max_lf:
            flags.append(f"low-frequency noise ({r['lf_dipole_excess']:.0f} dB)")
        r["flags"] = "; ".join(flags)
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(rows)}")

    # one excerpt per recording
    by_rec = defaultdict(list)
    for r in rows:
        by_rec[(r["dataset"], r["source"])].append(r)
    best, fallback = [], []
    for recs in by_rec.values():
        clean = [r for r in recs if not r["flags"]]
        pool = clean or recs
        z = zscores(pool)
        typical = pool[int(np.argmin(np.linalg.norm(z - np.median(z, axis=0), axis=1)))]
        typical["clean_excerpts"] = f"{len(clean)}/{len(recs)}"
        (best if clean else fallback).append(typical)

    # spread within each group
    groups = defaultdict(list)
    for r in best:
        groups[group_of(r)].append(r)
    shortlist = []
    for g in sorted(groups):
        picked = farthest_points(groups[g], quota.get(g, OTHER_EIGENSCAPE))
        picked.sort(key=lambda r: (r["laeq_est"] is None, r["laeq_est"] or 0))
        shortlist += picked
        print(f"{g}: {len(picked)} of {len(groups[g])} usable recordings")

    out_cols = ["candidate_id", "class", "place", "laeq_est", "l10_l90", "max_rise_db", "centroid_hz",
                "bird_band", "lf_dipole_excess", "clean_excerpts", "description", "flags", "keep", "note", "preview"]
    table = []
    for r in shortlist + sorted(fallback, key=lambda r: r["candidate_id"]):
        r["description"] = describe(r)
        r["keep"], r["note"] = "", ""
        table.append({c: ("" if r.get(c) is None else r.get(c)) for c in out_cols})
    write_csv(WORK / "shortlist.csv", table)
    write_html(WORK / "shortlist.html", table, len(shortlist))
    print(f"\n{len(shortlist)} shortlisted, {len(fallback)} fallbacks -> {WORK / 'shortlist.html'}")


def write_html(path: Path, rows: list[dict], n_short: int) -> None:
    cols = ["candidate_id", "place", "laeq_est", "l10_l90", "max_rise_db", "centroid_hz", "bird_band",
            "clean_excerpts", "description", "flags"]
    head = "<th>preview</th>" + "".join(f"<th>{c}</th>" for c in cols)
    parts, current = [], None
    for i, r in enumerate(rows):
        section = "Fallbacks (every excerpt of the recording is flagged)" if i >= n_short else r["class"]
        if section != current:
            if current is not None:
                parts.append("</tbody></table>")
            parts.append(f"<h2>{html.escape(section)}</h2><table><thead><tr>{head}</tr></thead><tbody>")
            current = section
        cells = "".join(f"<td>{html.escape(str(r[c]))}</td>" for c in cols)
        parts.append(f"<tr><td><audio controls preload='none' src='{html.escape(r['preview'])}'></audio></td>{cells}</tr>")
    parts.append("</tbody></table>")
    path.write_text(f"""<!doctype html><meta charset="utf-8"><title>Shortlist</title>
<style>body{{font:14px system-ui,sans-serif;margin:1.5rem}}table{{border-collapse:collapse;margin-bottom:1.5rem}}
td,th{{border-bottom:1px solid #ddd;padding:.3rem .5rem;text-align:left;white-space:nowrap}}
th{{background:#f3f3f0}}audio{{height:28px}}h2{{margin-top:2rem}}</style>
<h1>Shortlist ({n_short} excerpts, one per recording)</h1>
<p>Selected from the indicators only, to cover the range within each class. Previews are peak-normalised:
judge loudness from laeq_est. Mark your choices in shortlist.csv (columns keep and note).
bird_band: -1 = energy only at 0.2-1.5 kHz, +1 = only at 2-8 kHz.
clean_excerpts: excerpts of this recording without flags.</p>
{''.join(parts)}
""", encoding="utf-8")


if __name__ == "__main__":
    main()
