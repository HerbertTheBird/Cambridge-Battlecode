# Diffs: Lethe_baseline -> Lethe

Generated 2026-04-19 03:27.

Unified diffs for every `.py` file that differs between `bots/Lethe_baseline` and `bots/Lethe`.

## File list

- Files Lethe_baseline/comms.py and Lethe/comms.py differ
- Files Lethe_baseline/comms_positional.py and Lethe/comms_positional.py differ
- Files Lethe_baseline/comms_stats.py and Lethe/comms_stats.py differ
- Files Lethe_baseline/log.py and Lethe/log.py differ
- Files Lethe_baseline/main.py and Lethe/main.py differ
- Files Lethe_baseline/map_info.py and Lethe/map_info.py differ
- Files Lethe_baseline/pathing.py and Lethe/pathing.py differ
- Files Lethe_baseline/units/builder.py and Lethe/units/builder.py differ
- Files Lethe_baseline/units/core.py and Lethe/units/core.py differ
- Files Lethe_baseline/units/states/attack.py and Lethe/units/states/attack.py differ
- Files Lethe_baseline/units/states/disrupt.py and Lethe/units/states/disrupt.py differ
- Files Lethe_baseline/units/states/explore.py and Lethe/units/states/explore.py differ
- Files Lethe_baseline/units/states/harvest.py and Lethe/units/states/harvest.py differ
- Files Lethe_baseline/units/states/heal.py and Lethe/units/states/heal.py differ
- Files Lethe_baseline/units/states/route.py and Lethe/units/states/route.py differ
- Files Lethe_baseline/units/states/sabotage.py and Lethe/units/states/sabotage.py differ
- Files Lethe_baseline/units/turret_gunner.py and Lethe/units/turret_gunner.py differ
- Files Lethe_baseline/units/turret_sentinel.py and Lethe/units/turret_sentinel.py differ
- Only in Lethe: version.txt

## Diffs

### `comms.py`

```diff
--- bots/Lethe_baseline/comms.py	2026-04-18 20:19:30
+++ bots/Lethe/comms.py	2026-04-17 02:52:03
@@ -1,7 +1,9 @@
-from cambc import Controller, Position, Direction, EntityType, GameError
+from cambc import Controller, Position, Direction, EntityType
+
 import map_info
 from log import DRAW_DEBUG, log
 import comms_positional
+
 #type = 0:launch, 1:explore, 2:harvest, 3:route
 POS_BITS = 12
 SYM_BITS = 3
@@ -93,7 +95,7 @@
     sender_dir = _DIRS_8[sender_dir_idx]
     dx, dy = map_info._DIRECTION_DELTAS[sender_dir]
     sender_pos = Position(pos.x + dx, pos.y + dy)
-    return (val, sender_pos)
+    return (val, pos, sender_pos)
 
 
 def get_new_messages():
@@ -141,6 +143,8 @@
     best = None # (priority, pos, tile_id)
 
     for pos in adjacent_tiles:
+        if pos == rc.get_position():
+            continue
         if _is_bad_marker_spot(pos):
             continue
 
@@ -167,7 +171,7 @@
                 best = (1, pos, tile_id)
 
         # Priority 2: replace own road
-        elif (entity_type == EntityType.ROAD and not rc.get_tile_builder_bot_id(pos)):
+        elif (entity_type == EntityType.ROAD and not map_info.has_builder_bot(pos)):
             if best is None or best[0] > 2:
                 best = (2, pos, tile_id)
 
@@ -175,14 +179,13 @@
     if best:
         priority, pos, tile_id = best
         sym = get_sym_bits()
-        sample_bits = 0
-        # sample_bits = comms_positional.encode_sample_bits(pos, sym)
+        sample_bits = comms_positional.encode_sample_bits(pos, sym)
         sender_dir = pos.direction_to(map_info._my_pos)
         sender_loc = _DIR_TO_IDX.get(sender_dir, 0)
         val = encode(target_idx, type, sym, sample_bits, sender_loc)
 
         _my_markers.discard(tile_id)
-        if tile_id is not None and rc.can_destroy(pos):
+        if tile_id is not None and not map_info.has_builder_bot(pos) and rc.can_destroy(pos):
             rc.destroy(pos)
             
             # Don't bother updating map if we replaced marker with marker
```

### `comms_positional.py`

```diff
--- bots/Lethe_baseline/comms_positional.py	2026-04-18 20:19:30
+++ bots/Lethe/comms_positional.py	2026-04-17 02:01:47
@@ -1,12 +1,10 @@
-from __future__ import annotations
-
 from cambc import Position
 
 import comms_stats
 import map_info
 from log import log
 
-COMMS_SAMPLE_DISTANCE = 8
+COMMS_SAMPLE_DISTANCE = 7
 
 OFFSETS = (
     (0, 0),
@@ -80,7 +78,7 @@
     return map_info._IDX_ENV_ORE_TI
 
 def encode_sample_bits(marker_pos: Position, sym_bits: int) -> int:
-    corresponding = get_corresponding_pos_by_symmetry(marker_pos, sym_bits)
+    corresponding = get_corresponding_pos(marker_pos)
     env_mask = map_info._bm_env[_sample_env_idx(marker_pos)]
     seen = map_info._bm_seen
     width = map_info._width
@@ -97,7 +95,7 @@
     return result
 
 def decode_sample_positions(marker_pos: Position, sample_bits: int, sym_bits: int):
-    corresponding = get_corresponding_pos_by_symmetry(marker_pos, sym_bits)
+    corresponding = get_corresponding_pos(marker_pos)
     for i, (dx, dy) in enumerate(OFFSETS):
         if not ((sample_bits >> i) & 1):
             continue
```

### `comms_stats.py`

```diff
--- bots/Lethe_baseline/comms_stats.py	2026-04-18 20:19:30
+++ bots/Lethe/comms_stats.py	2026-04-17 02:01:47
@@ -1,10 +1,8 @@
-from __future__ import annotations
+from cambc import Controller
 
 import json
 from pathlib import Path
 
-from cambc import Controller
-
 PROFILE_DIR = Path("profiles")
 SUMMARY_PATH = PROFILE_DIR / "comm_stats_summary.txt"
 DETAIL_PREFIX = "comm_stats_unit_"
```

### `log.py`

```diff
--- bots/Lethe_baseline/log.py	2026-04-18 20:19:30
+++ bots/Lethe/log.py	2026-04-17 02:37:32
@@ -1,5 +1,5 @@
-DEBUG_LOGGING = False
-DRAW_DEBUG = False
+DEBUG_LOGGING = True
+DRAW_DEBUG = True
 
 if DEBUG_LOGGING:
     def log(*args, **kwargs):
```

### `main.py`

```diff
--- bots/Lethe_baseline/main.py	2026-04-18 20:19:30
+++ bots/Lethe/main.py	2026-04-19 01:53:03
@@ -19,6 +19,7 @@
 
 ENABLE_PROFILER = False
 ENABLE_COMMS_STATS = False
+ENABLE_VIS = False  # emit ##VIS## grids to stdout for the Rust replay viewer
 
 if ENABLE_PROFILER or ENABLE_COMMS_STATS:
     import cProfile
@@ -27,9 +28,72 @@
     import shutil
 
     PROFILE_DIR = pathlib.Path("profiles")
-    
+
     comms_stats.ENABLED = ENABLE_COMMS_STATS
 
+if ENABLE_VIS:
+    from visualiser import (
+        BoolGrid, Colour, FOG, Palette, PaletteStop, Tiles, TRANSPARENT, emit,
+    )
+
+    def _p(r: int, g: int, b: int, a: int) -> Palette:
+        return Palette(stops=[
+            PaletteStop(t=False, colour=TRANSPARENT),
+            PaletteStop(t=True, colour=Colour(r, g, b, a)),
+        ])
+
+    P_CONV_LOADED = _p(100, 255, 100, 140)
+    P_DEAD_END    = _p(255, 150,   0, 180)
+    P_CONV_STUCK  = _p(200,   0, 200, 180)
+    P_THREAT      = _p(255,  50,  50, 140)
+    P_TURRET_ADJ  = _p(255, 120,  60, 120)
+
+
+def _bm_to_bool_grid(bm: int, total: int) -> list[bool]:
+    """Bitmask → flat row-major bool list of length `total`. Bit `x + y*w`
+    maps to index `x + y*w`, matching tile indexing used throughout.
+
+    Fast path: format the int as a 0-padded binary string (C-implemented),
+    reverse to get LSB-first ordering, then one char comparison per tile.
+    ~10× faster than a per-tile shift on large ints for 60×60+ maps."""
+    if not bm:
+        return [False] * total
+    bm &= (1 << total) - 1
+    return [c == '1' for c in format(bm, f'0{total}b')[::-1]]
+
+
+def _mask_positions(bm: int, w: int) -> list[tuple[int, int]]:
+    """Bitmask → list of (x, y) positions. Used for the Tiles overlay, which
+    is much cheaper than a full BoolGrid when the set is sparse."""
+    positions = []
+    while bm:
+        lsb = bm & -bm
+        n = lsb.bit_length() - 1
+        positions.append((n % w, n // w))
+        bm ^= lsb
+    return positions
+
+
+def _emit_vis() -> None:
+    """Emit this unit's belief state. Each unit is sandboxed, so its
+    map_info globals reflect only what *it* has personally seen. In the
+    viewer, per-unit toggles show each bot's individual view of the world."""
+    w = map_info._width
+    total = w * map_info._height
+    board = map_info._board_mask
+
+    emit(
+        fog=BoolGrid(_bm_to_bool_grid(~map_info._bm_seen & board, total), palette=FOG),
+        conv_loaded=BoolGrid(_bm_to_bool_grid(map_info._bm_conv_loaded, total), palette=P_CONV_LOADED),
+        dead_end=BoolGrid(_bm_to_bool_grid(map_info._bm_dead_end, total), palette=P_DEAD_END),
+        conv_stuck=BoolGrid(_bm_to_bool_grid(map_info._bm_conv_stuck, total), palette=P_CONV_STUCK),
+        threat=BoolGrid(_bm_to_bool_grid((map_info._bm_enemy_soft_threat | map_info._bm_enemy_hard_threat), total), palette=P_THREAT),
+        turret_adj=BoolGrid(_bm_to_bool_grid(map_info._bm_enemy_launch_adj, total), palette=P_TURRET_ADJ),
+        friendly_bots=Tiles(_mask_positions(map_info._bm_friendly_bots, w)),
+        enemy_bots=Tiles(_mask_positions(map_info._bm_enemy_bots, w)),
+    )
+
+
 SPAWN_TURN = -2
 
 
@@ -39,8 +103,9 @@
         self.me: ModuleType
 
         if ENABLE_PROFILER:
-            self.profiler = None
             self.profiler_path = None
+            self.accumulated_stats: pstats.Stats | None = None
+            self.timeout_count = 0
 
     def _prepare_profile_dir(self, c: Controller) -> None:
         if not (ENABLE_PROFILER or ENABLE_COMMS_STATS):
@@ -60,16 +125,14 @@
             comms_stats.prepare_dir()
 
     def _write_profile(self) -> None:
-        if not ENABLE_PROFILER or self.profiler is None or self.profiler_path is None:
+        if not ENABLE_PROFILER or self.accumulated_stats is None or self.profiler_path is None:
             return
 
-        stats = pstats.Stats(self.profiler)
-
         # stats.stats:
         # key   = (filename, lineno, funcname)
         # value = (cc, nc, tt, ct, callers)
         # tt = tottime, ct = cumtime
-        rows = list(stats.stats.items())
+        rows = list(self.accumulated_stats.stats.items())
         rows.sort(key=lambda item: item[1][2], reverse=True)  # sort by tottime
 
         total_calls = sum(v[1] for _, v in rows)
@@ -77,8 +140,9 @@
         total_cumtime = sum(v[3] for _, v in rows)
 
         with self.profiler_path.open("w", encoding="utf-8") as f:
-            f.write("Profile sorted by total time (tottime)\n")
+            f.write("Profile sorted by total time (tottime) — timed-out turns only\n")
             f.write(f"Unit profile: {self.profiler_path.name}\n")
+            f.write(f"Timed-out turns: {self.timeout_count}\n")
             f.write(f"Total calls: {total_calls}\n")
             f.write(f"Total tottime: {total_tottime * 1_000_000:.3f} us\n")
             f.write(f"Total cumtime: {total_cumtime * 1_000_000:.3f} us\n")
@@ -107,14 +171,11 @@
 
             if ENABLE_PROFILER:
                 self.profiler_path = PROFILE_DIR / f"unit_{c.get_id()}.txt"
-                self.profiler = cProfile.Profile()
 
         if SPAWN_TURN == -2:
             SPAWN_TURN = c.get_current_round() - 1
 
-        if ENABLE_PROFILER and self.profiler is not None:
-            self.profiler.enable()
-
+        turn_profiler = None
         try:
             start_time = time.perf_counter_ns()
             etype = c.get_entity_type()
@@ -140,8 +201,18 @@
                 self.me.init(c)
                 self.initialized = True
 
+            if ENABLE_PROFILER:
+                turn_profiler = cProfile.Profile()
+                turn_profiler.enable()
+
             self.me.run()
 
+            if ENABLE_PROFILER and turn_profiler is not None:
+                turn_profiler.disable()
+
+            if ENABLE_VIS:
+                _emit_vis()
+
             end_time = time.perf_counter_ns()
             elapsed_us = end_time - start_time
 
@@ -156,21 +227,21 @@
                     file=sys.stderr,
                 )
                 c.draw_indicator_line(Position(0, 0), c.get_position(), 255, 0, 0)
-            #     if ENABLE_PROFILER and self.profiler is not None:
-            #         self.profiler.disable()
-            #         self._write_profile()
-            # else:
-            #     if ENABLE_PROFILER and self.profiler is not None:
-            #         self.profiler.disable()
-                    # self.profiler.clear()
+                if ENABLE_PROFILER and turn_profiler is not None:
+                    self.timeout_count += 1
+                    import io
+                    turn_stats = pstats.Stats(turn_profiler, stream=io.StringIO())
+                    if self.accumulated_stats is None:
+                        self.accumulated_stats = turn_stats
+                    else:
+                        self.accumulated_stats.add(turn_profiler)
+                    self._write_profile()
 
         except Exception as e:
+            if ENABLE_PROFILER and turn_profiler is not None:
+                turn_profiler.disable()
             print("Error:", e)
             print(f"Error: {e}", file=sys.stderr)
             c.draw_indicator_line(Position(-100, -100), c.get_position(), 255, 0, 0)
             traceback.print_exc(file=sys.stdout)
             traceback.print_exc(file=sys.stderr)
-
-        if ENABLE_PROFILER and self.profiler is not None:
-            self.profiler.disable()
-            self._write_profile()
```

### `map_info.py`

```diff
--- bots/Lethe_baseline/map_info.py	2026-04-18 20:19:30
+++ bots/Lethe/map_info.py	2026-04-19 02:03:50
@@ -1,7 +1,5 @@
-from __future__ import annotations
-from typing import Optional, Set, Tuple
-from cambc import Controller, Position, Environment, EntityType, Team, Direction, ResourceType, GameError, GameConstants
-from collections import deque
+from cambc import Controller, Position, Environment, EntityType, Team, Direction, ResourceType, GameConstants
+
 import pathing
 import units.builder as builder
 import comms
@@ -10,13 +8,17 @@
 _HAS_DIRECTION  = frozenset(e for e in (EntityType.ARMOURED_CONVEYOR, EntityType.BREACH, EntityType.CONVEYOR, EntityType.GUNNER, EntityType.SENTINEL, EntityType.SPLITTER))
 _CONVEYOR_TYPES = frozenset(
     e for e in (
-        EntityType.CONVEYOR, 
+        EntityType.CONVEYOR,
         EntityType.ARMOURED_CONVEYOR,
-        EntityType.BRIDGE, 
+        EntityType.BRIDGE,
         EntityType.SPLITTER
     )
 )
 
+# Max tiles a resource propagates along a conveyor chain per scan, and max tiles
+# ahead to invalidate when a conveyor changes. Stops earlier at intersections.
+_CONV_PROP_DEPTH = 3
+
 _ACCEPT_ORE = frozenset(
     e for e in (
         EntityType.CONVEYOR, 
@@ -164,7 +166,9 @@
 _building_hp: list[int] = []
 _building_dir: list[int] = []
 _building_conv_target: list[int] = []
-_conv_reverse: list[int] = []   # reverse[tn] = bitmask of my conveyors whose output target is tile tn
+_building_team_idx: list[int] = []  # team index per tile (-1 = no building)
+_conv_reverse: list[int] = []   # reverse[tn] = bitmask of conveyors (either team) whose output target is tile tn
+_env_idx_by_tile: list[int] = []  # env index per tile (0=empty by default)
 
 # Bitmask lists indexed by _ET_INT / _TM_INT / _ENV_INT
 _bm_et: list[int] = []      # one bitmask per EntityType
@@ -172,11 +176,13 @@
 _bm_env: list[int] = []     # one bitmask per Environment
 _bm_seen: int = 0           # seen tiles
 _bm_any_building: int = 0   # union of all tracked building bitmasks
+_board_mask: int = 0         # (1 << (w*h)) - 1, cached
 
 # Derived bitmasks
 _bm_blocked: int = 0            # walls + non-passable buildings + enemy core area
 _bm_conveyors: int = 0          # all conveyor-type buildings + my core area
 _bm_conveyor_targets: int = 0   # output target tiles of conveyors
+_bm_conv_by_dir: list[int] = []  # per facing (0..7): CONVEYOR|ARMOURED_CONVEYOR tiles facing that direction
 _bm_my_core_area: int = 0       # my core 3x3
 _bm_their_core_area: int = 0    # enemy core 3x3
 _bm_enemy_launch_adj: int = 0   # tiles adjacent to enemy launchers
@@ -186,10 +192,15 @@
 _bm_conv_raw_ax: int = 0        # conveyors observed containing raw axionite
 _bm_conv_ti: int = 0            # conveyors observed containing titanium
 _bm_conv_refined: int = 0       # conveyors observed containing refined axionite
+_bm_conv_stuck: int = 0         # conveyors observed holding the same resource stack
+                                # across consecutive observations (not moving)
+_conveyor_resource_id: list[int] = []  # per-tile: last-observed resource stack id (0 = none)
 _bm_ti_fed: int = 0             # targets of conveyors believed to carry titanium
 _bm_ax_fed: int = 0             # targets of conveyors believed to carry refined axionite
-_bm_dead_end: int = 0           # routable conveyors whose output is not connected to ore-accepting network
-_bm_enemy_turret_threat: int = 0  # tiles enemy turrets can shoot
+_bm_dead_end: int = 0           # tiles that dead-end conveyors point into (output tiles)
+_bm_enemy_soft_threat: int = 0    # tiles enemy sentinels can shoot (low dps)
+_bm_enemy_hard_threat: int = 0    # tiles enemy gunners/breaches can shoot (high dps)
+_bm_my_gunner_claims: int = 0     # tiles already covered by one of my gunners' current ray
 _bm_visible: int = 0              # tiles visible this turn
 _nearby_tiles: list = []           # cached rc.get_nearby_tiles() for this round
 _nearby_tiles_pos = None           # position at which _nearby_tiles was computed
@@ -310,7 +321,10 @@
 def dir_at(x, y):
     return _INT_DIR[_building_dir[x+y*_width]]
 def conv_target_at(x, y):
-    return Position(_building_conv_target[x+y*_width]%_width, _building_conv_target[x+y*_width]//_width)
+    tn = _building_conv_target[x+y*_width]
+    if tn < 0:
+        return None
+    return Position(tn % _width, tn // _width)
 def is_conveyor(type):
     return type in _CONVEYOR_TYPES
 def is_turret(type):
@@ -416,23 +430,31 @@
     return result
 
 
-def _compute_enemy_turret_threat() -> int:
-    """Compute aggregate bitmask of all tiles enemy turrets can attack.
-    Uses bitmask shifting for breach/sentinel (no wall blocking).
-    Uses per-turret ray for gunner (wall blocking)."""
+def _compute_enemy_turret_threat() -> tuple[int, int]:
+    """Compute (soft, hard) threat bitmasks.
+
+    Soft: sentinels (low dps).
+    Hard: gunners + breaches (high dps).
+
+    Sentinel/breach use bitmask shifting (no wall blocking).
+    Gunner uses per-turret ray (wall blocking, current facing only — rotating
+    costs 10 Ti + 1 cooldown)."""
     w = _width
     h = _height
     enemy_idx = 1 - _my_team_idx
-    threat = 0
+    soft = 0
+    hard = 0
     building_dir = _building_dir
     bm_team_enemy = _bm_team[enemy_idx]
 
-    # Breach + Sentinel: aggregate with bitmask shifting per direction
-    for turret_idx, offsets_table in ((_IDX_BREACH, _BREACH_OFFSETS), (_IDX_SENTINEL, _SENTINEL_OFFSETS)):
+    # Sentinel (soft) + Breach (hard): aggregate with bitmask shifting per direction
+    for turret_idx, offsets_table, is_hard in (
+        (_IDX_SENTINEL, _SENTINEL_OFFSETS, False),
+        (_IDX_BREACH, _BREACH_OFFSETS, True),
+    ):
         turrets = _bm_et[turret_idx] & bm_team_enemy
         if not turrets:
             continue
-        # Split turrets by direction
         dir_masks = [0] * 8
         m = turrets
         while m:
@@ -441,6 +463,7 @@
             di = building_dir[n]
             dir_masks[di] |= lsb
             m ^= lsb
+        acc = 0
         for di in range(8):
             dm = dir_masks[di]
             if not dm:
@@ -451,11 +474,15 @@
                     continue
                 offset = dx + dy * w
                 if offset > 0:
-                    threat |= (dm & shift_mask) << offset
+                    acc |= (dm & shift_mask) << offset
                 else:
-                    threat |= (dm & shift_mask) >> (-offset)
+                    acc |= (dm & shift_mask) >> (-offset)
+        if is_hard:
+            hard |= acc
+        else:
+            soft |= acc
 
-    # Gunner: per-turret, all 8 rays (wall blocking)
+    # Gunner (hard): per-turret ray in current facing.
     gunners = _bm_et[_IDX_GUNNER] & bm_team_enemy
     if gunners:
         walls = _bm_env[_IDX_ENV_WALL]
@@ -465,23 +492,88 @@
             n = lsb.bit_length() - 1
             px = n % w
             py = n // w
-            for ray_di in range(8):
-                for dx, dy in _GUNNER_RAYS[ray_di]:
-                    nx, ny = px + dx, py + dy
-                    if not (0 <= nx < w and 0 <= ny < h):
-                        break
-                    bit = 1 << (nx + ny * w)
-                    if walls & bit:
-                        break
-                    threat |= bit
+            di = building_dir[n]
+            for dx, dy in _GUNNER_RAYS[di]:
+                nx, ny = px + dx, py + dy
+                if not (0 <= nx < w and 0 <= ny < h):
+                    break
+                bit = 1 << (nx + ny * w)
+                if walls & bit:
+                    break
+                hard |= bit
             m ^= lsb
 
-    return threat
+    return soft, hard
+
+
+def _compute_my_gunner_claims() -> int:
+    """Bitmask of tiles already covered by one of my gunners' current ray.
+    Recomputed each round so facing changes are picked up the round we see them."""
+    w = _width
+    h = _height
+    gunners = _bm_et[_IDX_GUNNER] & _bm_team[_my_team_idx]
+    if not gunners:
+        return 0
+    walls = _bm_env[_IDX_ENV_WALL]
+    building_dir = _building_dir
+    claims = 0
+    m = gunners
+    while m:
+        lsb = m & -m
+        n = lsb.bit_length() - 1
+        px = n % w
+        py = n // w
+        di = building_dir[n]
+        for dx, dy in _GUNNER_RAYS[di]:
+            nx, ny = px + dx, py + dy
+            if not (0 <= nx < w and 0 <= ny < h):
+                break
+            bit = 1 << (nx + ny * w)
+            if walls & bit:
+                break
+            claims |= bit
+        m ^= lsb
+    return claims
+
+
+def _clear_downstream_conv_bits(n: int, start_n: int, exclude: int,
+                                 bm_loaded: int, bm_ax: int, bm_ti: int, bm_ref: int):
+    """Conveyor at tile n has changed: invalidate predicted resource bits on up
+    to _CONV_PROP_DEPTH tiles ahead of it along its (old) chain. Stops when the
+    next tile isn't a conveyor, is in `exclude` (e.g. freshly observed this
+    round), or is an intersection (has other incoming conveyors)."""
+    if start_n < 0:
+        return bm_loaded, bm_ax, bm_ti, bm_ref
+    bm_et = _bm_et
+    conv_types = (bm_et[_IDX_CONVEYOR] | bm_et[_IDX_ARMOURED_CONVEYOR]
+                  | bm_et[_IDX_BRIDGE] | bm_et[_IDX_SPLITTER])
+    conv_reverse = _conv_reverse
+    building_conv_target = _building_conv_target
+    prev_bit = 1 << n
+    cur = start_n
+    for _ in range(_CONV_PROP_DEPTH):
+        cur_bit = 1 << cur
+        if not (conv_types & cur_bit):
+            break
+        if exclude & cur_bit:
+            break
+        if conv_reverse[cur] & ~prev_bit:
+            break
+        nmask = ~cur_bit
+        bm_loaded &= nmask
+        bm_ax &= nmask
+        bm_ti &= nmask
+        bm_ref &= nmask
+        prev_bit = cur_bit
+        cur = building_conv_target[cur]
+        if cur < 0:
+            break
+    return bm_loaded, bm_ax, bm_ti, bm_ref
 
 
 def update_at(pos: Position) -> None:
     """Re-scan a single tile from the controller and update all bitmasks. Call after any build/destroy."""
-    global _bm_blocked, _bm_conveyors, _bm_conveyor_targets, _bm_conv_loaded, _bm_conv_raw_ax, _bm_conv_ti, _bm_conv_refined, _bm_dead_end, _bm_damaged, _bm_very_damaged, _bm_any_building, _bm_harv_adj
+    global _bm_blocked, _bm_conveyors, _bm_conveyor_targets, _bm_conv_loaded, _bm_conv_raw_ax, _bm_conv_ti, _bm_conv_refined, _bm_dead_end, _bm_damaged, _bm_very_damaged, _bm_any_building, _bm_harv_adj, _bm_conv_stuck
     if not in_bounds(pos):
         return
 
@@ -500,18 +592,22 @@
             tn = _building_conv_target[n]
             if tn >= 0:
                 _bm_conveyor_targets &= ~(1 << tn)
-            if (_bm_team[_my_team_idx] & bit) and tn >= 0:
                 _conv_reverse[tn] &= ~bit
-        for i in range(_NUM_TEAM):
-            if _bm_team[i] & bit:
-                _bm_team[i] &= ~bit
-                break
+                _bm_conv_loaded, _bm_conv_raw_ax, _bm_conv_ti, _bm_conv_refined = _clear_downstream_conv_bits(
+                    n, tn, 0, _bm_conv_loaded, _bm_conv_raw_ax, _bm_conv_ti, _bm_conv_refined
+                )
+            if old_et_idx == _IDX_CONVEYOR or old_et_idx == _IDX_ARMOURED_CONVEYOR:
+                _bm_conv_by_dir[_building_dir[n]] &= ~bit
+        old_ti = _building_team_idx[n]
+        if old_ti >= 0:
+            _bm_team[old_ti] &= ~bit
         _bm_blocked &= ~bit
         _bm_conveyors &= ~bit
         _bm_conv_loaded &= ~bit
         _bm_conv_raw_ax &= ~bit
         _bm_conv_ti &= ~bit
         _bm_conv_refined &= ~bit
+        _bm_conv_stuck &= ~bit
         _bm_dead_end &= ~bit
         _bm_damaged &= ~bit
         _bm_very_damaged &= ~bit
@@ -519,7 +615,9 @@
         _building_et_idx[n] = -1
         _building_hp[n] = 0
         _building_dir[n] = 0
-        _building_conv_target[n] = 0
+        _building_conv_target[n] = -1
+        _building_team_idx[n] = -1
+        _conveyor_resource_id[n] = 0
 
     # Read current state from controller
     entity_id = rc.get_tile_building_id(pos)
@@ -548,6 +646,7 @@
     _building_hp[n] = rc.get_hp(entity_id)
     _building_dir[n] = _DIR_INT[direction] if direction else 0
     _building_conv_target[n] = (target.x + target.y * _width) if target else -1
+    _building_team_idx[n] = team_idx
 
     _bm_et[et_idx] |= bit
     _bm_team[team_idx] |= bit
@@ -556,6 +655,8 @@
     _freshly_loaded = False
     if _IS_CONVEYOR[et_idx]:
         _bm_conveyors |= bit
+        if et_idx == _IDX_CONVEYOR or et_idx == _IDX_ARMOURED_CONVEYOR:
+            _bm_conv_by_dir[_building_dir[n]] |= bit
         res = rc.get_stored_resource(entity_id)
         if res is not None:
             _bm_conv_loaded |= bit
@@ -572,10 +673,19 @@
                 _bm_conv_refined |= bit
                 _bm_conv_raw_ax &= ~bit
                 _bm_conv_ti &= ~bit
-        if _building_conv_target[n]:
+            # Stuck check: compare the stored resource stack id to last obs.
+            res_id = rc.get_stored_resource_id(entity_id)
+            if res_id is not None and res_id == _conveyor_resource_id[n]:
+                _bm_conv_stuck |= bit
+            else:
+                _bm_conv_stuck &= ~bit
+            _conveyor_resource_id[n] = res_id if res_id is not None else 0
+        else:
+            _bm_conv_stuck &= ~bit
+            _conveyor_resource_id[n] = 0
+        if _building_conv_target[n] >= 0:
             _bm_conveyor_targets |= (1 << _building_conv_target[n])
-            if team_idx == _my_team_idx:
-                _conv_reverse[_building_conv_target[n]] |= bit
+            _conv_reverse[_building_conv_target[n]] |= bit
 
     if _IS_BLOCKED[et_idx]:
         _bm_blocked |= bit
@@ -591,8 +701,9 @@
     if _freshly_loaded:
         res_ax = bool(_bm_conv_raw_ax & bit)
         res_ti = not res_ax and bool(_bm_conv_ti & bit)
-        tn = _building_conv_target[n]
-        for _ in range(3):
+        cur = n
+        for _ in range(_CONV_PROP_DEPTH):
+            tn = _building_conv_target[cur]
             if tn < 0:
                 break
             tbit = 1 << tn
@@ -609,7 +720,7 @@
                 _bm_conv_refined |= tbit
                 _bm_conv_raw_ax &= ~tbit
                 _bm_conv_ti &= ~tbit
-            tn = _building_conv_target[tn]
+            cur = tn
 
     # Refresh harvester adjacency if a harvester was added or removed
     has_harvester = _building_et_idx[n] == _IDX_HARVESTER
@@ -658,9 +769,9 @@
 def init(c: Controller):
     global _rc, _width, _height
     global _my_team, _my_team_idx
-    global _building_id, _building_et_idx, _building_hp, _building_dir, _building_conv_target, _conv_reverse
-    global _bm_et, _bm_team, _bm_env, _bm_seen, _bm_any_building
-    global _bm_blocked, _bm_conveyors, _bm_conveyor_targets
+    global _building_id, _building_et_idx, _building_hp, _building_dir, _building_conv_target, _building_team_idx, _conv_reverse, _env_idx_by_tile
+    global _bm_et, _bm_team, _bm_env, _bm_seen, _bm_any_building, _board_mask
+    global _bm_blocked, _bm_conveyors, _bm_conveyor_targets, _bm_conv_stuck, _conveyor_resource_id, _bm_conv_by_dir
     global _bm_my_core_area, _bm_their_core_area, _bm_enemy_launch_adj
     global _not_left_col, _not_right_col, _not_left_col_2, _not_right_col_2, _not_left_col_3, _not_right_col_3
     global _MAP_CENTER
@@ -671,12 +782,15 @@
     _height = _rc.get_map_height()
     _MAP_CENTER = Position(_width // 2, _height // 2)
     tiles = _width * _height
+    _board_mask = (1 << tiles) - 1
     _building_id          = [0] * tiles
     _building_et_idx      = [-1] * tiles
     _building_hp          = [0] * tiles
     _building_dir         = [0] * tiles
-    _building_conv_target = [0] * tiles
+    _building_conv_target = [-1] * tiles
+    _building_team_idx    = [-1] * tiles
     _conv_reverse         = [0] * tiles
+    _env_idx_by_tile      = [_IDX_ENV_EMPTY] * tiles
 
     _bm_et   = [0] * _NUM_ET
     _bm_team = [0] * _NUM_TEAM
@@ -686,6 +800,9 @@
     _bm_blocked = 0
     _bm_conveyors = 0
     _bm_conveyor_targets = 0
+    _bm_conv_stuck = 0
+    _conveyor_resource_id = [0] * tiles
+    _bm_conv_by_dir = [0] * 8
 
     # Column masks for safe bit-shifting (prevent wrap-around)
     left_col = 0
@@ -733,11 +850,7 @@
 
 def _env_at_idx(n):
     """Return the env list index for tile n."""
-    bit = 1 << n
-    for i in range(_NUM_ENV):
-        if _bm_env[i] & bit:
-            return i
-    return _IDX_ENV_EMPTY
+    return _env_idx_by_tile[n]
 
 def flip(pos: Position):
     if not _solved_sym:
@@ -768,8 +881,6 @@
     _bm_their_core_area = 0
     bm_et = _bm_et
     bm_team = _bm_team
-    num_et = _NUM_ET
-    num_team = _NUM_TEAM
     if _my_core is not None:
         n = _my_core.x+_my_core.y*_width
         my_team_idx = _my_team_idx
@@ -778,12 +889,15 @@
                 m = x+y*_width
                 bit = 1 << m
                 # Clear any old entity/team bits at this tile
-                for i in range(num_et):
-                    bm_et[i] &= ~bit
-                for i in range(num_team):
-                    bm_team[i] &= ~bit
+                old_et = _building_et_idx[m]
+                if old_et >= 0:
+                    bm_et[old_et] &= ~bit
+                old_ti = _building_team_idx[m]
+                if old_ti >= 0:
+                    bm_team[old_ti] &= ~bit
                 _building_id[m] = _building_id[n]
                 _building_et_idx[m] = _IDX_CORE
+                _building_team_idx[m] = my_team_idx
                 _building_hp[m] = _building_hp[n]
                 _bm_my_core_area |= bit
                 _bm_any_building |= bit
@@ -794,19 +908,22 @@
         enemy_team_idx = 1 - _my_team_idx
         for x in range(_their_core.x - 1, _their_core.x + 2):
             for y in range(_their_core.y - 1, _their_core.y + 2):
-                    m = x+y*_width
-                    bit = 1 << m
-                    for i in range(num_et):
-                        bm_et[i] &= ~bit
-                    for i in range(num_team):
-                        bm_team[i] &= ~bit
-                    _building_id[m] = _building_id[n]
-                    _building_et_idx[m] = _IDX_CORE
-                    _building_hp[m] = _building_hp[n]
-                    _bm_their_core_area |= bit
-                    _bm_any_building |= bit
-                    bm_et[_IDX_CORE] |= bit
-                    bm_team[enemy_team_idx] |= bit
+                m = x+y*_width
+                bit = 1 << m
+                old_et = _building_et_idx[m]
+                if old_et >= 0:
+                    bm_et[old_et] &= ~bit
+                old_ti = _building_team_idx[m]
+                if old_ti >= 0:
+                    bm_team[old_ti] &= ~bit
+                _building_id[m] = _building_id[n]
+                _building_et_idx[m] = _IDX_CORE
+                _building_team_idx[m] = enemy_team_idx
+                _building_hp[m] = _building_hp[n]
+                _bm_their_core_area |= bit
+                _bm_any_building |= bit
+                bm_et[_IDX_CORE] |= bit
+                bm_team[enemy_team_idx] |= bit
 
 def _compute_route_targets() -> int:
     """Bitmask of tiles the route state can path toward.
@@ -852,6 +969,10 @@
     ti_harv_adj = expand_manhattan(ti_harvesters) if ti_harvesters else 0
 
     dead_ends = 0
+    conv_loaded = _bm_conv_ti | _bm_conv_refined | _bm_conv_raw_ax
+    enemy_hard_non_road = bm_enemy & ~_bm_et[_IDX_MARKER] & ~_bm_et[_IDX_ROAD]
+    marker_mask = _bm_et[_IDX_MARKER]
+    seen_mask = _bm_seen
 
     mask = all_convs
     while mask:
@@ -859,17 +980,30 @@
         n = lsb.bit_length() - 1
         tn = conv_target[n]
         is_my_conv = bool(bm_my & lsb)
+        is_loaded = bool(conv_loaded & lsb)
 
         # Dead-end: output not pointing into an ore-accepting building
         if 0 <= tn < tiles:
             tbit = 1 << tn
+            # My conveyor pointing into enemy non-marker, non-road building:
+            # mark THIS conveyor so route rebuilds it in a different direction.
+            if is_my_conv and (enemy_hard_non_road & tbit):
+                dead_ends |= lsb
             # Enemy conveyors: NOT dead-end if pointing into enemy non-marker building
-            if not is_my_conv and (enemy_hard & tbit):
+            elif not is_my_conv and (enemy_hard & tbit):
                 pass
+            # Output is a marker: ignore (markers should not trigger routing)
+            elif marker_mask & tbit:
+                pass
+            # Output is unseen territory: not a dead end (we don't know what's there)
+            elif not (seen_mask & tbit):
+                pass
             elif not (ore_accepting & tbit):
-                dead_ends |= lsb
+                if is_loaded:
+                    dead_ends |= tbit
             elif (_bm_conv_raw_ax & lsb) and not (_bm_et[_IDX_FOUNDRY] & tbit) and (((_bm_conv_ti | _bm_conv_refined) & tbit) or (ti_harv_adj & tbit)):
-                dead_ends |= lsb
+                if is_loaded:
+                    dead_ends |= tbit
         else:
             dead_ends |= lsb
         mask ^= lsb
@@ -877,6 +1011,8 @@
     _bm_dead_end = dead_ends
 
     # --- Downstream: validate chains from empty conveyors ---
+    # Interleaves upstream propagation so subsequent empty conveyors that
+    # are already validated (as feeders of a prior chain) skip their walk.
     empty_convs = my_convs & ~_bm_conv_loaded
     if not empty_convs:
         return result
@@ -890,6 +1026,9 @@
         lsb = mask & -mask
         mask ^= lsb
 
+        if valid_convs & lsb:
+            continue
+
         chain = 0
         cur = lsb
         cur_n = cur.bit_length() - 1
@@ -920,46 +1059,46 @@
             elif not (bm_seen & tbit):
                 chain_valid = True
             elif (cur & bm_visible) and not (tbit & bm_visible):
-                # Conveyor in vision but target is not — treat as unseen
                 chain_valid = True
             break
 
         if chain_valid:
             valid_convs |= chain
+            # Immediate upstream propagation from this chain
+            to_visit = chain
+            visited = valid_convs
+            while to_visit:
+                next_visit = 0
+                m = to_visit
+                while m:
+                    b = m & -m
+                    n = b.bit_length() - 1
+                    feeders = reverse[n] & ~visited
+                    if feeders:
+                        visited |= feeders
+                        valid_convs |= feeders
+                        next_visit |= feeders
+                    m ^= b
+                to_visit = next_visit
 
-    # --- Upstream: propagate from valid conveyors ---
-    to_visit = valid_convs
-    visited = valid_convs
-    while to_visit:
-        next_visit = 0
-        m = to_visit
-        while m:
-            lsb = m & -m
-            n = lsb.bit_length() - 1
-            feeders = reverse[n] & ~visited
-            if feeders:
-                visited |= feeders
-                valid_convs |= feeders
-                next_visit |= feeders
-            m ^= lsb
-        to_visit = next_visit
-
     result |= valid_convs
+    # Stuck conveyors (resource hasn't moved across observations) are dead
+    # destinations — routing into them just adds to the backlog.
+    result &= ~_bm_conv_stuck
     return result
 
 def recompute_derived() -> None:
     """Rebuild derived bitmasks from the current tracked map state."""
     global _bm_blocked, _bm_conveyors, _bm_conveyor_targets, _bm_ti_fed, _bm_ax_fed
     global _bm_enemy_launch_adj, _bm_routable, _bm_route_targets
-    global _bm_enemy_turret_threat, _bm_harv_adj
+    global _bm_enemy_soft_threat, _bm_enemy_hard_threat, _bm_my_gunner_claims, _bm_harv_adj
 
-    width = _width
-    height = _height
     my_team_idx = _my_team_idx
     bm_et = _bm_et
     bm_team = _bm_team
     bm_env = _bm_env
     building_conv_target = _building_conv_target
+    building_dir = _building_dir
 
     # Conveyors (all conveyor-type buildings + my core area)
     _bm_conveyors = (
@@ -969,11 +1108,24 @@
         | bm_et[_IDX_SPLITTER]
     )
 
+    # Per-facing conveyor buckets (CONVEYOR + ARMOURED_CONVEYOR only)
+    new_conv_by_dir = [0] * 8
+    cmask = bm_et[_IDX_CONVEYOR] | bm_et[_IDX_ARMOURED_CONVEYOR]
+    while cmask:
+        lsb = cmask & -cmask
+        cn = lsb.bit_length() - 1
+        new_conv_by_dir[building_dir[cn]] |= lsb
+        cmask ^= lsb
+    for d in range(8):
+        _bm_conv_by_dir[d] = new_conv_by_dir[d]
+
     # Routable = my team's conveyor-type buildings
     _bm_routable = _bm_conveyors & bm_team[my_team_idx]
 
     _bm_route_targets = _compute_route_targets()
 
+    harv = bm_et[_IDX_HARVESTER]
+
     # Blocked = walls + non-passable buildings + enemy core area
     _bm_blocked = bm_env[_IDX_ENV_WALL]
     _bm_blocked |= bm_et[_IDX_HARVESTER] | bm_et[_IDX_FOUNDRY]
@@ -982,7 +1134,7 @@
     _bm_blocked |= bm_et[_IDX_BARRIER] & ~bm_team[my_team_idx]  # enemy barriers only
     _bm_blocked |= _bm_their_core_area
 
-    # Conveyor targets + fed bitmasks
+    # Conveyor targets + fed bitmasks — single pass over all conveyors
     _bm_conveyor_targets = 0
     _bm_ti_fed = 0
     _bm_ax_fed = 0
@@ -1004,32 +1156,22 @@
 
     # Enemy launcher adjacency
     enemy_launchers = bm_et[_IDX_LAUNCHER] & ~bm_team[my_team_idx]
-    _bm_enemy_launch_adj = 0
-    mask = enemy_launchers
-    while mask:
-        lsb = mask & -mask
-        ln = lsb.bit_length() - 1
-        lx = ln % width
-        ly = ln // width
-        for dx, dy in _DIRECTION_DELTAS.values():
-            nx = lx + dx
-            ny = ly + dy
-            if 0 <= nx < width and 0 <= ny < height:
-                _bm_enemy_launch_adj |= 1 << (nx + ny * width)
-        mask ^= lsb
+    _bm_enemy_launch_adj = expand_chebyshev(enemy_launchers) if enemy_launchers else 0
 
-    # Enemy turret threat
-    _bm_enemy_turret_threat = _compute_enemy_turret_threat()
+    # Enemy turret threat (soft = sentinel, hard = gunner + breach)
+    _bm_enemy_soft_threat, _bm_enemy_hard_threat = _compute_enemy_turret_threat()
 
+    # Tiles already covered by one of my gunners' current ray
+    _bm_my_gunner_claims = _compute_my_gunner_claims()
+
     # Harvester adjacency (for _is_bad_marker_spot)
-    harv = bm_et[_IDX_HARVESTER]
     _bm_harv_adj = expand_manhattan(harv) if harv else 0
 
 def update(recompute: bool = True) -> None:
     global _my_core, _their_core, _core_id, _solved_sym
     global _hor_sym, _ver_sym, _rot_sym
     global _rush_tiebroken, _predicted_enemy_core
-    global _bm_blocked, _bm_conveyors, _bm_conveyor_targets, _bm_enemy_launch_adj, _bm_routable, _bm_route_targets, _bm_conv_loaded, _bm_conv_raw_ax, _bm_conv_ti, _bm_conv_refined, _bm_dead_end, _bm_enemy_turret_threat, _bm_damaged, _bm_very_damaged, _conv_reverse, _bm_any_building
+    global _bm_blocked, _bm_conveyors, _bm_conveyor_targets, _bm_enemy_launch_adj, _bm_routable, _bm_route_targets, _bm_conv_loaded, _bm_conv_raw_ax, _bm_conv_ti, _bm_conv_refined, _bm_dead_end, _bm_enemy_soft_threat, _bm_enemy_hard_threat, _bm_damaged, _bm_very_damaged, _conv_reverse, _bm_any_building, _bm_conv_stuck
     global _bm_seen, _bm_visible, _prev_pos, _nearby_tiles, _nearby_tiles_pos, _my_pos
     global _bm_friendly_bots, _bm_enemy_bots
     global _bm_others_5x5, _bm_others_3x3
@@ -1041,6 +1183,8 @@
     building_hp = _building_hp
     building_dir = _building_dir
     building_conv_target = _building_conv_target
+    building_team_idx = _building_team_idx
+    env_idx_by_tile = _env_idx_by_tile
 
     bm_et = _bm_et
     bm_team = _bm_team
@@ -1051,8 +1195,6 @@
     bm_conv_ti = _bm_conv_ti
     bm_conv_refined = _bm_conv_refined
     conv_reverse = _conv_reverse
-    my_team_idx_local = _my_team_idx
-    num_team = _NUM_TEAM
     is_conv = _IS_CONVEYOR
     has_dir = _HAS_DIR
     deltas_i = _DIRECTION_DELTAS_I
@@ -1080,6 +1222,7 @@
     rc_get_tile_building_id   = rc.get_tile_building_id
     rc_get_entity_type        = rc.get_entity_type
     rc_get_stored_resource    = rc.get_stored_resource
+    rc_get_stored_resource_id = rc.get_stored_resource_id
     rc_get_team               = rc.get_team
     rc_get_hp                 = rc.get_hp
     rc_get_direction          = rc.get_direction
@@ -1097,6 +1240,7 @@
             env = rc_get_tile_env(tile)
             env_idx = _ENV_INT[env]
             bm_env[env_idx] |= bit
+            env_idx_by_tile[n] = env_idx
             bm_seen |= bit
             if _solved_sym:
                 # Symmetry committed — skip verification and propagate env to the flipped tile.
@@ -1109,6 +1253,7 @@
                 fn = fx+fy*width
                 fbit = 1 << fn
                 bm_env[env_idx] |= fbit
+                env_idx_by_tile[fn] = env_idx
                 bm_seen |= fbit
             else:
                 rx = width-1-x
@@ -1137,15 +1282,24 @@
                 old_tn = building_conv_target[n]
                 if old_tn >= 0 and (conv_reverse[old_tn] & bit):
                     conv_reverse[old_tn] &= ~bit
-                building_conv_target[n] = 0
+                if is_conv[old_et_idx] and old_tn >= 0:
+                    bm_conv_loaded, bm_conv_raw_ax, bm_conv_ti, bm_conv_refined = _clear_downstream_conv_bits(
+                        n, old_tn, freshly_loaded, bm_conv_loaded, bm_conv_raw_ax, bm_conv_ti, bm_conv_refined
+                    )
+                if is_conv[old_et_idx]:
+                    _bm_conv_stuck &= ~bit
+                    _conveyor_resource_id[n] = 0
+                if old_et_idx == _IDX_CONVEYOR or old_et_idx == _IDX_ARMOURED_CONVEYOR:
+                    _bm_conv_by_dir[building_dir[n]] &= ~bit
+                building_conv_target[n] = -1
                 bm_et[old_et_idx] &= ~bit
                 _bm_any_building &= ~bit
-                for i in range(num_team):
-                    if bm_team[i] & bit:
-                        bm_team[i] &= ~bit
-                        break
+                old_ti = building_team_idx[n]
+                if old_ti >= 0:
+                    bm_team[old_ti] &= ~bit
                 building_id[n] = 0
                 building_et_idx[n] = -1
+                building_team_idx[n] = -1
             _bm_damaged &= ~bit
             _bm_very_damaged &= ~bit
             continue
@@ -1180,6 +1334,18 @@
                         bm_conv_refined |= bit
                         bm_conv_raw_ax &= ~bit
                         bm_conv_ti &= ~bit
+                    res_id = rc_get_stored_resource_id(entity_id)
+                    if res_id is not None and res_id == _conveyor_resource_id[n]:
+                        _bm_conv_stuck |= bit
+                    else:
+                        _bm_conv_stuck &= ~bit
+                    _conveyor_resource_id[n] = res_id if res_id is not None else 0
+                else:
+                    bm_conv_raw_ax &= ~bit
+                    bm_conv_ti &= ~bit
+                    bm_conv_refined &= ~bit
+                    _bm_conv_stuck &= ~bit
+                    _conveyor_resource_id[n] = 0
         elif comms._marker_id_at[n] == entity_id:
             # Already-seen marker — skip all controller calls
             continue
@@ -1197,13 +1363,22 @@
                     old_tn = building_conv_target[n]
                     if old_tn >= 0 and (conv_reverse[old_tn] & bit):
                         conv_reverse[old_tn] &= ~bit
-                    building_conv_target[n] = 0
+                    if is_conv[old_et_idx] and old_tn >= 0:
+                        bm_conv_loaded, bm_conv_raw_ax, bm_conv_ti, bm_conv_refined = _clear_downstream_conv_bits(
+                            n, old_tn, freshly_loaded, bm_conv_loaded, bm_conv_raw_ax, bm_conv_ti, bm_conv_refined
+                        )
+                    if is_conv[old_et_idx]:
+                        _bm_conv_stuck &= ~bit
+                        _conveyor_resource_id[n] = 0
+                    if old_et_idx == _IDX_CONVEYOR or old_et_idx == _IDX_ARMOURED_CONVEYOR:
+                        _bm_conv_by_dir[building_dir[n]] &= ~bit
+                    building_conv_target[n] = -1
                     bm_et[old_et_idx] &= ~bit
                     _bm_any_building &= ~bit
-                    for i in range(num_team):
-                        if bm_team[i] & bit:
-                            bm_team[i] &= ~bit
-                            break
+                    old_ti = building_team_idx[n]
+                    if old_ti >= 0:
+                        bm_team[old_ti] &= ~bit
+                    building_team_idx[n] = -1
                 building_id[n] = 0
                 building_et_idx[n] = -1
                 _bm_damaged &= ~bit
@@ -1217,12 +1392,20 @@
                 old_tn = building_conv_target[n]
                 if old_tn >= 0 and (conv_reverse[old_tn] & bit):
                     conv_reverse[old_tn] &= ~bit
+                if is_conv[old_et_idx] and old_tn >= 0:
+                    bm_conv_loaded, bm_conv_raw_ax, bm_conv_ti, bm_conv_refined = _clear_downstream_conv_bits(
+                        n, old_tn, freshly_loaded, bm_conv_loaded, bm_conv_raw_ax, bm_conv_ti, bm_conv_refined
+                    )
+                if is_conv[old_et_idx]:
+                    _bm_conv_stuck &= ~bit
+                    _conveyor_resource_id[n] = 0
+                if old_et_idx == _IDX_CONVEYOR or old_et_idx == _IDX_ARMOURED_CONVEYOR:
+                    _bm_conv_by_dir[building_dir[n]] &= ~bit
                 bm_et[old_et_idx] &= ~bit
                 _bm_any_building &= ~bit
-                for i in range(num_team):
-                    if bm_team[i] & bit:
-                        bm_team[i] &= ~bit
-                        break
+                old_ti = building_team_idx[n]
+                if old_ti >= 0:
+                    bm_team[old_ti] &= ~bit
 
             direction     = rc_get_direction(entity_id) if has_dir[et_idx] else None
             team_val = rc_get_team(entity_id)
@@ -1235,12 +1418,13 @@
                 target = Position(tile.x + _ddx, tile.y + _ddy)
             building_id[n] = entity_id
             building_et_idx[n] = et_idx
+            building_team_idx[n] = team_idx
             hp = rc_get_hp(entity_id)
             building_hp[n] = hp
             building_dir[n] = _DIR_INT[direction] if direction else 0
             new_tn = (target.x + target.y * width) if target else -1
             building_conv_target[n] = new_tn
-            if new_tn >= 0 and is_conv[et_idx] and team_idx == my_team_idx_local:
+            if new_tn >= 0 and is_conv[et_idx]:
                 conv_reverse[new_tn] |= bit
 
             # Set new bitmask bits
@@ -1258,6 +1442,8 @@
                 _bm_very_damaged &= ~bit
 
             if is_conv[et_idx]:
+                if et_idx == _IDX_CONVEYOR or et_idx == _IDX_ARMOURED_CONVEYOR:
+                    _bm_conv_by_dir[building_dir[n]] |= bit
                 res = rc_get_stored_resource(entity_id)
                 if res is not None:
                     bm_conv_loaded |= bit
@@ -1274,6 +1460,18 @@
                         bm_conv_refined |= bit
                         bm_conv_raw_ax &= ~bit
                         bm_conv_ti &= ~bit
+                    res_id = rc_get_stored_resource_id(entity_id)
+                    if res_id is not None and res_id == _conveyor_resource_id[n]:
+                        _bm_conv_stuck |= bit
+                    else:
+                        _bm_conv_stuck &= ~bit
+                    _conveyor_resource_id[n] = res_id if res_id is not None else 0
+                else:
+                    bm_conv_raw_ax &= ~bit
+                    bm_conv_ti &= ~bit
+                    bm_conv_refined &= ~bit
+                    _bm_conv_stuck &= ~bit
+                    _conveyor_resource_id[n] = 0
 
             if et is EntityType.CORE:
                 if _my_core is None and team_val == my_team:
@@ -1303,25 +1501,25 @@
                 bm_team[enemy_team_idx] |= pbit
                 building_hp[pos] = GameConstants.CORE_MAX_HP
             build_core_areas()
-        for x in range(width):
-            for y in range(height):
-                n = x+y*width
-                nbit = 1 << n
-                if bm_seen & nbit:
-                    if _ver_sym:
-                        flipped = (x)+(height-1-y)*width
-                    elif _hor_sym:
-                        flipped = (width-1-x)+(y)*width
-                    else:
-                        flipped = (width-1-x)+(height-1-y)*width
-                    fbit = 1 << flipped
-                    if not (bm_seen & fbit):
-                        # Copy env from source tile to flipped tile
-                        for env_i in range(_NUM_ENV):
-                            if bm_env[env_i] & nbit:
-                                bm_env[env_i] |= fbit
-                                break
-                        bm_seen |= fbit
+        remaining = bm_seen
+        while remaining:
+            lsb = remaining & -remaining
+            n = lsb.bit_length() - 1
+            remaining ^= lsb
+            x = n % width
+            y = n // width
+            if _ver_sym:
+                flipped = x+(height-1-y)*width
+            elif _hor_sym:
+                flipped = (width-1-x)+y*width
+            else:
+                flipped = (width-1-x)+(height-1-y)*width
+            fbit = 1 << flipped
+            if not (bm_seen & fbit):
+                src_env = env_idx_by_tile[n]
+                bm_env[src_env] |= fbit
+                env_idx_by_tile[flipped] = src_env
+                bm_seen |= fbit
         _bm_seen = bm_seen
 
     if _my_core:
@@ -1351,19 +1549,26 @@
                 else:
                     _predicted_enemy_core = hsym_core
 
+    fresh_conveyors = bm_et[_IDX_CONVEYOR] | bm_et[_IDX_ARMOURED_CONVEYOR] | bm_et[_IDX_BRIDGE] | bm_et[_IDX_SPLITTER]
     mask = freshly_loaded
     while mask:
         lsb = mask & -mask
+        mask ^= lsb
+        if not (fresh_conveyors & lsb):
+            continue
         n = lsb.bit_length() - 1
         res_ax = bool(bm_conv_raw_ax & lsb)
         res_ti = not res_ax and bool(bm_conv_ti & lsb)
-        tn = building_conv_target[n]
-        for _ in range(3):
+        cur = n
+        for _ in range(_CONV_PROP_DEPTH):
+            tn = building_conv_target[cur]
             if tn < 0:
                 break
             tbit = 1 << tn
-            if not (_bm_conveyors & tbit):
+            if not (fresh_conveyors & tbit):
                 break
+            if freshly_loaded & tbit:
+                break
             if res_ax:
                 if not ((bm_conv_ti | bm_conv_refined) & tbit):
                     bm_conv_raw_ax |= tbit
@@ -1375,8 +1580,7 @@
                 bm_conv_refined |= tbit
                 bm_conv_raw_ax &= ~tbit
                 bm_conv_ti &= ~tbit
-            tn = building_conv_target[tn]
-        mask ^= lsb
+            cur = tn
 
     _bm_conv_loaded = bm_conv_loaded
     _bm_conv_raw_ax = bm_conv_raw_ax
@@ -1462,9 +1666,20 @@
     bid = _rc.get_tile_building_id(pos)
     return bid is not None and _rc.get_entity_type(bid) is EntityType.MARKER
 
+def has_builder_bot(pos: Position, include_self: bool = False) -> bool:
+    if not in_bounds(pos):
+        return False
+    if include_self and pos == _my_pos:
+        return True
+    n = pos.x + pos.y * _width
+    bit = 1 << n
+    return bool((_bm_friendly_bots | _bm_enemy_bots) & bit)
+
 def can_place_at_restrictive(pos: Position):
     if not in_bounds(pos): 
         return False
+    if has_builder_bot(pos, include_self=True):
+        return False
     if is_tile_empty(pos): 
         return True
     if not _rc.can_destroy(pos): 
@@ -1497,6 +1712,11 @@
     # avoid_core = _rc.get_tile_building_id(_rc.get_position()) != _core_id
     mask = _bm_blocked
     if avoid_conveyors:
+        # Guards of a specific ore tile are unmasked at the call site in
+        # calculate_conveyor_path (scoped via _conv_reverse[start]). Here we
+        # treat all conveyors (including guards) as avoid, so a routing BFS
+        # can't step through an unrelated guard and feed the wrong resource
+        # into someone else's harvester.
         mask |= _bm_conveyors | _bm_conveyor_targets | _bm_my_core_area
     if avoid_ore:
         ore = _bm_env[_IDX_ENV_ORE_TI] | _bm_env[_IDX_ENV_ORE_AX]
@@ -1507,7 +1727,7 @@
     #     mask |= _bm_my_core_area
     if avoid_builders:
         mask |= _bm_friendly_bots | _bm_enemy_bots
-    threat = _bm_enemy_turret_threat
+    threat = _bm_enemy_hard_threat
     pos = _my_pos
     my_bit = 1 << (pos.x + pos.y * _width)
     if not (threat & my_bit):
```

### `pathing.py`

```diff
--- bots/Lethe_baseline/pathing.py	2026-04-18 20:19:30
+++ bots/Lethe/pathing.py	2026-04-19 01:53:03
@@ -1,18 +1,13 @@
-import heapq
-import map_info
-from cambc import Controller, Direction, Position, EntityType, ResourceType, Environment
-import comms
-import math
-from collections.abc import Collection
+from cambc import Controller, Direction, Position, EntityType
+
 import time
-import units.builder as builder
-import sys
-from functools import lru_cache
+
+import map_info
 from log import DRAW_DEBUG, log
 
 ALL_DIRS = list(Direction)
 ALL_DIRS_DELTAS = [(d, d.delta()) for d in ALL_DIRS]
-
+import units.builder as builder
 CARD_DIR = [
     Direction.NORTH,
     Direction.SOUTH,
@@ -27,8 +22,7 @@
 bridge_cost = 6
 barrier_cost = 15
 threat_cost = 20
-conveyor_end_cost = 10
-non_walkable_cost = 1
+conveyor_end_cost = 6
 
 
 
@@ -53,7 +47,7 @@
         if destroyed_barriers[p]+1 > current_round:
             continue
         id = rc.get_tile_building_id(p)
-        if id and rc.get_entity_type(id) == EntityType.ROAD and rc.get_team(id) == my_team and rc.can_destroy(p) and not rc.get_tile_builder_bot_id(p):
+        if id and rc.get_entity_type(id) == EntityType.ROAD and rc.get_team(id) == my_team and rc.can_destroy(p) and not map_info.has_builder_bot(p):
             rc.destroy(p)
             map_info.update_at(p)
         if rc.can_build_barrier(p):
@@ -68,32 +62,40 @@
         return 0
     if not others_mask:
         return claims
-    w = map_info._width
-    board = (1 << (w * map_info._height)) - 1
-    avoid = map_info.get_avoid(False, False, False)
-    passable = (~avoid & board) | claims
 
+    mi = map_info
+    w = mi._width
+    board = mi._board_mask
+    nlc = mi._not_left_col
+    nrc = mi._not_right_col
+    passable = (~mi.get_avoid(False, False, False) & board) | claims
+
     my_front = my_mask & passable
     other_front = others_mask & passable
-    my_claimed = my_front
-    other_claimed = other_front
-    all_claimed = my_claimed | other_claimed
 
-    while (claims & ~all_claimed) and (my_front or other_front):
+    my_claimed = my_front
+    all_claimed = my_front | other_front
+    remaining = claims & ~all_claimed
+    c = 0
+    while remaining and (my_front or other_front) and c < 10:
+        c += 1
         if my_front:
-            my_expand = map_info.expand_chebyshev(my_front) & passable & ~all_claimed
-            my_claimed |= my_expand
-            all_claimed |= my_expand
-            my_front = my_expand
-        if not (claims & ~all_claimed):
-            break
+            h = my_front | ((my_front & nrc) << 1) | ((my_front & nlc) >> 1)
+            my_front = (h | (h << w) | (h >> w)) & passable & ~all_claimed
+            my_claimed |= my_front
+            all_claimed |= my_front
+            remaining = claims & ~all_claimed
+            # builder.draw_mask(my_claimed, 0, 255, 0)
+            if not remaining:
+                break
+
         if other_front:
-            other_expand = map_info.expand_chebyshev(other_front) & passable & ~all_claimed
-            other_claimed |= other_expand
-            all_claimed |= other_expand
-            other_front = other_expand
+            h = other_front | ((other_front & nrc) << 1) | ((other_front & nlc) >> 1)
+            other_front = (h | (h << w) | (h >> w)) & passable & ~all_claimed
+            all_claimed |= other_front
+            remaining = claims & ~all_claimed
 
-    return my_claimed & claims
+    return ~(all_claimed & ~my_claimed) & claims
 
 class Pathing:
 
@@ -124,7 +126,7 @@
         if pos is None:
             pos = map_info._my_pos
         w = map_info._width
-        board = (1 << (w * map_info._height)) - 1
+        board = map_info._board_mask
         avoid = map_info.get_avoid(False, False, False)
         passable = (~avoid & board) |  targets
         start = 1 << (pos.x + pos.y * w)
@@ -264,7 +266,7 @@
         if not map_info.in_bounds(new_pos):
             return False
         id = rc.get_tile_building_id(new_pos)
-        if rc.get_tile_builder_bot_id(new_pos) != None:
+        if map_info.has_builder_bot(new_pos):
             return False
         if id and rc.get_entity_type(id) == EntityType.BARRIER and rc.can_destroy(new_pos) and rc.get_action_cooldown() == 0 and rc.get_global_resources()[0] > rc.get_road_cost()[0]:
             rc.destroy(new_pos)
@@ -304,8 +306,9 @@
         width = self.width
         height = self.height
         if avoid is None:
-            avoid = map_info.get_avoid(False, True, False)
+            avoid = map_info.get_avoid(False, False, False)
         avoid &= ~start_mask
+        builders_mask = map_info._bm_friendly_bots | map_info._bm_enemy_bots
         my_team_idx = map_info._my_team_idx
         barriers = map_info._bm_et[map_info._IDX_BARRIER] & map_info._bm_team[my_team_idx]
         barriers &= ~start_mask
@@ -315,7 +318,7 @@
         # builder.draw_mask(barriers, 0, 0, 255)
         threat = map_info._bm_enemy_launch_adj
         if avoid_turret:
-            threat |= map_info._bm_enemy_turret_threat
+            threat |= (map_info._bm_enemy_soft_threat | map_info._bm_enemy_hard_threat)
         if threat & start_mask:
             threat &= ~start_mask
 
@@ -323,41 +326,29 @@
                     | map_info._bm_conveyors
                     | map_info._bm_my_core_area
                     | map_info._bm_their_core_area)
-        nw_cost = 1
 
         start_time = time.perf_counter_ns()
 
         nlc = map_info._not_left_col
         nrc = map_info._not_right_col
         w = width
-        board = (1 << (w * height)) - 1
+        board = map_info._board_mask
         not_avoid = board & ~avoid
 
-        wk = walkable & board
-        nw = ~walkable & board
+        # 4 combo masks: barrier/no-barrier × threat/no-threat
+        nb_nt = board & ~barriers & ~threat
+        b_nt  = board & barriers & ~threat
+        nb_t  = board & ~barriers & threat
+        b_t   = board & barriers & threat
 
-        # 8 combo masks: walkable/non-walkable × barrier/no-barrier × threat/no-threat
-        wk_nb_nt = wk & ~barriers & ~threat
-        wk_b_nt  = wk & barriers & ~threat
-        wk_nb_t  = wk & ~barriers & threat
-        wk_b_t   = wk & barriers & threat
-        nw_nb_nt = nw & ~barriers & ~threat
-        nw_b_nt  = nw & barriers & ~threat
-        nw_nb_t  = nw & ~barriers & threat
-        nw_b_t   = nw & barriers & threat
-
-        max_c = 1 + nw_cost + barrier_cost + threat_cost
-        max_seed = nw_cost + barrier_cost + threat_cost
-        cycle_len = max(max_c, max_seed) + 1
+        max_c = 1 + barrier_cost + threat_cost
+        max_seed = barrier_cost + threat_cost
+        cycle_len = (max(max_c, max_seed) + 1) * 2
         frontier = [0] * cycle_len
-        frontier[0]                                          = target_mask & wk_nb_nt
-        frontier[nw_cost % cycle_len]                       |= target_mask & nw_nb_nt
-        frontier[barrier_cost % cycle_len]                  |= target_mask & wk_b_nt
-        frontier[(nw_cost + barrier_cost) % cycle_len]      |= target_mask & nw_b_nt
-        frontier[threat_cost % cycle_len]                   |= target_mask & wk_nb_t
-        frontier[(nw_cost + threat_cost) % cycle_len]       |= target_mask & nw_nb_t
-        frontier[(barrier_cost + threat_cost) % cycle_len]  |= target_mask & wk_b_t
-        frontier[(nw_cost + barrier_cost + threat_cost) % cycle_len] |= target_mask & nw_b_t
+        frontier[0]                                         = target_mask & nb_nt
+        frontier[barrier_cost % cycle_len]                 |= target_mask & b_nt
+        frontier[threat_cost % cycle_len]                  |= target_mask & nb_t
+        frontier[(barrier_cost + threat_cost) % cycle_len] |= target_mask & b_t
 
         effective_len = max_seed + 1
         visited = 0
@@ -367,6 +358,7 @@
             # log("move",i,file=sys.stderr)
             slot = i % cycle_len
             cur_frontier = frontier[slot] & ~visited
+            # builder.draw_mask(cur_frontier, (i*64)%256, 0, 0)
             frontier[slot] = 0
             visited_layers.append(cur_frontier)
             visited |= cur_frontier
@@ -383,8 +375,6 @@
                 vl_len = len(visited_layers)
 
                 extra_cost = 0
-                if start_bit & nw:
-                    extra_cost += nw_cost
                 if start_bit & barriers:
                     extra_cost += barrier_cost
                 if start_bit & threat:
@@ -402,20 +392,43 @@
                 cur_edge_dist = min(cx, cy, w_minus_1 - cx, h_minus_1 - cy)
                 in_edge_band = cur_edge_dist < 4
 
-                best_key = (2, 2, 2, 3)
-                chosen_prev = None
+                # For each non-blocked neighbor, scan layers from optimal (i - step_cost - extra_cost) to i
+                # to find the first (closest-to-target) layer where the tile is set
+                best_layer = -1
+                candidates = []
                 for dx, dy, step_cost in self._MOVE_OFFSETS:
+                    if dx == 0 and dy == 0:
+                        continue
                     px = cx - dx
                     py = cy - dy
                     if not (0 <= px < width and 0 <= py < height):
                         continue
-                    prev_layer = i - step_cost - extra_cost
-                    if prev_layer < 0 or prev_layer >= vl_len:
-                        continue
                     prev_bit = 1 << (py * width + px)
-                    if not (visited_layers[prev_layer] & prev_bit):
+                    if prev_bit & builders_mask:
                         continue
+                    if prev_bit & avoid:
+                        continue
+                    layer = -1
+                    start_l = (i - step_cost - extra_cost) % vl_len
+                    l = start_l
+                    while True:
+                        if visited_layers[l] & prev_bit:
+                            layer = l
+                            break
+                        if l == i:
+                            break
+                        l = (l + 1) % vl_len
+                    candidates.append((dx, dy, px, py, prev_bit, layer))
+                    if layer >= 0 and (best_layer < 0 or layer < best_layer):
+                        best_layer = layer
 
+                # Tiebreak among tiles at the best (lowest) layer
+                best_key = (2, 2, 2, 2, 3)
+                chosen_prev = None
+                for dx, dy, px, py, prev_bit, layer in candidates:
+                    if layer != best_layer:
+                        continue
+                    k_wk = 0 if (prev_bit & walkable) else 1
                     diag = dx != 0 and dy != 0
                     k0 = 0 if diag else 1
 
@@ -432,7 +445,7 @@
                         else:
                             k3 = 2
 
-                    key = (k0, k1, k2, k3)
+                    key = (k_wk, k0, k1, k2, k3)
                     if key < best_key:
                         best_key = key
                         chosen_prev = Position(px, py)
@@ -444,6 +457,7 @@
             if cur_frontier == 0:
                 i += 1
                 if i >= effective_len:
+                    print("bfs move miss")
                     return None
                 continue
 
@@ -456,14 +470,10 @@
             expanded = h | (h << w) | (h >> w)
             new = expanded & not_avoid & ~visited
 
-            frontier[(i + 1) % cycle_len]                                      |= new & wk_nb_nt
-            frontier[(i + 1 + nw_cost) % cycle_len]                             |= new & nw_nb_nt
-            frontier[(i + 1 + barrier_cost) % cycle_len]                        |= new & wk_b_nt
-            frontier[(i + 1 + nw_cost + barrier_cost) % cycle_len]              |= new & nw_b_nt
-            frontier[(i + 1 + threat_cost) % cycle_len]                         |= new & wk_nb_t
-            frontier[(i + 1 + nw_cost + threat_cost) % cycle_len]               |= new & nw_nb_t
-            frontier[(i + 1 + barrier_cost + threat_cost) % cycle_len]          |= new & wk_b_t
-            frontier[(i + 1 + nw_cost + barrier_cost + threat_cost) % cycle_len] |= new & nw_b_t
+            frontier[(i + 1) % cycle_len]                                     |= new & nb_nt
+            frontier[(i + 1 + barrier_cost) % cycle_len]                       |= new & b_nt
+            frontier[(i + 1 + threat_cost) % cycle_len]                        |= new & nb_t
+            frontier[(i + 1 + barrier_cost + threat_cost) % cycle_len]         |= new & b_t
             i += 1
 
     def bfs_route(self, start_mask: int, target_mask: int, avoid: int | None = None, end_cost_mask: int = 0):
@@ -474,12 +484,15 @@
         height = self.height
         if avoid is None:
             avoid = map_info.get_avoid(False, True, False)
-        # builder.draw_mask(avoid, 255, 0, 0)
 
         # builder.draw_mask(target_mask, 0, 255, 255)
-        avoid &= ~start_mask
+        # avoid &= ~start_mask
 
+        # builder.draw_mask(avoid, 255, 0, 0)
+        # builder.draw_mask(avoid_target, 255, 255, 0)
+
         start_time = time.perf_counter_ns()
+        # builder.draw_mask(target_mask, 0, 0, 255)
 
         if end_cost_mask:
             t_end = target_mask & end_cost_mask
@@ -488,6 +501,8 @@
             convs = map_info._bm_conveyors & ~map_info._bm_my_core_area
             t_end = target_mask & convs
             t_core = target_mask & ~convs
+        # builder.draw_mask(t_end, 255, 0, 0)
+        # builder.draw_mask(t_core, 0, 255, 0)
 
         max_c = bridge_cost
         max_seed = conveyor_end_cost
@@ -502,7 +517,7 @@
         nlc3 = map_info._not_left_col_3
         nrc3 = map_info._not_right_col_3
         w = width
-        board = (1 << (w * height)) - 1
+        board = map_info._board_mask
         not_avoid = board & ~avoid
 
         effective_len = max_seed + 1
@@ -516,7 +531,7 @@
             frontier[slot] = 0
             visited_layers.append(cur_frontier)
             visited |= cur_frontier
-
+            # builder.draw_mask(cur_frontier, (i*64)%256, 0, 0)
             hit = cur_frontier & start_mask
             if hit:
                 end_time = time.perf_counter_ns()
@@ -596,7 +611,7 @@
                 continue
             if not map_info.is_passable(p):
                 continue
-            if rc.is_in_vision(p) and rc.get_tile_builder_bot_id(p):
+            if map_info.has_builder_bot(p):
                 continue
             adj.add(p)
         if not adj:
@@ -606,7 +621,7 @@
                 adj.add(pos)
         return self.move_to(adj, **kwargs)
 
-    def move_to(self, target: Position | set[Position], avoid_empty: bool = False, avoid_turret: bool = True):
+    def move_to(self, target: Position | set[Position], avoid_turret: bool = True):
         log("move to", target)
         if isinstance(target, Position):
             target_set = {target}
@@ -614,9 +629,7 @@
             target_set = target
         if target_set != self.target_p:
             self.forget_launcher.clear()
-        avoid = map_info.get_avoid(False, True, False)
-        if avoid_empty:
-            avoid |= map_info._bm_seen & ~map_info._bm_any_building & ~map_info._bm_env[map_info._IDX_ENV_WALL]
+        avoid = map_info.get_avoid(False, False, False)
         my_pos = map_info._my_pos
         if target_set == self.target_p and my_pos == self.prev_pos and my_pos not in target_set and all(max(abs(my_pos.x - t.x), abs(my_pos.y - t.y)) > 1 for t in target_set):
             self.stuck_turns += 1
@@ -642,8 +655,8 @@
         s_pos, p_pos, _ = result
         if s_pos == p_pos:
             return False
-        if DRAW_DEBUG:
-            self.rc.draw_indicator_line(s_pos, p_pos, 0, 255, 255)
+        # if DRAW_DEBUG:
+        #     self.rc.draw_indicator_line(s_pos, p_pos, 0, 255, 255)
         return self.move(s_pos.direction_to(p_pos))
 
 
@@ -651,10 +664,9 @@
     def calculate_conveyor_path(self, start: Position, raw_axionite: bool, update: bool = False):
         log("conveyors from ", start, raw_axionite)
         w = self.width
-        if update:
-            target, avoid = self._get_conveyor_targets_and_avoid(raw_axionite, start.x + start.y * map_info._width)
-        else:
-            target, avoid = self._get_conveyor_targets_and_avoid(raw_axionite)
+        target, avoid = self._get_conveyor_targets_and_avoid(raw_axionite, start.x + start.y * w)
+        # Conveyors pointing into `start` are legitimate route starts; unmask them.
+        avoid &= ~map_info._conv_reverse[start.x + start.y * w]
         if not target:
             return None
         if not update:
@@ -666,15 +678,18 @@
             if start_mask == 0:
                 return None
         else:
-            start_mask = 1 << (map_info._building_conv_target[start.x + start.y * w])
-        end_cost_mask = self.raw_ax_foundry_sites() if raw_axionite else 0
-        result = self.bfs_route(start_mask, target, avoid, end_cost_mask=end_cost_mask)
+            start_mask = 1 << (start.x + start.y * w)
+        if raw_axionite:
+            end_cost_mask = self.raw_ax_foundry_sites() & ~map_info._bm_et[map_info._IDX_FOUNDRY]
+            result = self.bfs_route(start_mask, target, avoid, end_cost_mask=end_cost_mask)
+        else:
+            result = self.bfs_route(start_mask, target, avoid)
         if result is None:
             return None
         s_pos, p_pos, dist = result
-        if DRAW_DEBUG:
-            self.rc.draw_indicator_line(s_pos, p_pos, 255, 0, 255)
-            self.rc.draw_indicator_dot(s_pos, 255, 0, 255)
+        # if DRAW_DEBUG:
+        #     self.rc.draw_indicator_line(s_pos, p_pos, 255, 0, 255)
+        #     self.rc.draw_indicator_dot(s_pos, 255, 0, 255)
         return (s_pos, p_pos, dist)
 
     def conveyor_cost(self, dist, scaling=None):
@@ -718,27 +733,44 @@
         core_adj = map_info.expand_manhattan(map_info._bm_my_core_area)
         return (adj & ~blocked & at_least_two) | (core_adj & map_info._bm_conveyors & map_info._bm_team[my_idx] & map_info._bm_conv_ti)
 
-    def _get_conveyor_targets_and_avoid(
-        self, raw_axionite: bool, conveyor = None
-    ):
+    def _get_conveyor_targets_and_avoid(self, raw_axionite: bool, from_route: int):
         avoid = map_info.get_avoid(True, False, True)
         if raw_axionite:
             ti_harvesters = map_info.expand_manhattan(map_info._bm_et[map_info._IDX_HARVESTER] & map_info._bm_env[map_info._IDX_ENV_ORE_TI])
             target = self.raw_ax_foundry_sites()
+            avoid &= ~(1<<from_route)
             avoid |= ti_harvesters
             target |= map_info._bm_route_targets & map_info._bm_conv_raw_ax
-            if conveyor:
-                avoid &= ~(1<<map_info._building_conv_target[conveyor])
-                target &= ~(1<<conveyor)
+            # Existing allied foundries are free endpoints — routing into one
+            # avoids building a redundant foundry. Added to t_core (not
+            # end_cost_mask), so they seed at cost 0 like raw-ax chains.
+            target |= map_info._bm_et[map_info._IDX_FOUNDRY] & map_info._bm_team[map_info._my_team_idx]
+            # builder.draw_mask(avoid, 255, 0, 0)
+            # builder.draw_mask(target, 0, 255, 0)
+
             return target, avoid
         else:
             ax_harvesters = map_info.expand_manhattan(map_info._bm_et[map_info._IDX_HARVESTER] & map_info._bm_env[map_info._IDX_ENV_ORE_AX])
-            target = (map_info._bm_route_targets & ~map_info._bm_conv_raw_ax) | map_info._bm_my_core_area
+            target = (map_info._bm_route_targets & (map_info._bm_conv_ti | map_info._bm_conv_refined)) | map_info._bm_my_core_area
             target &= ~ax_harvesters
+            avoid &= ~(1<<from_route)
             avoid |= ax_harvesters
+            # Ti/refined chains must not run cardinally adjacent to axionite ore —
+            # a future ax harvester on that ore would pick up the wrong resource.
+            # Skip landlocked ax ore (4 cardinal neighbors are also ore): no ax
+            # harvester can get output through that tile, so it doesn't matter.
+            ax_ore = map_info._bm_env[map_info._IDX_ENV_ORE_AX]
+            if ax_ore:
+                w = map_info._width
+                all_ore = map_info._bm_env[map_info._IDX_ENV_ORE_TI] | ax_ore
+                nrc = map_info._not_right_col
+                nlc = map_info._not_left_col
+                landlocked = (all_ore
+                              & (all_ore >> 1 & nrc)
+                              & (all_ore << 1 & nlc)
+                              & (all_ore >> w)
+                              & (all_ore << w))
+                avoid |= map_info.expand_manhattan(ax_ore & ~landlocked)
             if not target:
                 return 0, 0
-            if conveyor:
-                avoid &= ~(1<<map_info._building_conv_target[conveyor])
-                target &= ~(1<<conveyor)
             return target, avoid
```

### `units/builder.py`

```diff
--- bots/Lethe_baseline/units/builder.py	2026-04-18 20:19:30
+++ bots/Lethe/units/builder.py	2026-04-18 23:37:42
@@ -1,9 +1,5 @@
-from cambc import Controller, Position, Direction, EntityType, Environment, GameError
+from cambc import *
 
-from enum import Enum
-import random
-import sys
-
 import map_info
 import pathing
 import comms
@@ -43,28 +39,48 @@
     current_round = rc.get_current_round()
     comms_positional.start_round_stats()
     w = map_info._width
-    for v, sender_pos, estimated_turn in comms.get_new_messages():
+    for v, marker_pos, sender_pos, estimated_turn in comms.get_new_messages():
         sym = comms.decode_sym(v)
         map_info.update_symmetry_from_comms(sym)
         if estimated_turn + 3 < current_round:
             continue
         idx = comms.decode_location(v)
         flag = comms.decode_type(v)
+        sample = comms.decode_sample_bits(v)
+        comms_positional.apply_message(marker_pos, sym, sample)
+        
         claimed_targets[flag] |= 1 << idx
         _target_rounds[flag][idx] = estimated_turn
         if map_info.in_bounds(sender_pos):
             sn = sender_pos.x + sender_pos.y * w
             claimed_senders[flag] |= 1 << sn
             _sender_rounds[flag][sn] = estimated_turn
+    # Tile-based prune: 3-turn expiry inside vision, 50-turn expiry outside.
+    vision_mask = 0
     for p in map_info._nearby_tiles:
-        idx = p.x + p.y * w
-        for i in range(len(claimed_targets)):
-            if idx in _target_rounds[i] and _target_rounds[i][idx] + 3 < current_round:
-                del _target_rounds[i][idx]
-                claimed_targets[i] &= ~(1 << idx)
-            if idx in _sender_rounds[i] and _sender_rounds[i][idx] + 3 < current_round:
-                del _sender_rounds[i][idx]
-                claimed_senders[i] &= ~(1 << idx)
+        vision_mask |= 1 << (p.x + p.y * w)
+    for i in range(len(claimed_targets)):
+        if i != 7:
+            # Heal flag stores enemy UIDs, not tile indices, so skip it here.
+            stale = [
+                k for k, r in _target_rounds[i].items()
+                if r + (3 if (vision_mask >> k) & 1 else 50) < current_round
+            ]
+            for k in stale:
+                del _target_rounds[i][k]
+                claimed_targets[i] &= ~(1 << k)
+        stale = [
+            k for k, r in _sender_rounds[i].items()
+            if r + (3 if (vision_mask >> k) & 1 else 50) < current_round
+        ]
+        for k in stale:
+            del _sender_rounds[i][k]
+            claimed_senders[i] &= ~(1 << k)
+    # Age-based prune for heal flag target claims (UIDs, not tiles).
+    stale_heal = [k for k, r in _target_rounds[7].items() if r + 3 < current_round]
+    for k in stale_heal:
+        del _target_rounds[7][k]
+        claimed_targets[7] &= ~(1 << k)
     comms_positional.flush_round_stats(current_round)
 def draw_mask(mask, r, g, b):
     if not DRAW_DEBUG:
@@ -78,8 +94,7 @@
     """Flood-fill Manhattan from both cores simultaneously.
     Tiles reached by my core first are my harvest zone."""
     w = map_info._width
-    h = map_info._height
-    board = (1 << (w * h)) - 1
+    board = map_info._board_mask
     walls = map_info._bm_env[map_info._IDX_ENV_WALL]
     passable = board & ~walls
 
@@ -113,6 +128,9 @@
     handle_comms()
     map_info.recompute_derived()
     pathing.rebuild_broken_barriers(rc)
+    # Debug: purple dots on stuck conveyors.
+    # if DRAW_DEBUG and map_info._bm_conv_stuck:
+        # draw_mask(map_info._bm_conv_stuck, 128, 0, 128)
     if map_info._my_core and not _harvest_zone_final:
         if map_info._solved_sym and map_info._predicted_enemy_core is not None:
             # Symmetry solved — compute Voronoi partition once
```

### `units/core.py`

```diff
--- bots/Lethe_baseline/units/core.py	2026-04-18 20:19:30
+++ bots/Lethe/units/core.py	2026-04-19 03:22:29
@@ -1,13 +1,44 @@
-from cambc import Controller, Position
+from cambc import Controller, Position, Environment, EntityType, GameError
+
 import map_info
 from log import log
 
 rc: Controller
 
 # --- Configurable ---
-SCALE_MULT = 0.5
+SCALE_MULT = 0.6
 
 
+def get_closest_titanium_tile() -> Position | None:
+    """Return the closest visible titanium ore without an allied harvester."""
+    core_pos = rc.get_position()
+    min_dist_sq = float('inf')
+    closest_ore = None
+
+    for pos in rc.get_nearby_tiles():
+        if rc.get_tile_env(pos) != Environment.ORE_TITANIUM:
+            continue
+
+        building_id = rc.get_tile_building_id(pos)
+        has_allied_harvester = False
+        if building_id is not None:
+            try:
+                building_type = rc.get_entity_type(building_id)
+                building_team = rc.get_team(building_id)
+                if building_type == EntityType.HARVESTER and building_team == rc.get_team():
+                    has_allied_harvester = True
+            except GameError:
+                pass
+
+        if not has_allied_harvester:
+            dist_sq = pos.distance_squared(core_pos)
+            if dist_sq < min_dist_sq:
+                min_dist_sq = dist_sq
+                closest_ore = pos
+
+    return closest_ore
+
+
 def _spawn_toward_center():
     """Spawn on the core tile closest to map center."""
     core_pos = rc.get_position()
@@ -27,17 +58,33 @@
 
 
 def run():
-    # if rc.get_current_round() == 100:
+    # if rc.get_current_round() == 1500:
     #     rc.resign()
-    titanium = rc.get_global_resources()[0]
-    axionite = rc.get_global_resources()[1]
+    round_num = rc.get_current_round()
+    core_pos = rc.get_position()
+
+    # --- Spawn towards closest titanium on turn 1 ---
+    if round_num == 1:
+        titanium_pos = get_closest_titanium_tile()
+        if titanium_pos is not None:
+            dx = max(-1, min(1, titanium_pos.x - core_pos.x))
+            dy = max(-1, min(1, titanium_pos.y - core_pos.y))
+            spawn_pos = Position(core_pos.x + dx, core_pos.y + dy)
+            if rc.can_spawn(spawn_pos):
+                rc.spawn_builder(spawn_pos)
+                return  # Only spawn 1 builder for turn 1
+
+    titanium, axionite = rc.get_global_resources()
     scaling = rc.get_scale_percent()
-    if scaling * SCALE_MULT + 300 < titanium:
+    if scaling * SCALE_MULT + 250 < titanium:
         _spawn_toward_center()
     if rc.get_current_round() < 1500 and titanium < 4 * rc.get_harvester_cost()[0]:
+        # Idea: change this to axionite - 2 before final submission
+        # Since other teams are also keeping 1 axionite in reserve for tiebreakers
         rc.convert(min(max(axionite - 1, 0), max((3 * rc.get_harvester_cost()[0] - titanium) // 4, 0)))
 
 
 def init(c: Controller):
     global rc
     rc = c
+    map_info.init(c)
```

### `units/states/attack.py`

```diff
--- bots/Lethe_baseline/units/states/attack.py	2026-04-18 20:19:30
+++ bots/Lethe/units/states/attack.py	2026-04-19 02:03:12
@@ -1,10 +1,11 @@
+from cambc import *
+
 import map_info
 import pathing
 from pathing import Pathing
 import comms
 import units.builder
-from cambc import *
-from log import log
+from log import DRAW_DEBUG, log
 
 
 rc: Controller = None
@@ -20,249 +21,360 @@
 
 
 BUILDING_SCORE = [0] * map_info._NUM_ET
-BUILDING_SCORE[map_info._IDX_CORE] = 100
-BUILDING_SCORE[map_info._IDX_HARVESTER] = 10
-BUILDING_SCORE[map_info._IDX_FOUNDRY] = 15
+BUILDING_SCORE[map_info._IDX_CORE] = 96
+BUILDING_SCORE[map_info._IDX_HARVESTER] = 12
+BUILDING_SCORE[map_info._IDX_FOUNDRY] = 16
 BUILDING_SCORE[map_info._IDX_GUNNER] = 20
 BUILDING_SCORE[map_info._IDX_SENTINEL] = 20
-BUILDING_SCORE[map_info._IDX_BREACH] = 25
-BUILDING_SCORE[map_info._IDX_LAUNCHER] = 15
+BUILDING_SCORE[map_info._IDX_BREACH] = 24
+BUILDING_SCORE[map_info._IDX_LAUNCHER] = 8
 BUILDING_SCORE[map_info._IDX_CONVEYOR] = 2
-BUILDING_SCORE[map_info._IDX_ARMOURED_CONVEYOR] = 3
-BUILDING_SCORE[map_info._IDX_BARRIER] = 1
+BUILDING_SCORE[map_info._IDX_ARMOURED_CONVEYOR] = 4
+BUILDING_SCORE[map_info._IDX_BARRIER] = 4
 BUILDING_SCORE[map_info._IDX_BRIDGE] = 2
 BUILDING_SCORE[map_info._IDX_SPLITTER] = 2
 
+_SCORED_NON_CORE_TYPES = [
+    (map_info._IDX_FOUNDRY, BUILDING_SCORE[map_info._IDX_FOUNDRY]),
+    (map_info._IDX_GUNNER, BUILDING_SCORE[map_info._IDX_GUNNER]),
+    (map_info._IDX_SENTINEL, BUILDING_SCORE[map_info._IDX_SENTINEL]),
+    (map_info._IDX_BREACH, BUILDING_SCORE[map_info._IDX_BREACH]),
+    (map_info._IDX_LAUNCHER, BUILDING_SCORE[map_info._IDX_LAUNCHER]),
+    (map_info._IDX_HARVESTER, BUILDING_SCORE[map_info._IDX_HARVESTER]),
+    (map_info._IDX_CONVEYOR, BUILDING_SCORE[map_info._IDX_CONVEYOR]),
+    (map_info._IDX_ARMOURED_CONVEYOR, BUILDING_SCORE[map_info._IDX_ARMOURED_CONVEYOR]),
+    (map_info._IDX_BARRIER, BUILDING_SCORE[map_info._IDX_BARRIER]),
+    (map_info._IDX_BRIDGE, BUILDING_SCORE[map_info._IDX_BRIDGE]),
+    (map_info._IDX_SPLITTER, BUILDING_SCORE[map_info._IDX_SPLITTER]),
+]
 
-def _get_loaders(pos):
-    """Return list of direction indices (0-7) from pos toward buildings that feed it."""
-    w = map_info._width
-    h = map_info._height
-    px, py = pos.x, pos.y
-    pos_n = px + py * w
-    loaders = []
+_NUM_PLANES = 13  # fits per-direction gunner scores (~500) summed across 8 dirs (~4000)
 
-    harvesters = map_info._bm_et[map_info._IDX_HARVESTER]
-    conveyors = (map_info._bm_et[map_info._IDX_CONVEYOR]
-                 | map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR])
+SCORE_THRESHOLD_FACTOR = 0.25
+MIN_ATTACK_SCORE = 16
+GUNNER_SCORE_MULTIPLIER = 4
+THREAT_PENALTY = 4
 
-    # Cardinal-adjacent harvesters
-    for di, (dx, dy) in zip([0, 2, 4, 6], [(0, -1), (1, 0), (0, 1), (-1, 0)]):
-        nx, ny = px + dx, py + dy
-        if 0 <= nx < w and 0 <= ny < h:
-            if harvesters & (1 << (nx + ny * w)):
-                loaders.append(di)
+cant_attack = 0
 
-    # Any neighbor conveyor whose output targets this tile
-    for di in range(8):
-        dx, dy = map_info._DIR_VECS[di]
-        nx, ny = px + dx, py + dy
-        if 0 <= nx < w and 0 <= ny < h:
-            nn = nx + ny * w
-            if (conveyors & (1 << nn)) and map_info._building_conv_target[nn] == pos_n:
-                if di not in loaders:
-                    loaders.append(di)
 
-    return loaders
+# ---------------------------------------------------------------------------
+# Bit-sliced score plane helpers
+# ---------------------------------------------------------------------------
 
+_SCORE_BITS_CACHE: dict = {}
 
-def get_best_direction(pos):
-    """Pick the best (direction, turret_type) for a turret at pos.
-    Blocked: turret cannot face toward a loading building.
-    Exception: gunner with 2+ loaders can face any direction.
-    Score = sum of BUILDING_SCORE for enemy buildings the turret can hit."""
-    w = map_info._width
-    h = map_info._height
-    px, py = pos.x, pos.y
+def _bits_of_score(c):
+    b = _SCORE_BITS_CACHE.get(c)
+    if b is None:
+        b = []
+        x, i = c, 0
+        while x:
+            if x & 1:
+                b.append(i)
+            x >>= 1
+            i += 1
+        _SCORE_BITS_CACHE[c] = b
+    return b
 
-    my_team_idx = map_info._my_team_idx
-    enemy_buildings = map_info._bm_team[1 - my_team_idx]
-    my_buildings = map_info._bm_team[my_team_idx]
-    walls = map_info._bm_env[map_info._IDX_ENV_WALL]
 
-    loaders = _get_loaders(pos)
-    loader_dirs = set(loaders)
-    sentinel_blocked = loader_dirs
-    breach_blocked = loader_dirs
-    gunner_blocked = set() if len(loaders) >= 2 else loader_dirs
+def _add_const_to_planes(planes, c, mask):
+    """Bit-sliced: add constant `c` to counters at every set bit of `mask`."""
+    if not mask or not c:
+        return
+    for i in _bits_of_score(c):
+        carry = planes[i] & mask
+        planes[i] ^= mask
+        j = i + 1
+        while carry and j < _NUM_PLANES:
+            new_carry = planes[j] & carry
+            planes[j] ^= carry
+            carry = new_carry
+            j += 1
 
-    my_foundries = map_info._bm_et[map_info._IDX_FOUNDRY] & my_buildings
-    adj_foundry = False
-    for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
-        nx, ny = px + dx, py + dy
-        if 0 <= nx < w and 0 <= ny < h and (my_foundries & (1 << (nx + ny * w))):
-            adj_foundry = True
-            break
 
-    best_b_dir, best_b_score = Direction.NORTH, -1
-    best_s_dir, best_s_score = Direction.NORTH, -1
-    best_g_dir, best_g_score = Direction.NORTH, -1
+def _read_score(planes, tile_n):
+    """Read the integer score stored at `tile_n` across the planes."""
+    score = 0
+    for i in range(_NUM_PLANES):
+        if (planes[i] >> tile_n) & 1:
+            score |= 1 << i
+    return score
 
-    for di in range(8):
-        # Breach score
-        if di not in breach_blocked:
-            core_counted = False
-            b_score = 0
-            for dx, dy in map_info._BREACH_OFFSETS[di]:
-                sx, sy = px + dx, py + dy
-                if 0 <= sx < w and 0 <= sy < h:
-                    sbit = 1 << (sx + sy * w)
-                    if enemy_buildings & sbit:
-                        et_idx = map_info._building_et_idx[sx + sy * w]
-                        if et_idx >= 0 and (not core_counted or et_idx != map_info._IDX_CORE):
-                            b_score += BUILDING_SCORE[et_idx]
-                            if et_idx == map_info._IDX_CORE:
-                                core_counted = True
-            if b_score > best_b_score:
-                best_b_score = b_score
-                best_b_dir = map_info._DIRECTIONS[di]
 
-        # Sentinel score
-        if di not in sentinel_blocked:
-            core_counted = False
-            s_score = 0
-            for dx, dy in map_info._SENTINEL_OFFSETS[di]:
-                sx, sy = px + dx, py + dy
-                if 0 <= sx < w and 0 <= sy < h:
-                    sbit = 1 << (sx + sy * w)
-                    if enemy_buildings & sbit:
-                        et_idx = map_info._building_et_idx[sx + sy * w]
-                        if et_idx >= 0 and (not core_counted or et_idx != map_info._IDX_CORE):
-                            s_score += BUILDING_SCORE[et_idx]
-                            if et_idx == map_info._IDX_CORE:
-                                core_counted = True
-            if s_score > best_s_score:
-                best_s_score = s_score
-                best_s_dir = map_info._DIRECTIONS[di]
+def _max_score_in_mask(planes, mask):
+    """Maximum counter value among tiles whose bit is set in `mask`. Bit-parallel."""
+    if not mask:
+        return 0
+    max_val = 0
+    cur = mask
+    for i in range(_NUM_PLANES - 1, -1, -1):
+        hi = planes[i] & cur
+        if hi:
+            max_val |= 1 << i
+            cur = hi
+    return max_val
 
-        # Gunner score — single ray, wall/friendly-blocked
-        if di not in gunner_blocked:
-            g_score = 0
-            for dx, dy in map_info._GUNNER_RAYS[di]:
-                sx, sy = px + dx, py + dy
-                if not (0 <= sx < w and 0 <= sy < h):
-                    break
-                sbit = 1 << (sx + sy * w)
-                if walls & sbit:
-                    break
-                if my_buildings & sbit:
-                    if not map_info._bm_et[map_info._IDX_ROAD] & sbit:
-                        break
-                if enemy_buildings & sbit:
-                    et_idx = map_info._building_et_idx[sx + sy * w]
-                    if et_idx >= 0:
-                        g_score += BUILDING_SCORE[et_idx]
-            g_score *= 5
-            if g_score > best_g_score:
-                best_g_score = g_score
-                best_g_dir = map_info._DIRECTIONS[di]
 
-    if adj_foundry:
-        if best_b_score > 0:
-            return best_b_dir, EntityType.BREACH, best_b_score
-        if best_s_score > 0:
-            return best_s_dir, EntityType.SENTINEL, best_s_score
-        return best_g_dir, EntityType.GUNNER, best_g_score
+def _ge_threshold_mask(planes, threshold, candidates):
+    """Bitmask of tiles in `candidates` whose counter >= `threshold`. Bit-parallel."""
+    if threshold <= 0:
+        return candidates
+    eq = candidates
+    gt = 0
+    for i in range(_NUM_PLANES - 1, -1, -1):
+        p = planes[i]
+        if (threshold >> i) & 1:
+            eq &= p
+        else:
+            gt |= eq & p
+            eq &= ~p
+    return gt | eq
 
-    if best_s_score >= best_g_score:
-        return best_s_dir, EntityType.SENTINEL, best_s_score
-    return best_g_dir, EntityType.GUNNER, best_g_score
 
+# ---------------------------------------------------------------------------
+# Sentinel: returns 8 plane-lists, one per facing direction
+# ---------------------------------------------------------------------------
 
-def _my_turret_coverage():
-    """Bitmask of all tiles my turrets can attack (regardless of ammo)."""
-    my_team_idx = map_info._my_team_idx
-    my_team_bm = map_info._bm_team[my_team_idx]
+def _compute_sentinel_dir_scores(enemy_team_bm, threat, sentinel_masks):
+    """For each of 8 facing directions, compute a per-tile sentinel score plane
+    list. Returns: list of 8 plane-lists (list[list[int]]). Reading position n
+    from the d-th inner list yields the sentinel's total damage-score if
+    placed at n facing direction d — but ONLY if n is a valid placement tile
+    for that direction (per `sentinel_masks[d]`); otherwise the score reads 0.
+
+    Scores sum BUILDING_SCORE for each enemy building in the sentinel's
+    offset pattern. THREAT_PENALTY is baked in: non-threat tiles get
+    +THREAT_PENALTY so threat tiles read THREAT_PENALTY lower."""
     w = map_info._width
-    h = map_info._height
-    coverage = 0
+    shift_masks = map_info._turret_shift_masks
+    bm_et = map_info._bm_et
+    offsets_table = map_info._SENTINEL_OFFSETS
 
-    for turret_idx, offsets_table in ((map_info._IDX_BREACH, map_info._BREACH_OFFSETS),
-                                      (map_info._IDX_SENTINEL, map_info._SENTINEL_OFFSETS)):
-        turrets = map_info._bm_et[turret_idx] & my_team_bm
-        if not turrets:
-            continue
-        dir_masks = [0] * 8
-        m = turrets
-        while m:
-            lsb = m & -m
-            n = lsb.bit_length() - 1
-            di = map_info._building_dir[n]
-            dir_masks[di] |= lsb
-            m ^= lsb
-        for di in range(8):
-            dm = dir_masks[di]
-            if not dm:
+    core_mask = bm_et[map_info._IDX_CORE] & enemy_team_bm
+    core_score = BUILDING_SCORE[map_info._IDX_CORE]
+
+    # Group non-core enemy types by score; within a single offset, the masks
+    # for types sharing a score are disjoint (one building per tile), so we
+    # can OR-union them and do one _add_const_to_planes per (offset, score).
+    score_to_union = {}
+    for t_idx, s in _SCORED_NON_CORE_TYPES:
+        bm_t = bm_et[t_idx] & enemy_team_bm
+        if bm_t:
+            score_to_union[s] = score_to_union.get(s, 0) | bm_t
+    score_groups = list(score_to_union.items())
+
+    non_threat = map_info._board_mask & ~threat
+
+    all_planes = []
+    for d in range(8):
+        planes = [0] * _NUM_PLANES
+        core_reach = 0
+        for dx, dy in offsets_table[d]:
+            sm = shift_masks.get((-dx, -dy))
+            if sm is None:
                 continue
-            for dx, dy in offsets_table[di]:
-                shift_mask = map_info._turret_shift_masks.get((dx, dy))
-                if shift_mask is None:
+            rev_off = -dx + (-dy) * w
+            if core_mask:
+                masked = core_mask & sm
+                if masked:
+                    if rev_off >= 0:
+                        core_reach |= masked << rev_off
+                    else:
+                        core_reach |= masked >> (-rev_off)
+            for s, bm_group in score_groups:
+                masked = bm_group & sm
+                if not masked:
                     continue
-                offset = dx + dy * w
-                if offset > 0:
-                    coverage |= (dm & shift_mask) << offset
+                if rev_off >= 0:
+                    contrib = masked << rev_off
                 else:
-                    coverage |= (dm & shift_mask) >> (-offset)
+                    contrib = masked >> (-rev_off)
+                _add_const_to_planes(planes, s, contrib)
+        if core_reach:
+            _add_const_to_planes(planes, core_score, core_reach)
+        if THREAT_PENALTY:
+            _add_const_to_planes(planes, THREAT_PENALTY, non_threat)
+        # Restrict every plane to placement-candidate tiles for this direction.
+        mask_d = sentinel_masks[d]
+        for i in range(_NUM_PLANES):
+            planes[i] &= mask_d
+        all_planes.append(planes)
+    return all_planes
 
-    gunners = map_info._bm_et[map_info._IDX_GUNNER] & my_team_bm
-    if gunners:
-        walls = map_info._bm_env[map_info._IDX_ENV_WALL]
-        m = gunners
-        while m:
-            lsb = m & -m
-            n = lsb.bit_length() - 1
-            px = n % w
-            py = n // w
-            for ray_di in range(8):
-                for dx, dy in map_info._GUNNER_RAYS[ray_di]:
-                    nx, ny = px + dx, py + dy
-                    if not (0 <= nx < w and 0 <= ny < h):
-                        break
-                    bit = 1 << (nx + ny * w)
-                    if walls & bit:
-                        break
-                    coverage |= bit
-            m ^= lsb
 
-    return coverage
+# ---------------------------------------------------------------------------
+# Gunner: one plane-list. Either a single facing, or max over all 8 facings.
+# ---------------------------------------------------------------------------
 
+def _gunner_ray_blocked_mask():
+    """Tiles that block a gunner ray: walls + allied non-road, non-marker
+    buildings. A gunner can't shoot through its own infrastructure."""
+    walls = map_info._bm_env[map_info._IDX_ENV_WALL]
+    my_team = map_info._bm_team[map_info._my_team_idx]
+    my_solid = (my_team
+                & ~map_info._bm_et[map_info._IDX_ROAD]
+                & ~map_info._bm_et[map_info._IDX_MARKER])
+    return walls | my_solid
 
-def _high_value_targets():
-    """Bitmask of enemy high-value buildings not already covered by my turrets."""
-    my_team_idx = map_info._my_team_idx
-    enemy_idx = 1 - my_team_idx
-    enemy = map_info._bm_team[enemy_idx]
 
-    high_value = (
-        map_info._bm_et[map_info._IDX_FOUNDRY]
-        | map_info._bm_et[map_info._IDX_GUNNER]
-        | map_info._bm_et[map_info._IDX_SENTINEL]
-        | map_info._bm_et[map_info._IDX_BREACH]
-        | map_info._bm_et[map_info._IDX_CORE]
-        | map_info._bm_et[map_info._IDX_LAUNCHER]
-        | map_info._bm_et[map_info._IDX_HARVESTER]
-    ) & enemy
-    if not high_value:
-        return 0
+def _compute_gunner_dir_scores(enemy_team_bm, threat, gunner_masks):
+    """For each of 8 facing directions, compute a per-tile gunner score plane
+    list. Returns: list of 8 plane-lists. Reading position n from the d-th
+    inner list yields the gunner's total damage-score if placed at n facing
+    direction d — but ONLY if n is a valid placement tile for that direction
+    (per `gunner_masks[d]`); otherwise the score reads 0.
 
-    my_coverage = _my_turret_coverage()
-    return high_value & ~my_coverage
+    Gunner rays are blocked by walls AND by allied non-road, non-marker
+    buildings. Scores are pre-multiplied by GUNNER_SCORE_MULTIPLIER so they
+    compare directly with sentinel scores. THREAT_PENALTY is baked in:
+    non-threat tiles get +THREAT_PENALTY so threat tiles read THREAT_PENALTY
+    lower."""
+    w = map_info._width
+    shift_masks = map_info._turret_shift_masks
+    bm_et = map_info._bm_et
+    dir_vecs = map_info._DIR_VECS
+    gunner_rays = map_info._GUNNER_RAYS
+    not_blocked = map_info._board_mask & ~_gunner_ray_blocked_mask()
 
+    core_mask = bm_et[map_info._IDX_CORE] & enemy_team_bm
+    core_score_mult = BUILDING_SCORE[map_info._IDX_CORE] * GUNNER_SCORE_MULTIPLIER
 
+    score_to_union_mult = {}
+    for t_idx, s in _SCORED_NON_CORE_TYPES:
+        bm_t = bm_et[t_idx] & enemy_team_bm
+        if bm_t:
+            gs = s * GUNNER_SCORE_MULTIPLIER
+            score_to_union_mult[gs] = score_to_union_mult.get(gs, 0) | bm_t
+
+    non_threat = map_info._board_mask & ~threat
+
+    all_planes = []
+    for d in range(8):
+        planes = [0] * _NUM_PLANES
+        dx, dy = dir_vecs[d]
+        max_step = len(gunner_rays[d])
+        sdx, sdy = -dx, -dy
+        sm = shift_masks.get((sdx, sdy))
+        if sm is None or max_step == 0:
+            all_planes.append(planes)
+            continue
+        soff = sdx + sdy * w
+        core_cur = core_mask
+        type_cur = dict(score_to_union_mult)
+        core_reach = 0
+        for _ in range(max_step):
+            def _shift_one(m, _sm=sm, _soff=soff, _nb=not_blocked):
+                masked = m & _sm
+                return (masked << _soff if _soff >= 0 else masked >> (-_soff)) & _nb
+            if core_cur:
+                core_cur = _shift_one(core_cur)
+                if core_cur:
+                    core_reach |= core_cur
+            new_type_cur = {}
+            for gs, bm_t in type_cur.items():
+                shifted = _shift_one(bm_t)
+                if shifted:
+                    new_type_cur[gs] = shifted
+                    _add_const_to_planes(planes, gs, shifted)
+            type_cur = new_type_cur
+            if not core_cur and not type_cur:
+                break
+        if core_reach:
+            _add_const_to_planes(planes, core_score_mult, core_reach)
+        if THREAT_PENALTY:
+            _add_const_to_planes(planes, THREAT_PENALTY, non_threat)
+        # Restrict every plane to placement-candidate tiles for this direction.
+        mask_d = gunner_masks[d]
+        for i in range(_NUM_PLANES):
+            planes[i] &= mask_d
+        all_planes.append(planes)
+    return all_planes
+
+
+# ---------------------------------------------------------------------------
+# Per-tile "best direction / best type" pick
+# ---------------------------------------------------------------------------
+
+def get_best_direction(pos):
+    """Pick (Direction, turret_type, score) for a turret at pos.
+
+    Sentinel: iterate the 8 sentinel plane-lists, pick the best non-blocked
+    direction by reading the score at this tile.
+    Gunner: read the single gunner max-plane at this tile for the cross-dir
+    score, then call get_best_gunner_dir() to pick the actual facing.
+
+    Breach is ignored for now — never returned."""
+    w = map_info._width
+    px, py = pos.x, pos.y
+    n = px + py * w
+    bit = 1 << n
+
+    _ensure_score_planes()
+    sent_planes_by_dir = _round_cache_sentinel_planes
+    gun_planes_by_dir = _round_cache_gunner_planes
+    sentinel_masks = _round_cache_placement_masks[0]
+    gunner_masks = _round_cache_placement_masks[1]
+
+    directions = map_info._DIRECTIONS
+
+    # Sentinel: per-direction planes are already placement-filtered; only
+    # read a direction where `pos` is a valid placement for it.
+    best_s_dir, best_s_score = Direction.NORTH, -1
+    for d in range(8):
+        if not (sentinel_masks[d] & bit):
+            continue
+        s = _read_score(sent_planes_by_dir[d], n)
+        if s > best_s_score:
+            best_s_score = s
+            best_s_dir = directions[d]
+
+    # Gunner: same pattern, using per-direction gunner planes.
+    best_g_dir, best_g_score = Direction.NORTH, -1
+    if gun_planes_by_dir is not None:
+        for d in range(8):
+            if not (gunner_masks[d] & bit):
+                continue
+            g = _read_score(gun_planes_by_dir[d], n)
+            if g > best_g_score:
+                best_g_score = g
+                best_g_dir = directions[d]
+
+    if best_s_score >= best_g_score:
+        return best_s_dir, EntityType.SENTINEL, best_s_score
+    return best_g_dir, EntityType.GUNNER, best_g_score
+
+
+# ---------------------------------------------------------------------------
+# Candidate generation
+# ---------------------------------------------------------------------------
+
 def _placement_candidates():
-    """Bitmask of tiles where a turret could be placed."""
+    """Returns (sentinel_masks, gunner_masks): two lists of 8 bitmasks, one per
+    facing direction. Loader blockers are baked in:
+      sentinel_masks[d] = tiles where a sentinel can face direction d
+      gunner_masks[d]   = tiles where a gunner can face direction d
+    Gunners with 2+ loader directions get the full-360 exemption."""
     my_team_idx = map_info._my_team_idx
     enemy_idx = 1 - my_team_idx
     my_team = map_info._bm_team[my_team_idx]
     enemy_team = map_info._bm_team[enemy_idx]
+    
+    w = map_info._width
+    bm_et = map_info._bm_et
+    shift_masks = map_info._turret_shift_masks
+    dir_vecs = map_info._DIR_VECS
 
-    # Location filter: conveyor outputs + cardinal adj to harvesters
+    my_sentinels = bm_et[map_info._IDX_SENTINEL] & my_team
+    if my_sentinels:
+        taken_harvesters = map_info.expand_manhattan(my_sentinels) & bm_et[map_info._IDX_HARVESTER]
+    else:
+        taken_harvesters = 0
     candidates = map_info._bm_ti_fed | map_info._bm_ax_fed
-    harvesters = (map_info._bm_et[map_info._IDX_HARVESTER]&map_info._bm_env[map_info._IDX_ENV_ORE_TI]) | map_info._bm_et[map_info._IDX_FOUNDRY]  # double for safety margin
+    harvesters = (map_info._bm_et[map_info._IDX_HARVESTER] & map_info._bm_env[map_info._IDX_ENV_ORE_TI] & ~taken_harvesters) | map_info._bm_et[map_info._IDX_FOUNDRY]
     if harvesters:
         candidates |= map_info.expand_manhattan(harvesters)
 
-    # Tile content filter: empty, or clearable
     empty = ~map_info._bm_any_building
 
     my_clearable = (
@@ -277,96 +389,211 @@
     ) & enemy_team
 
     candidates &= (empty | my_clearable | enemy_clearable)
-
-    # Exclusions
     candidates &= ~map_info._bm_env[map_info._IDX_ENV_WALL]
 
-    # Exclude tiles with any builder bots (except me)
     my_bit = 1 << (map_info._my_pos.x + map_info._my_pos.y * map_info._width)
     all_bots = (map_info._bm_friendly_bots | map_info._bm_enemy_bots) & ~my_bit
     candidates &= ~all_bots
 
-    # Avoid enemy builder bots within 6 manhattan — only for enemy road candidates
+    enemy_roads = map_info._bm_et[map_info._IDX_ROAD] & enemy_team
+    danger_for_roads = map_info._bm_enemy_launch_adj
     enemy_bots = map_info._bm_enemy_bots
     if enemy_bots:
         danger = enemy_bots
-        for _ in range(6):
-            danger = map_info.expand_manhattan(danger)
-        enemy_roads = map_info._bm_et[map_info._IDX_ROAD] & enemy_team
-        candidates &= ~(danger & enemy_roads)
+        for _ in range(4):
+            danger = map_info.expand_chebyshev(danger)
+        danger_for_roads |= danger
+    candidates &= ~(danger_for_roads & enemy_roads)
 
-    return candidates
+    candidates &= ~cant_attack
 
+    # Facing blockers: block direction D at tile P if P+delta_D has a friendly
+    # harvester/foundry (always blocks), or a conveyor whose output points back
+    # at P (direction == opposite of D). Conveyors pointing away are fine.
+    base_block = bm_et[map_info._IDX_HARVESTER] | bm_et[map_info._IDX_FOUNDRY]
 
-def _sentinel_all_offsets():
-    """Union of all sentinel offsets across all 8 directions as (dx, dy) set."""
-    offsets = set()
-    for di in range(8):
-        for dx, dy in map_info._SENTINEL_OFFSETS[di]:
-            offsets.add((dx, dy))
-    return offsets
-
-_sentinel_all_reach_cache = None
-
-def _sentinel_all_reach(targets):
-    """Bitmask of positions from which a sentinel (any direction) could hit at least one target.
-    Uses reverse-shift of the union of all direction offsets."""
-    global _sentinel_all_reach_cache
-    if _sentinel_all_reach_cache is None:
-        _sentinel_all_reach_cache = list(_sentinel_all_offsets())
-    w = map_info._width
-    reachable = 0
-    for dx, dy in _sentinel_all_reach_cache:
-        rdx, rdy = -dx, -dy
-        shift_mask = map_info._turret_shift_masks.get((rdx, rdy))
-        if shift_mask is None:
+    blockers = [0] * 8
+    for d in range(8):
+        dx, dy = dir_vecs[d]
+        sm = shift_masks.get((-dx, -dy))
+        if sm is None:
             continue
-        offset = rdx + rdy * w
-        if offset > 0:
-            reachable |= (targets & shift_mask) << offset
-        else:
-            reachable |= (targets & shift_mask) >> (-offset)
-    return reachable
+        incoming_conv = map_info._bm_conv_by_dir[(d + 4) & 7] & my_team
+        src = (base_block | incoming_conv) & sm
+        if not src:
+            continue
+        soff = -dx + (-dy) * w
+        blockers[d] = (src << soff) if soff >= 0 else (src >> (-soff))
 
+    # Sentinels have low dps and shouldn't sit in gunner/breach fire. Gunners
+    # have high dps and can trade into hard threats.
+    sentinel_cands = candidates & ~map_info._bm_enemy_hard_threat
+    sentinel_masks = [sentinel_cands & ~blockers[d] for d in range(8)]
+    gunner_masks   = [candidates & ~blockers[d] for d in range(8)]
+    return sentinel_masks, gunner_masks
 
+
 def _get_attack_candidates():
-    """Return (non_roaded, roaded) candidate bitmasks."""
-    candidates = _placement_candidates()
-    if not candidates:
-        return 0, 0
+    """Return (non_roaded, roaded) candidate bitmasks.
 
-    targets = _high_value_targets()
-    if not targets:
+    Threshold filter: keep only candidates whose best non-blocked sentinel
+    direction score, OR whose gunner max-score, is within
+    SCORE_THRESHOLD_FACTOR of the global best. Threat penalty is baked into
+    both plane representations already."""
+    sentinel_masks, gunner_masks = _placement_candidates()
+    _round_cache_placement_masks[0] = sentinel_masks
+    _round_cache_placement_masks[1] = gunner_masks
+
+    gunner_any = 0
+    for d in range(8):
+        gunner_any |= gunner_masks[d]
+    filtered = gunner_any
+    for d in range(8):
+        filtered |= sentinel_masks[d]
+    if not filtered:
         return 0, 0
 
-    # Filter to candidates that can hit at least one target in some direction
-    reachable = _sentinel_all_reach(targets)
-    filtered = candidates & reachable
+    _ensure_score_planes()
+    sent_planes_by_dir = _round_cache_sentinel_planes
+    gun_planes_by_dir = _round_cache_gunner_planes
 
-    if not filtered:
+    # NOTE: gunner SUM planes would double-count THREAT_PENALTY once per
+    # direction (8x) and report non-zero for tiles with no enemy damage. Use
+    # per-direction max for filtering — matches get_best_direction's pick.
+    max_score = 0
+    for d in range(8):
+        if sentinel_masks[d]:
+            s = _max_score_in_mask(sent_planes_by_dir[d], sentinel_masks[d])
+            if s > max_score:
+                max_score = s
+        if gun_planes_by_dir is not None and gunner_masks[d]:
+            g = _max_score_in_mask(gun_planes_by_dir[d], gunner_masks[d])
+            if g > max_score:
+                max_score = g
+
+    global _round_cache_threshold
+    _round_cache_threshold = 0
+    if max_score < MIN_ATTACK_SCORE:
         return 0, 0
+    if max_score > 0:
+        # THREAT_PENALTY is baked into every non-threat tile as a flat bonus;
+        # a tile whose ONLY contribution is that bonus has 0 real enemy damage.
+        # Require threshold > THREAT_PENALTY to exclude those.
+        threshold = max(int(max_score * SCORE_THRESHOLD_FACTOR), THREAT_PENALTY + 1)
+        _round_cache_threshold = threshold
+        keep = 0
+        for d in range(8):
+            if sentinel_masks[d]:
+                keep |= _ge_threshold_mask(sent_planes_by_dir[d], threshold, sentinel_masks[d])
+            if gun_planes_by_dir is not None and gunner_masks[d]:
+                keep |= _ge_threshold_mask(gun_planes_by_dir[d], threshold, gunner_masks[d])
+        filtered &= keep
+        if not filtered:
+            return 0, 0
 
-    # Split into non-enemy-roaded vs enemy-roaded
     my_team_idx = map_info._my_team_idx
     enemy_idx = 1 - my_team_idx
     enemy_roads = map_info._bm_et[map_info._IDX_ROAD] & map_info._bm_team[enemy_idx]
 
     roaded = filtered & enemy_roads
     non_roaded = filtered & ~enemy_roads
-
     return non_roaded, roaded
 
 
+# ---------------------------------------------------------------------------
+# Round cache
+# ---------------------------------------------------------------------------
+
+_round_cache_round = -1
+_round_cache_attack_candidates = (0, 0)
+_round_cache_sentinel_planes = None    # list of 8 plane-lists, one per direction
+_round_cache_gunner_planes = None      # list of 8 plane-lists, one per direction
+_round_cache_threshold = 0
+_round_cache_placement_masks = [None, None]  # [sentinel_masks[8], gunner_masks[8]]
+
+
+def _ensure_round_cache():
+    global _round_cache_round, _round_cache_attack_candidates
+    global _round_cache_sentinel_planes, _round_cache_gunner_planes
+    r = rc.get_current_round()
+    if _round_cache_round == r:
+        return
+    _round_cache_round = r
+    _round_cache_sentinel_planes = None
+    _round_cache_gunner_planes = None
+    _round_cache_attack_candidates = _get_attack_candidates()
+    if DRAW_DEBUG:
+        non_roaded, roaded = _round_cache_attack_candidates
+        if non_roaded | roaded:
+            _draw_attack_candidates(non_roaded | roaded)
+
+
+def _ensure_score_planes():
+    """Lazily build sentinel and gunner planes once per round. Requires the
+    placement masks to already be populated in _round_cache_placement_masks."""
+    global _round_cache_sentinel_planes, _round_cache_gunner_planes
+    if _round_cache_sentinel_planes is not None:
+        return
+    # Drop tiles already covered by one of my gunners' current ray — they're
+    # being shot at already, no point scoring another turret on them.
+    enemy_team_bm = map_info._bm_team[1 - map_info._my_team_idx] & ~map_info._bm_my_gunner_claims
+    threat = (map_info._bm_enemy_soft_threat | map_info._bm_enemy_hard_threat)
+    sentinel_masks, gunner_masks = _round_cache_placement_masks
+    _round_cache_sentinel_planes = _compute_sentinel_dir_scores(
+        enemy_team_bm, threat, sentinel_masks
+    )
+    _round_cache_gunner_planes = _compute_gunner_dir_scores(
+        enemy_team_bm, threat, gunner_masks
+    )
+
+
+# ---------------------------------------------------------------------------
+# Debug drawing
+# ---------------------------------------------------------------------------
+
+def _draw_attack_candidates(filtered):
+    """Debug: for each filtered attack candidate tile, draw what run() would
+    pick. Sentinel wins → white length-1 line in its facing direction. Gunner
+    wins → red dot."""
+    w = map_info._width
+    h = map_info._height
+    dir_deltas = map_info._DIRECTION_DELTAS
+    m = filtered
+    while m:
+        lsb = m & -m
+        n = lsb.bit_length() - 1
+        x, y = n % w, n // w
+        direction, turret_type, _ = get_best_direction(Position(x, y))
+        dx, dy = dir_deltas[direction]
+        ex, ey = x + dx, y + dy
+        if turret_type == EntityType.GUNNER:
+            r = 255
+            g = 0
+            b = 0
+        else:
+            r = 0
+            g = 0
+            b = 255
+        if 0 <= ex < w and 0 <= ey < h:
+            rc.draw_indicator_line(Position(x, y), Position(ex, ey), r, g, b)
+        m ^= lsb
+
+# ---------------------------------------------------------------------------
+# Claims + state hooks
+# ---------------------------------------------------------------------------
+
 def _my_claims():
     w = map_info._width
     my_mask = 1 << (map_info._my_pos.x + map_info._my_pos.y * w)
-    non_roaded, roaded = _get_attack_candidates()
+    _ensure_round_cache()
+    non_roaded, roaded = _round_cache_attack_candidates
     combined = non_roaded | roaded
     claimed = pathing.voronoi_claim(my_mask, units.builder.claimed_senders[comm_flag], combined)
     return claimed & non_roaded, claimed & roaded
 
-_cached_claims = (0, 0)  # set by score(), reused by run()
 
+_cached_claims = (0, 0)
+
 def score():
     global _cached_claims
     if rc.get_global_resources()[0] < rc.get_sentinel_cost()[0]:
@@ -374,10 +601,15 @@
         return 0
     _cached_claims = _my_claims()
     non_roaded, roaded = _cached_claims
-    return 6 if (non_roaded or roaded) else 0
+    if non_roaded:
+        return 8
+    if roaded:
+        return 6
+    return 0
 
 
 def run():
+    global cant_attack
     log("ATTACK")
     non_roaded, roaded = _cached_claims
 
@@ -388,7 +620,6 @@
     my_team_idx = map_info._my_team_idx
     candidates = non_roaded | roaded
 
-    # Evaluate all adjacent candidate tiles and pick highest scoring
     my_pos = map_info._my_pos
     best = None
     best_score = -1
@@ -406,7 +637,6 @@
         if max(abs(px - my_pos.x), abs(py - my_pos.y)) <= 1:
             pos = Position(px, py)
             direction, turret_type, dir_score = get_best_direction(pos)
-            # Prefer non-roaded tiles
             is_er = bool(enemy_roads & lsb)
             adj_score = (0 if is_er else 1, dir_score)
             if adj_score > (0 if best_is_enemy_road else 1, best_score):
@@ -418,12 +648,12 @@
         mask ^= lsb
 
     if best is None:
-        # No adjacent candidates, move toward closest
         if non_roaded:
             best, _ = nav.closest(non_roaded)
         if best is None and roaded:
             best, _ = nav.closest(roaded)
         if best is None:
+            cant_attack |= non_roaded | roaded
             return
         best_direction, best_turret_type, _ = get_best_direction(best)
         best_n = best.x + best.y * width
@@ -439,19 +669,14 @@
     is_enemy_road = best_is_enemy_road
     log(f"Attack: best={best}, dir={direction}, type={turret_type}, enemy_road={is_enemy_road}")
 
-    my_team = map_info._my_team
+    zone = 1 << (map_info._my_pos.x + map_info._my_pos.y * width)
+    zone = map_info.expand_chebyshev(map_info.expand_chebyshev(zone))
+    enemy_bot_nearby = bool(map_info._bm_enemy_bots & zone)
 
-    count = 0
-    for uid in rc.get_nearby_units(4):
-        if rc.get_entity_type(uid) != map_info._ET_BUILDER_BOT or rc.get_team(uid) == my_team:
-            continue
-        count += 1
-
     if is_enemy_road:
-        # Move onto enemy road, fire it, step off
         nav.move_to(best)
         if rc.can_fire(best):
-            if count == 0 or rc.get_hp(best_id) <= 2: # bait them to move away
+            if not enemy_bot_nearby or rc.get_hp(best_id) <= 2:
                 rc.fire(best)
         for d in map_info._ALL_DIRECTIONS:
             if d == Direction.CENTRE:
@@ -461,23 +686,17 @@
                 map_info.update_move()
                 break
     else:
-        # Move adjacent and destroy own building if needed
         nav.move_adjacent(best)
         if best_id and is_mine:
-            if rc.can_destroy(best) and rc.get_action_cooldown() == 0:
+            if not map_info.has_builder_bot(best) and rc.can_destroy(best) and rc.get_action_cooldown() == 0:
                 log(f"Attack destroy own building at {best}")
                 rc.destroy(best)
                 map_info.update_at(best)
 
-    # Place turret
     if turret_type == EntityType.GUNNER:
         if rc.can_build_gunner(best, direction):
             rc.build_gunner(best, direction)
             map_info.update_at(best)
-    elif turret_type == EntityType.BREACH:
-        if rc.can_build_breach(best, direction):
-            rc.build_breach(best, direction)
-            map_info.update_at(best)
     else:
         if rc.can_build_sentinel(best, direction):
             rc.build_sentinel(best, direction)
```

### `units/states/disrupt.py`

```diff
--- bots/Lethe_baseline/units/states/disrupt.py	2026-04-18 20:19:30
+++ bots/Lethe/units/states/disrupt.py	2026-04-19 01:53:03
@@ -1,9 +1,10 @@
+from cambc import *
+
 import map_info
 import pathing
 from pathing import Pathing
 import comms
 import units.builder
-from cambc import *
 from log import log
 
 rc: Controller = None
@@ -11,6 +12,8 @@
 
 comm_flag = 2
 
+cant_disrupt = 0
+
 def init(c: Controller):
     global rc, nav
     rc = c
@@ -21,11 +24,19 @@
                | map_info._bm_env[map_info._IDX_ENV_ORE_AX])
     clearable = (map_info._bm_et[map_info._IDX_ROAD]
                  | map_info._bm_et[map_info._IDX_MARKER])
-    return (all_ore
-            & (~map_info._bm_any_building | clearable)
-            & ~units.builder._harvest_zone
-            & ~map_info._bm_enemy_turret_threat
-            & ~map_info._bm_enemy_launch_adj)
+    result = (all_ore
+              & (~map_info._bm_any_building | clearable)
+              & ~units.builder._harvest_zone
+              & ~(map_info._bm_enemy_soft_threat | map_info._bm_enemy_hard_threat)
+              & ~map_info._bm_enemy_launch_adj
+              & ~cant_disrupt)
+    if rc.get_current_round() < 200:
+        w = map_info._width
+        my_zone = 1 << (map_info._my_pos.x + map_info._my_pos.y * w)
+        for _ in range(5):
+            my_zone = map_info.expand_chebyshev(my_zone)
+        result &= my_zone
+    return result
 
 def _my_claims():
     w = map_info._width
@@ -38,6 +49,7 @@
     return 2 if _my_claims() else 0
 
 def run():
+    global cant_disrupt
     log("DISRUPT")
     available = _my_claims()
     if not available:
@@ -45,6 +57,7 @@
 
     best, _ = nav.closest(available)
     if best is None:
+        cant_disrupt |= available
         return
 
     width = map_info._width
@@ -57,7 +70,7 @@
     if best_id and (map_info._bm_team[my_team_idx] & best_bit):
         # Friendly road/marker — move adjacent and destroy
         nav.move_adjacent(best)
-        if rc.can_destroy(best) and rc.get_action_cooldown() == 0:
+        if not map_info.has_builder_bot(best) and rc.can_destroy(best) and rc.get_action_cooldown() == 0:
             rc.destroy(best)
             map_info.update_at(best)
     elif best_id and (map_info._bm_et[map_info._IDX_ROAD]&best_bit):
```

### `units/states/explore.py`

```diff
--- bots/Lethe_baseline/units/states/explore.py	2026-04-18 20:19:30
+++ bots/Lethe/units/states/explore.py	2026-04-17 05:11:53
@@ -1,8 +1,9 @@
+from cambc import *
+
 import map_info
 from pathing import Pathing
 import comms
 import units.builder
-from cambc import *
 import random
 from log import log
 
@@ -11,82 +12,59 @@
 
 explore_target = None
 comm_flag = 1
+_first_explore = True # New global flag
 
 def init(c: Controller):
     global rc, nav
     rc = c
     nav = Pathing(rc)
+    global _first_explore # Reset for each unit
+    _first_explore = True
 
 def score():
     return 1
 
 def generate_explore_target():
     global explore_target
-    w = map_info._width
-    nlc = map_info._not_left_col
-    nrc = map_info._not_right_col
-    board = (1 << (w * map_info._height)) - 1
-    avoid = map_info.get_avoid(False, False, False)
-    if rc.get_global_resources()[0] < rc.get_harvester_cost()[0]*2:
-        avoid |= map_info._bm_seen & ~map_info._bm_any_building & ~map_info._bm_env[map_info._IDX_ENV_WALL]
-    passable = ~avoid & board
+    global _first_explore
 
-    # Seed with all other builders' claimed tiles + incremental steps from
-    # the nearest friendly bot toward each claim, plus my own position.
-    seeds = 0
-    claims = 0
-    for i, f in enumerate(units.builder.claimed_targets):
-        if i == 7:  # heal flag uses enemy IDs, not tile positions
-            continue
-        claims |= f
-    seeds |= claims
+    if _first_explore:
+        _first_explore = False
+        core_pos = rc.get_position()
+        attempts = 0
+        while attempts < 10: # Try a few times
+            dx = random.randint(-10, 10)
+            dy = random.randint(-10, 10)
+            # Check distance squared (dx*dx + dy*dy <= 100)
+            if dx*dx + dy*dy <= 100:
+                potential_target = Position(core_pos.x + dx, core_pos.y + dy)
+                if map_info.in_bounds(potential_target): # Use map_info.in_bounds
+                    explore_target = potential_target
+                    log(f"First explore target: {explore_target}")
+                    return
+            attempts += 1
+        log("Couldn't find nearby target, falling back to random logic.")
 
-    my_pos = map_info._my_pos
-    my_n = my_pos.x + my_pos.y * w
-    seeds |= 1 << my_n
+    # Subsequent runs: Pick a random location at least 3 units from map edges.
+    w = map_info._width
+    h = map_info._height
+    
+    min_x = 3
+    max_x = w - 4 # w - 1 (last index) - 3 (margin)
+    min_y = 3
+    max_y = h - 4 # h - 1 (last index) - 3 (margin)
 
-    # Seed tiles every 5 Chebyshev steps from my position toward each claim.
-    bx, by = my_pos.x, my_pos.y
-    mask = claims
-    while mask:
-        lsb = mask & -mask
-        n = lsb.bit_length() - 1
-        tx, ty = n % w, n // w
-        steps = max(abs(bx - tx), abs(by - ty))
-        for s in range(5, steps, 5):
-            ix = bx + (tx - bx) * s // steps
-            iy = by + (ty - by) * s // steps
-            seeds |= 1 << (ix + iy * w)
-        mask ^= lsb
+    if min_x > max_x or min_y > max_y: # Handle very small maps
+        log("Map too small for 3-unit edge margin, picking random on whole map.")
+        explore_target = Position(random.randint(0, w - 1), random.randint(0, h - 1))
+    else:
+        rand_x = random.randint(min_x, max_x)
+        rand_y = random.randint(min_y, max_y)
+        explore_target = Position(rand_x, rand_y)
+    
+    log(f"New explore target: {explore_target}")
 
-    visited = seeds
-    frontier = seeds
-    prev_frontier = frontier
-    c = 0
-    while frontier and c < 100:
-        prev_frontier = frontier
-        expanded = frontier | ((frontier & nrc) << 1) | ((frontier & nlc) >> 1) | (frontier << w) | (frontier >> w)
-        frontier = expanded & passable & ~visited
-        visited |= frontier
-        c += 1
 
-    # prev_frontier is the last ring before flood filled everything.
-    # Pick a random unset bit from that ring (tiles NOT claimed by anyone).
-    unclaimed = prev_frontier & ~units.builder.claimed_targets[comm_flag]
-    pool = unclaimed if unclaimed else prev_frontier
-    count = pool.bit_count()
-    if count == 0:
-        explore_target = Position(random.randint(0, map_info._width - 1),
-                                  random.randint(0, map_info._height - 1))
-        return
-    pick = random.randint(0, count - 1)
-    mask = pool
-    for _ in range(pick):
-        mask &= mask - 1
-    lsb = mask & -mask
-    n = lsb.bit_length() - 1
-    explore_target = Position(n % w, n // w)
-
 def run():
     log("EXPLORE")
     if explore_target is None or map_info._my_pos.distance_squared(explore_target) <= 18:
```

### `units/states/harvest.py`

```diff
--- bots/Lethe_baseline/units/states/harvest.py	2026-04-18 20:19:30
+++ bots/Lethe/units/states/harvest.py	2026-04-19 01:53:03
@@ -1,8 +1,9 @@
+from cambc import *
+
 import map_info
 import pathing
 from pathing import Pathing
 import comms
-from cambc import *
 import units.builder
 from log import log
 
@@ -27,7 +28,7 @@
 _cost_map: dict[int, int] = {}  # tile index -> min titanium cost to harvest
 def possible_ore():
     ore = map_info._bm_env[map_info._IDX_ENV_ORE_TI]
-    if (map_info._bm_team[map_info._my_team_idx] & map_info._bm_et[map_info._IDX_HARVESTER] & map_info._bm_env[map_info._IDX_ENV_ORE_TI]) and rc.get_current_round() >= 1000:
+    if (map_info._bm_team[map_info._my_team_idx] & map_info._bm_et[map_info._IDX_HARVESTER] & map_info._bm_env[map_info._IDX_ENV_ORE_TI]) and rc.get_current_round() >= 750:
         ore |= map_info._bm_env[map_info._IDX_ENV_ORE_AX]
     return ore
 def harvestable_ore():
@@ -66,15 +67,22 @@
     )
     enemy_hard_adj = map_info.expand_manhattan(enemy_hard)
 
+    # Axionite ore adjacent to my conveyors actively carrying Ti or refined — mixing
+    # those with fresh raw-ax would contaminate the flow. Empty/unclassified conveyors
+    # (e.g. guards we just placed) are fine; they'll pick up raw-ax once the harvester runs.
+    wrong_conveyors = (map_info._bm_conv_ti | map_info._bm_conv_refined) & map_info._bm_team[my_team_idx]
+    ax_ore_near_non_raw = map_info._bm_env[map_info._IDX_ENV_ORE_AX] & map_info.expand_manhattan(wrong_conveyors) if wrong_conveyors else 0
+
     return (ore
             & ~landlocked
             & ~map_info._bm_et[map_info._IDX_HARVESTER]
             & ~enemy_blocking
             & ~friendly_blocking
             & ~enemy_hard_adj
-            & ~map_info._bm_enemy_turret_threat
-            & units.builder._harvest_zone
-            & ~cant_harvest)
+            & ~(map_info._bm_enemy_soft_threat | map_info._bm_enemy_hard_threat)
+            # & units.builder._harvest_zone
+            & ~cant_harvest
+            & ~ax_ore_near_non_raw)
 
 def _too_expensive():
     """Bitmask of tiles we know we can't afford right now."""
@@ -87,11 +95,10 @@
     return result
 
 def score():
+
     return 3 if _my_claims() else 0
 
-CARD = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]
 
-
 def run():
     global cant_harvest
     log("HARVEST")
@@ -118,7 +125,7 @@
             continue
         # Check all 4 cardinal sides are secured
         secured = True
-        for cd in CARD:
+        for cd in map_info._CARDINAL:
             cp = map_info.pos_add(p, cd)
             if not map_info.in_bounds(cp):
                 continue
@@ -142,7 +149,7 @@
         _cost_map[pn] = cost
         if cost > rc.get_global_resources()[0]:
             continue
-        if rc.get_action_cooldown() == 0 and rc.can_destroy(p) and (map_info.type_at(p.x, p.y) == EntityType.ROAD or map_info.type_at(p.x, p.y) == EntityType.BARRIER) and not ((map_info._bm_friendly_bots | map_info._bm_enemy_bots) & pbit):
+        if rc.get_action_cooldown() == 0 and rc.can_destroy(p) and (map_info.type_at(p.x, p.y) == EntityType.ROAD or map_info.type_at(p.x, p.y) == EntityType.BARRIER) and not map_info.has_builder_bot(p):
             rc.destroy(p)
             map_info.update_at(p)
         if rc.can_build_harvester(p):
@@ -178,7 +185,7 @@
         return
     # --- Secure each cardinal side ---
     all_secured = True
-    for d in CARD:
+    for d in map_info._CARDINAL:
         p = map_info.pos_add(best_ore, d)
         if not map_info.in_bounds(p):
             continue
@@ -213,7 +220,7 @@
         # Empty, marker, enemy marker, or my road — needs barrier
         all_secured = False
         nav.move_to(best_ore)
-        if pid and is_mine and rc.can_destroy(p) and rc.get_action_cooldown() == 0:
+        if pid and is_mine and not map_info.has_builder_bot(p) and rc.can_destroy(p) and rc.get_action_cooldown() == 0:
             rc.destroy(p)
             map_info.update_at(p)
         if rc.can_build_barrier(p):
@@ -231,6 +238,21 @@
     ore_n = best_ore.x + best_ore.y * w
     ore_bit = 1 << ore_n
     ore_id = map_info._building_id[ore_n]
+
+    # If an enemy road sits on the ore, step ONTO the ore and fire until it's
+    # gone. This has to happen before any adjacent-move, because move cooldown
+    # is spent by the first nav.move_to of the turn.
+    if ore_id:
+        is_mine = bool(map_info._bm_team[my_team_idx] & ore_bit)
+        is_road = bool(map_info._bm_et[map_info._IDX_ROAD] & ore_bit)
+        if not is_mine and is_road:
+            nav.move_to(best_ore)
+            if rc.can_fire(map_info._my_pos):
+                rc.fire(map_info._my_pos)
+            comms.mark(best_ore.x + best_ore.y * map_info._width, comm_flag)
+            return
+
+    # Pick a passable tile adjacent to best_ore to stand on while building.
     targets = set()
     for d in map_info._ALL_DIRECTIONS:
         p = map_info.pos_add(path[0], d)
@@ -248,14 +270,7 @@
 
     if ore_id:
         is_mine = bool(map_info._bm_team[my_team_idx] & ore_bit)
-        is_road = bool(map_info._bm_et[map_info._IDX_ROAD] & ore_bit)
-        if not is_mine and is_road:
-            nav.move_to(best_ore)
-            if rc.can_fire(map_info._my_pos):
-                rc.fire(map_info._my_pos)
-            comms.mark(best_ore.x + best_ore.y * map_info._width, comm_flag)
-            return
-        if is_mine and rc.can_destroy(best_ore) and rc.get_action_cooldown() == 0 and map_info._my_pos != best_ore:
+        if is_mine and not map_info.has_builder_bot(best_ore) and rc.can_destroy(best_ore) and rc.get_action_cooldown() == 0 and map_info._my_pos != best_ore:
             rc.destroy(best_ore)
             map_info.update_at(best_ore)
 
```

### `units/states/heal.py`

```diff
--- bots/Lethe_baseline/units/states/heal.py	2026-04-18 20:19:30
+++ bots/Lethe/units/states/heal.py	2026-04-19 03:20:52
@@ -1,8 +1,9 @@
+from cambc import *
+
 import map_info
 from pathing import Pathing
 import comms
 import units.builder
-from cambc import *
 from log import log
 
 rc: Controller = None
@@ -13,7 +14,10 @@
 CONV_CHASE_CHEB = 8
 ID_MASK = (1 << 12) - 1
 
+# Friendly buildings previously unreachable for heal — excluded from target sets.
+cant_heal = 0
 
+
 def init(c: Controller):
     global rc, nav
     rc = c
@@ -106,12 +110,12 @@
 
 def _very_damaged_targets():
     """Bitmask of friendly buildings with > 2 damage."""
-    return _healable_mask() & map_info._bm_very_damaged
+    return _healable_mask() & map_info._bm_very_damaged & ~cant_heal
 
 
 def _heal_targets():
     """Bitmask of friendly damaged buildings."""
-    return _healable_mask() & map_info._bm_damaged & ~map_info._bm_enemy_bots
+    return _healable_mask() & map_info._bm_damaged & ~map_info._bm_enemy_bots & ~cant_heal
 
 
 _cached_chase_target = None  # set by score(), reused by run()
@@ -127,38 +131,26 @@
         if _conv_zone() & (1<<(target[1].x + target[1].y * map_info._width)):
             log("high priority heal", target[0])
             return 7
-        else:
-            log("low priority heal", target[0])
-            return 2.5
+        # else:
+        #     log("low priority heal", target[0])
+        #     return 2.5
     return 0
 
 
 def _try_barrier_dead_ends():
-    """Barrier any adjacent tiles that are dead-end conveyor targets."""
+    """Barrier any adjacent dead-end conveyor output tiles."""
     w = map_info._width
     dead_ends = map_info._bm_dead_end
     if not dead_ends:
         return
-    # Only dead-end conveyors whose output is empty / marker / enemy building
+    # dead_ends are output tiles; only barrier those that are empty / marker / enemy building
     my_team_idx = map_info._my_team_idx
     enemy_idx = 1 - my_team_idx
     enemy_any = map_info._bm_team[enemy_idx]
     marker = map_info._bm_et[map_info._IDX_MARKER]
     empty_mask = ~map_info._bm_any_building & ~map_info._bm_env[map_info._IDX_ENV_WALL]
 
-    targets = 0
-    mask = dead_ends
-    conv_target = map_info._building_conv_target
-    tiles = w * map_info._height
-    while mask:
-        lsb = mask & -mask
-        n = lsb.bit_length() - 1
-        tn = conv_target[n]
-        if tn and 0 <= tn < tiles:
-            tbit = 1 << tn
-            if (empty_mask & tbit) or (marker & tbit) or (enemy_any & tbit):
-                targets |= lsb
-        mask ^= lsb
+    targets = dead_ends & (empty_mask | marker | enemy_any)
     if not targets:
         return
     my_pos = map_info._my_pos
@@ -171,7 +163,7 @@
         if not (targets & pbit):
             continue
         if rc.get_action_cooldown() == 0:
-            if rc.can_destroy(p):
+            if not map_info.has_builder_bot(p) and rc.can_destroy(p):
                 rc.destroy(p)
                 map_info.update_at(p)
         if rc.can_build_barrier(p):
@@ -223,10 +215,15 @@
         return
 
     # Priority 2: move to most damaged building and heal
+    global cant_heal
     very_damaged = _very_damaged_targets()
     targets = very_damaged if very_damaged else _heal_targets()
     if targets:
         best, dist = nav.closest(targets)
-        if best is not None:
+        if best is None:
+            cant_heal |= targets
+        else:
             nav.move_adjacent(best, avoid_turret=False)
     _do_best_heal()
+    if rc.can_fire(rc.get_position()) and rc.get_team(rc.get_tile_building_id(rc.get_position())) != rc.get_team():
+        rc.fire(rc.get_position())
\ No newline at end of file
```

### `units/states/route.py`

```diff
--- bots/Lethe_baseline/units/states/route.py	2026-04-18 20:19:30
+++ bots/Lethe/units/states/route.py	2026-04-19 01:53:03
@@ -1,8 +1,8 @@
+from cambc import *
 import map_info
 import pathing
 from pathing import Pathing
 import comms
-from cambc import *
 import units.builder
 from log import log
 
@@ -55,9 +55,8 @@
     return result
 
 def _dead_end_conveyors():
-    """Bitmask of routable conveyors whose output is not connected to my ore-accepting network."""
-    return map_info._bm_dead_end & ~map_info._bm_enemy_turret_threat
-
+    """Bitmask of dead-end conveyor output tiles not connected to my ore-accepting network."""
+    return map_info._bm_dead_end & ~(map_info._bm_enemy_soft_threat | map_info._bm_enemy_hard_threat) & ~map_info._bm_et[map_info._IDX_HARVESTER]
 def _orphan_harvesters():
     """Bitmask of my harvesters with no adjacent conveyor/turret/core."""
     my_team_idx = map_info._my_team_idx
@@ -74,7 +73,7 @@
     ) & map_info._bm_team[my_team_idx]
 
     served = map_info.expand_manhattan(my_connected)
-    return my_harvesters & ~served & ~map_info._bm_enemy_turret_threat
+    return my_harvesters & ~served & ~(map_info._bm_enemy_soft_threat | map_info._bm_enemy_hard_threat)
 def _orphan_foundries():
     """Bitmask of my foundries with no adjacent conveyor/turret/core."""
     my_team_idx = map_info._my_team_idx
@@ -94,14 +93,14 @@
         map_info._bm_et[map_info._IDX_CONVEYOR]
         | map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR]
         | map_info._bm_et[map_info._IDX_SPLITTER]
+        | map_info._bm_et[map_info._IDX_BRIDGE]
     ) & map_info._bm_team[my_team_idx]
     my_connected = (directional & ~pointing_into) | (
-        (map_info._bm_et[map_info._IDX_BRIDGE] | map_info._bm_et[map_info._IDX_CORE])
-        & map_info._bm_team[my_team_idx]
+        map_info._bm_et[map_info._IDX_CORE] & map_info._bm_team[my_team_idx]
     )
 
     served = map_info.expand_manhattan(my_connected)
-    return my_foundries & ~served & ~map_info._bm_enemy_turret_threat
+    return my_foundries & ~served & ~(map_info._bm_enemy_soft_threat | map_info._bm_enemy_hard_threat)
 def cant_claim():
     w = map_info._width
     my_pos = map_info._my_pos
@@ -124,12 +123,17 @@
     w = map_info._width
     my_mask = 1 << (map_info._my_pos.x + map_info._my_pos.y * w)
     avoid = avoid_mask()
+    # units.builder.draw_mask(_dead_end_conveyors() & ~avoid, 255, 0, 0)
+    # print("random info", map_info._building_conv_target[7+9*w]%w, map_info._building_conv_target[7+9*w]//w)
+    # units.builder.draw_mask(map_info._bm_conv_raw_ax, 0, 255, 0)
+    my_5x5 = map_info.expand_chebyshev(map_info.expand_chebyshev(my_mask))
     candidates = (_dead_end_conveyors() | _orphan_harvesters() | _orphan_foundries()) & ~avoid
-    return pathing.voronoi_claim(my_mask, units.builder.claimed_senders[comm_flag], candidates)
+    return pathing.voronoi_claim(my_mask, units.builder.claimed_senders[comm_flag], candidates) | (candidates&my_5x5)
 
 _cached_claims = 0  # set by score(), reused by run()
 
 def score():
+    # units.builder.draw_mask(map_info._bm_route_targets, 255, 255, 0)
     global _cached_claims
     _cached_claims = _my_claims()
     return 4 if _cached_claims else 0
@@ -181,26 +185,12 @@
         target_conveyor = [path[0], path[1]]
         # Route from harvester: expand start to cardinal neighbors
     else:
-        # Dead-end conveyor: route from its output tile
-        target_n = map_info._building_conv_target[best_n]
-
-        can_heal_road = False
-        target_zone = 1 << target_n
-        for _ in range(3):
-            target_zone = map_info.expand_chebyshev(target_zone)
-        if target_zone & map_info._bm_enemy_bots:
-            can_heal_road = True
         path = nav.calculate_conveyor_path(best, is_raw_ax, update=True)
-        print("PATH", path)
         if path is None:
             unpathable |= best_bit
             return
         target_conveyor = [path[0], path[1]]
-        if (map_info._bm_team[1-map_info._my_team_idx] & (1 << target_n)) and not map_info.type_at(target_n%width, target_n//width) == EntityType.MARKER and not (map_info.type_at(target_n%width, target_n//width) == EntityType.ROAD and not can_heal_road):
-            new_path = nav.calculate_conveyor_path(best, is_raw_ax, update=True)
-            if new_path is not None and new_path[1] != path[0]:
-                path = new_path
-                target_conveyor = [path[0], path[1]]
+    claim_n = target_conveyor[0].x + target_conveyor[0].y * width
     near_enemy = False
     if target_conveyor[0].distance_squared(target_conveyor[1]) == 1:
         tc1_zone = 1 << (target_conveyor[1].x + target_conveyor[1].y * width)
@@ -213,7 +203,7 @@
         nav.move_to(target)
         if rc.can_fire(target):
             rc.fire(target)
-        comms.mark(best.x + best.y * map_info._width, comm_flag)
+        comms.mark(claim_n, comm_flag)
         return
     foundry_sites = nav.raw_ax_foundry_sites() if is_raw_ax else 0
     # units.builder.draw_mask(foundry_sites, 255, 0, 0)
@@ -222,17 +212,17 @@
         foundry_cost = rc.get_foundry_cost()[0]
         _cost_map[best_n] = foundry_cost + nav.conveyor_cost(path[2], rc.get_scale_percent()/100+0.5)
         if rc.get_global_resources()[0] < foundry_cost + nav.conveyor_cost(path[2]):
-            comms.mark(best.x + best.y * map_info._width, comm_flag)
+            comms.mark(claim_n, comm_flag)
             return
         nav.move_adjacent(target_conveyor[0])
         if rc.get_action_cooldown() == 0:
-            if rc.can_destroy(target_conveyor[0]):
+            if not map_info.has_builder_bot(target_conveyor[0]) and rc.can_destroy(target_conveyor[0]):
                 rc.destroy(target_conveyor[0])
                 map_info.update_at(target_conveyor[0])
             if rc.can_build_foundry(target_conveyor[0]):
                 rc.build_foundry(target_conveyor[0])
                 map_info.update_at(target_conveyor[0])
-        comms.mark(best.x + best.y * map_info._width, comm_flag)
+        comms.mark(claim_n, comm_flag)
         return
     can_build = False
     cost = nav.conveyor_cost(path[2])
@@ -241,7 +231,7 @@
         _cost_map[best_n] = cost
         if rc.get_global_resources()[0] < cost:
             log("can't afford", cost)
-            comms.mark(best.x + best.y * map_info._width, comm_flag)
+            comms.mark(claim_n, comm_flag)
             return
     if near_enemy:
         nav.move_to(target_conveyor[1])
@@ -297,4 +287,4 @@
             map_info._bm_conv_loaded |= last_unloaded_bit
             log("set loaded", (last_unloaded_bit.bit_length() - 1) % width, (last_unloaded_bit.bit_length() - 1) // width)
 
-    comms.mark(best.x + best.y * map_info._width, comm_flag)
+    comms.mark(claim_n, comm_flag)
```

### `units/states/sabotage.py`

```diff
--- bots/Lethe_baseline/units/states/sabotage.py	2026-04-18 20:19:30
+++ bots/Lethe/units/states/sabotage.py	2026-04-19 01:53:03
@@ -1,9 +1,10 @@
+from cambc import *
+
 import map_info
 import pathing
 from pathing import Pathing
 import comms
 import units.builder
-from cambc import *
 from log import log
 
 rc: Controller = None
@@ -11,6 +12,8 @@
 
 comm_flag = 5
 
+cant_sabotage = 0
+
 def init(c: Controller):
     global rc, nav
     rc = c
@@ -27,20 +30,20 @@
         map_info._bm_et[map_info._IDX_CONVEYOR]
         | map_info._bm_et[map_info._IDX_SPLITTER]
         | map_info._bm_et[map_info._IDX_BRIDGE]
-    ) & enemy
+    ) & enemy & (map_info._bm_conv_ti | map_info._bm_conv_raw_ax | map_info._bm_conv_refined)
 
     if not targets:
         return 0
 
     # Exclude tiles in turret threat or adjacent to enemy launcher
-    danger = map_info._bm_enemy_turret_threat | map_info._bm_enemy_launch_adj
+    danger = (map_info._bm_enemy_soft_threat | map_info._bm_enemy_hard_threat) | map_info._bm_enemy_launch_adj
     targets &= ~danger
 
     # Avoid enemy builder bots within 6 pathing distance
     enemy_bots = map_info._bm_enemy_bots
     if enemy_bots:
         w = map_info._width
-        board = (1 << (w * map_info._height)) - 1
+        board = map_info._board_mask
         avoid = map_info.get_avoid(False, False, False)
         passable = ~avoid & board
         nlc = map_info._not_left_col
@@ -82,7 +85,7 @@
         if target not in invalid_sabotage_locations:
             pruned_targets |= (1 << (target.x + target.y * map_info._width))
 
-    return pruned_targets
+    return pruned_targets & ~cant_sabotage
 
 def _my_claims():
     w = map_info._width
@@ -93,6 +96,7 @@
     return 5 if _my_claims() else 0
 
 def run():
+    global cant_sabotage
     log("SABOTAGE")
     targets = _my_claims()
 
@@ -101,6 +105,7 @@
 
     best, _ = nav.closest(targets)
     if best is None:
+        cant_sabotage |= targets
         return
 
     # Move onto the tile and fire
```

### `units/turret_gunner.py`

```diff
--- bots/Lethe_baseline/units/turret_gunner.py	2026-04-18 20:19:30
+++ bots/Lethe/units/turret_gunner.py	2026-04-19 02:01:53
@@ -1,4 +1,5 @@
 from cambc import Controller, Direction, EntityType, Position, Team, Environment, GameConstants
+
 import map_info
 from log import log
 
@@ -10,6 +11,7 @@
 
 # --- Ported from dragonfruit/globals.py ---
 TURRET_TYPES = {EntityType.GUNNER, EntityType.SENTINEL, EntityType.BREACH}
+CARDINAL_OFFSETS = [(0, 1), (0, -1), (-1, 0), (1, 0)]
 
 INF = 999999
 
@@ -136,11 +138,14 @@
             if not map_info.in_bounds(cur):
                 break
 
-            if map_info.ground_at(x, y):
+            if map_info.ground_at(x, y) == Environment.WALL:
                 break
 
             threat_tiles.add(cur)
 
+            if not rc.is_in_vision(cur):
+                continue
+
             bbid = rc.get_tile_builder_bot_id(cur)
             if bbid is not None:
                 if rc.get_team(bbid) == my_team:
@@ -200,10 +205,43 @@
 
     return enemy_units
 
+def _get_loaders(pos):
+    """Return list of direction indices (0-7) from pos toward buildings that feed it."""
+    w = map_info._width
+    h = map_info._height
+    px, py = pos.x, pos.y
+    pos_n = px + py * w
+    loaders = []
+
+    harvesters = map_info._bm_et[map_info._IDX_HARVESTER]
+    conveyors = (map_info._bm_et[map_info._IDX_CONVEYOR]
+                 | map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR])
+
+    # Cardinal-adjacent harvesters
+    for di, (dx, dy) in zip([0, 2, 4, 6], [(0, -1), (1, 0), (0, 1), (-1, 0)]):
+        nx, ny = px + dx, py + dy
+        if 0 <= nx < w and 0 <= ny < h:
+            if harvesters & (1 << (nx + ny * w)):
+                loaders.append(di)
+
+    # Any neighbor conveyor whose output targets this tile
+    for di in range(8):
+        dx, dy = map_info._DIR_VECS[di]
+        nx, ny = px + dx, py + dy
+        if 0 <= nx < w and 0 <= ny < h:
+            nn = nx + ny * w
+            if (conveyors & (1 << nn)) and map_info._building_conv_target[nn] == pos_n:
+                if di not in loaders:
+                    loaders.append(di)
+
+    return loaders
+
 def choose_rotate_dir(enemies) -> Direction | None:
     current_dir = rc.get_direction()
     rotate_dir = None
     rotate_dist = INF
+    blocked_dirs = _get_loaders(my_pos)
+    can_face_any_dir = len(blocked_dirs) >= 2
 
     for (eid, etype, tpos, team) in enemies:
         if etype not in TURRET_TYPES:
@@ -219,13 +257,40 @@
 
         if desired_dir == current_dir:
             continue
+        if not can_face_any_dir and desired_dir in blocked_dirs:
+            continue
 
         if dist < rotate_dist:
             rotate_dist = dist
             rotate_dir = desired_dir
 
     return rotate_dir
+
+def choose_builder_bot_rotate_dir() -> Direction | None:
+    """Rotates towards adjacent enemy builder bots on allied conveyors."""
+    for d in tuple(Direction):
+        adj_pos = map_info.pos_add(my_pos, d)
+        if not map_info.in_bounds(adj_pos):
+            continue
 
+        bot_id = rc.get_tile_builder_bot_id(adj_pos)
+        if bot_id is None or rc.get_team(bot_id) == my_team:
+            continue
+
+        # Check if on allied conveyor or bridge
+        building_id = rc.get_tile_building_id(adj_pos)
+        if building_id is None:
+            continue
+
+        b_type = rc.get_entity_type(building_id)
+        b_team = rc.get_team(building_id)
+
+        if b_team == my_team and (b_type in {EntityType.CONVEYOR, EntityType.BRIDGE, EntityType.ARMOURED_CONVEYOR}):
+             # Rotate towards them
+             return my_pos.direction_to(adj_pos)
+    
+    return None
+
 # --- Ported and adapted from dragonfruit/units/gunner/run.py ---
 def run():
     global last_fired_round, skipped_firing_turns
@@ -243,10 +308,13 @@
     elif rc.get_global_resources()[0] >= 60:
         rotate_dir = choose_rotate_dir(enemies)
 
+        if rotate_dir is None:
+            rotate_dir = choose_builder_bot_rotate_dir()
+
         if rotate_dir is not None and rc.can_rotate(rotate_dir):
             rc.rotate(rotate_dir)
             skipped_firing_turns = 0
-            log(f"gunner rotated toward adjacent enemy turret: {rotate_dir}")
+            log(f"gunner rotated: {rotate_dir}")
 
     if rc.get_action_cooldown() == 0:
         skipped_firing_turns += 1
@@ -255,5 +323,19 @@
         if len(enemies) > 0:
             last_fired_round = rc.get_current_round()
             skipped_firing_turns -= 1
-        if (rc.get_scale_percent() > 500 or skipped_firing_turns >= 32):
+        if (rc.get_scale_percent() > 500 or skipped_firing_turns >= 32) and not _should_stay():
             rc.self_destruct()
+
+def _should_stay():
+    my_pos = rc.get_position()
+    my_team = map_info._my_team
+    for dx, dy in CARDINAL_OFFSETS:
+        p = Position(my_pos.x + dx, my_pos.y + dy)
+        if map_info.in_bounds(p):
+            bid = rc.get_tile_building_id(p)
+            if bid and rc.get_entity_type(bid) == EntityType.HARVESTER:
+                return True
+            bot_id = rc.get_tile_builder_bot_id(p)
+            if bot_id and rc.get_team(bot_id) != my_team:
+                return True
+    return False
\ No newline at end of file
```

### `units/turret_sentinel.py`

```diff
--- bots/Lethe_baseline/units/turret_sentinel.py	2026-04-18 20:19:30
+++ bots/Lethe/units/turret_sentinel.py	2026-04-19 01:20:06
@@ -1,4 +1,5 @@
-from cambc import Controller, Position, EntityType, Direction
+from cambc import Controller, Position, EntityType
+
 import map_info
 from log import log
 
@@ -12,7 +13,7 @@
     EntityType.BREACH: 60,
     EntityType.SENTINEL: 50,
     EntityType.LAUNCHER: 10,
-    EntityType.HARVESTER: 35,
+    EntityType.HARVESTER: 0,
     EntityType.BUILDER_BOT: 15,
     EntityType.GUNNER: 40,
     EntityType.FOUNDRY: 55,
```

