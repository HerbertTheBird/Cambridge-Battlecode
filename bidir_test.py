#!/usr/bin/env python3
"""
Bidirectional head-to-head tester.
Runs bot_a vs bot_b AND bot_b vs bot_a across all maps (both sides).
Reports unbiased win rate for bot_a.

Usage:
    python bidir_test.py bot_a bot_b [--threads 14] [--seed 1]
"""
import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tournament import discover_maps, run_match

sys.stdout.reconfigure(line_buffering=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("bot_a")
    parser.add_argument("bot_b")
    parser.add_argument("--maps-dir", type=Path, default=Path("maps"))
    parser.add_argument("--threads", type=int, default=14)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--map-count", type=int, default=None)
    args = parser.parse_args()

    maps = discover_maps(args.maps_dir)
    if args.map_count and args.map_count < len(maps):
        import random
        random.seed(args.seed)
        maps = sorted(random.sample(maps, args.map_count))

    # Generate jobs: each map played both directions
    jobs = []
    for m in maps:
        jobs.append((args.bot_a, args.bot_b, m, args.seed))  # A side
        jobs.append((args.bot_b, args.bot_a, m, args.seed))  # B side

    print(f"Bidir test: {args.bot_a} vs {args.bot_b}")
    print(f"Maps: {len(maps)} | Total matches: {len(jobs)} | Threads: {args.threads}")
    print()

    a_wins_as_a = a_wins_as_b = 0
    b_wins_as_a = b_wins_as_b = 0
    draws_as_a = draws_as_b = 0
    errors = 0
    completed = 0
    t0 = time.perf_counter()

    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        futures = {pool.submit(run_match, *j): j for j in jobs}
        for f in as_completed(futures):
            mr = f.result()
            completed += 1
            is_a_on_a_side = (mr.bot_a == args.bot_a)
            if mr.error:
                errors += 1
            elif mr.winner == args.bot_a:
                if is_a_on_a_side:
                    a_wins_as_a += 1
                else:
                    a_wins_as_b += 1
            elif mr.winner == args.bot_b:
                if is_a_on_a_side:
                    b_wins_as_a += 1
                else:
                    b_wins_as_b += 1
            else:
                if is_a_on_a_side:
                    draws_as_a += 1
                else:
                    draws_as_b += 1
            if completed % 10 == 0 or completed == len(jobs):
                elapsed = time.perf_counter() - t0
                a_total = a_wins_as_a + a_wins_as_b
                b_total = b_wins_as_a + b_wins_as_b
                print(f"  [{completed}/{len(jobs)}] {args.bot_a}: {a_total}W  {args.bot_b}: {b_total}W  draws: {draws_as_a+draws_as_b}  errors: {errors}  ({elapsed:.0f}s)")

    elapsed = time.perf_counter() - t0

    a_wins_total = a_wins_as_a + a_wins_as_b
    b_wins_total = b_wins_as_a + b_wins_as_b
    decided = a_wins_total + b_wins_total
    a_wr = a_wins_total / decided if decided else 0.0

    print()
    print("=" * 70)
    print(f"RESULTS ({elapsed:.0f}s total)")
    print("=" * 70)
    print(f"{args.bot_a} as A (vs {args.bot_b}): {a_wins_as_a}W - {b_wins_as_a}L - {draws_as_a}D")
    print(f"{args.bot_a} as B (vs {args.bot_b}): {a_wins_as_b}W - {b_wins_as_b}L - {draws_as_b}D")
    print()
    print(f"{args.bot_a} TOTAL: {a_wins_total}W - {b_wins_total}L - {draws_as_a+draws_as_b}D  ({a_wr*100:.1f}% win rate)")
    print(f"{args.bot_b} TOTAL: {b_wins_total}W - {a_wins_total}L - {draws_as_a+draws_as_b}D")
    print(f"Errors: {errors}")


if __name__ == "__main__":
    main()
