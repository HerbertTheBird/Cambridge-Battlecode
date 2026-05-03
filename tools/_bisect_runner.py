"""Bisection driver: runs each Hades snapshot vs each opponent on a fixed map set."""
from __future__ import annotations

import random
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tournament import run_match, discover_maps

COMMITS = [
    ("b6167d0", "bug fix (BASELINE)"),
    ("6ea7b21", "misc changes"),
    ("c082a41", "bug fixes by claude (80W-60L)"),
    ("1e59040", "bug fixes"),
    ("58bc752", "Merge"),
    ("8982951", "instant attack prefer"),
    ("b39b7ee", "small heal fix"),
    ("215352f", "small changes"),
    ("fe3e88a", "oops left in resign"),
    ("18f7527", "misc changes"),
    ("1f7a0e4", "new bot submitted"),
]

OPPONENTS = ["Lethe", "v872"]
NUM_MAPS = 4
THREADS = 8
SEED = 1


def main():
    repo = Path(__file__).parent
    all_maps = discover_maps(repo / "maps")
    random.Random(42).shuffle(all_maps)
    maps = sorted(all_maps[:NUM_MAPS])
    print(f"Maps ({len(maps)}): {[m.stem for m in maps]}")
    print(f"Commits: {len(COMMITS)} | Opponents: {OPPONENTS}")
    print()

    jobs = []
    for sha, _label in COMMITS:
        bot = f"bots/Hades_{sha}"
        for opp in OPPONENTS:
            opp_path = f"bots/{opp}"
            for m in maps:
                jobs.append((bot, opp_path, m, SEED, sha, opp))
                jobs.append((opp_path, bot, m, SEED, sha, opp))

    print(f"Total matches: {len(jobs)}")
    # results[(sha, opp)] = [wins, losses, draws, errors]
    results: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    completed = 0
    t0 = time.perf_counter()

    def submit(bot_a, bot_b, m, seed, sha, opp):
        return run_match(bot_a, bot_b, m, seed), sha, opp, bot_a, bot_b

    with ThreadPoolExecutor(max_workers=THREADS) as ex:
        futs = [ex.submit(submit, *j) for j in jobs]
        for f in as_completed(futs):
            mr, sha, opp, bot_a, bot_b = f.result()
            completed += 1
            entry = results[(sha, opp)]
            hades_bot = bot_a if "Hades_" in bot_a else bot_b
            if mr.error:
                entry[3] += 1
            elif mr.winner is None:
                entry[2] += 1
            elif mr.winner == hades_bot:
                entry[0] += 1
            else:
                entry[1] += 1
            elapsed = time.perf_counter() - t0
            rate = completed / elapsed
            eta = (len(jobs) - completed) / rate if rate > 0 else 0
            print(f"  [{completed}/{len(jobs)}] {sha} vs {opp}: {mr.winner.split('/')[-1] if mr.winner else 'draw'} t={mr.turn} {mr.elapsed_s:.0f}s | ETA {eta:.0f}s", flush=True)

    # Print results table
    print()
    print("=" * 90)
    print(f"{'Commit':<10} {'Label':<35} | " + "  ".join(f"{o:<14}" for o in OPPONENTS) + "  TOTAL")
    print("=" * 90)
    for sha, label in COMMITS:
        row = []
        tot_w = tot_l = tot_d = tot_e = 0
        for opp in OPPONENTS:
            w, l, d, e = results[(sha, opp)]
            tot_w += w; tot_l += l; tot_d += d; tot_e += e
            n = w + l + d
            wr = w / n * 100 if n else 0.0
            row.append(f"{w}-{l}-{d}({wr:.0f}%)")
        n_tot = tot_w + tot_l + tot_d
        wr_tot = tot_w / n_tot * 100 if n_tot else 0.0
        print(f"{sha:<10} {label:<35} | " + "  ".join(f"{r:<14}" for r in row) + f"  {tot_w}-{tot_l}-{tot_d}({wr_tot:.1f}%) E:{tot_e}")


if __name__ == "__main__":
    main()
