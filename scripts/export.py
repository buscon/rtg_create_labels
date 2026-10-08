"""Export the database to CSV files for analysis.

    python scripts/export.py [--out export/]

Writes:
  participants.csv            one row per session (without the config snapshot)
  triads.csv, responses.csv, play_events.csv, interviewer_judgements.csv, elements.csv
  constructs.csv              all constructs with triad, chosen pair and odd clip
  constructs_by_participant/  one text file per participant, for reading and coding

Grid files for OpenRepGrid need ratings, which are collected in the second step.
The export for OpenRepGrid will be added together with the rating stage.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rgtconf import db_path, load_config  # noqa: E402

CONSTRUCTS_SQL = """
SELECT c.construct_id, c.participant_id, p.mode, p.language, t.order_index, t.set_number,
       e1.filename AS pair_clip_1, e2.filename AS pair_clip_2, e3.filename AS odd_clip,
       c.similarity_pole, c.contrast_pole, c.auto_is_new, c.nearest_construct, c.similarity_score,
       c.similarity_backend, ij.judged_new AS interviewer_judged_new, ij.same_as AS interviewer_same_as,
       ij.revised_similarity_pole, ij.revised_contrast_pole, ij.note AS interviewer_note,
       r.rt_ms, c.created_at, p.config_version
FROM constructs c
JOIN triads t ON t.triad_id = c.triad_id
JOIN participants p ON p.participant_id = c.participant_id
JOIN responses r ON r.construct_id = c.construct_id
LEFT JOIN elements e1 ON e1.element_id = r.pair_a
LEFT JOIN elements e2 ON e2.element_id = r.pair_b
LEFT JOIN elements e3 ON e3.element_id = r.odd_one
LEFT JOIN interviewer_judgements ij ON ij.construct_id = c.construct_id
WHERE c.is_practice = 0
ORDER BY c.participant_id, t.order_index
"""


def dump(con: sqlite3.Connection, sql: str, path: Path) -> int:
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None, help="default: private/config.ini")
    ap.add_argument("--db", default=None, help="SQLite file, e.g. made by import_dump.py (default: db_path from config)")
    ap.add_argument("--out", default="export")
    args = ap.parse_args()
    cfg = load_config(args.config)
    db = args.db or db_path(cfg)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)

    participant_cols = [r[1] for r in con.execute("PRAGMA table_info(participants)") if r[1] != "config_snapshot"]
    n = dump(con, f"SELECT {', '.join(participant_cols)} FROM participants ORDER BY started_at",
             out / "participants.csv")
    print(f"participants.csv: {n} rows")
    for table in ("elements", "triads", "responses", "play_events", "interviewer_judgements"):
        n = dump(con, f"SELECT * FROM {table}", out / f"{table}.csv")
        print(f"{table}.csv: {n} rows")
    n = dump(con, CONSTRUCTS_SQL, out / "constructs.csv")
    print(f"constructs.csv: {n} rows")

    by_p = out / "constructs_by_participant"
    by_p.mkdir(exist_ok=True)
    con.row_factory = sqlite3.Row
    for p in con.execute("SELECT participant_id, mode, stop_reason FROM participants"):
        rows = con.execute(CONSTRUCTS_SQL.replace("WHERE c.is_practice = 0",
                                                  "WHERE c.is_practice = 0 AND c.participant_id = ?"),
                           (p["participant_id"],)).fetchall()
        with open(by_p / f"{p['participant_id']}.txt", "w", encoding="utf-8") as f:
            f.write(f"{p['participant_id']}  mode={p['mode']}  stop={p['stop_reason']}\n\n")
            for r in rows:
                flag = "new" if r["auto_is_new"] else f"repeat of {r['nearest_construct']}"
                f.write(f"[{r['order_index']:>2}] {r['similarity_pole']}  ↔  {r['contrast_pole']}"
                        f"   ({flag}, score={r['similarity_score']})\n")
    print(f"per-participant files in {by_p}")


if __name__ == "__main__":
    main()
