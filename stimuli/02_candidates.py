"""Step 2: cut the downloaded recordings into 30-s candidate excerpts and describe them.

    python stimuli/02_candidates.py [--step 30] [--eigenscape-offset DB] [--no-previews]

For every excerpt the script computes, on the omnidirectional channel (W):
  laeq_dbfs        A-weighted level relative to full scale
  laeq_est         estimated LAeq in dB: dBFS + calibration offset (see below)
  l10_l90          difference between loud and quiet moments (A-weighted, 125 ms):
                   high values = eventful, low = steady
  max_rise_db      largest jump of the short-term level within 1 s (sudden events)
  centroid_hz      spectral centroid of the A-weighted signal (brightness)
  ndsi, bi, aci    scikit-maad indices: NDSI (> 0: more energy 1-10 kHz, typical of
                   birds; < 0: more energy below 1 kHz, typical of traffic),
                   bioacoustic index, acoustic complexity index
  lf_dipole_excess how much louder the directional channels are than W below 200 Hz
                   (> 0 dB suggests low-frequency noise; use a high-pass in step 3)

Calibration: USotW gives a measured LAeq for each recording, so the script derives
one dBFS-to-dB offset (median over the downloaded USotW files). EigenScape has no
calibration information. Pass --eigenscape-offset to put EigenScape on the same
scale; without it, laeq_est stays empty for EigenScape (see README).

Output in stimuli/work/:
  candidates.csv           one row per excerpt
  candidates.html          overview with audio players (open in a browser)
  previews/*.mp3           rough stereo previews (two virtual cardioids, NOT the
                           final binaural rendering), 30 s each
  selection_template.csv   copy to stimuli/selection.csv and fill in (step 3)
"""
from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import RAW, WORK, a_weighting, leq_dbfs, read_csv, write_csv  # noqa: E402
from common import usotw_offset as usotw_offset_fn  # noqa: E402

try:
    from maad import features, sound
except ImportError:          # indices are optional
    features = sound = None


def short_levels(w: np.ndarray, fs: int, win_s: float = 0.125) -> np.ndarray:
    wa = signal.sosfilt(a_weighting(fs), w)
    n = int(win_s * fs)
    frames = wa[: len(wa) // n * n].reshape(-1, n)
    return 10 * np.log10(np.mean(frames ** 2, axis=1) + 1e-20)


def describe(x: np.ndarray, fs: int) -> dict:
    w = x[:, 0]
    lv = short_levels(w, fs)
    per_s = int(1 / 0.125)
    rises = [lv[i + 1: i + per_s + 1].max() - lv[i] for i in range(len(lv) - per_s)] or [0]
    f, p = signal.welch(w, fs, nperseg=4096)
    _, pa = signal.welch(signal.sosfilt(a_weighting(fs), w), fs, nperseg=4096)
    centroid = float((f * pa).sum() / (pa.sum() + 1e-20))   # A-weighted, closer to perceived brightness
    lo = f < 200
    _, pd = signal.welch(x[:, 1:].T, fs, nperseg=4096)
    lf_excess = 10 * np.log10(pd[:, lo].sum(axis=1).max() + 1e-20) - 10 * np.log10(p[lo].sum() + 1e-20)
    out = {
        "laeq_dbfs": round(leq_dbfs(w, fs), 1),
        "l10_l90": round(float(np.percentile(lv, 90) - np.percentile(lv, 10)), 1),
        "max_rise_db": round(float(max(rises)), 1),
        "centroid_hz": round(centroid),
        "ndsi": "", "bi": "", "aci": "",
        "lf_dipole_excess": round(float(lf_excess), 1),
    }
    if sound is not None:
        sxx, tn, fn, _ = sound.spectrogram(w, fs, nperseg=1024, noverlap=512)
        ndsi, *_ = features.soundscape_index(sxx, fn)
        out["ndsi"] = round(float(ndsi), 3)
        out["bi"] = round(float(features.bioacoustics_index(sxx, fn)), 2)
        out["aci"] = round(float(features.acoustic_complexity_index(sxx)[2]), 1)
    return out


def stereo_preview(x: np.ndarray, fs: int, dest: Path, sr_out: int = 24000) -> None:
    """Two virtual cardioids at +-90 degrees (ACN/SN3D: W=0, Y=1). For listening only."""
    w, y = x[:, 0], x[:, 1]
    st = np.stack([0.5 * (w + y), 0.5 * (w - y)], axis=1)
    st = signal.resample_poly(st, sr_out, fs, axis=0)
    peak = np.abs(st).max() + 1e-12
    st = st / peak * 10 ** (-3 / 20)            # previews are peak-normalised: compare content, not level
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.suffix == ".mp3":
        sf.write(dest, st.astype(np.float32), sr_out, format="MP3", subtype="MPEG_LAYER_III")
    else:
        sf.write(dest, st.astype(np.float32), sr_out, subtype="PCM_16")


def preview_ext() -> str:
    """MP3 plays in every browser (FLAC does not in Safari). Needs libsndfile >= 1.1; otherwise WAV."""
    return ".mp3" if "MP3" in sf.available_formats() else ".wav"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--length", type=float, default=30.0, help="excerpt length in s (default 30)")
    ap.add_argument("--step", type=float, default=30.0, help="distance between excerpt starts in s (default 30)")
    ap.add_argument("--eigenscape-offset", type=float, default=None,
                    help="dB to add to EigenScape dBFS values to estimate LAeq (default: unknown)")
    ap.add_argument("--no-previews", action="store_true")
    args = ap.parse_args()

    usotw_meta = {r["Recording"]: r for r in read_csv(RAW / "usotw" / "USotW_metadata.csv")}
    eig_meta = {r["Filename"].rsplit(".", 1)[0]: r for r in read_csv(RAW / "eigenscape" / "Metadata-EigenScape.csv")}

    sources = []
    for p in sorted((RAW / "usotw").glob("R*_segment_ambisonics.wav")):
        rid = p.name.split("_")[0]
        m = usotw_meta.get(rid, {})
        sources.append(("usotw", rid, p, f"{m.get('City', '')}: {m.get('Location', '')}", "urban/park (USotW)",
                        float(m["LAeq,1min"]) if m.get("LAeq,1min") else None))
    for p in sorted((RAW / "eigenscape").glob("*.flac")):
        cls, num = p.stem.split(".")
        name = f"{cls}-{int(num):02d}"
        m = eig_meta.get(name, {})
        place = m.get("Location", "") + (f" ({m['Additional Information']})" if m.get("Additional Information") else "")
        sources.append(("eigenscape", name, p, place, cls, None))
    if not sources:
        sys.exit("no recordings in stimuli/raw/ - run 01_download.py first")

    # USotW calibration: offset between the published LAeq (1 min) and W in dBFS
    usotw_offset, offsets = usotw_offset_fn()
    if offsets:
        print(f"USotW calibration offset: {usotw_offset:.1f} dB (spread {max(offsets) - min(offsets):.1f} dB over {len(offsets)} files)")
    if args.eigenscape_offset is None and any(s[0] == "eigenscape" for s in sources):
        print("EigenScape: no calibration given (--eigenscape-offset); laeq_est left empty for EigenScape")

    ext = preview_ext()
    rows = []
    for ds, sid, p, place, cls, _ in sources:
        info = sf.info(p)
        fs = info.samplerate
        starts = np.arange(0, info.duration - args.length + 1e-6, args.step)
        print(f"{sid}: {len(starts)} excerpts")
        for st in starts:
            x, _ = sf.read(p, start=int(st * fs), frames=int(args.length * fs))
            d = describe(x, fs)
            off = usotw_offset if ds == "usotw" else args.eigenscape_offset
            cid = f"{ds}:{sid}@{int(st)}"
            prev = WORK / "previews" / f"{ds}_{sid}_{int(st):03d}{ext}"
            if not args.no_previews:
                stereo_preview(x, fs, prev)
            rows.append({"candidate_id": cid, "dataset": ds, "source": sid, "start_s": int(st),
                         "class": cls, "place": place,
                         "laeq_est": round(d["laeq_dbfs"] + off, 1) if off is not None else "",
                         **d, "preview": str(prev.relative_to(WORK)) if not args.no_previews else ""})

    WORK.mkdir(parents=True, exist_ok=True)
    write_csv(WORK / "candidates.csv", rows)
    write_csv(WORK / "selection_template.csv", [{"candidate_id": rows[0]["candidate_id"], "use": "clip",
                                                  "name": "clip_01", "category": "natural", "note": ""}])
    write_html(WORK / "candidates.html", rows)
    print(f"\n{len(rows)} candidates -> {WORK / 'candidates.csv'}\n"
          f"overview with players -> {WORK / 'candidates.html'}")


def write_html(path: Path, rows: list[dict]) -> None:
    cols = ["candidate_id", "class", "place", "laeq_est", "laeq_dbfs", "l10_l90", "max_rise_db",
            "centroid_hz", "ndsi", "bi", "aci", "lf_dipole_excess"]
    head = "".join(f"<th onclick='sortBy({i})'>{c}</th>" for i, c in enumerate(cols)) + "<th>preview</th>"
    body = []
    for r in rows:
        cells = "".join(f"<td>{html.escape(str(r[c]))}</td>" for c in cols)
        audio = f"<audio controls preload='none' src='{html.escape(r['preview'])}'></audio>" if r["preview"] else ""
        body.append(f"<tr>{cells}<td>{audio}</td></tr>")
    path.write_text(f"""<!doctype html><meta charset="utf-8"><title>Stimulus candidates</title>
<style>body{{font:14px system-ui,sans-serif;margin:1.5rem}}table{{border-collapse:collapse}}
td,th{{border-bottom:1px solid #ddd;padding:.3rem .5rem;text-align:left;white-space:nowrap}}
th{{cursor:pointer;background:#f3f3f0;position:sticky;top:0}}audio{{height:28px}}</style>
<h1>Stimulus candidates ({len(rows)})</h1>
<p>Click a column to sort. Previews are rough stereo (virtual cardioids) and peak-normalised:
use them to judge content, not loudness or spatial quality. Level: see laeq_est / laeq_dbfs.</p>
<table id="t"><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>
<script>
let dir = 1;
function sortBy(i) {{
  const tb = document.querySelector('#t tbody'); dir = -dir;
  const rows = [...tb.rows].sort((a, b) => {{
    const x = a.cells[i].textContent, y = b.cells[i].textContent;
    const nx = parseFloat(x), ny = parseFloat(y);
    return (isNaN(nx) || isNaN(ny) ? x.localeCompare(y) : nx - ny) * dir;
  }});
  rows.forEach(r => tb.appendChild(r));
}}
</script>""", encoding="utf-8")


if __name__ == "__main__":
    main()
