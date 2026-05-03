# Lethe Dev vs Stable: Behavioral Changes

**Executive Summary**

The dev version introduces three major architectural shifts: (1) **soft vs hard threat differentiation** — sentinels and breaches are treated as low-damage threats, while gunners are high-damage, affecting pathing and turret scoring; (2) **conveyor stuck detection** — the bot now tracks resources by ID to detect when conveyors halt, invalidating downstream predictions and triggering re-routing; (3) **bit-sliced turret scoring** — attack placement switches from per-tile iteration to bit-parallel computation across 13 score planes, enabling faster multi-directional evaluation and better allocation of turrets to high-value targets. Secondary changes include core spawn behavior toward titanium, gunner claim tracking to avoid coverage overlap, and simplified explore targeting.

---

## comms.py

- **Removed learn_map protocol**: No longer sends/receives communicated map learning markers (LEARN_MAP_TYPE=0). Message format streamlined from 4 types to 3 (explore, harvest, route).
- **Removed binary search import**: Uses inline binary search loop instead of `bisect_left` in `estimate_turn()`.
- **Simplified marker placement**: Removed special handling for learn_map markers; all remaining markers now follow the same sender-direction encoding.

## comms_positional.py

- **Removed learn_map communication**: Deleted `encode_learn_map()`, `decode_learn_map_*()`, `apply_learn_map_message()`, and `get_learn_map_*()` functions.
- **Removed LEARN_MAP_OFFSETS**: Previously a 5x5 pattern; no longer needed.
- **Removed symmetry-inference logic from `note_comm_env()`**: Previously propagated learned tiles across mirror symmetries and pruned symmetries on conflict; now only marks the single learned tile.
- **Simplified API**: `note_comm_env()` now takes `Position` instead of `(x, y)` for consistency.

## log.py

- **Debug modes enabled**: `DEBUG_LOGGING = True` and `DRAW_DEBUG = True` (dev build).

## main.py

- **Added visualization support**: New `ENABLE_VIS` flag emits per-unit world state (fog, conveyors, threats, bots) to the Rust replay viewer.
- **Profiler refactored**: Removed `PROFILER_ONLY_TLE`; now profiles only timeout turns (no longer profiles all turns selectively).
- **Removed early-game resign logic**: Deleted commented-out code for resigning at round 200.

## map_info.py

- **Threat split into soft and hard**:
  - `_bm_enemy_turret_threat` → `_bm_enemy_soft_threat` (sentinels, low DPS) + `_bm_enemy_hard_threat` (gunners + breaches, high DPS).
  - Sentinel/breach use bitmask shifting (no wall blocking); gunner uses only current facing ray (not all 8 rays).

- **Conveyor stuck tracking**:
  - New `_bm_conv_stuck`: bitmask of conveyors holding the same resource stack across rounds (not moving).
  - New `_conveyor_resource_id`: per-tile list tracking last-observed resource ID.
  - New `_clear_downstream_conv_bits()` function invalidates predicted resource bits on downstream tiles when a conveyor changes, up to `_CONV_PROP_DEPTH=3` tiles ahead (stops at intersections).

- **Gunner claim tracking**:
  - New `_bm_my_gunner_claims`: bitmask of tiles covered by one of my gunners' current ray.
  - New `_compute_my_gunner_claims()` recomputed each round to pick up facing changes.

- **Per-direction conveyor buckets**:
  - New `_bm_conv_by_dir[d]`: conveyors (CONVEYOR + ARMOURED_CONVEYOR only) facing direction d, for fast iteration.

- **Simplified functions**:
  - `pos_add_xy(x, y, d)` → `pos_add(pos, d)` returning `Position`.
  - `in_bounds_xy(x, y)` → `in_bounds_coords(x, y)`.
  - Removed separate `has_builder_bot_xy()` and `is_passable_xy()` overloads; consolidated to single `has_builder_bot(pos)` and `is_passable(pos)`.

- **Removed symmetry resolution logic**: Symmetry-solving code moved earlier in update; no longer applies reflected tiles on the fly during symmetry-unknown phase.

- **Downstream propagation**: Resource propagation now stops at freshly-observed conveyors (to avoid clobbering just-observed state) and skips non-freshly-loaded conveyors in the intersection check.

## pathing.py

- **Threat splitting**: `_bm_enemy_turret_threat` replaced with `(map_info._bm_enemy_soft_threat | map_info._bm_enemy_hard_threat)`.

- **Threat cost now 20** (from variable nw_cost of 1); non-walkable cost removed — all tiles treated as cost 1.

- **Conveyor-end cost reduced**: `conveyor_end_cost = 6` (was 10), making routing through conveyor endpoints cheaper.

- **BFS optimization**: Removed non-walkable tile cost; only barrier + threat cost tiers remain. Frontier management simplified (4 masks instead of 8).

- **Move validation**: Uses `map_info.pos_add()` instead of manual `pos_add_xy()`.

- **Conveyor routing**:
  - `calculate_conveyor_path()` now takes `Position` instead of `(x, y)`.
  - Unmasking now uses `_conv_reverse[]` to allow conveyors feeding into the start tile.
  - Refined axionite now excludes existing allied foundries from the avoid set, treating them as free endpoints (cost 0).
  - Added check: avoid tiles cardinally adjacent to axionite ore (unless landlocked) when routing titanium/refined, preventing future harvester contamination.

## units/core.py

- **Turn 1 titanium targeting**: New `get_closest_titanium_tile()` spawns one builder toward the nearest titanium ore without an allied harvester.
- **Spawn scale increased**: `SCALE_MULT = 0.6` (was 0.5); core spawns more aggressively toward center.
- **Titanium threshold lowered**: Spawn when `scaling * 0.6 + 250 < titanium` (was 300), unlocking earlier spawning in mid-game.

## units/builder.py

- **Removed learn_map communication**: No more `_maybe_mark_learn_map()` or related comms flags.
- **Removed state sorting**: States are no longer sorted by `MAX_SCORE` at init; they run in declaration order.
- **Simplified comms handling**: No learn_map message branch; only handles standard 0–6 comm flags (explore, disrupt, harvest, route, sabotage, attack).

## units/states/attack.py

- **Complete scoring redesign** using bit-sliced computation:
  - Building scores adjusted: launcher 15→8, harvester 10→12, foundry 15→16, breach 25→24, barrier 1→4, armoured-conveyor 3→4, core 100→96.
  - New constants: `GUNNER_SCORE_MULTIPLIER=4` (gunner scores × 4 to match sentinel damage-per-tile), `THREAT_PENALTY=4` (non-threat tiles +4 to read lower on threat).
  - New `_compute_sentinel_dir_scores()` and `_compute_gunner_dir_scores()`: build per-direction score plane-lists (13 planes each) via bit-parallel addition, grouping enemy types by score.
  - Gunner now evaluates single facing (current direction) only, not all 8 rays.
  - New helper functions: `_bits_of_score()`, `_add_const_to_planes()`, `_read_score()`, `_max_score_in_mask()`, `_ge_threshold_mask()` for bit-sliced operations.

- **Behavioral**: Turret placement now prefers directions that maximize total enemy score (accounting for threat penalty) rather than iterating per-tile. Gunners no longer consider all 8 directions; only current facing scores, reducing overlap with existing coverage.

## units/states/explore.py

- **Simplified targeting**:
  - First explore: pick a random tile within 10 units (Chebyshev distance ≤ 100) of core.
  - Subsequent explores: pick uniformly from interior tiles (≥3 units from edges).
  - Removed: flood-fill seeding, Voronoi claim logic, claim-integration step.

## units/states/harvest.py

- **Removed gunner-build logic**: No longer builds gunners to protect harvester construction; only places barriers.
- **Removed helper `has_gunner_covering()`**: Gunner placement delegated to other states.
- **Threat check split**: `~_bm_enemy_turret_threat` → `~(_bm_enemy_soft_threat | _bm_enemy_hard_threat)`.
- **Axionite ore filter**: Exclude axionite adjacent to my conveyors carrying titanium/refined (non-empty conveyors), preventing resource contamination on fresh harvesters.
- **Harvestable ore threshold**: Include raw axionite starting at round 750 (was 1000).
- **Diagonal ore cost heuristic**: Print ore placement costs for debugging; removed `harvest_zone` filtering (commented out).

## units/states/disrupt.py

- **Added `cant_disrupt` mask**: Accumulates tiles where disruption is too costly, avoiding thrashing.
- **Early-game zone restriction**: Before round 200, restrict disruptable ore to a 5x5 Chebyshev zone around the builder.
- **Threat check split**: `~_bm_enemy_turret_threat` → `~(_bm_enemy_soft_threat | _bm_enemy_hard_threat)`.

## units/states/route.py

- **Dead-end definition refined**: `_bm_dead_end` now represents output tiles (not conveyor tiles), excluding harvesters and threat tiles.
- **Threat check split**: All occurrences of `_bm_enemy_turret_threat` → `(_bm_enemy_soft_threat | _bm_enemy_hard_threat)`.
- **Bridging logic**: Bridges now count toward `my_connected` in orphan-harvester/foundry checks (not just conveyors).
- **Voronoi addition**: Route targets now include all candidates within a 2-step (5x5 Chebyshev) zone of the builder, reducing reliance on Voronoi for nearby tiles.
- **Conveyor path API**: `calculate_conveyor_path(x, y, ...)` → `calculate_conveyor_path(pos, ...)`.

## units/states/heal.py

- **Added `cant_heal` mask**: Excludes previously unreachable buildings from future heal targeting, avoiding pathfinding thrash.
- **Low-priority heal removed**: Heal no longer returns score 2.5 for non-zone targets; only very-damaged (score 7) or nothing.
- **Dead-end barrier logic simplified**: `_try_barrier_dead_ends()` now directly intersects `_bm_dead_end` with candidate tiles (empty/marker/enemy), avoiding per-tile lookup loop.
- **Ammo threshold lowered**: `rc.get_ammo_amount() < 10` → `< 5`, allowing more frequent healing before ammo limits.

## units/states/sabotage.py

- **Added `cant_sabotage` mask**: Tracks tiles where sabotage failed, avoiding re-pathfinding.
- **Sabotage target filter**: Only target enemy conveyors/bridges/splitters that are currently known to carry a resource (raw axionite, titanium, or refined), avoiding dead conveyors.
- **Threat check split**: `~_bm_enemy_turret_threat` → `(~_bm_enemy_soft_threat & ~_bm_enemy_hard_threat)`.

## units/turret_gunner.py

- **Ray tile computation**: Uses `map_info.pos_add()` instead of `pos_add_xy()`; checks attackability before adding tiles.
- **Builder rotation**: Uses `map_info.pos_add()` and `map_info.in_bounds()`.

## units/turret_sentinel.py

- **Harvester priority zeroed**: Sentinel no longer prioritizes adjacent harvesters (score 35→0 in `THREAT_PRIORITIES`), focusing on harder targets.
- **Ammo threshold halved**: Fire when ammo `< 5` (was `< 10`), less cautious.
- **Inline position construction**: Replaced `(x, y)` unpacking with direct `Position(my_pos.x + dx, my_pos.y + dy)`.
