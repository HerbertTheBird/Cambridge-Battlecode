#!/usr/bin/env python3
"""
replay_analyzer.py — feed a .replay26 replay file frame-by-frame to a bot
and print what it would have done on each turn.

The replay's protobuf binary is decoded to reconstruct the full game state
at every turn.  For each unit on the selected team, a fresh isolated copy
of the bot is loaded and driven with a MockController (no real actions
are executed — everything is just logged to stdout).

Usage:
    python replay_analyzer.py \\
        --replay replay.replay26 \\
        --bot    bots/Artemis_v0_2 \\
        --team   A \\
        [--turns 1-100] \\
        [--output analysis.log]

Arguments:
    --replay   Path to the .replay26 file to analyze (required)
    --bot      Path to the bot directory to run (required)
    --team     Which team to observe: A or B (default: A)
    --turns    Turn range to analyze, e.g. "1-50" or "42" (default: all)
    --output   If given, write [DBG] lines to this file as well as stdout

Module isolation note:
    Each unit gets its own freshly-imported copy of the bot's modules so that
    module-level globals (rc, mode, …) do not bleed between units, matching
    the real engine's per-unit subinterpreter behaviour.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import os
import sys

# ── Force stdout to UTF-8 so bot print() with unicode arrows/symbols works ────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Bootstrap: add wrapper directory to path ──────────────────────────────────

_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
_WRAPPER_DIR  = os.path.join(_SCRIPT_DIR, "bots", "debug_wrapper")
if _WRAPPER_DIR not in sys.path:
    sys.path.insert(0, _WRAPPER_DIR)

from replay_parser import parse_replay, BotOutput
from game_state    import GameState
from mock_controller import MockController


# ── Bot loader with module isolation ──────────────────────────────────────────

# These are the module names that the bots typically use at module level.
# They are cleared from sys.modules before each fresh bot load so that
# each unit gets its own independent copy.
_BOT_MODULE_NAMES = [
    "main",
    "map_info", "pathing", "comms",
    "units", "units.builder", "units.builder_states",
    "units.builder_states.explore",
    "units.builder_states.harvest",
    "units.builder_states.builder_rush",
    "units.core",
    "units.turret_gunner",
    "units.turret_sentinel",
    "units.turret_breach",
    "units.turret_launcher",
]


def _load_fresh_player(bot_path_abs: str, unit_id: int) -> tuple[object, dict]:
    """
    Load the bot's Player class in a completely isolated module environment.

    Returns (player_instance, {module_name: module_object}).
    The caller MUST install the returned module dict into sys.modules (and add
    bot_path_abs to sys.path) before each player.run() invocation so that
    any function-level dynamic imports inside the bot resolve correctly.
    """
    # 1. Save and remove any previously loaded bot modules from the cache.
    saved: dict = {}
    for name in _BOT_MODULE_NAMES:
        if name in sys.modules:
            saved[name] = sys.modules.pop(name)

    # 2. Temporarily add the bot directory to sys.path so relative imports
    #    inside the bot (e.g. `import units.builder`) resolve correctly.
    prepended = bot_path_abs not in sys.path
    if prepended:
        sys.path.insert(0, bot_path_abs)

    fresh_modules: dict[str, object] = {}
    try:
        spec = importlib.util.spec_from_file_location(
            f"_bot_main_u{unit_id}",
            os.path.join(bot_path_abs, "main.py"),
        )
        main_mod = importlib.util.module_from_spec(spec)
        # Register under "main" so intra-bot imports of `main` resolve.
        sys.modules["main"] = main_mod
        spec.loader.exec_module(main_mod)
        player = main_mod.Player()
    finally:
        # 3. Collect all freshly loaded bot modules as a name→module dict.
        for name in _BOT_MODULE_NAMES:
            m = sys.modules.pop(name, None)
            if m is not None:
                fresh_modules[name] = m
        # 4. Restore the previously saved modules.
        sys.modules.update(saved)
        if prepended and bot_path_abs in sys.path:
            sys.path.remove(bot_path_abs)

    return player, fresh_modules


def _run_player(player, mc, unit_mods: dict, bot_path_abs: str) -> None:
    """
    Call player.run(mc) with the unit's private modules installed in
    sys.modules and bot_path in sys.path, then clean up afterwards.
    This ensures any function-level dynamic imports (e.g. `from units.builder
    import log` inside map_info.py) resolve to the correct unit's module.
    """
    # Save anything that's currently occupying those names.
    displaced: dict = {}
    for name in unit_mods:
        if name in sys.modules:
            displaced[name] = sys.modules[name]
        sys.modules[name] = unit_mods[name]

    path_prepended = bot_path_abs not in sys.path
    if path_prepended:
        sys.path.insert(0, bot_path_abs)
    try:
        player.run(mc)
    finally:
        # Remove this unit's modules; restore any displaced ones.
        for name in unit_mods:
            sys.modules.pop(name, None)
        sys.modules.update(displaced)
        if path_prepended and bot_path_abs in sys.path:
            sys.path.remove(bot_path_abs)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_turn_range(spec: str | None, max_turn: int) -> tuple[int, int]:
    if not spec:
        return 1, max_turn
    if "-" in spec:
        lo, hi = spec.split("-", 1)
        return int(lo), int(hi)
    t = int(spec)
    return t, t


def _resolve(path: str) -> str:
    if os.path.exists(path):
        return os.path.abspath(path)
    cand = os.path.join(_SCRIPT_DIR, path)
    if os.path.exists(cand):
        return os.path.abspath(cand)
    raise FileNotFoundError(f"Cannot find: {path!r}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay a .replay26 file through a bot and log what it would have done.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--replay",  required=True, help=".replay26 file to analyze")
    parser.add_argument("--bot",     required=True, help="Bot directory to run")
    parser.add_argument("--team",    choices=["A", "B"], default="A",
                        help="Which team to observe (default: A)")
    parser.add_argument("--turns",   default=None,
                        help="Turn range, e.g. '1-100' or '42' (default: all)")
    parser.add_argument("--output",  default=None,
                        help="Optional file to write [DBG] lines into")
    parser.add_argument("--no-dbg", action="store_true",
                        help="Suppress [DBG] action lines; show only bot print() output")
    args = parser.parse_args()

    replay_path  = _resolve(args.replay)
    bot_path     = _resolve(args.bot)
    bot_path_abs = os.path.abspath(bot_path)
    team_int     = 0 if args.team == "A" else 1

    # ── Parse replay ──────────────────────────────────────────────────────────
    print(f"[replay_analyzer] Parsing {replay_path} ...", flush=True)
    replay = parse_replay(replay_path)
    print(f"[replay_analyzer] Map {replay.map.width}x{replay.map.height}, "
          f"{len(replay.turns)} turns.", flush=True)

    turn_lo, turn_hi = _parse_turn_range(args.turns, len(replay.turns))
    print(f"[replay_analyzer] Analyzing turns {turn_lo}-{turn_hi} for team {args.team}.",
          flush=True)
    print(f"[replay_analyzer] Bot: {bot_path_abs}", flush=True)
    print()

    out_file = open(args.output, "w", encoding="utf-8") if args.output else None

    # ── Build initial game state ───────────────────────────────────────────────
    gs = GameState(replay.map)

    # Per-unit storage: unit_id -> (Player, MockController, {name: module})
    unit_players: dict[int, tuple]       = {}
    unit_modules: dict[int, dict]        = {}

    if args.no_dbg:
        # Silence all [DBG] action lines — only bot print() output will appear
        MockController._log = lambda self, msg: None

    elif out_file:
        # Replace MockController._log so we can tee to file
        def _tee_log(self, msg: str) -> None:
            try:
                me = self._gs.entities.get(self._unit_id)
                label = (f"{me.entity_type}#{self._unit_id}@{me.pos}"
                         if me else f"UNIT#{self._unit_id}")
            except Exception:
                label = f"UNIT#{self._unit_id}"
            line = f"[DBG][T{self._gs.current_round:04d}][{label}] {msg}"
            print(line, flush=True)
            out_file.write(line + "\n")

        MockController._log = _tee_log

    # ── Frame loop ────────────────────────────────────────────────────────────
    try:
        for turn_idx, game_turn in enumerate(replay.turns):
            turn_num = turn_idx + 1   # 1-based

            # Always advance game state (even outside the analysis window)
            gs.advance_turn(game_turn.updates)

            if not (turn_lo <= turn_num <= turn_hi):
                continue

            # Print original bot output for this turn (from replay) as context
            for upd in game_turn.updates:
                if isinstance(upd, BotOutput):
                    e = gs.entities.get(upd.id)
                    if e and e.team == team_int and upd.stdout.strip():
                        etype = e.entity_type
                        for raw_line in upd.stdout.splitlines():
                            if raw_line.strip():
                                print(f"[ORIG][T{turn_num:04d}][{etype}#{upd.id}] {raw_line}",
                                      flush=True)

            # Collect all units of the observed team still alive
            team_units = sorted(
                eid for eid, e in gs.entities.items()
                if e.team == team_int
                and e.entity_type in ("BUILDER_BOT", "CORE",
                                      "GUNNER", "SENTINEL", "BREACH", "LAUNCHER")
            )

            for unit_id in team_units:
                # First time we see this unit: create isolated player + controller
                if unit_id not in unit_players:
                    player, fresh_mods = _load_fresh_player(bot_path_abs, unit_id)
                    mc = MockController(gs, unit_id)
                    unit_players[unit_id] = (player, mc)
                    unit_modules[unit_id] = fresh_mods

                player, mc = unit_players[unit_id]
                # mc._gs is shared — it already reflects the current turn
                try:
                    _run_player(player, mc, unit_modules[unit_id], bot_path_abs)
                except SystemExit:
                    pass   # some bots call sys.exit; catch gracefully
                except Exception as exc:
                    if not args.no_dbg:
                        e = gs.entities.get(unit_id)
                        label = f"{e.entity_type}#{unit_id}" if e else f"UNIT#{unit_id}"
                        print(f"[DBG][T{turn_num:04d}][{label}] ERROR: {exc}", flush=True)

            # Prune dead units from our tables
            dead = [uid for uid in list(unit_players) if uid not in gs.entities]
            for uid in dead:
                del unit_players[uid]
                del unit_modules[uid]

    finally:
        if out_file:
            out_file.close()
            print(f"\n[replay_analyzer] [DBG] lines written to: {args.output}")

    # ── Summary ───────────────────────────────────────────────────────────────
    winner_str = {0: "Team A", 1: "Team B"}.get(replay.winner, "unknown")
    print(f"\n[replay_analyzer] Done. Total turns: {len(replay.turns)}. Winner: {winner_str}.")


if __name__ == "__main__":
    main()
