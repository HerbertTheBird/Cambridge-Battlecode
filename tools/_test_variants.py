"""Run multiple Hades variants vs Lethe + v872 in parallel with a shared thread pool."""
from __future__ import annotations

import random
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tournament import run_match, discover_maps

OPPONENTS = ["Lethe", "v872"]
NUM_MAPS = 4
THREADS = 8
SEED = 1


def main():
    if len(sys.argv) < 2:
        print("usage: python _test_variants.py <variant_path>...")
        sys.exit(1)
    variants = sys.argv[1:]

    repo = Path(__file__).parent
    all_maps = discover_maps(repo / "maps")
    random.Random(42).shuffle(all_maps)
    maps = sorted(all_maps[:NUM_MAPS])
    print(f"Maps ({len(maps)}): {[m.stem for m in maps]}")
    print(f"Variants: {variants}")
    print()

    jobs = []
    for variant in variants:
        for opp in OPPONENTS:
            opp_path = f"bots/{opp}"
            for m in maps:
                jobs.append((variant, opp_path, m, SEED, variant, opp))
                jobs.append((opp_path, variant, m, SEED, variant, opp))

    print(f"Total matches: {len(jobs)} ({len(variants)} variants x {len(OPPONENTS)} opps x {NUM_MAPS} maps x 2 sides)")
    # results[(variant, opp)] = [w, l, d, e]
    results: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    completed = 0
    t0 = time.perf_counter()

    def submit(bot_a, bot_b, m, seed, variant, opp):
        return run_match(bot_a, bot_b, m, seed), variant, opp, bot_a, bot_b

    with ThreadPoolExecutor(max_workers=THREADS) as ex:
        futs = [ex.submit(submit, *j) for j in jobs]
        for f in as_completed(futs):
            mr, variant, opp, bot_a, bot_b = f.result()
            completed += 1
            entry = results[(variant, opp)]
            hades = bot_a if bot_a == variant else bot_b
            if mr.error:
                entry[3] += 1
            elif mr.winner is None:
                entry[2] += 1
            elif mr.winner == hades:
                entry[0] += 1
            else:
                entry[1] += 1
            elapsed = time.perf_counter() - t0
            rate = completed / elapsed
            eta = (len(jobs) - completed) / rate if rate > 0 else 0
            who = mr.winner.split("/")[-1] if mr.winner else "draw"
            print(f"  [{completed}/{len(jobs)}] {variant.split('/')[-1]} vs {opp}: {who} t={mr.turn} {mr.elapsed_s:.0f}s | ETA {eta:.0f}s", flush=True)

    print()
    print("=" * 80)
    print(f"{'Variant':<35} | " + "  ".join(f"{o:<14}" for o in OPPONENTS) + "  TOTAL")
    print("=" * 80)
    for variant in variants:
        row = []
        tot_w = tot_l = tot_d = tot_e = 0
        for opp in OPPONENTS:
            w, l, d, e = results[(variant, opp)]
            tot_w += w; tot_l += l; tot_d += d; tot_e += e
            n = w + l + d
            wr = w / n * 100 if n else 0.0
            row.append(f"{w}-{l}-{d}({wr:.0f}%)")
        n_tot = tot_w + tot_l + tot_d
        wr_tot = tot_w / n_tot * 100 if n_tot else 0.0
        print(f"{variant.split('/')[-1]:<35} | " + "  ".join(f"{r:<14}" for r in row) + f"  {tot_w}-{tot_l}-{tot_d}({wr_tot:.1f}%) E:{tot_e}")


if __name__ == "__main__":
    main()
