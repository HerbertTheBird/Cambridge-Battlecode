from cambc import Controller, EntityType, Position

import random
import time
import traceback
import sys
from types import ModuleType

import units.builder as builder
import units.core as core
import units.turret_gunner as gunner
import units.turret_sentinel as sentinel
import units.turret_breach as breach
import units.turret_launcher as launcher
import map_info
import comms
import comms_stats
from log import log

ENABLE_PROFILER = False
ENABLE_COMMS_STATS = False
ENABLE_VIS = False  # emit ##VIS## grids to stdout for the Rust replay viewer

if ENABLE_PROFILER or ENABLE_COMMS_STATS:
    import cProfile
    import pstats
    import pathlib
    import shutil

    PROFILE_DIR = pathlib.Path("profiles")

    comms_stats.ENABLED = ENABLE_COMMS_STATS

if ENABLE_VIS:
    from visualiser import (
        BoolGrid, Colour, FOG, Palette, PaletteStop, Tiles, TRANSPARENT, emit,
    )

    def _p(r: int, g: int, b: int, a: int) -> Palette:
        return Palette(stops=[
            PaletteStop(t=False, colour=TRANSPARENT),
            PaletteStop(t=True, colour=Colour(r, g, b, a)),
        ])

    P_CONV_LOADED = _p(100, 255, 100, 140)
    P_DEAD_END    = _p(255, 150,   0, 180)
    P_CONV_STUCK  = _p(200,   0, 200, 180)
    P_THREAT      = _p(255,  50,  50, 140)
    P_TURRET_ADJ  = _p(255, 120,  60, 120)


def _bm_to_bool_grid(bm: int, total: int) -> list[bool]:
    """Bitmask → flat row-major bool list of length `total`. Bit `x + y*w`
    maps to index `x + y*w`, matching tile indexing used throughout.

    Fast path: format the int as a 0-padded binary string (C-implemented),
    reverse to get LSB-first ordering, then one char comparison per tile.
    ~10× faster than a per-tile shift on large ints for 60×60+ maps."""
    if not bm:
        return [False] * total
    bm &= (1 << total) - 1
    return [c == '1' for c in format(bm, f'0{total}b')[::-1]]


def _mask_positions(bm: int, w: int) -> list[tuple[int, int]]:
    """Bitmask → list of (x, y) positions. Used for the Tiles overlay, which
    is much cheaper than a full BoolGrid when the set is sparse."""
    positions = []
    while bm:
        lsb = bm & -bm
        n = lsb.bit_length() - 1
        positions.append((n % w, n // w))
        bm ^= lsb
    return positions


def _emit_vis() -> None:
    """Emit this unit's belief state. Each unit is sandboxed, so its
    map_info globals reflect only what *it* has personally seen. In the
    viewer, per-unit toggles show each bot's individual view of the world."""
    w = map_info._width
    total = w * map_info._height
    board = map_info._board_mask

    emit(
        fog=BoolGrid(_bm_to_bool_grid(~map_info._bm_seen & board, total), palette=FOG),
        conv_loaded=BoolGrid(_bm_to_bool_grid(map_info._bm_conv_loaded, total), palette=P_CONV_LOADED),
        dead_end=BoolGrid(_bm_to_bool_grid(map_info._bm_dead_end, total), palette=P_DEAD_END),
        conv_stuck=BoolGrid(_bm_to_bool_grid(map_info._bm_conv_stuck, total), palette=P_CONV_STUCK),
        threat=BoolGrid(_bm_to_bool_grid((map_info._bm_enemy_soft_threat | map_info._bm_enemy_hard_threat), total), palette=P_THREAT),
        turret_adj=BoolGrid(_bm_to_bool_grid(map_info._bm_enemy_launch_adj, total), palette=P_TURRET_ADJ),
        friendly_bots=Tiles(_mask_positions(map_info._bm_friendly_bots, w)),
        enemy_bots=Tiles(_mask_positions(map_info._bm_enemy_bots, w)),
    )


SPAWN_TURN = -2


class Player:
    def __init__(self):
        self.initialized = False
        self.me: ModuleType

        if ENABLE_PROFILER:
            self.profiler_path = None
            self.accumulated_stats: pstats.Stats | None = None
            self.timeout_count = 0

    def _prepare_profile_dir(self, c: Controller) -> None:
        if not (ENABLE_PROFILER or ENABLE_COMMS_STATS):
            return

        # Guaranteed: exactly one of unit 1 or 2 exists, and it runs first.
        # So that first unit can safely clear the folder once.
        unit_id = c.get_id()

        if unit_id in (1, 2):
            if PROFILE_DIR.exists():
                shutil.rmtree(PROFILE_DIR)
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        else:
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        if ENABLE_COMMS_STATS:
            comms_stats.prepare_dir()

    def _write_profile(self) -> None:
        if not ENABLE_PROFILER or self.accumulated_stats is None or self.profiler_path is None:
            return

        # stats.stats:
        # key   = (filename, lineno, funcname)
        # value = (cc, nc, tt, ct, callers)
        # tt = tottime, ct = cumtime
        rows = list(self.accumulated_stats.stats.items())
        rows.sort(key=lambda item: item[1][2], reverse=True)  # sort by tottime

        total_calls = sum(v[1] for _, v in rows)
        total_tottime = sum(v[2] for _, v in rows)
        total_cumtime = sum(v[3] for _, v in rows)

        with self.profiler_path.open("w", encoding="utf-8") as f:
            f.write("Profile sorted by total time (tottime) — timed-out turns only\n")
            f.write(f"Unit profile: {self.profiler_path.name}\n")
            f.write(f"Timed-out turns: {self.timeout_count}\n")
            f.write(f"Total calls: {total_calls}\n")
            f.write(f"Total tottime: {total_tottime * 1_000_000:.3f} us\n")
            f.write(f"Total cumtime: {total_cumtime * 1_000_000:.3f} us\n")
            f.write("\n")
            f.write(f"{'ncalls':>12} {'tottime_us':>14} {'cumtime_us':>14}  function\n")
            f.write("-" * 100 + "\n")

            for (filename, lineno, funcname), (cc, nc, tt, ct, callers) in rows:
                if cc == nc:
                    calls_str = str(nc)
                else:
                    calls_str = f"{nc}/{cc}"

                f.write(
                    f"{calls_str:>12} "
                    f"{tt * 1_000_000:14.3f} "
                    f"{ct * 1_000_000:14.3f}  "
                    f"{filename}:{lineno}({funcname})\n"
                )

    def run(self, c: Controller) -> None:
        global SPAWN_TURN

        if not self.initialized:
            self._prepare_profile_dir(c)

            if ENABLE_PROFILER:
                self.profiler_path = PROFILE_DIR / f"unit_{c.get_id()}.txt"

        if SPAWN_TURN == -2:
            SPAWN_TURN = c.get_current_round() - 1

        turn_profiler = None
        try:
            start_time = time.perf_counter_ns()
            etype = c.get_entity_type()

            if not self.initialized:
                random.seed(c.get_current_round())

                if etype == EntityType.CORE:
                    self.me = core
                elif etype == EntityType.BUILDER_BOT:
                    self.me = builder
                elif etype == EntityType.GUNNER:
                    self.me = gunner
                elif etype == EntityType.SENTINEL:
                    self.me = sentinel
                elif etype == EntityType.BREACH:
                    self.me = breach
                elif etype == EntityType.LAUNCHER:
                    self.me = launcher

                map_info.init(c)
                comms.init(c)
                self.me.init(c)
                self.initialized = True

            if ENABLE_PROFILER:
                turn_profiler = cProfile.Profile()
                turn_profiler.enable()

            self.me.run()

            if ENABLE_PROFILER and turn_profiler is not None:
                turn_profiler.disable()

            if ENABLE_VIS:
                _emit_vis()

            end_time = time.perf_counter_ns()
            elapsed_us = end_time - start_time

            log(f"{elapsed_us/1000000:.3f} ms")

            if end_time - start_time > 2_000_000:
                log(
                    "timed out",
                    c.get_id(),
                    c.get_current_round(),
                    f"{elapsed_us / 1000000:.3f} ms",
                    file=sys.stderr,
                )
                c.draw_indicator_line(Position(0, 0), c.get_position(), 255, 0, 0)
                if ENABLE_PROFILER and turn_profiler is not None:
                    self.timeout_count += 1
                    import io
                    turn_stats = pstats.Stats(turn_profiler, stream=io.StringIO())
                    if self.accumulated_stats is None:
                        self.accumulated_stats = turn_stats
                    else:
                        self.accumulated_stats.add(turn_profiler)
                    self._write_profile()

        except Exception as e:
            if ENABLE_PROFILER and turn_profiler is not None:
                turn_profiler.disable()
            print("Error:", e)
            print(f"Error: {e}", file=sys.stderr)
            c.draw_indicator_line(Position(-100, -100), c.get_position(), 255, 0, 0)
            traceback.print_exc(file=sys.stdout)
            traceback.print_exc(file=sys.stderr)
