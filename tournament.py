#!/usr/bin/env python3
"""
Tournament Runner — round-robin between bot versions with ELO ratings.

Run every bot version against every other on all maps, compute ELO
ratings, and produce a ranked leaderboard. Essential for measuring
incremental progress across bot iterations.

Usage:
    python tournament.py Artemis_v0 Artemis_v0_1 Artemis_v0_2
    python tournament.py Artemis_v0 Artemis_v0_2 --maps-dir maps --threads 4
    python tournament.py bots/Artemis_v0 bots/baseline --rounds 3 --map-filter arena  # repeat 3 seeds
"""

from __future__ import annotations

import argparse
import math
import shlex
import subprocess
import sys
import time
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path


# ── ELO system ───────────────────────────────────────────────────────────────

DEFAULT_ELO = 1500.0
K_FACTOR = 32.0


def elo_expected(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def elo_update(rating: float, expected: float, actual: float) -> float:
    return rating + K_FACTOR * (actual - expected)


# ── Match running ────────────────────────────────────────────────────────────

WINNER_RE = __import__("re").compile(r"Winner:\s+([^\s]+)")
TURN_RE = __import__("re").compile(r"turn\s+(\d+)\)")


@dataclass
class MatchResult:
    bot_a: str
    bot_b: str
    map_name: str
    seed: int
    winner: str | None   # bot name or None
    turn: int | None
    elapsed_s: float
    error: bool = False


def run_match(bot_a: str, bot_b: str, map_path: Path, seed: int) -> MatchResult:
    command = ["cambc", "run", bot_a, bot_b, str(map_path), "--seed", str(seed)]
    t0 = time.perf_counter()
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=120,
        )
        output = result.stdout or ""
        elapsed = time.perf_counter() - t0

        winner_match = WINNER_RE.search(output)
        turn_match = TURN_RE.search(output)

        winner = winner_match.group(1) if winner_match else None
        turn = int(turn_match.group(1)) if turn_match else None

        return MatchResult(
            bot_a=bot_a, bot_b=bot_b, map_name=map_path.stem,
            seed=seed, winner=winner, turn=turn,
            elapsed_s=elapsed, error=result.returncode != 0,
        )
    except subprocess.TimeoutExpired:
        return MatchResult(
            bot_a=bot_a, bot_b=bot_b, map_name=map_path.stem,
            seed=seed, winner=None, turn=None,
            elapsed_s=time.perf_counter() - t0, error=True,
        )
    except Exception as e:
        return MatchResult(
            bot_a=bot_a, bot_b=bot_b, map_name=map_path.stem,
            seed=seed, winner=None, turn=None,
            elapsed_s=time.perf_counter() - t0, error=True,
        )


# ── Tournament ───────────────────────────────────────────────────────────────

@dataclass
class BotStats:
    name: str
    elo: float = DEFAULT_ELO
    wins: int = 0
    losses: int = 0
    draws: int = 0
    errors: int = 0
    total_turns: int = 0
    match_count: int = 0

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses + self.draws
        return self.wins / total * 100 if total else 0.0

    @property
    def avg_turns(self) -> float:
        return self.total_turns / self.match_count if self.match_count else 0.0


@dataclass
class HeadToHead:
    bot_a: str
    bot_b: str
    a_wins: int = 0
    b_wins: int = 0
    draws: int = 0

    @property
    def total(self) -> int:
        return self.a_wins + self.b_wins + self.draws


def run_tournament(
    bots: list[str],
    maps: list[Path],
    seeds: list[int],
    threads: int = 1,
    verbose: bool = False,
) -> tuple[dict[str, BotStats], list[MatchResult], dict[tuple[str, str], HeadToHead]]:

    stats: dict[str, BotStats] = {b: BotStats(name=b) for b in bots}
    results: list[MatchResult] = []
    h2h: dict[tuple[str, str], HeadToHead] = {}

    # Generate all match jobs
    jobs: list[tuple[str, str, Path, int]] = []
    for bot_a, bot_b in combinations(bots, 2):
        h2h[(bot_a, bot_b)] = HeadToHead(bot_a=bot_a, bot_b=bot_b)
        for map_path in maps:
            for seed in seeds:
                jobs.append((bot_a, bot_b, map_path, seed))

    total_matches = len(jobs)
    print(f"Tournament: {len(bots)} bots, {len(maps)} maps, {len(seeds)} seed(s)")
    print(f"Total matches: {total_matches}")
    print(f"Matchups: {len(list(combinations(bots, 2)))}")
    print(f"Threads: {threads}")
    print()

    completed = 0
    match_started = time.perf_counter()

    def process_result(mr: MatchResult) -> None:
        nonlocal completed
        completed += 1

        results.append(mr)
        a_name, b_name = mr.bot_a, mr.bot_b

        if mr.error:
            stats[a_name].errors += 1
            stats[b_name].errors += 1
            return

        stats[a_name].match_count += 1
        stats[b_name].match_count += 1
        if mr.turn:
            stats[a_name].total_turns += mr.turn
            stats[b_name].total_turns += mr.turn

        key = (a_name, b_name) if (a_name, b_name) in h2h else (b_name, a_name)
        h2h_entry = h2h[key]

        # Determine result
        if mr.winner == a_name:
            actual_a, actual_b = 1.0, 0.0
            stats[a_name].wins += 1
            stats[b_name].losses += 1
            if key == (a_name, b_name):
                h2h_entry.a_wins += 1
            else:
                h2h_entry.b_wins += 1
        elif mr.winner == b_name:
            actual_a, actual_b = 0.0, 1.0
            stats[a_name].losses += 1
            stats[b_name].wins += 1
            if key == (a_name, b_name):
                h2h_entry.b_wins += 1
            else:
                h2h_entry.a_wins += 1
        else:
            actual_a = actual_b = 0.5
            stats[a_name].draws += 1
            stats[b_name].draws += 1
            h2h_entry.draws += 1

        # ELO update
        exp_a = elo_expected(stats[a_name].elo, stats[b_name].elo)
        exp_b = 1.0 - exp_a
        stats[a_name].elo = elo_update(stats[a_name].elo, exp_a, actual_a)
        stats[b_name].elo = elo_update(stats[b_name].elo, exp_b, actual_b)

        if verbose:
            emoji = {a_name: "W", b_name: "L"}.get(mr.winner or "", "D")
            print(f"  [{completed}/{total_matches}] {mr.map_name}: {a_name} vs {b_name} -> "
                  f"{mr.winner or 'draw'} (T{mr.turn or '?'}) [{mr.elapsed_s:.1f}s]")

    if threads == 1:
        for bot_a, bot_b, map_path, seed in jobs:
            mr = run_match(bot_a, bot_b, map_path, seed)
            process_result(mr)
            if not verbose:
                pct = completed / total_matches * 100
                print(f"\r  Progress: {completed}/{total_matches} ({pct:.0f}%)", end="", flush=True)
        if not verbose:
            print()
    else:
        futures: dict[Future[MatchResult], None] = {}
        with ThreadPoolExecutor(max_workers=threads) as executor:
            for bot_a, bot_b, map_path, seed in jobs:
                f = executor.submit(run_match, bot_a, bot_b, map_path, seed)
                futures[f] = None

            for f in as_completed(futures):
                mr = f.result()
                process_result(mr)
                if not verbose:
                    pct = completed / total_matches * 100
                    print(f"\r  Progress: {completed}/{total_matches} ({pct:.0f}%)", end="", flush=True)
        if not verbose:
            print()

    elapsed = time.perf_counter() - match_started
    print(f"Completed in {elapsed:.1f}s")

    return stats, results, h2h


# ── Output ───────────────────────────────────────────────────────────────────

def print_leaderboard(stats: dict[str, BotStats]) -> None:
    ranked = sorted(stats.values(), key=lambda s: -s.elo)

    print(f"\n{'=' * 85}")
    print("  TOURNAMENT LEADERBOARD")
    print(f"{'=' * 85}")

    name_w = max(len(s.name) for s in ranked) if ranked else 10
    print(f"  {'#':>3}  {'Bot':{name_w}}  {'ELO':>7}  {'W':>4}  {'L':>4}  {'D':>4}  {'WR':>6}  {'AvgT':>6}  {'Err':>4}")
    print(f"  {'---':>3}  {'-' * name_w}  {'---':>7}  {'--':>4}  {'--':>4}  {'--':>4}  {'--':>6}  {'----':>6}  {'---':>4}")

    for i, s in enumerate(ranked, 1):
        print(f"  {i:>3}  {s.name:{name_w}}  {s.elo:>7.1f}  {s.wins:>4}  {s.losses:>4}  "
              f"{s.draws:>4}  {s.win_rate:>5.1f}%  {s.avg_turns:>6.0f}  {s.errors:>4}")


def print_head_to_head(h2h: dict[tuple[str, str], HeadToHead], stats: dict[str, BotStats]) -> None:
    entries = sorted(h2h.values(), key=lambda e: e.total, reverse=True)
    if not entries:
        return

    print(f"\n{'=' * 80}")
    print("  HEAD-TO-HEAD")
    print(f"{'=' * 80}")

    name_w = max(max(len(e.bot_a), len(e.bot_b)) for e in entries) if entries else 10
    print(f"  {'Bot A':{name_w}}  vs  {'Bot B':{name_w}}  {'A':>4}  {'B':>4}  {'D':>4}")
    print(f"  {'-' * name_w}  --  {'-' * name_w}  {'--':>4}  {'--':>4}  {'--':>4}")

    for e in entries:
        print(f"  {e.bot_a:{name_w}}  vs  {e.bot_b:{name_w}}  {e.a_wins:>4}  {e.b_wins:>4}  {e.draws:>4}")


def print_map_breakdown(results: list[MatchResult], bots: list[str]) -> None:
    """Show per-map win rates for each bot."""
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

def discover_maps(maps_dir: Path) -> list[Path]:
    maps = sorted(p for p in maps_dir.glob("*.map26") if p.is_file())
    if not maps:
        print(f"No .map26 files found in {maps_dir}", file=sys.stderr)
        sys.exit(1)
    return maps


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a round-robin tournament between bot versions with ELO ratings."
    )
    parser.add_argument("bots", nargs="+", help="Bot paths (at least 2).")
    parser.add_argument("--maps-dir", type=Path, default=Path("maps"), help="Maps directory.")
    parser.add_argument("--map-filter", default="", help="Only maps containing this substring.")
    parser.add_argument("--seed", type=int, default=1, help="Starting seed.")
    parser.add_argument("--rounds", type=int, default=1, help="Number of seeds/rounds to play.")
    parser.add_argument("--threads", type=int, default=1, help="Parallel match threads.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print each match result.")
    parser.add_argument("--map-breakdown", action="store_true", help="Show per-map win breakdown.")
    args = parser.parse_args()

    bots = args.bots
    if len(bots) < 2:
        print("Need at least 2 bots for a tournament.", file=sys.stderr)
        return 1

    # Validate bot paths
    for bot in bots:
        bot_path = Path(bot)
        if bot_path.is_dir():
            if not (bot_path / "main.py").exists():
                print(f"Warning: {bot} has no main.py", file=sys.stderr)

    maps = discover_maps(args.maps_dir)
    if args.map_filter:
        maps = [m for m in maps if args.map_filter in m.name]
        if not maps:
            print(f"No maps match filter '{args.map_filter}'.", file=sys.stderr)
            return 1

    seeds = list(range(args.seed, args.seed + args.rounds))

    stats, results, h2h = run_tournament(
        bots=bots, maps=maps, seeds=seeds,
        threads=args.threads, verbose=args.verbose,
    )

    print_leaderboard(stats)
    print_head_to_head(h2h, stats)

    if args.map_breakdown:
        print_map_breakdown(results, bots)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
