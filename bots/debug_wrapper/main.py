"""
Debug wrapper bot — dynamically loads any target bot and wraps its Controller
with DebugController for the selected team, logging every action to stdout.

Environment variables (set by debug_game.py):
    CAMBC_DEBUG_BOT   — absolute path to the target bot directory
    CAMBC_DEBUG_TEAM  — "A" or "B" (which team to instrument; default "A")

The other team runs through this wrapper too, but without any wrapping, so
it behaves identically to the original bot.
"""

from __future__ import annotations

import importlib.util
import os
import sys

# ── Configuration ──────────────────────────────────────────────────────────────

_BOT_PATH: str = os.environ.get("CAMBC_DEBUG_BOT", "")
_DEBUG_TEAM: str = os.environ.get("CAMBC_DEBUG_TEAM", "A").upper()

if not _BOT_PATH:
    raise RuntimeError(
        "CAMBC_DEBUG_BOT env var is not set. "
        "Run via debug_game.py, not directly with cambc."
    )

# ── Load the target bot ────────────────────────────────────────────────────────

# Add the target bot's directory to sys.path so all its relative imports work
# (e.g. `import units.builder`, `import map_info`, etc.)
if _BOT_PATH not in sys.path:
    sys.path.insert(0, _BOT_PATH)

# Also add this wrapper's own directory so debug_controller.py is importable
_WRAPPER_DIR = os.path.dirname(os.path.abspath(__file__))
if _WRAPPER_DIR not in sys.path:
    sys.path.insert(0, _WRAPPER_DIR)

_target_main_path = os.path.join(_BOT_PATH, "main.py")
_spec = importlib.util.spec_from_file_location("_target_bot_main", _target_main_path)
_target_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_target_mod)  # executes the target bot's module-level code

_TargetPlayer = _target_mod.Player

from debug_controller import DebugController  # noqa: E402 (after sys.path setup)

# ── Wrapper Player ─────────────────────────────────────────────────────────────


class Player:
    """
    Thin wrapper around the target bot's Player.

    For the debugged team: wraps `c` in DebugController before handing it to
    the inner Player, so every action call is logged.

    For the other team: delegates without wrapping, so the bot is unaffected.
    """

    def __init__(self) -> None:
        self._inner = _TargetPlayer()

    def run(self, c) -> None:
        if c.get_team().name == _DEBUG_TEAM:
            c = DebugController(c)
        self._inner.run(c)
