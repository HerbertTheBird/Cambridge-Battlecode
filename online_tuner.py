#!/usr/bin/env python3
"""
Online Tuner — uses Optuna to optimize bot parameters via online unrated matches.

Like ``tuner.py`` but instead of running local matches, it submits the patched bot
and plays unrated matches against specified opponent teams on the platform.

Blue Dragon Team ID: 023ce802-d72e-44f5-b99e-71a6f97db4b7

Usage::

    python online_tuner.py Artemis_v0 --opponents TEAM_ID_1 TEAM_ID_2 --trials 10
    python online_tuner.py Artemis_v0 --opponents TEAM_ID_1 --trials 20 --rounds 2
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import textwrap
from pathlib import Path

import optuna

from online_challenge import run_online_challenge, compute_overall_win_rate, print_results
from tuner import parse_opt_annotations, suggest_value, create_patched_bot


# ── Optuna objective ────────────────────────────────────────────────────────

def make_online_objective(
    bot_dir: Path,
    annotations: list[dict],
    opponent_ids: list[str],
    rounds: int,
    tmp_root: Path,
):
    def objective(trial: optuna.Trial) -> float:
        # Suggest values for all annotated parameters
        values: dict[str, int | float | str] = {}
        for ann in annotations:
            values[ann["name"]] = suggest_value(trial, ann)

        # Create patched bot copy
        patched_dir = create_patched_bot(bot_dir, annotations, values, tmp_root)

        print(f"\n  Trial {trial.number}: {values}")

        # Submit and challenge online
        stats = run_online_challenge(
            bot_dir=patched_dir,
            opponent_ids=opponent_ids,
            rounds=rounds,
            skip_submit=False,
        )

        win_rate = compute_overall_win_rate(stats)
        total_wins = sum(s.total_wins for s in stats.values())
        total_losses = sum(s.total_losses for s in stats.values())
        print(f"  Trial {trial.number}: {values} -> win_rate={win_rate:.3f} "
              f"({total_wins}W/{total_losses}L)")

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
                        help="Challenge rounds per trial (5-min cooldown between rounds).")
    parser.add_argument("--study-name", default=None,
                        help="Optuna study name (default: online-tune-<bot>).")
    args = parser.parse_args()

    bot_path = Path(args.bot)
    if not bot_path.is_dir():
        bot_path = Path("bots") / args.bot
    if not bot_path.is_dir():
        print(f"Bot directory not found: {args.bot}", file=sys.stderr)
        return 1

    # Discover annotations
    annotations = parse_opt_annotations(bot_path)
    if not annotations:
        print(f"No '# opt:' annotations found in {bot_path}", file=sys.stderr)
        return 1

    print(f"Found {len(annotations)} tunable parameter(s):")
    for ann in annotations:
        rel = ann["file"].relative_to(bot_path)
        print(f"  {rel}:{ann['line']}  {ann['name']}  {ann['spec_type']}({ann['spec_args_str']})")
    print()

    study_name = args.study_name or f"online-tune-{bot_path.name}"

    with tempfile.TemporaryDirectory(prefix="online_tuner_") as tmp_root:
        tmp_path = Path(tmp_root)

        objective = make_online_objective(
            bot_dir=bot_path,
            annotations=annotations,
            opponent_ids=args.opponents,
            rounds=args.rounds,
            tmp_root=tmp_path,
        )

        study = optuna.create_study(
            study_name=study_name,
            direction="maximize",
        )

        print(f"Starting online Optuna study '{study_name}' with {args.trials} trials")
        print(f"  Opponents: {', '.join(args.opponents)}")
        print(f"  Rounds per trial: {args.rounds}")
        print()

        study.optimize(objective, n_trials=args.trials, n_jobs=1)

    # Report results
    print(f"\n{'=' * 70}")
    print("  ONLINE TUNING RESULTS")
    print(f"{'=' * 70}")
    print(f"  Best win rate: {study.best_value:.3f}")
    print(f"  Best trial:    #{study.best_trial.number}")
    print(f"  Best params:")
    for k, v in study.best_params.items():
        print(f"    {k} = {v!r}")

    # Show the lines to update
    print(f"\n  Suggested code changes in {bot_path}:")
    for ann in annotations:
        rel = ann["file"].relative_to(bot_path)
        val = study.best_params[ann["name"]]
        print(f"    {rel}:{ann['line']}  {ann['name']} = {val!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
