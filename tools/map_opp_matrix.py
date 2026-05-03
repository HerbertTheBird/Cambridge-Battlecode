#!/usr/bin/env python3
"""
Map × Opponent Matrix — runs the main bot vs each opponent on each map (both
sides) and outputs a matrix of paired-game win rates with confidence intervals,
sample counts, and side asymmetry. Used to identify weak combos to practice.

Differs from gauntlet's per-map breakdown: this is a 2D table of map (rows)
× opponent (columns), not a flat per-map total. Also reports side-A vs side-B
WR per cell so you can see "we lose this map only when starting B".

Reuses tournament.run_match for the actual play. Reuses paired-seed evaluation
to reduce variance.

Usage:
    python map_opp_matrix.py Hades --opponents v872 Lethe Khaos
    python map_opp_matrix.py Hades --opponents v872 --maps-dir maps_intl --threads 8
    python map_opp_matrix.py Hades --opponents v872 --rounds 3 --json results.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from pathlib import Path

from tournament import run_match, discover_maps, MatchResult, binomial_p_value
from tools.stats_utils import bo9_win_probability, bo9_from_pair_outcomes


# ── Cell stats ───────────────────────────────────────────────────────────────

@dataclass
class CellStats:
    map_name: str
    opp: str
    a_wins: int = 0   # main as A wins
    a_losses: int = 0
    a_draws: int = 0
    b_wins: int = 0   # main as B wins
    b_losses: int = 0
    b_draws: int = 0
    errors: int = 0
    sweeps: int = 0
    splits: int = 0
    pair_losses: int = 0
    pairs_with_draw: int = 0

    @property
    def total_games(self) -> int:
        return (self.a_wins + self.a_losses + self.a_draws
                + self.b_wins + self.b_losses + self.b_draws)

    @property
    def total_wins(self) -> int:
        return self.a_wins + self.b_wins

    @property
    def total_losses(self) -> int:
        return self.a_losses + self.b_losses

    @property
    def per_game_wr(self) -> float:
        decisive = self.total_wins + self.total_losses
        return self.total_wins / decisive if decisive else 0.0

    @property
    def side_a_wr(self) -> float:
        d = self.a_wins + self.a_losses
        return self.a_wins / d if d else 0.0

    @property
    def side_b_wr(self) -> float:
        d = self.b_wins + self.b_losses
        return self.b_wins / d if d else 0.0

    @property
    def bo9_prob(self) -> float | None:
        decisive = self.sweeps + self.splits + self.pair_losses
        if decisive == 0:
            return None
        return bo9_from_pair_outcomes(self.sweeps, self.splits, self.pair_losses)

    def wilson_95(self) -> tuple[float, float]:
        """Wilson 95% CI on per-game WR. (lo, hi)."""
        n = self.total_wins + self.total_losses
        if n == 0:
            return (0.0, 1.0)
        p = self.total_wins / n
        z = 1.96
        denom = 1 + z * z / n
        center = (p + z * z / (2 * n)) / denom
        rad = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
        return (max(0.0, center - rad), min(1.0, center + rad))


# ── Match driver ─────────────────────────────────────────────────────────────

def _run_pair(main_bot: str, opp: str, map_path: Path, seed: int) -> tuple[MatchResult, MatchResult]:
    a = run_match(main_bot, opp, map_path, seed)
    b = run_match(opp, main_bot, map_path, seed)
    return a, b


def _ingest(cell: CellStats, main_bot: str, mr_a: MatchResult, mr_b: MatchResult) -> None:
    """Update cell stats from one paired match (main as A, then main as B)."""
    if mr_a.error or mr_b.error:
        cell.errors += 1
        return

    a_main_won = mr_a.winner == main_bot
    a_main_drew = mr_a.winner is None
    b_main_won = mr_b.winner == main_bot
    b_main_drew = mr_b.winner is None

    if a_main_won:
        cell.a_wins += 1
    elif a_main_drew:
        cell.a_draws += 1
    else:
        cell.a_losses += 1

    if b_main_won:
        cell.b_wins += 1
    elif b_main_drew:
        cell.b_draws += 1
    else:
        cell.b_losses += 1

    if a_main_drew or b_main_drew:
        cell.pairs_with_draw += 1
    else:
        wins = int(a_main_won) + int(b_main_won)
        if wins == 2:
            cell.sweeps += 1
        elif wins == 1:
            cell.splits += 1
        else:
            cell.pair_losses += 1


def run_matrix(main_bot: str, opponents: list[str], maps: list[Path],
               seeds: list[int], threads: int) -> dict[tuple[str, str], CellStats]:
    cells: dict[tuple[str, str], CellStats] = {
        (m.stem, opp): CellStats(map_name=m.stem, opp=opp)
        for m in maps for opp in opponents
    }

    jobs: list[tuple[str, str, Path, int]] = []
    for opp in opponents:
        for m in maps:
            for s in seeds:
                jobs.append((main_bot, opp, m, s))

    total = len(jobs)
    print(f"Matrix: {len(maps)} maps x {len(opponents)} opps x {len(seeds)} seeds = {total} pairs ({total*2} games)")
    print(f"Threads: {threads}")

    completed = 0
    t0 = time.perf_counter()

    if threads <= 1:
        for main_bot_, opp, m, s in jobs:
            mr_a, mr_b = _run_pair(main_bot_, opp, m, s)
            _ingest(cells[(m.stem, opp)], main_bot_, mr_a, mr_b)
            completed += 1
            elapsed = time.perf_counter() - t0
            print(f"  [{completed}/{total}] {m.stem} vs {opp} (seed {s})  "
                  f"main W-L-D as A: {mr_a.winner}, as B: {mr_b.winner}  ({elapsed:.0f}s)",
                  flush=True)
    else:
        with ThreadPoolExecutor(max_workers=threads) as ex:
            futs: dict[Future, tuple[str, str, Path, int]] = {}
            for main_bot_, opp, m, s in jobs:
                fut = ex.submit(_run_pair, main_bot_, opp, m, s)
                futs[fut] = (main_bot_, opp, m, s)
            for fut in as_completed(futs):
                main_bot_, opp, m, s = futs[fut]
                mr_a, mr_b = fut.result()
                _ingest(cells[(m.stem, opp)], main_bot_, mr_a, mr_b)
                completed += 1
                elapsed = time.perf_counter() - t0
                rate = completed / elapsed if elapsed else 0
                eta = (total - completed) / rate if rate else 0
                print(f"  [{completed}/{total}] {m.stem} vs {opp} (seed {s}) "
                      f"({elapsed:.0f}s, ETA {eta:.0f}s)", flush=True)

    return cells


# ── Output ───────────────────────────────────────────────────────────────────

def _wr_color(wr: float) -> str:
    """Color-code a win rate for ANSI terminals. Tournament-readable thresholds."""
    if wr >= 0.7:
        return "\033[92m"   # bright green
    if wr >= 0.55:
        return "\033[32m"   # green
    if wr >= 0.45:
        return "\033[33m"   # yellow
    if wr >= 0.30:
        return "\033[31m"   # red
    return "\033[91m"       # bright red


RESET = "\033[0m"


def print_matrix(cells: dict[tuple[str, str], CellStats], maps: list[str],
                 opponents: list[str], use_color: bool) -> None:
    print(f"\n{'=' * 100}")
    print("  Map × Opponent Matrix  (per-game WR, paired-seed; format: WR% [CI95] / Bo9%)")
    print(f"{'=' * 100}")

    map_w = max(len(m) for m in maps)
    opp_w = max(max(len(o) for o in opponents), 16)

    # Header
    header = f"  {'Map':{map_w}}  "
    for o in opponents:
        header += f"  {o:>{opp_w}}"
    header += f"  {'AvgWR':>7}  {'AvgBo9':>7}"
    print(header)
    print(f"  {'-' * map_w}  " + "  ".join("-" * opp_w for _ in opponents) +
          f"  {'-----':>7}  {'------':>7}")

    for mname in maps:
        row = f"  {mname:{map_w}}  "
        wrs: list[float] = []
        bo9s: list[float] = []
        for o in opponents:
            cell = cells[(mname, o)]
            decisive = cell.total_wins + cell.total_losses
            if decisive == 0:
                cell_str = f"{'-':>{opp_w}}"
            else:
                wr = cell.per_game_wr
                bo9 = cell.bo9_prob
                lo, hi = cell.wilson_95()
                wrs.append(wr)
                if bo9 is not None:
                    bo9s.append(bo9)
                bo9_str = f"{bo9*100:>3.0f}%" if bo9 is not None else "  - "
                base = f"{wr*100:>3.0f}% [{lo*100:>2.0f}-{hi*100:>2.0f}]/{bo9_str}"
                if use_color:
                    base = f"{_wr_color(wr)}{base}{RESET}"
                visible_len = len(f"{wr*100:>3.0f}% [{lo*100:>2.0f}-{hi*100:>2.0f}]/{bo9_str}")
                pad = max(0, opp_w - visible_len)
                cell_str = " " * pad + base
            row += f"  {cell_str}"
        avg_wr = sum(wrs) / len(wrs) if wrs else 0.0
        avg_bo9 = sum(bo9s) / len(bo9s) if bo9s else 0.0
        wr_str = f"{avg_wr*100:>5.1f}%" if wrs else "  -  "
        bo9_str = f"{avg_bo9*100:>5.1f}%" if bo9s else "  -  "
        row += f"  {wr_str:>7}  {bo9_str:>7}"
        print(row)

    print()
    print("  --- Weakest combos (sorted by per-game WR ascending) ---")
    decisive_cells = [c for c in cells.values() if c.total_wins + c.total_losses >= 2]
    decisive_cells.sort(key=lambda c: c.per_game_wr)
    print(f"  {'Map':{map_w}}  {'Opponent':{opp_w}}  {'WR':>5}  {'Bo9':>5}  {'A-WR':>5}  {'B-WR':>5}  {'Pairs':>5}  {'p':>8}")
    print(f"  {'-' * map_w}  {'-' * opp_w}  {'--':>5}  {'---':>5}  {'----':>5}  {'----':>5}  {'-----':>5}  {'------':>8}")
    for c in decisive_cells[:15]:
        bo9 = c.bo9_prob
        bo9_str = f"{bo9*100:>3.0f}%" if bo9 is not None else "-"
        decisive = c.sweeps + c.splits + c.pair_losses
        p_val = binomial_p_value(c.total_wins, c.total_losses)
        print(f"  {c.map_name:{map_w}}  {c.opp:{opp_w}}  "
              f"{c.per_game_wr*100:>4.0f}%  {bo9_str:>5}  "
              f"{c.side_a_wr*100:>4.0f}%  {c.side_b_wr*100:>4.0f}%  {decisive:>5}  {p_val:>8.4f}")

    # Side asymmetry summary: which maps swing hardest by side?
    print()
    print("  --- Maps with biggest side asymmetry (|A-WR - B-WR|) ---")
    asymm = []
    for c in cells.values():
        if c.a_wins + c.a_losses < 2 or c.b_wins + c.b_losses < 2:
            continue
        asymm.append((abs(c.side_a_wr - c.side_b_wr), c))
    asymm.sort(key=lambda x: -x[0])
    print(f"  {'Map':{map_w}}  {'Opponent':{opp_w}}  {'A-WR':>5}  {'B-WR':>5}  {'Δ':>5}")
    print(f"  {'-' * map_w}  {'-' * opp_w}  {'----':>5}  {'----':>5}  {'-':>5}")
    for delta, c in asymm[:10]:
        print(f"  {c.map_name:{map_w}}  {c.opp:{opp_w}}  "
              f"{c.side_a_wr*100:>4.0f}%  {c.side_b_wr*100:>4.0f}%  {delta*100:>4.0f}%")


def cells_to_jsonable(cells: dict[tuple[str, str], CellStats]) -> list[dict]:
    return [asdict(c) for c in cells.values()]


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Build a map × opponent paired-game win-rate matrix.")
    parser.add_argument("bot", help="Main bot path or name in bots/.")
    parser.add_argument("--opponents", nargs="+", required=True, help="Opponent bots.")
    parser.add_argument("--maps-dir", type=Path, default=Path("maps"))
    parser.add_argument("--map-filter", default="")
    parser.add_argument("--map-count", type=int, default=None)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--rounds", type=int, default=1, help="Seeds per cell (default 1).")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--json", type=Path, default=None, help="Write per-cell stats to JSON.")
    args = parser.parse_args()

    main_bot = args.bot
    if not Path(main_bot).is_dir():
        candidate = Path("bots") / main_bot
        if candidate.is_dir():
            main_bot = str(candidate)

    opponents = []
    for o in args.opponents:
        if Path(o).is_dir():
            opponents.append(o)
        elif (Path("bots") / o).is_dir():
            opponents.append(str(Path("bots") / o))
        else:
            opponents.append(o)

    maps = discover_maps(args.maps_dir)
    if args.map_filter:
        maps = [m for m in maps if args.map_filter in m.name]
        if not maps:
            print(f"No maps match filter '{args.map_filter}'.", file=sys.stderr)
            return 1
    if args.map_count and args.map_count < len(maps):
        maps = sorted(random.sample(maps, args.map_count))

    seeds = list(range(args.seed, args.seed + args.rounds))

    cells = run_matrix(main_bot, opponents, maps, seeds, args.threads)

    map_names = [m.stem for m in maps]
    use_color = not args.no_color and sys.stdout.isatty()
    print_matrix(cells, map_names, opponents, use_color=use_color)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(cells_to_jsonable(cells), indent=2))
        print(f"\n  Per-cell stats written to {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
