"""Convert a CSV dump from the server into a local SQLite database for analysis.

    python scripts/import_dump.py path/to/dump-YYYYMMDD-HHMMSS [--out data/rgt.sqlite]

The dump is created on the server with  php private/backup.php  and downloaded
with SFTP. The resulting SQLite file has the same tables as the live database,
so scripts/export.py and scripts/simulate_stopping.py can read it:

    python scripts/export.py --db data/rgt.sqlite
    python scripts/simulate_stopping.py --db data/rgt.sqlite --threshold 0.80
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rgtconf import PRIVATE  # noqa: E402

TABLES = ["elements", "participants", "triads", "constructs", "responses",
          "interviewer_judgements", "play_events", "ratings"]
csv.field_size_limit(1 << 30)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dump", type=Path)
    ap.add_argument("--out", type=Path, default=Path("data/rgt.sqlite"))
    args = ap.parse_args()
    if args.out.exists():
        sys.exit(f"{args.out} exists; choose another --out or delete it first")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(args.out)
    con.executescript((PRIVATE / "schema.sqlite.sql").read_text(encoding="utf-8"))
    for table in TABLES:
        path = args.dump / f"{table}.csv"
        if not path.exists():
            print(f"missing: {path}")
            continue
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            cols = next(reader)
            rows = [[None if v == r"\N" else v for v in r] for r in reader]
        con.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", rows)
        print(f"{table:<24}{len(rows)} rows")
    con.commit()
    con.close()
    print(f"written: {args.out}")


if __name__ == "__main__":
    main()
