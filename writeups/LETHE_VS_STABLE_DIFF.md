# Lethe vs Lethe_stable — Full Diff Analysis

A complete accounting of every change between `Lethe_stable` (older, stronger) and `Lethe` (newer, weaker). Each entry lists what changed and why it is a plausible regression.

A note on the diff itself: every `Lethe_stable/*` file has **CRLF** line endings, every `Lethe/*` file has **LF**. Raw `diff` output shows every line as changed — all comparisons below were done with `--strip-trailing-cr`. This is cosmetic, but it also means whoever wrote the new version effectively rewrote every file from scratch, which is a lot of surface area for regressions.

Line-count deltas after normalizing line endings:

| File | +added | −removed |
|---|---|---|
| `units/states/attack.py` | **+550** | −230 |
| `map_info.py` | +281 | −135 |
| `pathing.py` | +116 | −101 |
| `units/core.py` | +53 | −6 |
| `units/states/explore.py` | +39 | **−61** |
| `units/states/harvest.py` | +35 | −18 |
| `units/builder.py` | +34 | −16 |
| `main.py` | +25 | −21 |
| `units/states/disrupt.py` | +20 | −7 |
| `units/states/heal.py` | +20 | −23 |
| `units/states/route.py` | +18 | −28 |
| `units/states/sabotage.py` | +9 | −4 |
| `comms.py` | +9 | −6 |
| `comms_positional.py` | +3 | −5 |
| `units/turret_sentinel.py` | +2 | −1 |
| `units/turret_gunner.py` | +1 | 0 |
| `log.py` | +2 | −2 |
| `comms_stats.py` | +1 | −3 |
| `units/turret_breach.py` | 0 | 0 |
| `units/turret_launcher.py` | 0 | 0 |

---

## TL;DR — most likely regression drivers, in rough order

1. **`harvest.py`: harvest-zone filter is commented out.** `harvestable_ore()` no longer intersects with `units.builder._harvest_zone`. Builders will path into enemy territory for ore and die to enemy turrets. (See §8.)
2. **`explore.py`: smart flood-fill replaced with random target picking.** The whole spread-out-from-other-bots algorithm was deleted in favor of `random.randint` within the map. Builders clump and re-explore each other's areas. (See §7.)
3. **`pathing.py`: `non_walkable_cost` removed.** Walking on empty ground and walking on roads/conveyors now cost the same in the movement BFS. The pressure toward existing infrastructure is gone, so builders freely stomp across undeveloped tiles. (See §5.)
4. **`pathing.py`: `voronoi_claim` tiebreak inverted.** The old return was `my_claimed & claims` (conservative — others win ties). The new return is `~(all_claimed & ~my_claimed) & claims` (liberal — I take anything not strictly owned by someone else). Combined with the `route.py` 5×5 override, this causes many more multi-builder collisions on the same target. (See §5 and §10.)
5. **`builder.py`: claim expiry stretched from 3→50 rounds outside vision.** If a builder dies holding a claim, other builders treat that tile as claimed for 50 rounds instead of 3. Lots of ghost claims suppressing work. (See §6.)
6. **`units/states/attack.py`: attack score promoted to 8 for non-roaded candidates.** It now beats heal (7). Builders ignore enemy bots in our territory and damaged buildings to go place turrets. (See §11.)
7. **`units/states/heal.py`: low-priority (score 2.5) heal is commented out.** The bot no longer responds to enemies outside our territory at all. (See §11.)
8. **Four new permanent blacklists**: `cant_attack`, `cant_disrupt`, `cant_heal`, `cant_sabotage`. Any tile that one builder fails to reach gets banned for the whole team for the rest of the game, with no re-validation when the map state changes. (See §9, §11, §12, §13.)
9. **`map_info.py`: `_bm_conv_stuck` marks conveyors whose resource id hasn't changed between two observations and removes them from route targets.** Normal full-pipeline rhythm (resource sitting on a conveyor for one tick) can trigger this and make the bot think its own infrastructure is broken. (See §4.)
10. **`map_info.py`: enemy-turret threat is no longer added to `get_avoid`.** The four-line block that ORs threat into the avoid mask is commented out. Threat is still applied via `threat_cost` in the BFS, but it is no longer a hard avoid — builders will cross threatened tiles when the cost arithmetic says it's cheaper. (See §4.)

The remaining sections catalogue everything else.

---

## 1. `main.py` — profiler restructuring only (non-gameplay)

- Per-turn profiler (was lifetime-cumulative). Profile output is now only written for *timed-out* turns and accumulated across them.
- New fields on `Player`: `accumulated_stats`, `timeout_count`. `self.profiler` removed.
- Profile output adds a `Timed-out turns:` line.

No gameplay impact.

---

## 2. `log.py`

- `DEBUG_LOGGING` and `DRAW_DEBUG` both flipped `False` → `True`.

**Concern:** enabling `DRAW_DEBUG` adds `draw_indicator_line`/`draw_indicator_dot` calls on hot paths (e.g. the new `_draw_attack_candidates` in `attack.py`, the purple-dots block in `builder.py` for `_bm_conv_stuck`, debug overlays in `route.py`/`harvest.py`). Possible CPU cost against the 2 ms budget — `main.py` flags timeouts at 2 ms. Also noisy logging can blow past turn budgets on large maps.

---

## 3. `comms.py` / `comms_positional.py` / `comms_stats.py`

### `comms.py`
- `decode_visible_marker()` return shape changed from `(val, sender_pos)` → `(val, pos, sender_pos)`. Propagates into `builder.py` (new unpacking below).
- `mark()` now skips placing on its own tile (`if pos == rc.get_position(): continue`). Safe.
- `rc.get_tile_builder_bot_id(pos)` replaced with `map_info.has_builder_bot(pos)`. Pure perf.
- `comms_positional.encode_sample_bits(pos, sym)` is **re-enabled** (was commented out as "effectively unused" — per the stable summary file). The positional encoding sent by markers now actually contains data.
- Added a `has_builder_bot` guard before destroying a tile for marker placement.

### `comms_positional.py`
- `COMMS_SAMPLE_DISTANCE` 8 → 7.
- `get_corresponding_pos_by_symmetry(marker_pos, sym_bits)` → `get_corresponding_pos(marker_pos)` in both encode and decode. The encode/decode pair no longer considers receiver-side symmetry state.

### `comms_stats.py`
- Only `from __future__` removed + import reordering. No behavior change.

**Concern:** re-activating `encode_sample_bits` + using a non-symmetry-aware corresponding-position function means markers are now carrying environment samples keyed to a possibly-wrong reference. If the two teams have different sym beliefs, they decode samples against different reference points. Stable shipped with this disabled *for a reason*.

---

## 4. `map_info.py`

### New state
- `_bm_conv_stuck: int` — conveyors whose stored resource stack *id* matched across two consecutive observations. Treated as "not moving → bad destination."
- `_conveyor_resource_id: list[int]` — last-observed stack id per tile, used to populate `_bm_conv_stuck`.
- `_building_team_idx: list[int]` — per-tile team index (replaces linear scan of `_bm_team[i]`). Pure perf.
- `_env_idx_by_tile: list[int]` — per-tile env index. Pure perf, except `env_idx(n)` is now O(1).
- `_board_mask: int` — cached `(1 << (w*h)) - 1`. Pure perf.
- `_CONV_PROP_DEPTH = 3` constant (was hard-coded `3` in two loops).

### Semantic changes
- **`_conv_reverse` now tracks conveyors of BOTH teams.** Stable only tracked `my conveyors whose output targets tile tn`. Any logic that used `_conv_reverse` as "my feeders" (sabotage's turret-feeding protection, route's resource tracing, etc.) is now reading mixed-team data. Downstream callers that didn't also AND with `_bm_team[my_team_idx]` will misbehave.
- **`_building_conv_target` default −1 (was 0).** Tile 0 is (0,0), a real tile. Old code using `if _building_conv_target[n]:` as truthy was buggy — fix is welcome, but callers using the default value as a sentinel need to be audited.
- **`_bm_dead_end` semantics inverted.** Comment in stable: *"routable conveyors whose output is not connected to ore-accepting network"* (source conveyors). Comment in new: *"tiles that dead-end conveyors point into (output tiles)"* (destination tiles). Every consumer of `_bm_dead_end` is affected:
  - `route.py::_dead_end_conveyors()` — old: source-conveyors, new: output-tiles. Route no longer knows *which* conveyor is bad, only where the broken flow arrives.
  - `heal.py::_try_barrier_dead_ends()` — rewrote to treat dead_ends as output tiles directly; consistent with the new semantics.
- **Dead-end detection rules changed:**
  - My conveyor pointing into an enemy hard (non-road, non-marker) building → mark as dead end (new).
  - Output on a marker → *not* a dead end (new).
  - Output on unseen territory → *not* a dead end (new; was marked dead).
  - Old marked the source `lsb`; new marks `tbit` (the output), and *only if `is_loaded`*. Unloaded dead-end chains are now invisible to routing.
- **Stuck-conveyor exclusion:** `_compute_route_targets()` ends with `result &= ~_bm_conv_stuck`. Any conveyor whose resource stack id was the same for two consecutive observations (which is *normal* rhythm when a stack sits on a tile for a tick) is removed from route targets.
- **`get_avoid` threat-inclusion block is commented out.** Stable added `_bm_enemy_turret_threat` to the avoid mask when the bot wasn't already inside it. New version leaves threat as only a *cost* factor inside `bfs_move`. Builders will walk through turret fire when the path arithmetic prefers it.
- **`is_tile_passable(pos)` now returns False when any builder bot (including self) is on the tile.** Stable's version checked building passability only.

### New helpers
- `has_builder_bot(pos, include_self=False)` — exported, widely adopted as a replacement for `rc.get_tile_builder_bot_id(...)`.
- `_clear_downstream_conv_bits(n, start_n, exclude, ...)` — invalidates predicted resource bits up to 3 tiles ahead when a conveyor changes. Used in multiple places in `update()` / `update_at()`. Intended bug fix for propagation staleness after rebuild.

### Perf refactors (mostly safe)
- Symmetry-completion loop now iterates the bitmask instead of scanning W×H. Uses `_env_idx_by_tile` to copy env.
- Conveyor resource propagation in `update()` tracks `freshly_loaded` this turn and short-circuits on it so in-flight changes aren't clobbered.
- Linear scans over `_bm_env[i]`, `_bm_team[i]`, etc. replaced with direct index lookups.

### Bugs / risks introduced
- **`_bm_conv_stuck` false positives.** A stack id that happens to match between two observations (e.g. stack 42 delivered and stack 42 spawned the next tick, or a conveyor genuinely holding the same stack for one tick because downstream was momentarily full) marks the conveyor as "stuck." This blacklists normal-rhythm supply lines.
- **`get_avoid` no longer a hard avoid for turret threat.** Combined with `non_walkable_cost = 0` in `pathing.py`, BFS weighting changes are more likely to route through threat.
- **`_bm_dead_end` semantic flip** not audited in all consumers (route, heal, and anything else that touches this mask).
- **`_conv_reverse` now mixed-team.** Every caller must intersect with the owning team to be correct. Stable's `sabotage.py` turret-feeder walk depended on `_conv_reverse` being my-team-only.

---

## 5. `pathing.py`

### Constants
- `non_walkable_cost = 1` **deleted.** Empty ground no longer costs +1 relative to road/conveyor in `bfs_move`.

Implication: the entire "prefer existing infrastructure" bias is gone from movement. Builders will freely cross undeveloped terrain when a diagonal saves a step, even when parallel road exists.

### `voronoi_claim`
- Added iteration cap: `while ... and c < 10`. Stable had no cap.
- Return value changed:
  - Stable: `return my_claimed & claims` — only tiles I strictly reached first.
  - New: `return ~(all_claimed & ~my_claimed) & claims` — all tiles *not* strictly won by others, i.e. I also claim ties.
- Effect: the global tiebreak convention inverted. Every state calling `voronoi_claim(my_mask, claimed_senders[...], candidates)` now claims shared-boundary tiles. Combined with the 5×5 override in `route.py`, multi-builder collisions on the same target become common.

### `bfs_move`
- Internal representation collapsed from 8 cost buckets (walkable × barrier × threat) to 4 (barrier × threat), because `non_walkable_cost` is gone.
- Path-reconstruction scan reworked:
  - Walks layers from optimal (`i - step_cost - extra_cost`) upward instead of a single direct lookup. More forgiving but also more expensive.
  - Adds an explicit guard `if prev_bit & builders_mask: continue` (skips any neighbor tile containing a builder bot during reconstruction).
  - Adds `if prev_bit & avoid: continue` (skips explicitly-avoid-masked tiles).
  - Tiebreak key gets a new leading component `k_wk = 0 if (prev_bit & walkable) else 1` — walkable (roads/conveyors) is preferred *as a tiebreaker*, but no longer as a *cost*.
- On path-reconstruction failure: prints `"bfs move miss"` to stdout. Potentially noisy.

### `move_to`
- `avoid_empty` parameter **removed**. Stable's explore state passed `avoid_empty=True` when titanium was low to push bots toward unseen tiles. That behavior is gone.
- Base avoid mask changed from `get_avoid(False, True, False)` (avoid builders) → `get_avoid(False, False, False)` (don't avoid builders). Builder-avoidance moved into BFS path reconstruction (see above).

### `calculate_conveyor_path`
- `start_mask = 1 << _building_conv_target[start.x + start.y * w]` → `start_mask = 1 << (start.x + start.y * w)`. This looks like a bug fix: the old version started the route BFS from the conveyor's *output* tile, not the conveyor itself.
- `_get_conveyor_targets_and_avoid` `conveyor=` parameter dropped (the old logic that unmasked a specific target when re-routing a particular conveyor is gone).
- Route target set tightened: `_bm_route_targets & ~_bm_conv_raw_ax` (anything that's not raw ax) → `_bm_route_targets & (_bm_conv_ti | _bm_conv_refined)` (only *known* ti/refined). Newly-built empty conveyors aren't route endpoints anymore.
- `calculate_conveyor_path` now unmasks `_conv_reverse[start]` from the avoid set so feeder conveyors for the start tile aren't treated as blocked by the routing BFS.
- New: ti/refined routes avoid tiles cardinally adjacent to axionite ore (so a future ax harvester there doesn't contaminate the flow). Landlocked ax ore is exempt.
- Several `DRAW_DEBUG` indicator lines commented out.

### `move`
- `rc.get_tile_builder_bot_id(new_pos) != None` → `map_info.has_builder_bot(new_pos)`. Perf.

### `move_adjacent`
- Same `get_avoid(False, True, False)` → `get_avoid(False, False, False)` change.
- Uses `has_builder_bot` instead of the raw controller call.

### Risks
- The `non_walkable_cost` deletion is the single biggest pathing regression — builders no longer prefer roads, so they trample fields and ignore the whole "build a road while passing through" micro that the stable version relied on.
- The inverted voronoi-claim tiebreak is a coordination regression — two bots will agree on the same target more often.
- Removing `avoid_empty` kills the low-titanium exploration bias.

---

## 6. `units/builder.py`

### Claim pruning — the expensive change
- Stable: for every nearby tile in vision, prune `_target_rounds[i][idx]` / `_sender_rounds[i][idx]` if older than 3 rounds.
- New: build `vision_mask` for this turn. For every claim:
  - If the claimed tile is in vision: expire after 3 rounds (same as before).
  - If not in vision: expire after **50 rounds**.
- Special-case flag 7 (heal — uses enemy UIDs, not tile indices): age-based 3-round prune regardless of vision.

Rationale is presumably "if I haven't seen the claimer, trust their claim longer so I don't duplicate work." Real effect: if a claimer dies, the team treats their tile as claimed for ~50 rounds. Work on that tile stalls.

### Other
- Message tuple unpacked as 4 elements (`v, marker_pos, sender_pos, estimated_turn`) — mirrors the comms.py return-shape change.
- `comms_positional.apply_message(marker_pos, sym, sample)` now called on every received message (side-effects the positional model).
- Uses `map_info._board_mask` instead of recomputing `(1 << w*h) - 1`. Perf.
- Debug: purple dots drawn on `_bm_conv_stuck` tiles if `DRAW_DEBUG`.

---

## 7. `units/states/explore.py`

**Near-complete replacement of the exploration algorithm.**

### Stable algorithm
1. Seed bitmask from: all other builders' claimed tiles + intermediate points every 5 Chebyshev steps toward each claim + my position.
2. Flood-fill from seeds on passable tiles for up to 100 iterations.
3. The *last* frontier ring = tiles maximally far from every seed. Pick randomly from the unclaimed subset of that ring.
4. If titanium is low (< 2× harvester cost), also avoid seen-empty tiles (`avoid_empty=True` on `move_to`) — pushes toward truly unseen territory.

### New algorithm
1. First call per unit: pick a random `(dx, dy)` in `[-10, 10]²` with `dx² + dy² ≤ 100`, within bounds.
2. Every subsequent call: pick a uniformly random tile with a 3-tile margin from every edge. If the map is too small, pick anywhere.

That's it. No seeding, no flood-fill, no claim-avoidance, no passable check, no titanium-aware bias. The `avoid_empty` flag was also deleted from `move_to`.

This is the single most obvious gameplay regression. Exploration lost its coordination *and* its terrain awareness.

---

## 8. `units/states/harvest.py`

### The big one
- In `harvestable_ore()`, `& units.builder._harvest_zone` is **commented out**.

Consequence: "harvestable ore" is no longer restricted to tiles inside this team's Voronoi partition. Builders will path into the enemy's half of the map to harvest there, cross enemy turret threat in the process (see `get_avoid` change in §4 and `non_walkable_cost` deletion in §5), and die.

### Other
- Axionite harvesting unlocks at round **750** instead of round 1000. Still requires an existing ti harvester.
- New exclusion: `ax_ore_near_non_raw = ax_ore & expand_manhattan(my ti/refined conveyors)`. Prevents placing axionite harvesters adjacent to titanium supply lines (contamination). Reasonable.
- Refactor: local `CARD` list replaced with `map_info._CARDINAL`. Cosmetic.
- `map_info.has_builder_bot(p)` safety guards added before several `rc.destroy(p)` calls.
- Enemy-road-on-ore handling moved earlier in `run()` (before the adjacent-move step). The old flow fired move-cooldown on `nav.move_adjacent` first and then couldn't step onto the ore. Ordering fix looks correct.
- `draw_mask(possible_ore(), ...)` / `draw_mask(harvestable_ore(), ...)` debug overlays added.

---

## 9. `units/states/disrupt.py`

- **New restriction**: for the first 200 rounds, disrupt targets are AND'd with `my_zone = expand_chebyshev⁵(my_pos_mask)` — i.e. only ore within Chebyshev 5 of the current bot. After round 200, the old behavior returns.

  Early-game is exactly when bots are scattered and can most cheaply deny enemy ore by dropping barriers on the far side of the map. Restricting to 5-Chebyshev for 200 rounds effectively turns disrupt off for the opening.

- `cant_disrupt` blacklist added. `_my_claims()` merges unreachable targets into it, permanently. No re-check.
- `has_builder_bot` guard before destroy.

---

## 10. `units/states/route.py`

### Semantic follow-ups from `map_info.py`
- `_dead_end_conveyors()` adapts to the new output-tile semantic of `_bm_dead_end` and additionally excludes harvester tiles (`~_bm_et[_IDX_HARVESTER]`). Otherwise harvesters feed themselves as dead-end outputs.
- `_all_route_targets` trimmed: stable included `(BRIDGE | CORE) & my_team`; new is `CORE & my_team`. Bridges are no longer terminal route targets in their own right, only part of the accepting set. Routes that were previously happy to terminate at an existing bridge now only accept termination at the core.

### Claim handling
- `_my_claims()` now returns `voronoi_claim(...) | (candidates & my_5x5)` where `my_5x5 = expand_chebyshev²(my_mask)`. Any candidate within 2 Chebyshev of the bot is claimed *regardless* of voronoi. Two nearby builders can both route to the same target.

### Behavior removed
- The whole "enemy road on target / enemy bot near target → try `calculate_conveyor_path(..., update=True)` for an alternate path" reroute block is gone. The print debug statement too. Route now just uses the original path without conflict resolution.
- `can_heal_road` logic (allow pathing through enemy road if enemy bots are within 3 Chebyshev) removed.

### Claim marker
- Stable marked the destination tile (`best.x + best.y * width`). New marks `claim_n = target_conveyor[0].x + target_conveyor[0].y * width` — the first conveyor tile in the path. Changes which tile shows up in other bots' `claimed_senders[4]`.

### Safety
- `has_builder_bot` guard before destroying a conveyor when rerouting.

---

## 11. `units/states/heal.py`

### Scoring changes
- Stable: returns **7**, **2.5**, or **0**.
- New: returns **7** or **0**. The `else: return 2.5` branch is commented out.

Implication: when an enemy builder is nearby but outside our harvest zone, heal no longer scores 2.5. Nothing beats explore (1) → builders do nothing about distant enemy-territory threats. Combined with the attack-score bump to 8 (next section), heal *also* loses priority against attack when both are applicable.

### Adaptation to new `_bm_dead_end` semantic
- `_try_barrier_dead_ends()` simplified. Old walked `_building_conv_target[n]` to find output tiles. New just treats `_bm_dead_end` directly as output tiles (consistent with the mask's new meaning).

### Other
- `cant_heal` blacklist added in `_my_claims()`: targets unreachable by `nav.closest` are unioned into `cant_heal` permanently.
- `has_builder_bot` guard before destroying a conveyor during the barrier-dead-ends micro.
- New end-of-run: `if rc.can_fire(rc.get_position()) and rc.get_team(rc.get_tile_building_id(rc.get_position())) != rc.get_team(): rc.fire(rc.get_position())` — fire an own-tile attack on an enemy building under the bot.

### Risk
- That end-of-run fire block assumes `rc.get_tile_building_id(rc.get_position())` is non-None. If the bot is standing on an empty tile, `rc.get_team(None)` will raise. The outer `can_fire` check may cover this (`can_fire` returns False when there's no attackable target on-tile), but the team comparison runs *after* `can_fire`. This should be verified.
- The 2.5 heal tier was specifically how the bot deterred enemies *before* they entered our territory. Disabling it is a strategic regression.

---

## 12. `units/states/sabotage.py`

- Sabotage targets tightened: old was enemy conveyor/splitter/bridge minus armoured; new further AND's `& (_bm_conv_ti | _bm_conv_raw_ax | _bm_conv_refined)` — i.e. only *known-loaded* enemy conveyors.

  Upside: don't waste shots on empty conveyors.
  Downside: if resource propagation is wrong (see `_bm_conv_stuck` + freshly-loaded exclusion in `map_info.py`), loaded conveyors may not be tagged correctly. Stable had a simpler filter and hit more targets.

- `cant_sabotage` blacklist added.
- `board` local replaced with cached `_board_mask`. Perf.

---

## 13. `units/states/attack.py` — near-total rewrite (+550/−230)

### Scoring constants
All enemy-building scores rebalanced:

| Building | Stable | New | Δ |
|---|---|---|---|
| CORE | 100 | **96** | −4 |
| BREACH | 25 | 24 | −1 |
| SENTINEL | 20 | 20 | 0 |
| GUNNER | 20 | 20 | 0 |
| FOUNDRY | 15 | 16 | +1 |
| HARVESTER | 10 | **12** | +2 |
| LAUNCHER | 15 | **8** | **−7** |
| ARMOURED_CONVEYOR | 3 | 4 | +1 |
| BARRIER | 1 | **4** | +3 |
| BRIDGE / CONVEYOR / SPLITTER | 2 | 2 | 0 |

Launcher priority collapsed by almost half; barrier value quadrupled. Attack now values shooting at barriers ~half as much as shooting at a conveyor... no wait, twice as much as a conveyor. Placing turrets aimed at barrier walls is now preferred over aiming at bridges/conveyors/splitters.

### New tunables
- `SCORE_THRESHOLD_FACTOR = 0.5` — only candidates whose best direction score is within 50% of the global best survive.
- `MIN_ATTACK_SCORE = 16` — if the global best candidate is below 16, attack state doesn't run at all.
- `GUNNER_SCORE_MULTIPLIER = 4` — gunner scores get ×4 (stable inlined ×5). Gunners now slightly less preferred over sentinels.
- `THREAT_PENALTY = 4` — flat penalty to candidates inside enemy turret threat. Baked into the sentinel/breach score planes by adding +4 to *non-threat* tiles, subtracted inline for gunner.
- `cant_attack` blacklist (same pattern as cant_harvest / cant_sabotage).

### Score return
- Stable: `return 6 if (non_roaded or roaded) else 0`.
- New: `return 8 if non_roaded else (6 if roaded else 0)`.

**This is probably the single biggest gameplay regression in this file.** The state-priority table from the stable summary:

| State | Score |
|---|---|
| Heal (urgent) | 7 |
| Attack | **8 (new)** / 6 (stable) |
| Sabotage | 5 |
| Route | 4 |
| Harvest | 3 |
| Heal (medium) | 2.5 (disabled in new) |
| Disrupt | 2 |
| Explore | 1 |

Attack now outranks heal-urgent (damaged buildings, enemies in our territory). Bots will build turrets instead of defending — against the whole point of heal-at-7.

### Algorithmic rewrite
Replaced per-tile scoring loops with a bit-sliced "score plane" representation:
- `_compute_dir_scores` / `_compute_gunner_dir_scores` build per-direction plane arrays using `_turret_shift_masks`, with bit-sliced addition (`_add_const_to_planes`, `_read_score`, `_max_score_in_mask`, `_ge_threshold_mask`).
- Round-scoped cache: `_round_cache_round`, `_round_cache_attack_candidates`, `_round_cache_sentinel_planes`, `_round_cache_breach_planes`, `_round_cache_gunner_planes`, `_round_cache_loader_blockers`, `_round_cache_need_breach`. Rebuilt once per round by `_ensure_round_cache()`.
- Breach planes only built when at least one candidate is cardinally adjacent to a friendly foundry (short-circuit).
- `_compute_loader_blockers()` computes per-direction blocker bitmasks for all candidate tiles in one pass, instead of computing per-candidate-tile loader sets.

Bugs to watch for:
- `THREAT_PENALTY` is baked into sentinel/breach planes as +4 on non-threat tiles, but applied *inline* for gunner (at `_max_score_in_mask`/`_read_score` time? — actually looking at the code, it's also baked into gunner planes: `if THREAT_PENALTY: _add_const_to_planes(planes, THREAT_PENALTY, non_threat)` sits inside `_compute_gunner_dir_scores`). This is consistent. Fine.
- `_NUM_PLANES = 10` caps counters at 1023. A tile attacking three enemy foundries + the core would be 16×3 + 96 = 144 — far below 1023. Safe.
- The "one sentinel per harvester" nicety: harvesters cardinally adjacent to an existing friendly sentinel are excluded from loader-blocker consideration. Intended to stop multiple sentinels from piling up on the same feeder. Useful.

### Placement candidates
- `_high_value_targets()` and `_my_turret_coverage()` + `_sentinel_all_reach(targets)` filtering — **all deleted**.

This matters. Stable restricted candidates to positions from which a sentinel could hit at least one *high-value, not-already-covered* enemy building. New version is "any position whose best direction score is within 50% of the global best and above MIN_ATTACK_SCORE." There is nothing that excludes a candidate because it would duplicate coverage of an enemy building my existing turrets already hit. Bots will build redundant turrets on the same enemy target.

### Danger filter for enemy-road candidates
- Stable: Manhattan-6 expansion of enemy bots. Filters `danger & enemy_roads` from candidates.
- New: Chebyshev-4 expansion of enemy bots, unioned with `_bm_enemy_launch_adj`. Filters `danger_for_roads & enemy_roads`.

Chebyshev-4 is smaller than Manhattan-6 in most directions — so enemy-road candidates near enemy bots are filtered *less* aggressively. Builders will step onto enemy roads closer to enemy bots and be easier to pressure.

### Adjacent-evaluation micro
- Replaced `rc.get_nearby_units(4)` scan for enemy bot count with `expand_chebyshev²(my_pos_mask) & _bm_enemy_bots`. Pure perf.
- Added `has_builder_bot` guard before destroying own building at the target tile.

---

## 14. `units/core.py`

- **Spawn threshold changed**:
  - Stable: `scaling * 0.5 + 300 < titanium` → spawn.
  - New: `scaling * 0.7 + 200 < titanium` → spawn.
  - Break-even at scale = 500% (both = 550 Ti). Below 500% scale, new threshold is *lower* — spawns earlier. Above 500%, new threshold is higher — spawns later.
- **New "smart first spawn" on turn 1**: `get_closest_titanium_tile()` finds the closest visible titanium ore without an allied harvester, then spawns the single builder toward that tile (one of the 9 core-ring positions in the direction of the ore).
- `init()` now calls `map_info.init(c)`. But `main.py` already calls `map_info.init(c)` immediately before `self.me.init(c)` — this is a redundant double-init. Unlikely to cause bugs but wasteful.
- Dead comment `# if rc.get_current_round() == 100: rc.resign()` shifted to round 200.

The first-spawn targeting seems fine in isolation but worth checking it doesn't interact badly with the new explore (which is now random and doesn't account for the first bot's direction).

---

## 15. Turrets (`turret_gunner.py`, `turret_sentinel.py`, `turret_breach.py`, `turret_launcher.py`)

Only whitespace / import changes (`Direction` dropped from sentinel's imports, blank line added). **No functional change.** Breach and launcher remain stubs.

---

## Appendix — blacklist additions (full inventory)

Newly introduced "fail once, forever banned" blacklists, by file:

| File | Blacklist | Populated when |
|---|---|---|
| `units/states/attack.py` | `cant_attack` | `nav.closest(non_roaded \| roaded)` returns None |
| `units/states/disrupt.py` | `cant_disrupt` | `nav.closest(available)` returns None |
| `units/states/heal.py` | `cant_heal` | `nav.closest(targets)` returns None |
| `units/states/sabotage.py` | `cant_sabotage` | `nav.closest(targets)` returns None |
| `units/states/harvest.py` | `cant_harvest` | already existed in stable |

None of the new blacklists have a re-check path. A tile unreachable for one bot once is banned for *every* bot for the rest of the game. Map state changes (enemy builds a road, ally clears a wall, threat zones shift) do not clear the blacklist.

---

## Appendix — coordination changes summarized

| Change | Location | Direction |
|---|---|---|
| `voronoi_claim` tiebreak inverted | `pathing.py` | Less conservative — more collisions |
| Route 5×5 override | `route.py` | Always claim nearby, ignore voronoi |
| Claim expiry 3 → 50 rounds outside vision | `builder.py` | Ghost claims linger |
| Explore smart flood-fill → random | `explore.py` | Zero coordination |
| Harvest zone filter removed | `harvest.py` | All bots eligible for all ore, compete for same tiles |

Cumulatively: the new version has *much less* per-bot differentiation when picking targets. Combined with the attack score bump to 8 and the low-priority heal removal, the state machine is both more aggressive (attack > heal) and less coordinated (weaker claims).

---

## Suggested first things to back out if you want to test hypotheses fast

Rank-ordered by expected impact per line of code reverted:

1. Un-comment `& units.builder._harvest_zone` in `harvest.py::harvestable_ore()`. One line.
2. Revert `units/states/explore.py` to stable's flood-fill algorithm. Self-contained.
3. Revert attack score return to stable's `return 6 if (non_roaded or roaded) else 0`. One block in `attack.py::score()`.
4. Restore the low-priority 2.5 branch in `heal.py::score()`. One block.
5. Restore `non_walkable_cost = 1` in `pathing.py` (and the 8 cost-tier frontier plumbing). Larger edit.
6. Revert `voronoi_claim` return to `my_claimed & claims`. One line.
7. Revert claim expiry in `builder.py::handle_comms()` to a flat 3-round rule. One block.
8. Un-comment the `get_avoid` threat-inclusion block in `map_info.py`. Four lines.
9. Revert `_bm_conv_stuck` exclusion from `_compute_route_targets`. One line (`result &= ~_bm_conv_stuck`).
10. Remove the four new `cant_*` blacklists, or at minimum add a re-validation pathway.

If time-to-signal is a concern, try #1 + #2 + #3 + #4 first — they are the cheapest to revert and most likely to individually move the needle.
