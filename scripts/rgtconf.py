"""Shared helpers for the Python scripts: read config.ini and apply the stopping rule.

The web application itself is written in PHP (public/). These scripts run on
the researcher's computer: preparing audio, downloading assets, exporting and
analysing data.
"""
from __future__ import annotations

import configparser
import csv
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PUBLIC = REPO / "public"
PRIVATE = PUBLIC / "private"
DEFAULT_CONFIG = PRIVATE / "config.ini"
LIST_KEYS = {("study", "languages"), ("stimuli", "practice_clips"),
             ("interviewer", "followup_de"), ("interviewer", "followup_en")}


def _typed(value: str):
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        return v[1:-1]
    low = v.lower()
    if low in ("true", "on", "yes"):
        return True
    if low in ("false", "off", "no", "none"):
        return False
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


class Config(dict):
    private_dir: Path

    def path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else self.private_dir / p

    @property
    def public_dir(self) -> Path:
        return self.private_dir.parent


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path or DEFAULT_CONFIG).resolve()
    cp = configparser.ConfigParser(inline_comment_prefixes=(";",), interpolation=None)
    cp.optionxform = str
    with open(path, encoding="utf-8") as f:
        cp.read_file(f)
    cfg = Config()
    for section in cp.sections():
        cfg[section] = {}
        for k, v in cp.items(section):
            val = _typed(v)
            if (section, k) in LIST_KEYS:
                val = [x.strip() for x in str(val).split("|") if x.strip()]
            cfg[section][k] = val
    cfg.private_dir = path.parent
    return cfg


def read_elements(cfg: Config) -> list[dict]:
    with open(cfg.path(cfg["stimuli"]["elements_file"]), newline="", encoding="utf-8") as f:
        return [{"element_id": int(r["element_id"]), "filename": r["filename"],
                 "category": r.get("category") or None} for r in csv.DictReader(f)]


def db_path(cfg: Config) -> Path:
    return cfg.path(cfg["server"]["db_path"])


# --- stopping rule: same logic as stop_reason() in public/private/lib/core.php

def trailing_not_new(history: list[bool]) -> int:
    n = 0
    for is_new in reversed(history):
        if is_new:
            break
        n += 1
    return n


def stop_reason(history: list[bool], elapsed_min: float, *, min_triads: int,
                consecutive_not_new: int, max_triads: int, time_limit_min: float) -> str | None:
    n = len(history)
    if n >= max_triads:
        return "max_triads"
    if elapsed_min >= time_limit_min:
        return "time_limit"
    if n >= min_triads and trailing_not_new(history) >= consecutive_not_new:
        return "saturation"
    return None
