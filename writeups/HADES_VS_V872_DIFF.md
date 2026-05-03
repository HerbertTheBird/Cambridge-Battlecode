# Behavioral diff: bots/Hades vs bots/v872

`bots/v872/` was added in commit `215352f` as a snapshot of the then-current Hades. Everything below summarizes how Hades has diverged since.

Commits to `bots/Hades/` since v872 was snapshotted:
- `fe3e88a` oops left in resign
- `18f7527` misc changes
- `1f7a0e4` new bot submitted
- `355e5c0` prefer to move further away from friendly bot
- `38f9d6a` why does it suck so much
- `b61730d` merge

## Files added/removed

- **Removed in Hades:** `units/states/disrupt.py`, `units/turret_gunner_old.py`, `units/turret_sentinel_old.py`
- **Identical:** `turret_breach.py` (only line-ending diff)

## attack.py — biggest behavioral changes

Constants:
- `_NUM_PLANES`: `9` → `12` (bigger score range)
- `SCORE_THRESHOLD_FACTOR`: `0.25` → `0` (no scaling threshold)
- New: `WANTED_ATTACK_THRESHOLD = 48`, `CANT_ATTACK_TTL = 100`

New behavior:
- `cant_attack()` / `_mark_cant_attack(mask)` — TTL-tracked memo of unattackable tiles, prevents thrashing on impossible targets
- `wanted_attack_tiles()` — returns tiles where a turret would score ≥ threshold *ignoring* harvester/feed adjacency. Used by `harvest`/`secure` to skip routing-cost portion of affordability checks for ore tiles whose harvesting would unlock a wanted attack site
- `_try_instant_preferred(preferred)` — fast-path: if a "preferred" attack tile is immediately reachable, take it without full `nav.closest`
- `_friendly_distance_score(n)` — tiebreak helper that prefers tiles further from friendly bots (the `355e5c0` feature)
- `_placement_candidates(require_feed=True)` — flag added so `wanted_attack_tiles` can ask for the broad set
- `run()` now calls `nav.closest(..., tiebreak_score=score_fn)` to use the friendly-distance tiebreak

`_placement_candidates` itself also changed: enemy-bot tracking widened — `tracked_zone = enemy_bots` → `expand_chebyshev(enemy_bots)`, and the "am being tracked" check uses `danger & my_bit` instead of `tracked_zone & my_bit`.

## map_info.py — fed-mask redesign

`_bm_ti_fed` / `_bm_ax_fed` semantics fundamentally changed.

- **v872:** target tiles of conveyors observed carrying titanium / refined axionite (one-hop forward of carrying conveyors only).
- **Hades:** "tiles where a turret placed there would be loaded with ammo" — 4-hop forward propagation from a richer seed:
  1. Cardinal neighbors of Ti harvesters (Ti) and foundries (Ax)
  2. Conveyors the source actually feeds (adj minus pointing-back)
  3. Conveyors observed carrying the matching resource, but only if they have an upstream feeder (`has_reverse`) **or** are adjacent to the source (orphan-carrying conveyors excluded)

Propagation includes bridges (via `_building_conv_target`) and chain *terminal* targets (so a non-conveyor tile that a chain ends in still counts as fed).

Note: `_compute_fed` deliberately seeds from harvesters/foundries of *both* teams — adjacency to enemy economy doubles as a strategic placement signal even though a turret there wouldn't actually load. (Empirically, restricting to own-team costs ~17pt.)

`_bm_guard_conveyor` doc updated: target ore tile must have a harvester (any team), not just be ore.

## pathing.py

- `closest(self, targets, pos=None, tiebreak_score=None)` — new `tiebreak_score` parameter; used by attack to prefer tiles further from friendlies
- New top-level `closest_impl(...)` (was only the inner method before)
- `calculate_conveyor_path(self, start, raw_axionite, update=False, refined=False)` — new `refined` param, passed through to `_get_conveyor_targets_and_avoid`
- `_get_conveyor_targets_and_avoid(self, raw_axionite, refined=False)` — when `refined=False`, includes own-team foundries in target set
- Constants unchanged (`bridge_cost=6`, `barrier_cost=15`, `threat_cost=20`, `conveyor_end_cost=4`)

## heal.py

- `_find_chase_target(damaged=True)` — `damaged` flag added. When `False`, chases any enemy bot; when `True` only chases very-damaged ones
- Visibility filter on `_bm_enemy_bots` removed in chase target lookup (chases also-known-but-not-currently-visible bots)
- `_heal_targets()` extended: now also includes own sentinels/gunners that are under enemy turret threat (heal turrets *before* they die, not just damaged buildings)
- `score()` returns `1.5` if `_heal_targets()` is non-empty, even without a chase target — gives heal a low-priority always-available trigger

## route.py

- New: `UNPATHABLE_TTL = 400`, `unpathable()`, `_mark_unpathable(mask)` — TTL memo for tiles that have failed to path
- `calculate_conveyor_path` calls now pass `refined=is_refined`
- `tc1_zone` near-enemy expansion: `range(8)` → `range(4)` (smaller danger zone around target conveyor end)

## secure.py

- `MAX_SCORE`: `7.5` → `8.5` (higher priority vs other states)
- `run()` skips conveyor-pathing for foundries (`if is_foundry: path = None`) before the cost-map update
- Cost map uses `attack.wanted_attack_tiles()` to skip the routing-cost portion when the ore tile is adjacent to a wanted attack tile

## harvest.py

- New: `CANT_HARVEST_TTL = 400`, `cant_harvest()`, `_mark_cant_harvest(mask)` — symmetric memo to `cant_secure`
- `possible_ore(allow_partial=False)` — new flag
- Cost map uses `attack.wanted_attack_tiles()` (same pattern as secure)
- `all_blocking` extended to include enemy harvesters: `| (_bm_et[_IDX_HARVESTER] & _bm_team[1-my_team_idx])`

## turret_gunner.py

- `_scan_ray(direction, attackable, feeder_mask, allow_builder_bots, bot_on_ally_conv_ok=False)` — new flag: when set, an enemy builder bot standing on an ally conveyor doesn't terminate the ray
- New: `_ALLY_CONV_TYPES` constant tuple
- New: `_draw_feeder_mask(mask)` debug helper
- Visibility filter `& map_info._bm_visible` added on enemy/friendly bot lookups (prevents acting on stale comms-only positions)

## turret_sentinel.py

Same signatures, same constants. Internal changes:
- Visibility filter `& map_info._bm_visible` added on enemy/friendly bot lookups (matches turret_gunner)

## comms.py — protocol simplification

Removed:
- `SAMPLE_BITS` (and related `_SAMPLE_MASK`, `_SAMPLE_SHIFT`)
- `decode_sample_bits()`, `decode_sender_location()`
- `mark(target_idx, type)` — markers no longer used to broadcast over comms

Added:
- Type IDs: `TYPE_LAUNCHER_ORDER = 0`, `TYPE_SYMMETRY_BROADCAST = 1` (replacing dynamic types)
- `TYPE_BITS = 5` (fixed)
- `broadcast_symmetry()`, `give_launcher_order(target_idx)` — replace the marker-based comms

`encode(target, type, sym=0, sender_loc=0)` — `sample_bits` arg dropped.

## builder.py — claim-tracking removed

Removed entirely:
- `_update_crowded_claims(current_round)`
- `exclude_crowded_claims(flag, mask)`
- `_clear_crowded_claim(flag, idx)`
- `register_active_target(flag, target)` / `clear_active_target()`

`handle_comms()` no longer takes `current_round`. The whole "crowded claim" coordination layer is gone — bots no longer broadcast which targets they're going for to deconflict with peers.

## core.py

Same signatures and constants. The `if rc.get_current_round() == 600: rc.resign()` was added in `215352f`, then commented out in `fe3e88a`. Both Hades and v872 have it commented out.

## Other files

- **explore.py:** signatures identical, only internal logic tweaks
- **spawn_plan.py:** signatures identical
- **turret_launcher.py:** signatures identical
- **main.py:** only `ENABLE_PROFILER = False` line whitespace
- **log.py:** only `DEBUG_LOGGING`/`DRAW_DEBUG` flag values
- **comms_positional.py:** internal only

## Summary of behavioral themes

1. **Memoization layers:** Hades adds `cant_attack`, `cant_harvest`, `unpathable` TTL caches that v872 didn't have. Goal: stop wasting cooldown re-trying impossible actions.
2. **Turret-placement quality vs ore-harvest economics:** `wanted_attack_tiles` couples attack-scoring back into harvest/secure cost decisions, so the bot will pay more for an ore tile that unlocks a strong placement.
3. **Fed-mask redesign:** v872's fed mask was just one-hop conveyor targets. Hades's is "would a turret here be loaded?" with multi-hop propagation and source-adjacency. Used as the placement candidate set.
4. **Stale-position hygiene:** `& map_info._bm_visible` filters added to turret enemy-bot lookups to avoid shooting at stale comms positions.
5. **Friendly-distance tiebreak:** attack.py uses `_friendly_distance_score` to spread bots out when multiple equal targets exist.
6. **Comms simplified:** marker-based comms removed; only fixed launcher-order and symmetry broadcasts remain.
7. **Coordination layer removed:** the "crowded claim" deconfliction in builder.py is gone — bots no longer announce targets to peers.
8. **Disrupt state removed:** `disrupt.py` (which routed bots to disrupt enemy-claimed ore) no longer exists.
