#!/usr/bin/env python3
"""
debug_game.py — run a Battlecode bot with full action logging.

Wraps the selected bot in DebugController so every action (move, build, fire,
heal, etc.) is printed to stdout before being executed.  The game plays out
normally — no decisions are changed.

Usage:
    python debug_game.py --bot bots/Artemis_v0_2 --team A \\
        --opponent bots/baseline --map maps/arena.map26 [--seed 1] [--output debug.log]

Arguments:
    --bot       Path to the bot directory you want to observe (required)
    --team      Which team slot that bot occupies: A or B (default: A)
    --opponent  Path to the opponent bot directory (required)
    --map       Path to the .map26 file (default: first map in maps/)
    --seed      Random seed for the match (default: 1 for reproducibility)
    --output    If given, tee [DBG] lines to this file in addition to stdout

Since matches are deterministic given the same seed + bots + map, running
the same command twice will always produce identical logs.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys


def _find_default_map() -> str | None:
    maps_dir = os.path.join(os.path.dirname(__file__), "maps")
    if os.path.isdir(maps_dir):
        for name in sorted(os.listdir(maps_dir)):
            if name.endswith(".map26"):
                return os.path.join(maps_dir, name)
    return None


def _resolve_path(path: str) -> str:
    """Return absolute path; search relative to project root if not found as-is."""
    if os.path.exists(path):
        return os.path.abspath(path)
    # Try relative to the script's directory
    candidate = os.path.join(os.path.dirname(__file__), path)
    if os.path.exists(candidate):
        return os.path.abspath(candidate)
    raise FileNotFoundError(f"Cannot find path: {path!r}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a Battlecode bot with full action logging via DebugController.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--bot", required=True, help="Bot directory to observe")
    parser.add_argument(
        "--team",
        choices=["A", "B"],
        default="A",
        help="Team slot for the observed bot (default: A)",
    )
    parser.add_argument("--opponent", required=True, help="Opponent bot directory")
    parser.add_argument(
        "--map",
        dest="map_path",
        default=None,
        help="Path to .map26 map file (default: first map in maps/)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=1,
        help="Random seed — use the same value to reproduce identical logs (default: 1)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional file to save [DBG] log lines (in addition to stdout)",
    )
    args = parser.parse_args()

    # ── Resolve paths ─────────────────────────────────────────────────────────
    bot_path = _resolve_path(args.bot)
    opponent_path = _resolve_path(args.opponent)
    wrapper_path = _resolve_path(os.path.join(os.path.dirname(__file__), "bots", "debug_wrapper"))

    if args.map_path:
        map_path = _resolve_path(args.map_path)
    else:
        map_path = _find_default_map()
        if not map_path:
            sys.exit("No map found. Specify --map or add .map26 files to maps/")

    # ── Build cambc run command ────────────────────────────────────────────────
    # Team A is always the first positional argument to `cambc run`.
    if args.team == "A":
        bot_a = wrapper_path
        bot_b = opponent_path
    else:
        bot_a = opponent_path
        bot_b = wrapper_path

    cmd = [
        sys.executable, "-m", "cambc", "run",
        bot_a,
        bot_b,
        map_path,
        "--seed", str(args.seed),
    ]

    env = os.environ.copy()
    env["CAMBC_DEBUG_BOT"] = bot_path
    env["CAMBC_DEBUG_TEAM"] = args.team

    # ── Run and optionally tee output ─────────────────────────────────────────
    print(f"[debug_game] Observing team {args.team}: {bot_path}")
    print(f"[debug_game] Opponent: {opponent_path}")
    print(f"[debug_game] Map: {map_path}  seed={args.seed}")
    print(f"[debug_game] Command: {' '.join(cmd)}")
    print()

    if args.output:
        # Stream to stdout AND write [DBG] lines to file
        out_file = open(args.output, "w", encoding="utf-8")
        try:
            proc = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                if line.startswith("[DBG]"):
                    out_file.write(line)
            proc.wait()
        finally:
            out_file.close()
        print(f"\n[debug_game] [DBG] lines saved to: {args.output}")
        sys.exit(proc.returncode)
    else:
        # Just stream directly — simplest path
        result = subprocess.run(cmd, env=env)
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
