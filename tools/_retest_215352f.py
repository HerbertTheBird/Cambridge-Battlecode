"""Focused retest of 215352f vs all opponents with more maps."""
from __future__ import annotations

import random
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tournament import run_match, discover_maps

SHA = "215352f"
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
    print()

    bot = f"bots/Hades_{SHA}"
    jobs = []
    for opp in OPPONENTS:
        opp_path = f"bots/{opp}"
        for m in maps:
            jobs.append((bot, opp_path, m, SEED, opp))
            jobs.append((opp_path, bot, m, SEED, opp))

    print(f"Total matches: {len(jobs)}")
    results: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    completed = 0
    t0 = time.perf_counter()

    def submit(bot_a, bot_b, m, seed, opp):
        return run_match(bot_a, bot_b, m, seed), opp, bot_a, bot_b

    with ThreadPoolExecutor(max_workers=THREADS) as ex:
        futs = [ex.submit(submit, *j) for j in jobs]
        for f in as_completed(futs):
            mr, opp, bot_a, bot_b = f.result()
            completed += 1
            entry = results[opp]
            hades = bot_a if "Hades_" in bot_a else bot_b
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
            print(f"  [{completed}/{len(jobs)}] vs {opp}: {who} t={mr.turn} {mr.elapsed_s:.0f}s | ETA {eta:.0f}s", flush=True)

    print()
    print(f"=== Hades_{SHA} (with rogue resign@600) — {NUM_MAPS} maps × 2 sides ===")
    tot_w = tot_l = tot_d = tot_e = 0
    for opp in OPPONENTS:
        w, l, d, e = results[opp]
        tot_w += w; tot_l += l; tot_d += d; tot_e += e
        n = w + l + d
        wr = w / n * 100 if n else 0.0
        print(f"  vs {opp:<10}  {w}-{l}-{d}  ({wr:.1f}%)  E:{e}")
    n = tot_w + tot_l + tot_d
    wr = tot_w / n * 100 if n else 0.0
    print(f"  {'TOTAL':<13}  {tot_w}-{tot_l}-{tot_d}  ({wr:.1f}%)  E:{tot_e}")


if __name__ == "__main__":
    main()
