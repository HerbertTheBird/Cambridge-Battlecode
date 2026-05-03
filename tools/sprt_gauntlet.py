#!/usr/bin/env python3
"""
SPRT Gauntlet — run paired-seed matches between a main bot and one opponent
until a Sequential Probability Ratio Test accepts H0 ("main bot is no better")
or H1 ("main bot is at least Elo1 stronger").

Why this exists: gauntlet.py runs a fixed number of games. SPRT runs only as
many as needed to reach a confident answer. Typical savings: 50-80% of compute
when the result is clear, more games when the bots are very close.

Pairing: one trial = both sides played on the same map+seed (a "pair"). This
cancels out map+seed noise so we need ~half the data of independent sampling
for the same statistical power.

The test treats each *pair* as a 3-outcome multinomial:
  - sweep (2-0 for main): score 1.0
  - split (1-1):           score 0.5
  - lost  (0-2):           score 0.0
We compute log-likelihood ratio under the Bradley-Terry-style model
implied by the two Elo hypotheses, and stop when LLR crosses the bounds.

Usage:
    python sprt_gauntlet.py Hades --opponent v872 --maps-dir maps
    python sprt_gauntlet.py Hades --opponent v872 --elo0 0 --elo1 20
    python sprt_gauntlet.py Hades --opponent v872 --threads 8 --max-pairs 200
"""

from __future__ import annotations

import argparse
import math
import random
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from tools.stats_utils import bo9_from_pair_outcomes, bo9_win_probability
from tournament import discover_maps, run_match, MatchResult, binomial_p_value


# ── SPRT bounds ──────────────────────────────────────────────────────────────

def sprt_bounds(alpha: float, beta: float) -> tuple[float, float]:
    """Lower / upper LLR bounds. Cross lower = accept H0, cross upper = accept H1."""
    lower = math.log(beta / (1.0 - alpha))
    upper = math.log((1.0 - beta) / alpha)
    return lower, upper


def elo_to_score(elo: float) -> float:
    """Per-game expected score given an Elo difference."""
    return 1.0 / (1.0 + 10.0 ** (-elo / 400.0))


def llr_increment_pair(score: float, p0: float, p1: float) -> float:
    """
    Log-likelihood ratio increment for one pair under H1 (per-game prob p1)
    vs H0 (p0). A pair has score = (game1_score + game2_score) / 2 in {0, 0.5, 1}.

    We use the per-game probabilities and multiply: a pair has 2 independent
    games (from the bots' perspective; pairing controls map+seed noise but
    each game's stochasticity is still independent).
    """
    if score == 1.0:
        # 2-0: both games won
        return 2.0 * (math.log(p1) - math.log(p0))
    if score == 0.0:
        return 2.0 * (math.log(1 - p1) - math.log(1 - p0))
    # 1-1: one win, one loss
    return (math.log(p1) + math.log(1 - p1)) - (math.log(p0) + math.log(1 - p0))


# ── Match orchestration ──────────────────────────────────────────────────────

@dataclass
class PairOutcome:
    map_name: str
    seed: int
    a_as_main: MatchResult   # main_bot played as A
    b_as_main: MatchResult   # main_bot played as B

    @property
    def has_error(self) -> bool:
        return self.a_as_main.error or self.b_as_main.error

    @property
    def has_draw(self) -> bool:
        return (
            self.a_as_main.winner is None or self.b_as_main.winner is None
        ) and not self.has_error

    def main_score(self, main_bot: str) -> float | None:
        """Returns main_bot's score for this pair: 1.0 (sweep), 0.5 (split), 0.0 (lost), or None."""
        if self.has_error or self.has_draw:
            return None
        wins = 0
        if self.a_as_main.winner == main_bot:
            wins += 1
        if self.b_as_main.winner == main_bot:
            wins += 1
        return wins / 2.0


@dataclass
class SPRTState:
    pairs_completed: int = 0
    sweeps: int = 0
    splits: int = 0
    losses: int = 0
    drawn_pairs: int = 0
    error_pairs: int = 0
    llr: float = 0.0
    decision: str = "continuing"   # "H0", "H1", "continuing", "max"


def run_sprt(
    main_bot: str,
    opponent: str,
    maps: list[Path],
    threads: int,
    elo0: float,
    elo1: float,
    alpha: float,
    beta: float,
    max_pairs: int,
    seed_base: int,
) -> SPRTState:
    p0 = elo_to_score(elo0)
    p1 = elo_to_score(elo1)
    lower, upper = sprt_bounds(alpha, beta)

    print(f"SPRT: {main_bot} vs {opponent}")
    print(f"  H0: Elo diff = {elo0:+.0f}  (per-game p={p0:.3f})")
    print(f"  H1: Elo diff = {elo1:+.0f}  (per-game p={p1:.3f})")
    print(f"  alpha={alpha} beta={beta}  bounds=[{lower:+.3f}, {upper:+.3f}]")
    print(f"  max pairs (cap): {max_pairs}  threads: {threads}")
    print()

    state = SPRTState()
    t0 = time.perf_counter()

    # Generate (map, seed) pair plan up to the cap. Cycle through maps with
    # incrementing seeds so we always sample diverse map/seed combos first.
    # Shuffle the map order each pass (deterministically by pass index +
    # seed_base) so SPRT can't be biased by always testing the easy maps first.
    plan: list[tuple[Path, int]] = []
    seed_offset = 0
    while len(plan) < max_pairs:
        # Per-pass deterministic shuffle: same seed_base reproduces the same
        # ordering, but each pass uses a different permutation.
        shuffled = list(maps)
        random.Random(seed_base * 1_000_003 + seed_offset).shuffle(shuffled)
        for m in shuffled:
            plan.append((m, seed_base + seed_offset))
            if len(plan) >= max_pairs:
                break
        seed_offset += 1

    if threads <= 1:
        for map_path, seed in plan:
            pair = _run_one_pair(main_bot, opponent, map_path, seed)
            _ingest_pair(state, pair, main_bot, p0, p1)
            _print_progress(state, lower, upper, t0)
            decision = _check_decision(state, lower, upper, max_pairs)
            if decision != "continuing":
                state.decision = decision
                break
    else:
        # Submit pairs in batches of `threads` and stop early if a decision
        # crosses while a batch is in flight (we accept the in-flight games but
        # don't queue more).
        idx = 0
        with ThreadPoolExecutor(max_workers=threads) as ex:
            in_flight: dict[Future[PairOutcome], tuple[Path, int]] = {}

            def submit_next() -> bool:
                nonlocal idx
                if idx >= len(plan):
                    return False
                map_path, seed = plan[idx]
                idx += 1
                fut = ex.submit(_run_one_pair, main_bot, opponent, map_path, seed)
                in_flight[fut] = (map_path, seed)
                return True

            # Prime the pool
            for _ in range(min(threads, len(plan))):
                submit_next()

            while in_flight:
                done = next(as_completed(in_flight))
                in_flight.pop(done)
                pair = done.result()
                _ingest_pair(state, pair, main_bot, p0, p1)
                _print_progress(state, lower, upper, t0)
                decision = _check_decision(state, lower, upper, max_pairs)
                if decision != "continuing":
                    state.decision = decision
                    break
                submit_next()

    elapsed = time.perf_counter() - t0
    print()
    _print_summary(state, main_bot, opponent, lower, upper, elapsed)
    return state


def _run_one_pair(main_bot: str, opponent: str, map_path: Path, seed: int) -> PairOutcome:
    """Play both sides on the given map+seed concurrently in this thread."""
    a_as_main = run_match(main_bot, opponent, map_path, seed)
    b_as_main = run_match(opponent, main_bot, map_path, seed)
    return PairOutcome(map_name=map_path.stem, seed=seed,
                       a_as_main=a_as_main, b_as_main=b_as_main)


def _ingest_pair(state: SPRTState, pair: PairOutcome, main_bot: str, p0: float, p1: float) -> None:
    state.pairs_completed += 1
    if pair.has_error:
        state.error_pairs += 1
        return
    if pair.has_draw:
        state.drawn_pairs += 1
        # Treat drawn pairs as no information for SPRT (excluded).
        return
    score = pair.main_score(main_bot)
    assert score is not None
    if score == 1.0:
        state.sweeps += 1
    elif score == 0.5:
        state.splits += 1
    else:
        state.losses += 1
    state.llr += llr_increment_pair(score, p0, p1)


def _check_decision(state: SPRTState, lower: float, upper: float, max_pairs: int) -> str:
    if state.llr <= lower:
        return "H0"
    if state.llr >= upper:
        return "H1"
    if state.pairs_completed >= max_pairs:
        return "max"
    return "continuing"


def _print_progress(state: SPRTState, lower: float, upper: float, t0: float) -> None:
    elapsed = time.perf_counter() - t0
    rate = state.pairs_completed / elapsed if elapsed > 0 else 0.0
    bo9 = bo9_from_pair_outcomes(state.sweeps, state.splits, state.losses)
    bo9_str = f" Bo9={bo9*100:5.1f}%" if bo9 is not None else ""
    # Per-game wins/losses from paired outcomes for the binomial p-value.
    per_game_wins = 2 * state.sweeps + state.splits
    per_game_losses = 2 * state.losses + state.splits
    p = binomial_p_value(per_game_wins, per_game_losses)
    print(f"  pair {state.pairs_completed:>3d}: "
          f"sweeps={state.sweeps} splits={state.splits} losses={state.losses} "
          f"draw={state.drawn_pairs} err={state.error_pairs}  "
          f"LLR={state.llr:+7.3f}  bounds=[{lower:+.2f}, {upper:+.2f}]"
          f"{bo9_str}  p={p:.4f}  ({rate:.2f} pairs/s)", flush=True)


def _print_summary(state: SPRTState, main_bot: str, opponent: str,
                   lower: float, upper: float, elapsed: float) -> None:
    print("=" * 80)
    print(f"  SPRT result: {main_bot} vs {opponent}")
    print("=" * 80)
    decision_label = {
        "H0": "H0 ACCEPTED - main bot is NOT better than the H0 threshold",
        "H1": "H1 ACCEPTED - main bot IS at least Elo1 stronger",
        "max": "INCONCLUSIVE - hit max_pairs cap before either bound crossed",
        "continuing": "INTERRUPTED",
    }[state.decision]
    print(f"  Decision:        {decision_label}")
    print(f"  Pairs played:    {state.pairs_completed}  ({elapsed:.1f}s)")
    print(f"  Sweeps / Splits / Losses: {state.sweeps} / {state.splits} / {state.losses}")
    print(f"  Drawn pairs:     {state.drawn_pairs}  (excluded from LLR)")
    print(f"  Error pairs:     {state.error_pairs}")
    print(f"  Final LLR:       {state.llr:+.3f}  (bounds: {lower:+.3f}, {upper:+.3f})")

    decisive = state.sweeps + state.splits + state.losses
    if decisive:
        per_game_wr = (state.sweeps + 0.5 * state.splits) / decisive
        bo9 = bo9_from_pair_outcomes(state.sweeps, state.splits, state.losses)
        per_game_wins = 2 * state.sweeps + state.splits
        per_game_losses = 2 * state.losses + state.splits
        p = binomial_p_value(per_game_wins, per_game_losses)
        print(f"  Paired per-game WR: {per_game_wr*100:.1f}%  ({per_game_wins}W-{per_game_losses}L)")
        if bo9 is not None:
            print(f"  Estimated Bo9 win prob (first to 5): {bo9*100:.1f}%")
        print(f"  Binomial p(main is better than 50%): {p:.4f}")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sequential Probability Ratio Test gauntlet — run only as many "
                    "paired-seed matches as needed to confidently rank two bots."
    )
    parser.add_argument("bot", help="Main bot to test (path or name in bots/).")
    parser.add_argument("--opponent", required=True, help="Single opponent bot (path or bots/<name>).")
    parser.add_argument("--maps-dir", type=Path, default=Path("maps"), help="Maps directory.")
    parser.add_argument("--map-filter", default="", help="Only maps containing this substring.")
    parser.add_argument("--map-count", type=int, default=None,
                        help="Randomly select N maps from the discovered set.")
    parser.add_argument("--seed", type=int, default=1, help="Starting seed for the first pair.")
    parser.add_argument("--threads", type=int, default=1, help="Parallel pair workers (each pair runs 2 games sequentially).")
    parser.add_argument("--elo0", type=float, default=0.0,
                        help="H0 Elo difference (default 0 = main is no better).")
    parser.add_argument("--elo1", type=float, default=20.0,
                        help="H1 Elo difference (default 20 = main is meaningfully better).")
    parser.add_argument("--alpha", type=float, default=0.05,
                        help="False-positive rate (rejecting H0 when true).")
    parser.add_argument("--beta", type=float, default=0.05,
                        help="False-negative rate (rejecting H1 when true).")
    parser.add_argument("--max-pairs", type=int, default=200,
                        help="Hard cap on pairs played (safety net).")
    args = parser.parse_args()

    main_bot = args.bot
    if not Path(main_bot).is_dir():
        candidate = Path("bots") / main_bot
        if candidate.is_dir():
            main_bot = str(candidate)
    opponent = args.opponent
    if not Path(opponent).is_dir():
        candidate = Path("bots") / opponent
        if candidate.is_dir():
            opponent = str(candidate)

    maps = discover_maps(args.maps_dir)
    if args.map_filter:
        maps = [m for m in maps if args.map_filter in m.name]
        if not maps:
            print(f"No maps match filter '{args.map_filter}'.", file=sys.stderr)
            return 1
    if args.map_count and args.map_count < len(maps):
        maps = sorted(random.sample(maps, args.map_count))

    state = run_sprt(
        main_bot=main_bot,
        opponent=opponent,
        maps=maps,
        threads=args.threads,
        elo0=args.elo0,
        elo1=args.elo1,
        alpha=args.alpha,
        beta=args.beta,
        max_pairs=args.max_pairs,
        seed_base=args.seed,
    )

    return 0 if state.decision in ("H0", "H1") else 1


if __name__ == "__main__":
    raise SystemExit(main())
