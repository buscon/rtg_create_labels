"""Find a small set of triads in which every pair of clips appears at least once
(a pair-covering design), with the clips used as evenly as possible.

    python scripts/make_triad_set.py --clips 8 [--triads 11] [--out private/data/triad_set.csv]

Without --triads, the script starts at the lower bound (Schoenheim bound) and adds
triads until a covering is found. Among coverings it prefers the one where the
number of appearances per clip differs least, then the fewest repeated pairs.
Known minimum sizes: 6 clips -> 6 triads, 7 -> 7, 8 -> 11, 9 -> 12, 10 -> 17.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import math
import random
from collections import Counter
from pathlib import Path


def lower_bound(v: int) -> int:
    return math.ceil(v / 3 * math.ceil((v - 1) / 2))


def search(v: int, b: int, tries: int, seed: int) -> list[tuple[int, int, int]] | None:
    rng = random.Random(seed)
    all_triads = list(itertools.combinations(range(1, v + 1), 3))
    best, best_key = None, None
    for _ in range(tries):
        chosen = rng.sample(all_triads, b)
        for _step in range(4000):
            covered = Counter(p for t in chosen for p in itertools.combinations(t, 2))
            missing = [p for p in itertools.combinations(range(1, v + 1), 2) if covered[p] == 0]
            if not missing:
                break
            # replace the triad whose removal loses least by one that covers a missing pair
            p = rng.choice(missing)
            cand = [t for t in all_triads if p[0] in t and p[1] in t]
            new = rng.choice(cand)
            loss = [sum(1 for q in itertools.combinations(t, 2) if covered[q] == 1) for t in chosen]
            m = min(loss)
            i = rng.choice([k for k, l in enumerate(loss) if l == m])
            chosen[i] = new
        else:
            continue
        use = Counter(c for t in chosen for c in t)
        pairs = Counter(p for t in chosen for p in itertools.combinations(t, 2))
        key = (max(use.values()) - min(use.values()), sum(n - 1 for n in pairs.values()))
        if best is None or key < best_key:
            best, best_key = sorted(chosen), key
    return best


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clips", type=int, required=True)
    ap.add_argument("--triads", type=int, default=None)
    ap.add_argument("--tries", type=int, default=300)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    v = args.clips
    b = args.triads or lower_bound(v)
    while True:
        result = search(v, b, args.tries, args.seed)
        if result or args.triads:
            break
        b += 1
    if not result:
        raise SystemExit(f"no covering with {b} triads found")
    use = Counter(c for t in result for c in t)
    pairs = Counter(p for t in result for p in itertools.combinations(t, 2))
    assert len(pairs) == v * (v - 1) // 2
    print(f"{v} clips: {len(result)} triads (lower bound {lower_bound(v)}), all {len(pairs)} pairs covered")
    print(f"appearances per clip: {dict(sorted(use.items()))}")
    print(f"pairs shown twice or more: {sum(1 for n in pairs.values() if n > 1)}")
    for i, t in enumerate(result, 1):
        print(f"  {i:2d}: {t}")
    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["triad", "clip_a", "clip_b", "clip_c"])
            for i, t in enumerate(result, 1):
                w.writerow([i, *t])
        print(f"written: {args.out}")


if __name__ == "__main__":
    main()
