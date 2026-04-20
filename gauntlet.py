#!/usr/bin/env python3
"""
Gauntlet Runner — test one bot against a field of opponents (no opponent-vs-opponent).

Unlike tournament.py which runs a full round-robin, this only runs the main bot
against each opponent, making it faster for evaluating a single bot.

Usage:
    python gauntlet.py Artemis_v0 --opponents rush z_do_nothing Hermes_v0
    python gauntlet.py Artemis_v0 --opponents rush --threads 4 --rounds 3
    python gauntlet.py bots/Artemis_v0 --opponents bots/rush --map-filter arena
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path

from tournament import (
    BotStats,
    DEFAULT_ELO,
    HeadToHead,
    MatchResult,
    discover_maps,
    elo_expected,
    elo_update,
    run_match,
)


# ── Gauntlet ────────────────────────────────────────────────────────────────

def run_gauntlet(
    main_bot: str,
    opponents: list[str],
    maps: list[Path],
    seeds: list[int],
    threads: int = 1,
    verbose: bool = False,
) -> tuple[dict[str, BotStats], list[MatchResult], dict[tuple[str, str], HeadToHead]]:
    """Run *main_bot* against each opponent (no opponent-vs-opponent matches)."""

    all_bots = [main_bot] + opponents
    stats: dict[str, BotStats] = {b: BotStats(name=b) for b in all_bots}
    results: list[MatchResult] = []
    h2h: dict[tuple[str, str], HeadToHead] = {}

    # Generate jobs: main_bot vs each opponent only
    jobs: list[tuple[str, str, Path, int]] = []
    for opp in opponents:
        h2h[(main_bot, opp)] = HeadToHead(bot_a=main_bot, bot_b=opp)
        for map_path in maps:
            for seed in seeds:
                jobs.append((main_bot, opp, map_path, seed))
                jobs.append((opp, main_bot, map_path, seed))

    total_matches = len(jobs)
    print(f"Gauntlet: {main_bot} vs {len(opponents)} opponent(s), {len(maps)} maps, {len(seeds)} seed(s)")
    print(f"Total matches: {total_matches}")
    print(f"Threads: {threads}")
    print()

    completed = 0
    match_started = time.perf_counter()

    def process_result(mr: MatchResult) -> None:
        nonlocal completed
        completed += 1

        results.append(mr)
        a_name, b_name = mr.bot_a, mr.bot_b
        main_side = "GOLD" if a_name == main_bot else "SILVER"
        opp = b_name if a_name == main_bot else a_name

        if mr.error:
            stats[a_name].errors += 1
            stats[b_name].errors += 1
            print(f"  [{completed}/{total_matches}] {mr.map_name} (seed {mr.seed}) | "
                  f"{main_bot} as {main_side}: ERROR [{mr.elapsed_s:.1f}s]")
            return

        stats[a_name].match_count += 1
        stats[b_name].match_count += 1
        if mr.turn:
            stats[a_name].total_turns += mr.turn
            stats[b_name].total_turns += mr.turn

        key = (main_bot, b_name) if a_name == main_bot else (main_bot, a_name)
        h2h_entry = h2h[key]

        if mr.winner == a_name:
            actual_a, actual_b = 1.0, 0.0
            stats[a_name].wins += 1
            stats[b_name].losses += 1
            if a_name == main_bot:
                h2h_entry.a_wins += 1
            else:
                h2h_entry.b_wins += 1
        elif mr.winner == b_name:
            actual_a, actual_b = 0.0, 1.0
            stats[a_name].losses += 1
            stats[b_name].wins += 1
            if b_name == main_bot:
                h2h_entry.a_wins += 1
            else:
                h2h_entry.b_wins += 1
        else:
            actual_a = actual_b = 0.5
            stats[a_name].draws += 1
            stats[b_name].draws += 1
            h2h_entry.draws += 1
            tail = "\n".join(mr.output.splitlines()[-20:])
            print(f"\n  DRAW: {a_name} vs {b_name} on {mr.map_name} (seed {mr.seed})")
            print(f"  Engine output (last 20 lines):\n{tail}")

        # ELO update
        exp_a = elo_expected(stats[a_name].elo, stats[b_name].elo)
        exp_b = 1.0 - exp_a
        stats[a_name].elo = elo_update(stats[a_name].elo, exp_a, actual_a)
        stats[b_name].elo = elo_update(stats[b_name].elo, exp_b, actual_b)

        if mr.winner == main_bot:
            outcome = "W"
        elif mr.winner is None:
            outcome = "D"
        else:
            outcome = "L"
        ms = stats[main_bot]
        print(f"  [{completed}/{total_matches}] {mr.map_name} (seed {mr.seed}) | "
              f"{main_bot} as {main_side} vs {opp}: {outcome} -> {mr.winner or 'draw'} "
              f"(T{mr.turn or '?'}) [{mr.elapsed_s:.1f}s] "
              f"| total: {ms.wins}W-{ms.losses}L-{ms.draws}D")

    if threads == 1:
        for bot_a, bot_b, map_path, seed in jobs:
            mr = run_match(bot_a, bot_b, map_path, seed)
            process_result(mr)
    else:
        futures: dict[Future[MatchResult], None] = {}
        with ThreadPoolExecutor(max_workers=threads) as executor:
            for bot_a, bot_b, map_path, seed in jobs:
                f = executor.submit(run_match, bot_a, bot_b, map_path, seed)
                futures[f] = None

            for f in as_completed(futures):
                mr = f.result()
                process_result(mr)

    elapsed = time.perf_counter() - match_started
    print(f"Completed in {elapsed:.1f}s")

    return stats, results, h2h


# ── Output ──────────────────────────────────────────────────────────────────

def print_leaderboard(stats: dict[str, BotStats]) -> None:
    ranked = sorted(stats.values(), key=lambda s: -s.elo)

    print(f"\n{'=' * 85}")
    print("  GAUNTLET LEADERBOARD")
    print(f"{'=' * 85}")

    name_w = max(len(s.name) for s in ranked) if ranked else 10
    print(f"  {'#':>3}  {'Bot':{name_w}}  {'ELO':>7}  {'W':>4}  {'L':>4}  {'D':>4}  {'WR':>6}  {'AvgT':>6}  {'Err':>4}")
    print(f"  {'---':>3}  {'-' * name_w}  {'---':>7}  {'--':>4}  {'--':>4}  {'--':>4}  {'--':>6}  {'----':>6}  {'---':>4}")

    for i, s in enumerate(ranked, 1):
        print(f"  {i:>3}  {s.name:{name_w}}  {s.elo:>7.1f}  {s.wins:>4}  {s.losses:>4}  "
              f"{s.draws:>4}  {s.win_rate:>5.1f}%  {s.avg_turns:>6.0f}  {s.errors:>4}")


def print_head_to_head(h2h: dict[tuple[str, str], HeadToHead]) -> None:
    entries = sorted(h2h.values(), key=lambda e: e.total, reverse=True)
    if not entries:
        return

    print(f"\n{'=' * 80}")
    print("  HEAD-TO-HEAD")
    print(f"{'=' * 80}")

    name_w = max(max(len(e.bot_a), len(e.bot_b)) for e in entries) if entries else 10
    print(f"  {'Main Bot':{name_w}}  vs  {'Opponent':{name_w}}  {'W':>4}  {'L':>4}  {'D':>4}")
    print(f"  {'-' * name_w}  --  {'-' * name_w}  {'--':>4}  {'--':>4}  {'--':>4}")

    for e in entries:
        print(f"  {e.bot_a:{name_w}}  vs  {e.bot_b:{name_w}}  {e.a_wins:>4}  {e.b_wins:>4}  {e.draws:>4}")


def print_side_breakdown(results: list[MatchResult], main_bot: str) -> None:
    """Print how many games main_bot won as GOLD (Team A) vs SILVER (Team B)."""
    gold_w = gold_l = gold_d = 0
    silver_w = silver_l = silver_d = 0

    for mr in results:
        if mr.error:
            continue
        if mr.bot_a == main_bot:
            if mr.winner == main_bot:
                gold_w += 1
            elif mr.winner is None:
                gold_d += 1
            else:
                gold_l += 1
        elif mr.bot_b == main_bot:
            if mr.winner == main_bot:
                silver_w += 1
            elif mr.winner is None:
                silver_d += 1
            else:
                silver_l += 1

    gold_total = gold_w + gold_l + gold_d
    silver_total = silver_w + silver_l + silver_d
    gold_wr = 100.0 * gold_w / gold_total if gold_total else 0.0
    silver_wr = 100.0 * silver_w / silver_total if silver_total else 0.0

    print(f"\n{'=' * 60}")
    print(f"  SIDE BREAKDOWN for {main_bot}")
    print(f"{'=' * 60}")
    print(f"  {'Side':<10}  {'W':>4}  {'L':>4}  {'D':>4}  {'Total':>6}  {'WR':>6}")
    print(f"  {'-' * 10}  {'--':>4}  {'--':>4}  {'--':>4}  {'-----':>6}  {'----':>6}")
    print(f"  {'GOLD (A)':<10}  {gold_w:>4}  {gold_l:>4}  {gold_d:>4}  {gold_total:>6}  {gold_wr:>5.1f}%")
    print(f"  {'SILVER (B)':<10}  {silver_w:>4}  {silver_l:>4}  {silver_d:>4}  {silver_total:>6}  {silver_wr:>5.1f}%")


def print_map_breakdown(results: list[MatchResult], bots: list[str]) -> None:
    map_stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for mr in results:
        if mr.error:
            continue
        if mr.winner:
            map_stats[mr.map_name][mr.winner] += 1
        map_stats[mr.map_name]["_total"] += 1

    if not map_stats:
        return

    print(f"\n{'=' * 80}")
    print("  MAP BREAKDOWN (wins per bot)")
    print(f"{'=' * 80}")

    map_w = max(len(m) for m in map_stats)
    bot_w = max(len(b) for b in bots)
    header = f"  {'Map':{map_w}}"
    for b in bots:
        header += f"  {b:>{bot_w}}"
    header += f"  {'Total':>6}"
    print(header)
    print(f"  {'-' * map_w}" + f"  {'-' * bot_w}" * len(bots) + f"  {'-----':>6}")

    for map_name in sorted(map_stats.keys()):
        ms = map_stats[map_name]
        row = f"  {map_name:{map_w}}"
        for b in bots:
            row += f"  {ms.get(b, 0):>{bot_w}}"
        row += f"  {ms['_total']:>6}"
        print(row)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a gauntlet: one bot vs a field of opponents (no opponent-vs-opponent)."
    )
    parser.add_argument("bot", help="Main bot to test.")
    parser.add_argument("--opponents", nargs="+", required=True, help="Opponent bots.")
    parser.add_argument("--maps-dir", type=Path, default=Path("maps"), help="Maps directory.")
    parser.add_argument("--map-filter", default="", help="Only maps containing this substring.")
    parser.add_argument("--seed", type=int, default=1, help="Starting seed.")
    parser.add_argument("--rounds", type=int, default=1, help="Number of seeds/rounds to play.")
    parser.add_argument("--threads", type=int, default=1, help="Parallel match threads.")
    parser.add_argument("--map-count", type=int, default=None, help="Randomly select N maps instead of using all.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print each match result.")
    parser.add_argument("--map-breakdown", action="store_true", help="Show per-map win breakdown.")
    args = parser.parse_args()

    bot = args.bot
    opponents = args.opponents

    # Validate bot paths
    for b in [bot] + opponents:
        p = Path(b)
        if p.is_dir() and not (p / "main.py").exists():
            print(f"Warning: {b} has no main.py", file=sys.stderr)

    maps = discover_maps(args.maps_dir)
    if args.map_filter:
        maps = [m for m in maps if args.map_filter in m.name]
        if not maps:
            print(f"No maps match filter '{args.map_filter}'.", file=sys.stderr)
            return 1
    if args.map_count and args.map_count < len(maps):
        maps = sorted(random.sample(maps, args.map_count))

    seeds = list(range(args.seed, args.seed + args.rounds))

    stats, results, h2h = run_gauntlet(
        main_bot=bot, opponents=opponents, maps=maps, seeds=seeds,
        threads=args.threads, verbose=args.verbose,
    )

    print_leaderboard(stats)
    print_head_to_head(h2h)
    print_side_breakdown(results, bot)

    if args.map_breakdown:
        print_map_breakdown(results, [bot] + opponents)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
