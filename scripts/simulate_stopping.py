"""Re-run the stopping rule on collected data with other parameter values.

    python scripts/simulate_stopping.py --threshold 0.80 --min-triads 6 --consecutive 3

For each participant, the stored similarity scores are compared with the new
threshold, and the stopping rule is applied to the resulting sequence. The
output shows at which triad the session would have stopped and how many new
constructs would have been collected by then.

Limitation: a simulation can only show EARLIER stops than the real ones. Triads
after the real end of a session were never presented, so a later stop cannot be
simulated. Time limits are not simulated.

Note: the stored score is that of the closest earlier construct. With a single
backend per session (the normal case) this gives the same answer as a full
re-computation for any threshold.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rgtconf import db_path, load_config  # noqa: E402
from rgtconf import stop_reason  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None, help="default: public/private/config.ini")
    ap.add_argument("--db", default=None, help="SQLite file, e.g. made by import_dump.py (default: db_path from config)")
    ap.add_argument("--threshold", type=float, help="embedding threshold (default from config)")
    ap.add_argument("--threshold-text", type=float, help="text threshold (default from config)")
    ap.add_argument("--min-triads", type=int)
    ap.add_argument("--consecutive", type=int)
    ap.add_argument("--max-triads", type=int)
    args = ap.parse_args()
    cfg = load_config(args.config)
    thr = args.threshold if args.threshold is not None else cfg["similarity"]["threshold_embedding"]
    thr_text = args.threshold_text if args.threshold_text is not None else cfg["similarity"]["threshold_text"]
    min_t = args.min_triads or cfg["stopping"]["min_triads"]
    cons = args.consecutive or cfg["stopping"]["consecutive_not_new"]
    max_t = args.max_triads or cfg["triads"]["max_triads"]
    nd_not_new = cfg["stopping"]["no_difference_counts_as_not_new"]

    con = sqlite3.connect(f"file:{args.db or db_path(cfg)}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    print(f"threshold embedding={thr} text={thr_text}  min_triads={min_t}  consecutive_not_new={cons}  max_triads={max_t}\n")
    print(f"{'participant':<16}{'mode':<12}{'real stop':<14}{'real n':>7}{'sim stop':>10}"
          f"{'new (real)':>12}{'new (sim)':>11}")
    for p in con.execute("SELECT participant_id, mode, stop_reason FROM participants "
                         "WHERE excluded = 0 ORDER BY started_at"):
        rows = con.execute(
            "SELECT r.no_difference, c.similarity_score, c.similarity_backend, c.auto_is_new "
            "FROM responses r JOIN triads t ON t.triad_id = r.triad_id "
            "LEFT JOIN constructs c ON c.construct_id = r.construct_id "
            "WHERE t.participant_id = ? AND t.order_index >= 1 ORDER BY t.order_index",
            (p["participant_id"],)).fetchall()
        if not rows:
            continue
        hist, sim_stop = [], None
        real_new = sum(1 for r in rows if not r["no_difference"] and r["auto_is_new"])
        sim_new_at_stop = 0
        for i, r in enumerate(rows, start=1):
            if r["no_difference"]:
                if nd_not_new:
                    hist.append(False)
            else:
                backend = r["similarity_backend"]
                if r["similarity_score"] is None:          # first construct of the session
                    is_new = True
                elif backend == "identical_text":
                    is_new = False
                else:
                    is_new = r["similarity_score"] < (thr if backend == "embedding" else thr_text)
                hist.append(is_new)
                sim_new_at_stop += int(is_new)
            if stop_reason(hist, 0, min_triads=min_t, consecutive_not_new=cons,
                           max_triads=max_t, time_limit_min=float("inf")):
                sim_stop = i
                break
        stop_txt = str(sim_stop) if sim_stop else f">{len(rows)}"
        print(f"{p['participant_id']:<16}{p['mode']:<12}{str(p['stop_reason']):<14}{len(rows):>7}"
              f"{stop_txt:>10}{real_new:>12}{sim_new_at_stop:>11}")


if __name__ == "__main__":
    main()
