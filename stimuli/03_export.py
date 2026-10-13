"""Step 3: cut the chosen excerpts as ambisonic files, ready for binaural rendering.

    python stimuli/03_export.py [--selection stimuli/selection.csv] [--eigenscape-offset DB]
                                [--highpass 80] [--equal-loudness]

Input: stimuli/selection.csv (copy of work/selection_template.csv), one row per clip:
    candidate_id   from candidates.csv, e.g. eigenscape:Woodland-05@270
    use            clip | practice
    name           output name without extension, e.g. clip_01, practice_1
    category       natural | mixed | urban (stored in the database, never shown to participants)
    note           free text

Output in stimuli/work/export/:
    foa/<name>.wav       4-channel first-order ambisonics, ACN/SN3D ("AmbiX"), 48 kHz, 24 bit
    export_report.csv    source, start, estimated LAeq, applied gain for each file
and stimuli/ATTRIBUTION.md (CC BY credits for all used recordings).

Levels: by default the real level differences between clips are kept. Every file is
multiplied by its dataset's calibration factor and then by ONE common gain, chosen so
that the loudest peak in the set is at -headroom dBFS. A quiet woodland therefore stays
quieter than a busy street. The binaural renderer must apply the same gain to all
files (step 4 checks this). With --equal-loudness, every clip is set to the same
A-weighted level instead.

EigenScape has no calibration, so --eigenscape-offset is required when EigenScape and
USotW clips are mixed (see README, "Calibration").
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (EIGENSCAPE, HERE, RAW, USOTW, WORK, leq_dbfs, parse_candidate,  # noqa: E402
                    read_csv, source_path, usotw_offset, write_csv)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selection", type=Path, default=HERE / "selection.csv")
    ap.add_argument("--length", type=float, default=30.0)
    ap.add_argument("--eigenscape-offset", type=float, default=None, help="dB, see README")
    ap.add_argument("--highpass", type=float, default=80.0,
                    help="high-pass cut-off in Hz against low-frequency noise (0 = off; default 80)")
    ap.add_argument("--fade", type=float, default=0.5, help="fade in/out in s (default 0.5)")
    ap.add_argument("--headroom", type=float, default=3.0, help="peak of the loudest file below 0 dBFS (default 3 dB)")
    ap.add_argument("--equal-loudness", action="store_true", help="same A-weighted level for every clip")
    args = ap.parse_args()

    if not args.selection.exists():
        sys.exit(f"{args.selection} not found: copy stimuli/work/selection_template.csv and fill it in")
    sel = [r for r in read_csv(args.selection) if r.get("candidate_id", "").strip()]
    names = [r["name"] for r in sel]
    if len(set(names)) != len(names):
        sys.exit("selection.csv: names must be unique")

    u_off, _ = usotw_offset()
    datasets = {parse_candidate(r["candidate_id"])[0] for r in sel}
    if not args.equal_loudness and "eigenscape" in datasets and "usotw" in datasets and args.eigenscape_offset is None:
        sys.exit("EigenScape and USotW clips are mixed, but EigenScape is not calibrated.\n"
                 "Give --eigenscape-offset (see README, 'Calibration') or use --equal-loudness.")
    offsets = {"usotw": u_off if u_off is not None else 0.0,
               "eigenscape": args.eigenscape_offset if args.eigenscape_offset is not None else 0.0}

    # 1. cut, high-pass, fade
    clips = []
    for r in sel:
        ds, src, start = parse_candidate(r["candidate_id"])
        path = source_path(ds, src)
        if not path.exists():
            sys.exit(f"{path} missing - download it with 01_download.py")
        fs = sf.info(path).samplerate
        x, _ = sf.read(path, start=int(start * fs), frames=int(args.length * fs))
        if args.highpass > 0:
            sos = signal.butter(4, args.highpass, "highpass", fs=fs, output="sos")
            x = signal.sosfiltfilt(sos, x, axis=0)
        n = int(args.fade * fs)
        ramp = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, n))
        x[:n] *= ramp[:, None]
        x[-n:] *= ramp[::-1, None]
        dbfs = leq_dbfs(x[:, 0], fs)
        clips.append({"row": r, "ds": ds, "src": src, "start": start, "fs": fs, "x": x,
                      "dbfs": dbfs, "laeq_est": dbfs + offsets[ds]})

    # 2. one common gain (or equal loudness), peak of the set at -headroom dBFS
    if args.equal_loudness:
        target = min(c["dbfs"] for c in clips)
        for c in clips:
            c["x"] = c["x"] * 10 ** ((target - c["dbfs"]) / 20)
    else:
        ref = min(offsets[c["ds"]] for c in clips)
        for c in clips:
            c["x"] = c["x"] * 10 ** ((offsets[c["ds"]] - ref) / 20)
    peak = max(np.abs(c["x"]).max() for c in clips)
    common = 10 ** (-args.headroom / 20) / peak

    out = WORK / "export" / "foa"
    out.mkdir(parents=True, exist_ok=True)
    report = []
    for c in clips:
        y = c["x"] * common
        sf.write(out / f"{c['row']['name']}.wav", y.astype(np.float32), c["fs"], subtype="PCM_24")
        report.append({"name": c["row"]["name"], "use": c["row"]["use"], "category": c["row"].get("category", ""),
                       "candidate_id": c["row"]["candidate_id"], "dataset": c["ds"], "source": c["src"],
                       "start_s": c["start"],
                       "laeq_est": round(c["laeq_est"], 1) if (c["ds"] == "usotw" and u_off is not None)
                       or (c["ds"] == "eigenscape" and args.eigenscape_offset is not None) else "",
                       "w_level_dbfs_out": round(leq_dbfs(y[:, 0], c["fs"]), 2),
                       "peak_dbfs_out": round(20 * np.log10(np.abs(y).max()), 2),
                       "note": c["row"].get("note", "")})
        print(f"  {c['row']['name']:<12} {c['row']['candidate_id']:<32} W {report[-1]['w_level_dbfs_out']:6.1f} dBFS(A)")
    write_csv(WORK / "export" / "export_report.csv", report)
    write_attribution(report)
    print(f"\n{len(report)} files -> {out}\n"
          f"mode: {'equal loudness' if args.equal_loudness else 'real level differences kept'}; "
          f"high-pass {args.highpass:g} Hz; format ACN/SN3D (AmbiX), 48 kHz, 24 bit\n"
          f"next: render to binaural (same gain for all files), then run 04_finalize.py")


def write_attribution(report: list[dict]) -> None:
    u = {r["Recording"]: r for r in read_csv(RAW / "usotw" / "USotW_metadata.csv")}
    e = {r["Filename"].rsplit(".", 1)[0]: r for r in read_csv(RAW / "eigenscape" / "Metadata-EigenScape.csv")}
    lines = ["# Sound recordings used in the study", "",
             "The stimuli are 30-second excerpts of the following recordings, high-pass filtered, faded in and out,",
             "level-adjusted and rendered to binaural audio.", ""]
    for r in sorted(report, key=lambda r: r["name"]):
        if r["dataset"] == "usotw":
            m = u.get(r["source"], {})
            where = f"{m.get('City', '')}, {m.get('Location', '')}"
            credit = USOTW["credit"]
        else:
            m = e.get(r["source"], {})
            where = m.get("Location", "")
            credit = EIGENSCAPE["credit"]
        lines.append(f"- **{r['name']}**: recording {r['source']} ({where}), from {r['start_s']} s. {credit}.")
    lines += ["", "Licence of the recordings: Creative Commons Attribution 4.0 International "
              "(https://creativecommons.org/licenses/by/4.0/)."]
    (HERE / "ATTRIBUTION.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
