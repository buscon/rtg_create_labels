"""Create stimuli for the headphone check (Huggins pitch).

    python scripts/make_headphone_stimuli.py [--variants 3]

Principle (after Milne et al., 2021): the same white noise is played to both
ears, except for a narrow frequency band that is phase-inverted in one ear.
Over headphones this produces a faint tone (Huggins pitch); over loudspeakers
the effect largely disappears. Each stimulus has three noise intervals, one of
which contains the Huggins pitch. The participant indicates which one.

Output: the sound files go to public/audio/headphone/ (random names), the
answer key to private/headphone_manifest.csv.

PARAMETERS ARE APPROXIMATIONS. Check centre frequency, bandwidth, interval
duration and level against Milne et al. (2021) before using this in the study,
or use the stimuli published with that paper.
"""
from __future__ import annotations

import argparse
import csv
import secrets
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rgtconf import load_config  # noqa: E402

SR = 44100
CENTRE_HZ = 600.0
BANDWIDTH = 0.06       # relative bandwidth of the inverted band (±3 %)
INTERVAL_S = 1.0
GAP_S = 0.5
RAMP_S = 0.02
LEVEL_DBFS = -26.0


def ramp(x: np.ndarray) -> np.ndarray:
    n = int(RAMP_S * SR)
    w = np.ones(len(x))
    w[:n] = np.linspace(0, 1, n)
    w[-n:] = np.linspace(1, 0, n)
    return x * w


def noise_interval(rng, huggins: bool) -> np.ndarray:
    n = int(INTERVAL_S * SR)
    left = rng.standard_normal(n)
    right = left.copy()
    if huggins:
        spec = np.fft.rfft(right)
        f = np.fft.rfftfreq(n, 1 / SR)
        band = (f > CENTRE_HZ * (1 - BANDWIDTH / 2)) & (f < CENTRE_HZ * (1 + BANDWIDTH / 2))
        spec[band] *= -1
        right = np.fft.irfft(spec, n)
    st = np.stack([ramp(left), ramp(right)], axis=1)
    return st / np.sqrt(np.mean(st ** 2)) * 10 ** (LEVEL_DBFS / 20)


def write_stereo(path: Path, data: np.ndarray) -> None:
    pcm = (np.clip(data, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None, help="default: private/config.ini")
    ap.add_argument("--variants", type=int, default=3, help="different noise samples per target position")
    args = ap.parse_args()
    cfg = load_config(args.config)
    out = cfg.public_dir / "audio" / "headphone"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("hp_*.wav"):        # remove stimuli from an earlier run
        old.unlink()
    rng = np.random.default_rng()
    gap = np.zeros((int(GAP_S * SR), 2))
    rows = []
    for target in (1, 2, 3):
        for v in range(1, args.variants + 1):
            parts = []
            for k in (1, 2, 3):
                parts.append(noise_interval(rng, huggins=(k == target)))
                if k < 3:
                    parts.append(gap)
            name = f"hp_{secrets.token_hex(4)}.wav"   # random, unseeded: must not reveal the answer
            write_stereo(out / name, np.concatenate(parts))
            rows.append({"filename": name, "target": target})
    old_manifest = out / "manifest.csv"           # earlier versions kept the key in the web folder
    if old_manifest.exists():
        old_manifest.unlink()
    key = cfg.private_dir / "headphone_manifest.csv"   # answer key: private folder, never public
    with open(key, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["filename", "target"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} stimuli written to {out}")
    print(f"answer key written to {key} (upload it to the private folder on the server)")


if __name__ == "__main__":
    main()
