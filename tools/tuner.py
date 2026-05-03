#!/usr/bin/env python3
"""
Hyperparameter Tuner — uses Optuna to optimize bot parameters annotated with ``# opt:``.

Annotate parameters in your bot code like this::

    ATTACK_VALUE = 10_000  # opt: int(9_997, 10_003)
    RETREAT_THRESHOLD = 0.3  # opt: float(0.1, 0.6, step=0.05)
    STRATEGY = "rush"  # opt: categorical(["rush", "defend", "balanced"])

Supported suggestion types (matching Optuna's ``trial.suggest_*``)::

    # opt: int(low, high, *[, step, log])
    # opt: float(low, high, *[, step, log])
    # opt: categorical(choices)

Usage::

    python tuner.py Artemis_v0 --opponents rush z_do_nothing --trials 50 --threads 4
    python tuner.py bots/Artemis_v0 --opponents bots/rush --rounds 2 --map-filter arena
"""

from __future__ import annotations

import argparse
import random
import re
import shutil
import sys
import tempfile
import textwrap
from pathlib import Path

import optuna

from gauntlet import run_gauntlet
from tournament import discover_maps

# ── Annotation parsing ──────────────────────────────────────────────────────

# Matches lines like:  VAR = <value>  # opt: int(1, 10)
OPT_RE = re.compile(
    r"^(?P<indent>\s*)"
    r"(?P<name>[A-Za-z_]\w*)"
    r"\s*=\s*"
    r"(?P<value>.+?)"
    r"\s*#\s*opt:\s*"
    r"(?P<spec>.+?)\s*$"
)

# Matches the suggestion type and arguments:  int(1, 10, step=2)
SPEC_RE = re.compile(r"^(?P<type>int|float|categorical)\((?P<args>.*)\)$")


def parse_opt_annotations(bot_dir: Path) -> list[dict]:
    """Scan all .py files in *bot_dir* for ``# opt:`` annotations.

    Returns a list of dicts, each containing:
        file, line, name, spec_type, spec_args_str, original_line
    """
    annotations: list[dict] = []
    for py_file in sorted(bot_dir.rglob("*.py")):
        for lineno, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), 1):
            m = OPT_RE.match(line)
            if not m:
                continue
            spec_m = SPEC_RE.match(m.group("spec"))
            if not spec_m:
                print(f"Warning: invalid opt spec on {py_file.name}:{lineno}: {m.group('spec')}")
                continue
            annotations.append({
                "file": py_file,
                "line": lineno,
                "name": m.group("name"),
                "spec_type": spec_m.group("type"),
                "spec_args_str": spec_m.group("args"),
                "original_line": line,
            })
    return annotations


def suggest_value(trial: optuna.Trial, ann: dict) -> int | float | str:
    """Use an Optuna trial to suggest a value for a single annotation."""
    name = ann["name"]
    spec_type = ann["spec_type"]
    args_str = ann["spec_args_str"]

    # Safely evaluate the arguments (only literals)
    try:
        args = eval(f"_suggest_parse({args_str})", {"_suggest_parse": lambda *a, **kw: (a, kw)})
    except Exception as exc:
        raise ValueError(f"Cannot parse opt args for {name}: {args_str}") from exc

    positional, kwargs = args

    if spec_type == "int":
        return trial.suggest_int(name, *positional, **kwargs)
    elif spec_type == "float":
        return trial.suggest_float(name, *positional, **kwargs)
    elif spec_type == "categorical":
        return trial.suggest_categorical(name, *positional, **kwargs)
    else:
        raise ValueError(f"Unknown spec type: {spec_type}")


def create_patched_bot(
    bot_dir: Path,
    annotations: list[dict],
    values: dict[str, int | float | str],
    tmp_root: Path,
) -> Path:
    """Copy *bot_dir* into a temp directory and patch the annotated values."""
    dest = tmp_root / bot_dir.name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(bot_dir, dest)

    # Group annotations by file
    by_file: dict[Path, list[dict]] = {}
    for ann in annotations:
        by_file.setdefault(ann["file"], []).append(ann)

    for src_file, anns in by_file.items():
        rel = src_file.relative_to(bot_dir)
        dst_file = dest / rel
        lines = dst_file.read_text(encoding="utf-8").splitlines()
        for ann in anns:
            idx = ann["line"] - 1  # 0-based
            old = lines[idx]
            m = OPT_RE.match(old)
            if not m:
                continue
            val = values[ann["name"]]
            val_repr = repr(val)
            # Reconstruct the line preserving indent, variable name, and the opt comment
            lines[idx] = f"{m.group('indent')}{m.group('name')} = {val_repr}  # opt: {ann['spec_type']}({ann['spec_args_str']})"
        dst_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return dest


# ── Optuna objective ────────────────────────────────────────────────────────

def make_objective(
    bot_dir: Path,
    annotations: list[dict],
    opponents: list[str],
    maps: list[Path],
    seeds: list[int],
    threads: int,
    tmp_root: Path,
    verbose: bool,
):
    def objective(trial: optuna.Trial) -> float:
        # Suggest values for all annotated parameters
        values: dict[str, int | float | str] = {}
        for ann in annotations:
            values[ann["name"]] = suggest_value(trial, ann)

        # Create patched bot copy
        patched_dir = create_patched_bot(bot_dir, annotations, values, tmp_root)
        patched_name = str(patched_dir)

        # Run gauntlet
        stats, _, _ = run_gauntlet(
            main_bot=patched_name,
            opponents=opponents,
            maps=maps,
            seeds=seeds,
            threads=threads,
            verbose=verbose,
        )

        main_stats = stats[patched_name]
        total = main_stats.wins + main_stats.losses + main_stats.draws
        if total == 0:
            return 0.0

        # Objective: win rate of the main bot
        win_rate = main_stats.wins / total
        print(f"  Trial {trial.number}: {values} -> win_rate={win_rate:.3f} "
              f"({main_stats.wins}W/{main_stats.losses}L/{main_stats.draws}D)")
        return win_rate

    return objective


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Tune bot hyperparameters annotated with '# opt:' using Optuna.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Example annotations in bot source code:
                ATTACK_VALUE = 10000  # opt: int(9997, 10003)
                RETREAT_PCT  = 0.3   # opt: float(0.1, 0.6, step=0.05)
                STRATEGY     = "rush" # opt: categorical(["rush", "defend"])
        """),
    )
    parser.add_argument("bot", help="Bot to tune (path or name).")
    parser.add_argument("--opponents", nargs="+", required=True, help="Opponent bots.")
    parser.add_argument("--trials", type=int, default=50, help="Number of Optuna trials (default: 50).")
    parser.add_argument("--maps-dir", type=Path, default=Path("maps"), help="Maps directory.")
    parser.add_argument("--map-filter", default="", help="Only maps containing this substring.")
    parser.add_argument("--seed", type=int, default=1, help="Starting seed.")
    parser.add_argument("--rounds", type=int, default=1, help="Number of seeds/rounds per trial.")
    parser.add_argument("--threads", type=int, default=1, help="Parallel match threads (for tournament, not Optuna).")
    parser.add_argument("--map-count", type=int, default=None, help="Randomly select N maps instead of using all.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print each match result.")
    parser.add_argument("--study-name", default=None, help="Optuna study name (default: tune-<bot>).")
    args = parser.parse_args()

    bot_path = Path(args.bot)
    if not bot_path.is_dir():
        # Try as relative to bots/
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

    # Discover maps
    maps = discover_maps(args.maps_dir)
    if args.map_filter:
        maps = [m for m in maps if args.map_filter in m.name]
        if not maps:
            print(f"No maps match filter '{args.map_filter}'.", file=sys.stderr)
            return 1
    if args.map_count and args.map_count < len(maps):
        maps = sorted(random.sample(maps, args.map_count))

    seeds = list(range(args.seed, args.seed + args.rounds))

    study_name = args.study_name or f"tune-{bot_path.name}"

    # Create temp directory for patched bots
    with tempfile.TemporaryDirectory(prefix="tuner_") as tmp_root:
        tmp_path = Path(tmp_root)

        objective = make_objective(
            bot_dir=bot_path,
            annotations=annotations,
            opponents=args.opponents,
            maps=maps,
            seeds=seeds,
            threads=args.threads,
            tmp_root=tmp_path,
            verbose=args.verbose,
        )

        study = optuna.create_study(
            study_name=study_name,
            direction="maximize",
        )

        print(f"Starting Optuna study '{study_name}' with {args.trials} trials")
        print(f"  Maps: {len(maps)}, Seeds: {len(seeds)}, Threads: {args.threads}")
        print(f"  Opponents: {', '.join(args.opponents)}")
        print()

        study.optimize(objective, n_trials=args.trials, n_jobs=1)

    # Report results
    print(f"\n{'=' * 70}")
    print("  TUNING RESULTS")
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
