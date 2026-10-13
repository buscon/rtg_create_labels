"""Shared settings and helpers for the stimulus scripts."""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
from scipy import signal

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"            # downloaded recordings (not in git)
WORK = HERE / "work"          # candidates, previews, exported excerpts (not in git)

USOTW = {
    "record": "10106181",
    "base": "https://zenodo.org/records/10106181/files/",
    "metadata": "USotW_metadata.csv",
    "zips": ["ambisonics_1to50.zip", "ambisonics_51to80.zip", "ambisonics_81to133.zip"],
    "credit": ("Urban Soundscapes of the World (De Coensel, Sun, Botteldooren et al.), "
               "Zenodo, doi:10.5281/zenodo.10106181, CC BY 4.0"),
}
EIGENSCAPE = {
    "record": "2628463",
    "base": "https://zenodo.org/records/2628463/files/",
    "metadata": "Metadata-EigenScape.csv",
    "zip": "Lite-EigenScape.zip",          # first-order ambisonics (FLAC), all classes
    "credit": ("EigenScape (Green & Murphy), Zenodo, doi:10.5281/zenodo.2628463, CC BY 4.0"),
}
CLASSES = ["Beach", "BusyStreet", "Park", "PedestrianZone", "QuietStreet",
           "ShoppingCentre", "TrainStation", "Woodland"]


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def eigenscape_member(filename: str) -> str:
    """Metadata name 'Woodland-05.wav' -> member name in the Lite zip 'Woodland.5.flac'."""
    stem = filename.rsplit(".", 1)[0]
    cls, num = stem.rsplit("-", 1)
    return f"{cls}.{int(num)}.flac"


def a_weighting(fs: int):
    """Second-order sections of the IEC 61672 A-weighting filter for sample rate fs."""
    f1, f2, f3, f4 = 20.598997, 107.65265, 737.86223, 12194.217
    a1000 = 1.9997
    nums = [(2 * np.pi * f4) ** 2 * (10 ** (a1000 / 20)), 0, 0, 0, 0]
    dens = np.polymul([1, 4 * np.pi * f4, (2 * np.pi * f4) ** 2],
                      [1, 4 * np.pi * f1, (2 * np.pi * f1) ** 2])
    dens = np.polymul(np.polymul(dens, [1, 2 * np.pi * f3]), [1, 2 * np.pi * f2])
    b, a = signal.bilinear(nums, dens, fs)
    return signal.tf2sos(b, a)


def leq_dbfs(x: np.ndarray, fs: int, weighted: bool = True) -> float:
    """Equivalent level of a mono signal in dB relative to full scale (A-weighted by default)."""
    if weighted:
        x = signal.sosfilt(a_weighting(fs), x)
    return float(10 * np.log10(np.mean(x ** 2) + 1e-20))


def usotw_offset(raw_dir: Path = RAW / "usotw") -> tuple[float | None, list[float]]:
    """dB to add to W-channel dBFS (A-weighted) to estimate LAeq, from USotW's published LAeq values."""
    import soundfile as sf
    meta = {r["Recording"]: r for r in read_csv(raw_dir / "USotW_metadata.csv")}
    offs = []
    for p in sorted(raw_dir.glob("R*_segment_ambisonics.wav")):
        m = meta.get(p.name.split("_")[0])
        if m and m.get("LAeq,1min"):
            x, fs = sf.read(p, frames=60 * 48000)
            offs.append(float(m["LAeq,1min"]) - leq_dbfs(x[:, 0], fs))
    return (float(np.median(offs)) if offs else None), offs


def parse_candidate(cid: str) -> tuple[str, str, int]:
    """'usotw:R0124@30' -> ('usotw', 'R0124', 30)"""
    ds, rest = cid.split(":", 1)
    src, start = rest.rsplit("@", 1)
    return ds, src, int(start)


def source_path(ds: str, src: str) -> Path:
    if ds == "usotw":
        return RAW / "usotw" / f"{src}_segment_ambisonics.wav"
    cls, num = src.rsplit("-", 1)
    return RAW / "eigenscape" / f"{cls}.{int(num)}.flac"
