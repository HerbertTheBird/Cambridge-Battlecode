#!/usr/bin/env python3
"""
Online Tuner — uses Optuna to optimize bot parameters via online unrated matches.

Like ``tuner.py`` but instead of running local matches, it submits the patched bot
and plays unrated matches against specified opponent teams on the platform.

New features (v2):
  - SPRT pruning: each Optuna trial uses an Asynchronous SPRT to stop early when
    the trial is clearly worse than the best-so-far, instead of always playing
    --rounds * 5 games.
  - Version cycling: if you supply --prev-match OPP_ID:MATCH_ID multiple times
    for the same OPP_ID, the tuner cycles through those prior versions across
    rounds to bypass the 5-min-per-opponent cooldown.
  - Map selection: --maps M1,M2,... pins specific maps (up to 5) per round.
    Repeat --maps to vary by round.

Blue Dragon Team ID: 023ce802-d72e-44f5-b99e-71a6f97db4b7

Usage:
    python online_tuner.py Hades --opponents TEAM_ID --trials 10
    python online_tuner.py Hades --opponents TEAM_ID --trials 20 --rounds 3 \
        --prev-match TEAM_ID:OLD_MATCH_ID_1 --prev-match TEAM_ID:OLD_MATCH_ID_2 \
        --maps arena,donut,orbit
    python online_tuner.py Hades --opponents TEAM_ID --sprt --sprt-elo1 30
"""

from __future__ import annotations

import argparse
import math
import sys
import tempfile
import textwrap
from pathlib import Path

import optuna

from tools.online_challenge import (
    run_online_challenge, compute_overall_win_rate, print_results, MatchOutcome,
    submit_bot, challenge_opponent, poll_match, get_our_team_id,
)
from tools.tuner import parse_opt_annotations, suggest_value, create_patched_bot
from tools.sprt_gauntlet import sprt_bounds, llr_increment_pair, elo_to_score


# ── SPRT-aware online trial runner ──────────────────────────────────────────

def _run_trial_with_sprt(
    bot_dir: Path,
    opponent_ids: list[str],
    prev_match_ids_by_opp: dict[str, list[str]],
    maps_per_round: list[list[str]] | None,
    max_rounds: int,
    elo0: float,
    elo1: float,
    alpha: float,
    beta: float,
    cooldown_override: int | None,
    skip_submit: bool,
) -> tuple[float, int, int]:
    """
    Submit the bot once, then play rounds against opponents until SPRT decides.

    Returns (per_game_wr_estimate, total_wins, total_losses).
    Treats each completed match-game as one Bernoulli trial.
    """
    if not skip_submit:
        if not submit_bot(bot_dir):
            print("Submission failed.", file=sys.stderr)
            return 0.0, 0, 0
        import time as _time
        _time.sleep(10)
    our_team_id = get_our_team_id()

    p0 = elo_to_score(elo0)
    p1 = elo_to_score(elo1)
    lower, upper = sprt_bounds(alpha, beta)
    print(f"  SPRT bounds: [{lower:+.3f}, {upper:+.3f}]  H0 p={p0:.3f}, H1 p={p1:.3f}")

    total_wins = 0
    total_losses = 0
    llr = 0.0

    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time as _time

    for round_num in range(1, max_rounds + 1):
        prev_for_round: dict[str, str | None] = {}
        for opp in opponent_ids:
            versions = prev_match_ids_by_opp.get(opp, [])
            prev_for_round[opp] = versions[(round_num - 1) % len(versions)] if versions else None

        maps_for_round = maps_per_round[(round_num - 1) % len(maps_per_round)] if maps_per_round else None

        # Challenge in parallel
        match_ids: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=len(opponent_ids)) as ex:
            futs = {
                ex.submit(challenge_opponent, opp, prev_for_round.get(opp), maps_for_round): opp
                for opp in opponent_ids
            }
            for f in as_completed(futs):
                opp = futs[f]
                mid = f.result()
                if mid:
                    match_ids[opp] = mid

        if not match_ids:
            print(f"  Round {round_num}: no matches queued; aborting trial")
            break

        # Poll in parallel
        with ThreadPoolExecutor(max_workers=len(match_ids)) as ex:
            futs = {ex.submit(poll_match, mid, our_team_id): opp for opp, mid in match_ids.items()}
            for f in as_completed(futs):
                opp = futs[f]
                outcome = f.result()
                outcome.opponent_id = opp
                if outcome.error:
                    print(f"  Round {round_num} {opp}: ERROR")
                    continue
                w = outcome.our_score
                l = outcome.their_score
                total_wins += w
                total_losses += l
                # Per-game LLR increments (each game in match = independent Bernoulli)
                for _ in range(w):
                    llr += math.log(p1) - math.log(p0)
                for _ in range(l):
                    llr += math.log(1 - p1) - math.log(1 - p0)
                print(f"  Round {round_num} {opp}: {w}-{l}  cumulative={total_wins}W-{total_losses}L  LLR={llr:+.3f}")

        # SPRT decision
        if llr <= lower:
            print(f"  SPRT: H0 accepted at round {round_num}; trial is worse — pruning")
            break
        if llr >= upper:
            print(f"  SPRT: H1 accepted at round {round_num}; trial is good — early-confirm")
            break

        if round_num < max_rounds:
            all_via_prev = all(prev_for_round.get(opp) is not None for opp in opponent_ids)
            wait = cooldown_override if cooldown_override is not None else (5 if all_via_prev else 310)
            if wait > 0:
                print(f"  Waiting {wait}s before next round...")
                _time.sleep(wait)

    n = total_wins + total_losses
    wr = total_wins / n if n else 0.0
    return wr, total_wins, total_losses


# ── Optuna objective ────────────────────────────────────────────────────────

def make_online_objective(
    bot_dir: Path,
    annotations: list[dict],
    opponent_ids: list[str],
    rounds: int,
    tmp_root: Path,
    use_sprt: bool,
    sprt_elo0: float,
    sprt_elo1: float,
    sprt_alpha: float,
    sprt_beta: float,
    prev_match_ids_by_opp: dict[str, list[str]],
    maps_per_round: list[list[str]] | None,
    cooldown_override: int | None,
):
    def objective(trial: optuna.Trial) -> float:
        values: dict[str, int | float | str] = {}
        for ann in annotations:
            values[ann["name"]] = suggest_value(trial, ann)

        patched_dir = create_patched_bot(bot_dir, annotations, values, tmp_root)
        print(f"\n  Trial {trial.number}: {values}")

        if use_sprt:
            win_rate, w, l = _run_trial_with_sprt(
                bot_dir=patched_dir,
                opponent_ids=opponent_ids,
                prev_match_ids_by_opp=prev_match_ids_by_opp,
                maps_per_round=maps_per_round,
                max_rounds=rounds,
                elo0=sprt_elo0, elo1=sprt_elo1,
                alpha=sprt_alpha, beta=sprt_beta,
                cooldown_override=cooldown_override,
                skip_submit=False,
            )
            print(f"  Trial {trial.number}: {values} -> win_rate={win_rate:.3f} ({w}W/{l}L) [SPRT]")
            return win_rate
        else:
            stats = run_online_challenge(
                bot_dir=patched_dir,
                opponent_ids=opponent_ids,
                rounds=rounds,
                skip_submit=False,
                prev_match_ids_by_opp=prev_match_ids_by_opp or None,
                maps_per_round=maps_per_round,
                cooldown_override=cooldown_override,
            )
            win_rate = compute_overall_win_rate(stats)
            tw = sum(s.total_wins for s in stats.values())
            tl = sum(s.total_losses for s in stats.values())
            print(f"  Trial {trial.number}: {values} -> win_rate={win_rate:.3f} ({tw}W/{tl}L)")
            return win_rate

    return objective


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Tune bot hyperparameters via online unrated matches using Optuna.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Example annotations in bot source code:
                ATTACK_VALUE = 10000  # opt: int(9997, 10003)
                RETREAT_PCT  = 0.3   # opt: float(0.1, 0.6, step=0.05)
                STRATEGY     = "rush" # opt: categorical(["rush", "defend"])
        """),
    )
    parser.add_argument("bot", help="Bot to tune (path or name in bots/).")
    parser.add_argument("--opponents", nargs="+", required=True,
                        help="Opponent team IDs to challenge.")
    parser.add_argument("--trials", type=int, default=10,
                        help="Number of Optuna trials (default: 10).")
    parser.add_argument("--rounds", type=int, default=1,
                        help="Max challenge rounds per trial (SPRT may stop earlier).")
    parser.add_argument("--study-name", default=None)

    # Version cycling and map selection
    parser.add_argument("--prev-match", action="append", default=[],
                        metavar="OPP_ID:MATCH_ID",
                        help="Use this prior-match opponent version (bypasses 5-min cooldown). "
                             "Repeat to cycle through multiple versions per opp.")
    parser.add_argument("--maps", action="append", default=[],
                        metavar="m1,m2,...",
                        help="Comma-separated map names per round (max 5). Repeat for round-specific.")
    parser.add_argument("--cooldown", type=int, default=None,
                        help="Override seconds-between-rounds wait.")

    # SPRT
    parser.add_argument("--sprt", action="store_true",
                        help="Enable SPRT pruning per trial.")
    parser.add_argument("--sprt-elo0", type=float, default=0.0)
    parser.add_argument("--sprt-elo1", type=float, default=20.0)
    parser.add_argument("--sprt-alpha", type=float, default=0.05)
    parser.add_argument("--sprt-beta", type=float, default=0.05)

    args = parser.parse_args()

    bot_path = Path(args.bot)
    if not bot_path.is_dir():
        bot_path = Path("bots") / args.bot
    if not bot_path.is_dir():
        print(f"Bot directory not found: {args.bot}", file=sys.stderr)
        return 1

    annotations = parse_opt_annotations(bot_path)
    if not annotations:
        print(f"No '# opt:' annotations found in {bot_path}", file=sys.stderr)
        return 1

    print(f"Found {len(annotations)} tunable parameter(s):")
    for ann in annotations:
        rel = ann["file"].relative_to(bot_path)
        print(f"  {rel}:{ann['line']}  {ann['name']}  {ann['spec_type']}({ann['spec_args_str']})")

    prev_by_opp: dict[str, list[str]] = {}
    for spec in args.prev_match:
        if ":" not in spec:
            print(f"--prev-match expects OPP_ID:MATCH_ID, got: {spec}", file=sys.stderr)
            return 1
        opp, mid = spec.split(":", 1)
        prev_by_opp.setdefault(opp, []).append(mid)

    maps_per_round = [m.split(",") for m in args.maps] if args.maps else None

    print()
    if args.sprt:
        print(f"SPRT enabled: H0={args.sprt_elo0:+.0f} Elo, H1={args.sprt_elo1:+.0f} Elo, "
              f"alpha={args.sprt_alpha}, beta={args.sprt_beta}")
    if prev_by_opp:
        for opp, mids in prev_by_opp.items():
            print(f"Version cycling for {opp}: {len(mids)} prior version(s)")
    if maps_per_round:
        print(f"Map selection: {len(maps_per_round)} round-specific map sets")

    study_name = args.study_name or f"online-tune-{bot_path.name}"

    with tempfile.TemporaryDirectory(prefix="online_tuner_") as tmp_root:
        tmp_path = Path(tmp_root)

        objective = make_online_objective(
            bot_dir=bot_path,
            annotations=annotations,
            opponent_ids=args.opponents,
            rounds=args.rounds,
            tmp_root=tmp_path,
            use_sprt=args.sprt,
            sprt_elo0=args.sprt_elo0,
            sprt_elo1=args.sprt_elo1,
            sprt_alpha=args.sprt_alpha,
            sprt_beta=args.sprt_beta,
            prev_match_ids_by_opp=prev_by_opp,
            maps_per_round=maps_per_round,
            cooldown_override=args.cooldown,
        )

        study = optuna.create_study(study_name=study_name, direction="maximize")
        print(f"Starting Optuna study '{study_name}' with {args.trials} trials")
        print(f"  Opponents: {', '.join(args.opponents)}")
        print(f"  Rounds per trial: {args.rounds}")
        print()
        study.optimize(objective, n_trials=args.trials, n_jobs=1)

    print(f"\n{'=' * 70}")
    print("  ONLINE TUNING RESULTS")
    print(f"{'=' * 70}")
    print(f"  Best win rate: {study.best_value:.3f}")
    print(f"  Best trial:    #{study.best_trial.number}")
    print(f"  Best params:")
    for k, v in study.best_params.items():
        print(f"    {k} = {v!r}")

    print(f"\n  Suggested code changes in {bot_path}:")
    for ann in annotations:
        rel = ann["file"].relative_to(bot_path)
        val = study.best_params[ann["name"]]
        print(f"    {rel}:{ann['line']}  {ann['name']} = {val!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
