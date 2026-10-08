"""Create placeholder clips so the tool can be tested before the real recordings exist.

    python scripts/make_placeholder_audio.py

Writes one WAV file per clip listed in the elements file and the three practice
clips. Each placeholder is filtered noise with a different character, plus a
spoken-like amplitude pattern, so that the clips are distinguishable. The real
recordings later simply replace these files (same file names).
"""
from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rgtconf import load_config, read_elements  # noqa: E402

SR = 22050


def write_wav(path: Path, data: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(data, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def placeholder(seed: int, seconds: float) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(SR * seconds)
    spec = np.fft.rfft(rng.standard_normal(n))
    freqs = np.fft.rfftfreq(n, 1 / SR)
    centre = 200 * (1.6 ** (seed % 10))            # different spectral centre per clip
    spec *= np.exp(-0.5 * ((np.log2(freqs + 1) - np.log2(centre)) / 1.0) ** 2)
    x = np.fft.irfft(spec, n)
    t = np.arange(n) / SR
    x *= 0.6 + 0.4 * np.sin(2 * np.pi * (0.1 + 0.15 * (seed % 5)) * t)  # slow modulation
    x = x / (np.sqrt(np.mean(x ** 2)) + 1e-12) * 10 ** (-23 / 20)        # about -23 dBFS RMS
    fade = int(0.05 * SR)
    x[:fade] *= np.linspace(0, 1, fade)
    x[-fade:] *= np.linspace(1, 0, fade)
    return x


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None, help="default: public/private/config.ini")
    ap.add_argument("--seconds", type=float, default=None, help="default: clip_duration_s from config")
    args = ap.parse_args()
    cfg = load_config(args.config)
    secs = args.seconds or cfg["stimuli"]["clip_duration_s"]
    audio = cfg.public_dir / "audio"
    names = [e["filename"] for e in read_elements(cfg)] + list(cfg["stimuli"]["practice_clips"])
    for i, name in enumerate(names):
        out = audio / name
        if out.exists():
            print(f"exists, skipped: {out}")
            continue
        if out.suffix.lower() != ".wav":
            print(f"skipped (placeholders are WAV only): {name}")
            continue
        write_wav(out, placeholder(i, secs))
        print(f"written: {out}")


if __name__ == "__main__":
    main()
