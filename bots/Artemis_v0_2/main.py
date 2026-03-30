# main.py

from cambc import Controller, EntityType, Position

import random
import time
import traceback
import sys
# import cProfile
# import pstats
# import pathlib
import shutil

import units.builder as builder
import units.builder_states.builder_rush as builder_rush
import units.core as core
import units.turret_gunner as gunner
import units.turret_sentinel as sentinel
import units.turret_breach as breach
import units.turret_launcher as launcher


# PROFILE_DIR = pathlib.Path("profiles")
SPAWN_TURN = -2


class Player:
    def __init__(self):
        self.initialized = False
        self.me = None
        

    # def _prepare_profile_dir(self, c: Controller) -> None:
    #     # Guaranteed: exactly one of unit 1 or 2 exists, and it runs first.
    #     # So that first unit can safely clear the folder once.
    #     unit_id = c.get_id()

    #     if unit_id in (1, 2):
    #         if PROFILE_DIR.exists():
    #             shutil.rmtree(PROFILE_DIR)
    #         PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    #     else:
    #         PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    # def _write_profile(self, profiler: cProfile.Profile, profiler_path: pathlib.Path) -> None:
    #     stats = pstats.Stats(profiler)

    #     # stats.stats:
    #     # key   = (filename, lineno, funcname)
    #     # value = (cc, nc, tt, ct, callers)
    #     # tt = tottime, ct = cumtime
    #     rows = list(stats.stats.items())
    #     rows.sort(key=lambda item: item[1][2], reverse=True)  # sort by tottime

    #     total_calls = sum(v[1] for _, v in rows)
    #     total_tottime = sum(v[2] for _, v in rows)
    #     total_cumtime = sum(v[3] for _, v in rows)

    #     with profiler_path.open("w", encoding="utf-8") as f:
    #         f.write("Profile sorted by total time (tottime)\n")
    #         f.write(f"Unit profile: {profiler_path.name}\n")
    #         f.write(f"Total calls: {total_calls}\n")
    #         f.write(f"Total tottime: {total_tottime * 1_000_000:.3f} us\n")
    #         f.write(f"Total cumtime: {total_cumtime * 1_000_000:.3f} us\n")
    #         f.write("\n")
    #         f.write(f"{'ncalls':>12} {'tottime_us':>14} {'cumtime_us':>14}  function\n")
    #         f.write("-" * 100 + "\n")

    #         for (filename, lineno, funcname), (cc, nc, tt, ct, callers) in rows:
    #             if cc == nc:
    #                 calls_str = str(nc)
    #             else:
    #                 calls_str = f"{nc}/{cc}"

    #             f.write(
    #                 f"{calls_str:>12} "
    #                 f"{tt * 1_000_000:14.3f} "
    #                 f"{ct * 1_000_000:14.3f}  "
    #                 f"{filename}:{lineno}({funcname})\n"
                # )

    def run(self, c: Controller) -> None:
        global SPAWN_TURN
        # if not self.initialized:
        #     self._prepare_profile_dir(c)
        
        if SPAWN_TURN == -2:
            SPAWN_TURN = c.get_current_round() - 1

        # profiler_path = PROFILE_DIR / f"unit_{c.get_id()}.txt"
        # profiler = cProfile.Profile()

        # profiler.enable()
        try:
            start_time = time.perf_counter()
            etype = c.get_entity_type()

            if not self.initialized:
                random.seed(c.get_current_round())

                if etype == EntityType.CORE:
                    self.me = core
                elif etype == EntityType.BUILDER_BOT:
                    if SPAWN_TURN == 0:
                        if (max(abs(c.get_map_width() / 2 - c.get_position().x), abs(c.get_map_height() / 2 - c.get_position().y)) > 40):
                            self.me = builder
                        else:
                            self.me = builder_rush
                    else:
                        self.me = builder
                elif etype == EntityType.GUNNER:
                    self.me = gunner
                elif etype == EntityType.SENTINEL:
                    self.me = sentinel
                elif etype == EntityType.BREACH:
                    self.me = breach
                elif etype == EntityType.LAUNCHER:
                    self.me = launcher

                self.me.init(c)
                self.initialized = True

            self.me.run()

            end_time = time.perf_counter()
            elapsed_us = (end_time - start_time) * 1_000_000

            print(f"{elapsed_us:.3f} μs")

            # if end_time - start_time > 0.002:
            #     print(
            #         "timed out",
            #         c.get_id(),
            #         c.get_current_round(),
            #         f"{elapsed_us:.3f} μs",
            #         file=sys.stderr,
            #     )

        except Exception as e:
            print("Error:", e)
            print(f"Error: {e}", file=sys.stderr)
            c.draw_indicator_line(Position(-100, -100), c.get_position(), 255, 0, 0)
            traceback.print_exc(file=sys.stdout)
            traceback.print_exc(file=sys.stderr)

        # finally:
        #     pass
            # profiler.disable()
            # self._write_profile(profiler, profiler_path)