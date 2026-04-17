# Lethe Bot — Complete Technical Reference

This document is designed so that someone with zero prior context can read it and understand exactly how Lethe works — every system, every decision, every micro detail.

---

## Table of Contents

1. [Game Context & Lethe's Strategy](#1-game-context--lethes-strategy)
2. [Architecture & Control Flow](#2-architecture--control-flow)
3. [The Bitmask Engine (`map_info.py`)](#3-the-bitmask-engine-map_infopy)
4. [Communication System (`comms.py`)](#4-communication-system-commspy)
5. [Pathfinding & Movement (`pathing.py`)](#5-pathfinding--movement-pathingpy)
6. [Builder Bot State Machine (`units/builder.py`)](#6-builder-bot-state-machine-unitsbuilderpy)
7. [State: Explore](#7-state-explore)
8. [State: Disrupt](#8-state-disrupt)
9. [State: Harvest](#9-state-harvest)
10. [State: Route](#10-state-route)
11. [State: Heal](#11-state-heal)
12. [State: Sabotage](#12-state-sabotage)
13. [State: Attack](#13-state-attack)
14. [Core Unit (`units/core.py`)](#14-core-unit-unitscorepy)
15. [Turret AI](#15-turret-ai)
16. [Support Modules](#16-support-modules)

---

## 1. Game Context & Lethe's Strategy

### What is Cambridge Battlecode?

A turn-based simulation where two teams compete on a grid map. Each team starts with a 3×3 **core** that spawns 1×1 **builder bots**. Builder bots walk around, build structures, and destroy enemy structures. The economic loop is: build **harvesters** on ore tiles to mine resources, then connect them via **conveyors** to your core (or to turrets that consume ammo). Resources flow along conveyor chains automatically. You spend titanium (Ti) on most things and refined axionite (Ax) on specialized buildings like breach turrets.

### Lethe's Overall Strategy

Lethe plays a **macro-focused economic game**:

1. **Explore** the map to discover ore.
2. **Harvest** — secure ore with barriers on all 4 cardinal sides, then place harvesters.
3. **Route** — build conveyor chains connecting harvesters to the core/turrets.
4. **Attack** — place turrets (sentinels, gunners, breaches) at conveyor endpoints to hit enemy buildings.
5. **Heal** — chase enemy builder bots that enter our territory and repair damaged buildings.
6. **Disrupt** — block enemy ore tiles with barriers so they can't harvest.
7. **Sabotage** — walk onto enemy conveyors and fire them to cut supply lines.

The priority system means higher-value actions (attack > sabotage > route > harvest > disrupt/heal > explore) take precedence when available. Each builder independently picks the best thing to do each turn.

Lethe divides the map into "my half" and "enemy half" using a **Voronoi partition** from both cores, so it doesn't waste time fighting over the enemy's territory for harvesting — but it does disrupt, sabotage, and attack there.

---

## 2. Architecture & Control Flow

### File Structure

```
bots/Lethe/
├── main.py              # Entry point — dispatches to unit module
├── map_info.py          # Bitmask map state engine (~1475 lines)
├── comms.py             # Marker-based communication
├── comms_positional.py  # Spatial environment encoding (sample_bits)
├── comms_stats.py       # Optional comms telemetry
├── pathing.py           # BFS pathfinding, movement, conveyor routing
├── log.py               # Debug logging + visualization toggle
└── units/
    ├── core.py           # Core spawning logic
    ├── builder.py        # Builder main loop + state selection
    ├── turret_gunner.py  # Gunner AI
    ├── turret_sentinel.py# Sentinel AI
    ├── turret_breach.py  # STUB (not implemented)
    ├── turret_launcher.py# STUB (not implemented)
    └── states/
        ├── explore.py
        ├── disrupt.py
        ├── harvest.py
        ├── route.py
        ├── heal.py
        ├── sabotage.py
        └── attack.py
```

### Sandboxing Model

Every unit (core, builder bot, turret) is a separate `Player` instance. All Python globals are per-unit — when two builder bots both import `map_info`, they each get their own copy of every variable. This means each unit maintains its own map knowledge, its own bitmasks, its own pathfinding state.

### Entry Point (`main.py`)

```python
class Player:
    def run(self, c: Controller):
```

On first call:
1. Detects entity type via `c.get_entity_type()`
2. Sets `self.me` to the appropriate module (core/builder/gunner/sentinel/breach/launcher)
3. Calls `map_info.init(c)`, `comms.init(c)`, `self.me.init(c)`
4. Seeds `random` with `c.get_current_round()`

Every subsequent call: `self.me.run()`

The whole turn is wrapped in try/except. If a turn takes >2ms, it logs a warning and draws a red indicator line. Optional cProfile profiling is available via `ENABLE_PROFILER`.

### Per-Turn Flow for a Builder Bot

```
builder.run()
  ├── map_info.update(recompute=False)     # Scan visible tiles, update bitmasks
  ├── handle_comms()                        # Decode marker messages from allies
  ├── map_info.recompute_derived()          # Rebuild derived bitmasks
  ├── pathing.rebuild_broken_barriers()     # Repair barriers we walked through
  ├── Update harvest zone (Voronoi or radius)
  ├── Score all 7 states, run highest
  ├── heal._do_best_heal()                  # Always heal adjacent damaged building
  └── Self-heal if possible
```

---

## 3. The Bitmask Engine (`map_info.py`)

This is the heart of Lethe. Nearly every decision flows through bitmask operations on Python arbitrary-precision integers.

### Core Concept

Each tile on a W×H map is a single bit at position `x + y * W` in an integer. A bitmask holding bit positions for multiple tiles lets you do set operations:

| Operation | Code | Meaning |
|-----------|------|---------|
| Union | `a \| b` | Tiles in A or B |
| Intersection | `a & b` | Tiles in both A and B |
| Difference | `a & ~b` | Tiles in A but not B |
| Contains | `mask & (1 << n)` | Is tile n in the set? |
| Iterate | `lsb = mask & -mask` | Extract lowest set bit |

This means operations like "all ore tiles not in enemy turret threat range that are within my harvest zone" become a single line of bitwise ops:

```python
ore & ~enemy_turret_threat & harvest_zone
```

### Expansion Functions

These are how Lethe does flood-fills, adjacency checks, and range computations — all on bitmasks.

**`expand_chebyshev(mask)`** — King-move expansion (8 directions). Every set bit grows to a 3×3 area. Implementation:
1. Horizontal: `mask | (mask << 1) | (mask >> 1)` (with column masks to prevent wrap)
2. Vertical: `result | (result << W) | (result >> W)`

**`expand_manhattan(mask)`** — Cardinal expansion (4 directions). Every set bit grows to a + shape.

Column masks (`_not_left_col`, `_not_right_col`, etc.) prevent bits at x=0 from wrapping to x=W-1 when shifting. Built as repeating patterns: `_not_left_col` has bit 0,W,2W,3W... cleared.

### Data Storage

**Per-tile arrays** (indexed by `n = x + y * W`):

| Array | Purpose |
|-------|---------|
| `_building_id[n]` | Entity ID of building at tile (0 if none) |
| `_building_et_idx[n]` | Integer index of entity type (-1 if empty) |
| `_building_hp[n]` | Current HP of building |
| `_building_dir[n]` | Direction index for directional buildings |
| `_building_conv_target[n]` | Target tile index for conveyors (-1 if none) |
| `_conv_reverse[n]` | **Bitmask** of my conveyors whose output targets tile n |

`_conv_reverse` is critical — it lets you walk backward through conveyor chains to find what feeds into a tile.

**Primary bitmasks** (directly tracked from vision):

| Bitmask | What it tracks |
|---------|----------------|
| `_bm_et[i]` | All tiles with entity type i (one per EntityType) |
| `_bm_team[i]` | All tiles owned by team i |
| `_bm_env[i]` | All tiles with environment i (WALL, ORE_TI, ORE_AX, EMPTY) |
| `_bm_seen` | Every tile ever observed by this unit |
| `_bm_visible` | Tiles visible this turn |
| `_bm_any_building` | Union of all building bitmasks |
| `_bm_friendly_bots` | Friendly builder bot positions |
| `_bm_enemy_bots` | Enemy builder bot positions |

**Derived bitmasks** (rebuilt every turn from primaries by `recompute_derived()`):

| Bitmask | What it tracks |
|---------|----------------|
| `_bm_blocked` | Walls + harvesters + foundries + turrets + enemy barriers + enemy core area. Tiles pathfinding can't cross. |
| `_bm_conveyors` | All conveyor/armoured_conveyor/bridge/splitter |
| `_bm_conveyor_targets` | Output tile of every tracked conveyor |
| `_bm_my_core_area` | 3×3 area of my core |
| `_bm_their_core_area` | 3×3 area of enemy core (predicted or known) |
| `_bm_routable` | My team's conveyor-type buildings |
| `_bm_route_targets` | Valid endpoints the route state can path toward |
| `_bm_conv_loaded` | Conveyors carrying any resource |
| `_bm_conv_raw_ax` | Conveyors carrying raw axionite |
| `_bm_conv_ti` | Conveyors carrying titanium |
| `_bm_conv_refined` | Conveyors carrying refined axionite |
| `_bm_ti_fed` | Tiles that are the output target of a titanium-carrying conveyor |
| `_bm_ax_fed` | Tiles that are the output target of a refined-axionite-carrying conveyor |
| `_bm_dead_end` | Conveyors whose output isn't connected properly |
| `_bm_enemy_turret_threat` | All tiles any enemy turret can attack |
| `_bm_enemy_launch_adj` | Tiles adjacent to enemy launchers (throw danger zone) |
| `_bm_damaged` | Buildings with HP < max |
| `_bm_very_damaged` | Buildings with HP < max - 2 |

### The `update()` Function

Called every turn. This is ~400 lines of critical logic:

1. **Cache position**: `_my_pos = rc.get_position()`, compute `_nearby_tiles` from vision radius
2. **Scan each visible tile**:
   - **First-time unseen tiles**: Read environment (wall/ore/empty). Test against each remaining symmetry — if the tile's environment doesn't match the flipped tile, eliminate that symmetry.
   - **Building changes**: If the entity ID at a tile changed since last seen, clear old bitmask bits and set new ones. Track HP, direction, conveyor targets.
   - **Markers**: For each friendly marker, call `comms.decode_visible_marker()`. If it's a new message, store `(decoded_value, sender_position, estimated_turn)` in `_new_marker_messages`.
   - **Resource propagation**: For conveyors carrying resources, propagate the resource type downstream through conveyor chains (up to 3 hops). This is how the bot knows "this conveyor chain carries titanium all the way to the core."
3. **Track builder bots**: Update `_bm_friendly_bots`, `_bm_enemy_bots`, `_bot_pos`, `_bot_team`, `_bot_at`.
4. **Record `_max_id_by_round`**: The highest entity ID seen by each round. Used by `comms.estimate_turn()` to determine when a marker was placed.
5. **Resolve symmetry**: When exactly 1 of (horizontal, vertical, rotational) remains, set `_solved_sym = True` and predict enemy core.

### `update_at(pos)` — Single-Tile Rescan

Called after building/destroying something. Reads the tile fresh from the controller and updates all bitmasks. Much cheaper than a full `update()`.

### `update_move()` — Post-Movement Scan

Computes which tiles just entered vision and calls `update_at()` for each. Used after the bot moves to pick up new information.

### Symmetry Detection

Maps in Cambridge Battlecode have one of three symmetries: horizontal mirror, vertical mirror, or 180° rotation. Lethe starts assuming all three are possible and eliminates them by checking: does the environment at tile (x, y) match the environment at the flipped position?

```python
# For horizontal symmetry: flip is (W-1-x, y)
# For vertical symmetry: flip is (x, H-1-y)  
# For rotational symmetry: flip is (W-1-x, H-1-y)
```

When exactly one symmetry survives, `_solved_sym = True` and the enemy core is predicted by flipping our core's position.

When two remain (can happen if we haven't seen enough of the map), `_rush_tiebroken` tracks a random guess for movement purposes.

### Dead-End Detection (`_compute_route_targets()`)

A conveyor is a "dead end" if its output doesn't connect to something useful. The detection:

1. **Output out of bounds** → dead end
2. **Output tile doesn't have an ore-accepting building** (conveyor, turret, core, foundry) → dead end
3. **Raw axionite conveyor feeding into titanium or refined conveyor, or into a ti-harvester-adjacent tile** → dead end (resource mismatch — raw ax needs a foundry, not the titanium network)
4. **Exception**: Enemy conveyor pointing into enemy non-marker building → NOT dead end (enemy's problem)

After marking initial dead ends, there's a **downstream validation pass**: follow empty (unloaded) conveyor chains up to 4 hops. If the chain reaches the core or an unseen tile, it's valid. Then an **upstream propagation pass** walks backward through `_conv_reverse` to mark all feeders of valid conveyors as also valid.

### Enemy Turret Threat (`_compute_enemy_turret_threat()`)

Aggregates all tiles that any enemy turret could hit.

- **Breach/Sentinel**: Uses precomputed shift masks. Groups turrets by direction, then for each direction and each offset in that turret type's attack pattern, shifts the entire group's bitmask at once. This is O(directions × offsets) instead of O(turrets × offsets).
- **Gunner**: Per-turret ray tracing, since gunner rays are blocked by walls. Walk the forward line until hitting a wall or going out of bounds.

### `get_avoid(avoid_conveyors, avoid_builders, avoid_ore)`

Returns a bitmask of tiles the bot shouldn't walk through. Base: `_bm_blocked`. Optional additions:
- `avoid_conveyors=True`: adds conveyors, conveyor targets, my core area (used during routing to not walk over our own supply lines)
- `avoid_builders=True`: adds all builder bots
- `avoid_ore=True`: adds non-landlocked ore

Always adds enemy turret threat unless the bot is already standing in it (so it can escape).

### Lookup Tables

Pre-built mappings for hot-path type checking:
- `_ET_INT[EntityType.BARRIER]` → integer index
- `_IDX_BARRIER` → same, as a module-level constant
- `_IS_CONVEYOR[et_idx]` → bool (is this a conveyor-type?)
- `_IS_BLOCKED[et_idx]` → bool (does this block movement?)
- `_MAX_HP_BY_IDX[et_idx]` → max HP for that type

Turret offset tables:
- `_BREACH_OFFSETS[dir_idx]` — 180° forward cone within distance² 5
- `_SENTINEL_OFFSETS[dir_idx]` — ±1 band around forward line within vision² 32
- `_GUNNER_RAYS[dir_idx]` — forward ray ordered by distance
- `_turret_shift_masks[(dx,dy)]` — precomputed bitmasks for aggregate turret threat computation

---

## 4. Communication System (`comms.py`)

Units communicate by placing **markers** — 1-HP buildings that store a 32-bit value. Allies read these markers when they come into vision.

### Why Markers?

There's no direct messaging API. Markers are the only way to share information — they persist on the map until destroyed and any ally can read them.

### Message Encoding

Each marker's 32-bit value encodes:

```
[31..27] type      (5 bits) — which state placed this (comm flag)
[26..24] sender    (3 bits) — direction from marker to sender's position
[23..15] sample    (9 bits) — environmental samples (reserved, mostly unused)
[14..12] sym       (3 bits) — symmetry status bits (which symmetries are still possible)
[11..0]  location  (12 bits) — target tile index (x + y * W, supports maps up to ~64×64)
```

### Encryption

Values are XOR'd with a deterministic key derived from map dimensions via SplitMix64. Since both teams see the same map dimensions, both teams generate the same key — so this isn't real encryption, it's obfuscation. The intent is to prevent the opponent from trivially reading marker values through the API.

### Key Functions

**`encode(target, type, sym, sample_bits, sender_loc)`** — Packs fields, XORs with key.

**`decode_visible_marker(id, pos)`** — Called by `map_info.update()` for each friendly marker seen. Returns `None` if:
- The bot placed this marker itself (`_my_markers` set)
- The marker was already decoded at this position (dedup via `_marker_id_at`)
Otherwise extracts the sender's approximate position from the direction field and returns `(decoded_value, sender_position)`.

**`estimate_turn(entity_id)`** — Binary-searches `_max_id_by_round` to estimate when an entity was created. Entity IDs are assigned sequentially, so higher ID = created later. Used to determine if a marker message is stale.

**`mark(target_idx, type)`** — Places a marker near this bot encoding the given target and state type. Placement priority:
1. Empty tile (best — doesn't destroy anything)
2. Own marker (overwrites previous message)
3. Own road without builder bot on it

Avoids placing on tiles adjacent to harvesters or on conveyor target tiles (would interfere with the supply chain). Destroys the existing building if needed before placing.

**`get_sym_bits()`** — Returns 3-bit encoding of which symmetries are still possible. Every marker broadcasts this, so symmetry knowledge spreads across all units.

---

## 5. Pathfinding & Movement (`pathing.py`)

### Constants

| Name | Value | Purpose |
|------|-------|---------|
| `bridge_cost` | 6 | Extra cost for bridge jumps in route BFS |
| `barrier_cost` | 15 | Penalty for walking through friendly barrier |
| `threat_cost` | 20 | Penalty for walking through enemy turret/launcher threat |
| `conveyor_end_cost` | 10 | Extra cost when routing targets at conveyor endpoints |
| `non_walkable_cost` | 1 | Extra cost for walking on empty ground (vs road/conveyor) |

### Movement Cost Tiers (`bfs_move`)

`bfs_move` is a weighted reverse BFS from target to start. It uses a **cyclic priority queue** — an array of bitmask frontiers indexed by cost, wrapping around like a circular buffer.

Every tile has a cost based on three binary properties:

| Walkable? | Barrier? | Threat? | Cost |
|-----------|----------|---------|------|
| Yes (road/conveyor/core) | No | No | 1 |
| No (empty ground) | No | No | 2 |
| Yes | Yes (friendly) | No | 16 |
| No | Yes | No | 17 |
| Yes | No | Yes | 21 |
| No | No | Yes | 22 |
| Yes | Yes | Yes | 36 |
| No | Yes | Yes | 37 |

**Walkable** means roads, conveyors, core tiles. Walking on these costs 1. Walking on empty ground costs 2 (base 1 + `non_walkable_cost` 1). This makes the bot prefer existing infrastructure.

**Barrier penalty** (15): walking through a friendly barrier is expensive because the bot will destroy the barrier, build a road, walk through, then repair it later. That costs resources and time. But it's better than a 15+ step detour.

**Threat penalty** (20): walking through enemy turret/launcher range. The bot avoids it unless there's no other way.

The BFS expands every frontier tile by Chebyshev (8-directional), classifies each new tile into one of 8 cost-tier buckets, and inserts into the cyclic queue.

### Path Reconstruction

When BFS reaches the start position, it reconstructs one step toward the target. It checks all 8 neighbors in `visited_layers[prev_layer]` where `prev_layer = current_cost - step_cost - extra_cost`.

**Tiebreaking** (when multiple neighbors are equally valid):

| Priority | Key | Meaning |
|----------|-----|---------|
| k0 | 0=diagonal, 1=cardinal | Diagonals preferred (cover more distance) |
| k1 | 0=moving away from edge, 1=not | When near map edge (<4 tiles), prefer moving inward |
| k2 | 0=neighbor ≥4 from edge, 1=not | Prefer positions well away from edges |
| k3 | 0/1/2 = diagonal family matching | Alternates diagonal families (NE/SW vs NW/SE) to avoid zigzag |

The diagonal-family alternation (k3) is a subtle smoothing mechanism: if the bot went NE last turn and NE the turn before, it switches to NW/SE family. If it went NE then a different direction, it continues NE family. This produces smooth diagonal paths instead of jagged steps.

### Conveyor Route BFS (`bfs_route`)

Separate BFS for finding where to build conveyor chains. Two step types:
- **Cardinals** (cost 1): place a regular conveyor one tile in a cardinal direction
- **Bridges** (cost 6): place a bridge that jumps up to distance² 9 (the full 5×5 Chebyshev-2 zone plus 3-step cardinal jumps)

Target tiles that are conveyor endpoints (not core) start with extra `conveyor_end_cost` (10), biasing toward routing to the core rather than appending to existing conveyor chains.

### `move(dir)` — Single Step

Before actually moving:
1. Check bounds and no builder bot at destination
2. If destination is a friendly barrier: destroy it, build a road in its place, record in `destroyed_barriers` for later repair
3. If destination is buildable: build a road (makes it walkable for future passes)
4. Execute `rc.move(dir)`, call `map_info.update_move()`

### `move_to(target)` — High-Level Movement

1. **Stuck detection**: If same target and same position for `2 + (id % 8)` turns, just move in any available direction. The ID-based offset prevents all stuck bots from unsticking on the same turn.
2. Call `bfs_move()` with avoid mask
3. Extract direction from start→next, call `move()`

### `move_adjacent(pos)` — Move Next To Something

Finds all passable tiles adjacent to `pos` (filtering out occupied tiles), then calls `move_to()` with all of them as targets. Used when the bot needs to be next to something to build/heal/destroy it.

### `closest(targets, pos)` — Nearest Target

Unweighted Chebyshev BFS from `pos`. Flood-fills until hitting any bit in `targets`. Returns `(position, distance)`. Used for quick "which target is nearest?" checks.

### `voronoi_claim(my_mask, others_mask, claims)` — Territory Division

Partitions `claims` tiles between this bot and others by simultaneous Chebyshev flood-fill:

1. Both sides start at their positions
2. Each iteration: expand my frontier, then expand others' frontier
3. **My side expands first** each iteration, winning ties
4. Return `my_claimed & claims`

This is the mechanism that prevents multiple builders from competing for the same target. Some states swap arguments and negate the result to give tiebreak to *others* (conservative approach — avoids two bots both thinking they own the same tile).

### `rebuild_broken_barriers(rc)`

Called every turn. Iterates `destroyed_barriers` dict (position → round destroyed). For each one within action range (distance² ≤ 2):
- Wait at least 1 round after destruction
- If there's a friendly road on the tile, destroy it
- Build barrier to restore it

### `conveyor_cost(dist, scaling)`

Estimates titanium cost of building `dist` conveyors at current scale percentage. Each conveyor costs `3 * scaling`, and scaling increases 0.01 per building placed.

### `raw_ax_foundry_sites()`

Finds valid positions for foundries (needed to process raw axionite into refined):
- Must be adjacent to a titanium harvester on ore
- Not adjacent to an existing foundry
- Not blocked by enemy/friendly buildings
- Has at least 2 open cardinal neighbors (so conveyors can connect)
- Intersection with allied conveyors carrying titanium that are adjacent to core

---

## 6. Builder Bot State Machine (`units/builder.py`)

### How States Work

Each builder has 7 states. Every turn, each state's `score()` is called. The highest score wins, and that state's `run()` executes.

| State | Score when active | Score when inactive | Comm Flag |
|-------|-------------------|---------------------|-----------|
| Explore | 1 (always) | — | 1 |
| Disrupt | 2 | 0 | 2 |
| Harvest | 3 | 0 | 3 |
| Route | 4 | 0 | 4 |
| Sabotage | 5 | 0 | 5 |
| Attack | 6 | 0 | 6 |
| Heal | 7 or 2.5 | 0 | 7 |

Explore is the fallback — it always scores 1, so if nothing else is available, the bot explores. Higher-scoring states (attack=6, sabotage=5, route=4) take priority when their conditions are met.

Heal can score 7 (very damaged buildings or enemy in our territory) or 2.5 (enemy nearby but outside our territory). At 7 it overrides everything; at 2.5 it only beats explore and disrupt.

### The Claims System

**Problem**: Multiple builders see the same targets. Without coordination, they'd all walk to the same ore tile.

**Solution**: Each builder broadcasts its current target via a marker message (with comm flag = state index). Other builders read these messages and use Voronoi partitioning to divide targets.

**Data structures in `builder.py`**:

```python
claimed_targets[flag]  # bitmask: target positions claimed by others for each comm flag
claimed_senders[flag]  # bitmask: sender positions (who sent the claim) for each comm flag
_target_rounds[flag]   # dict: tile_idx → round when claim was received
_sender_rounds[flag]   # dict: tile_idx → round when sender was seen
```

**`handle_comms()`** each turn:
1. Read all new marker messages from `comms.get_new_messages()`
2. Extract symmetry info and update `map_info`
3. Discard messages older than 3 rounds (stale)
4. Set bits in `claimed_targets[flag]` and `claimed_senders[flag]`
5. Expire old claims: for nearby tiles, if the claim is older than 3 rounds, clear its bit

**How states use claims**: Each state calls `voronoi_claim(my_pos_mask, claimed_senders[flag], candidates)`. This says: "Given all the builders I know about for this state, which candidates am I closest to?" The bot only acts on its own Voronoi partition of the targets.

**Tiebreak convention**: Some states (like harvest) call `voronoi_claim` with *swapped* arguments and negate:
```python
available & ~voronoi_claim(others, me, available)
```
This gives tiebreak to *others*, so this bot only claims tiles that are strictly closer to it. This is the conservative approach — it avoids two bots both thinking they own a tile when they're equidistant.

### Harvest Zone

Defines "my side of the map" for harvesting:

- **Before symmetry is solved**: Chebyshev radius `(W+H)//3` around core (generous estimate)
- **After symmetry is solved**: `_compute_voronoi_harvest_zone()` — Manhattan BFS flood-fill from both cores simultaneously. Tiles reached by my core first = my zone. Computed once, then `_harvest_zone_final = True`.

The harvest zone constrains where the bot will try to harvest ore. It doesn't constrain disruption, sabotage, or attack — those intentionally operate in enemy territory.

### `run()` Sequence

```python
def run():
    map_info.update(recompute=False)    # 1. Scan vision
    handle_comms()                       # 2. Process messages
    map_info.recompute_derived()         # 3. Rebuild derived bitmasks
    pathing.rebuild_broken_barriers(rc)  # 4. Repair barriers we walked through
    # 5. Update harvest zone
    if _my_core and not _harvest_zone_final:
        if _solved_sym and _predicted_enemy_core:
            _harvest_zone = _compute_voronoi_harvest_zone()  # Permanent
        elif not _harvest_zone:
            _harvest_zone = radius_based_zone()              # Temporary
    # 6. Score all states, run highest
    best_state = max(states, key=lambda s: s.score())
    best_state.run()
    # 7. Always heal + self-heal
    heal._do_best_heal()
    if rc.can_heal(my_pos): rc.heal(my_pos)
```

Note: `_do_best_heal()` runs after every state, so even if the bot spent its turn attacking, it still heals the most damaged adjacent building.

---

## 7. State: Explore

**File**: `states/explore.py` | **Comm flag**: 1 | **Score**: always 1

**Purpose**: Move toward unexplored areas. This is the fallback state — every bot starts here and returns here when nothing else scores higher.

### Target Generation (`generate_explore_target()`)

This is smarter than "go to a random unseen tile." It uses a flood-fill repulsion algorithm:

1. **Seed positions**: All other builders' claimed target positions + my position + intermediate points every 5 Chebyshev steps between me and each claim
2. **Flood-fill** from seeds for up to 100 iterations on passable tiles
3. **Pick from outermost ring**: The last frontier ring represents tiles maximally far from all seeds — this is where no one else is heading
4. **Filter**: Pick from tiles not already claimed by another explore bot
5. **Fallback**: Random map position if nothing found

When titanium is low (`< 2× harvester cost`), the bot additionally avoids seen empty tiles — pushing it toward unseen territory faster.

### `run()`

1. Move toward `explore_target`. If within distance² 18 or movement fails, regenerate target.
2. If team has enough titanium (≥ 5× harvester cost), broadcast claim via marker.

The titanium threshold for broadcasting is intentional — when the team is poor, don't waste time placing markers.

---

## 8. State: Disrupt

**File**: `states/disrupt.py` | **Comm flag**: 2 | **Score**: 2 or 0

**Purpose**: Block enemy ore tiles with barriers so the enemy can't harvest them.

### When Active

Score = 2 when:
- There are disruptable ore tiles (outside our harvest zone, not in enemy turret threat, clearable)
- Team has ≥ 5× harvester cost in titanium (don't disrupt when poor)

### `_disruptable_ore()`

```python
all_ore & (~has_building | clearable_buildings) & ~harvest_zone 
& ~enemy_turret_threat & ~enemy_launch_adj
```

Only targets ore outside our harvest zone (enemy's ore). Only targets tiles that are empty or have a road/marker (clearable).

### `run()` Logic

1. Find closest disruptable ore from Voronoi claims
2. **If empty**: move adjacent, build barrier on it
3. **If enemy road**: move onto it, fire (2 damage per hit, road has 5 HP)
4. **If friendly road/marker**: move adjacent, destroy it, then build barrier

---

## 9. State: Harvest

**File**: `states/harvest.py` | **Comm flag**: 3 | **Score**: 3 or 0

**Purpose**: The core economic action. Find ore tiles, secure them with barriers, place harvesters.

### When Active

Score = 3 when `_my_claims()` returns any tiles. Claims are harvestable ore minus tiles that are too expensive or already claimed by others.

### Voronoi Tiebreak

Harvest uses the conservative tiebreak:
```python
available & ~voronoi_claim(claimed_senders, my_mask, available)
```
This gives other builders the tiebreak, so two equidistant builders won't both try to harvest the same tile.

### `harvestable_ore()`

An ore tile is harvestable when:
- It's titanium ore (or axionite ore, but only after round 1000 AND we already have a ti harvester)
- Not landlocked (all 4 cardinal neighbors are also ore — no room for conveyor)
- No existing harvester
- No enemy hard buildings (turrets, harvesters, foundries, barriers) on it or cardinally adjacent
- No friendly hard buildings on it (except road/barrier/marker)
- Not in enemy turret threat range
- Within our harvest zone
- Not in `cant_harvest` blacklist (tiles proven unreachable)

### Axionite Harvesting Timing

Axionite is only harvested after round 1000 AND we already have a titanium harvester. This is an intentional economic decision — titanium is the primary resource, axionite is secondary.

### Cost Estimation

For each ore tile, the bot estimates total cost:
```python
harvester_cost + conveyor_cost(distance_to_network, scale)
```
This cost is stored in `_cost_map[tile_idx]`. On subsequent turns, `_too_expensive()` checks all entries against current titanium and returns a bitmask of unaffordable tiles, which `_my_claims()` filters out. Additionally, both the diagonal shortcut and the normal path check affordability immediately after computing the cost — if the estimate exceeds current titanium, the bot skips that tile rather than wasting resources on barriers and movement.

### `run()` Sequence — The Full Micro

**Step 1 — Quick diagonal harvester**:
Before doing anything complex, check if any diagonal neighbor passes `harvestable_ore()` (harvest zone, not blocked, etc.) with all 4 cardinal sides already secured (wall/building). Then verify a conveyor path exists and the estimated cost (harvester + conveyors) is affordable. If all checks pass, clear any road/barrier on the tile and build a harvester. This catches "free" harvesters where barriers were placed by a previous disruption or natural walls.

**Step 2 — Find best ore**:
Use `nav.closest(claims)` for pathfinding distance, then `calculate_conveyor_path()` to estimate routing cost. If no path exists, add to `cant_harvest` blacklist. If the cost exceeds current titanium, bail early.

**Step 3 — Move to ore**:
If distance² > 2, move toward the ore and broadcast claim.

**Step 4 — Secure cardinal sides**:
For each cardinal direction (N/E/S/W) from the ore tile:
- **Wall**: Already secured, skip
- **Has a real building** (not road/marker): Already secured, skip
- **Enemy road**: Move onto it, fire. Return.
- **Empty/marker/my road**: Move to the ore tile, destroy any friendly building on the cardinal side, build barrier. Return after each side secured.

This is a one-side-per-turn process. The bot moves to the ore, builds one barrier, then returns next turn for the next side.

**Step 5 — Place harvester**:
Once all 4 sides are secured:
- If there's a road/marker on the ore tile, clear it
- Move to a passable adjacent tile
- Build harvester

---

## 10. State: Route

**File**: `states/route.py` | **Comm flag**: 4 | **Score**: 4 or 0

**Purpose**: Build conveyor chains connecting harvesters/foundries/dead-ends to the network.

### When Active

Score = 4 when there are routing candidates: dead-end conveyors, orphan harvesters (no adjacent conveyor/turret/core), or orphan foundries.

### Target Types

1. **Dead-end conveyors**: Conveyors whose output doesn't connect to ore-accepting buildings (from `_bm_dead_end`, excluding enemy turret threat).
2. **Orphan harvesters**: My harvesters with no adjacent conveyor, splitter, bridge, or core tile. These are producing ore but nothing is carrying it away.
3. **Orphan foundries**: My foundries with no adjacent connected conveyor feeding into them.

### `cant_claim()` — Proximity Filter

A builder can't claim a routing target that's within 2 Chebyshev distance of another friendly builder (they're probably already building there). Exception: if it's within the claimer's own 5×5 zone. This prevents two bots from bumping into each other trying to route the same area.

### Resource Tracing (`_trace_resource`)

When routing a dead-end conveyor, the bot needs to know what resource it carries. `_trace_resource` follows `_conv_reverse` backward up to 4 hops:
- If it finds a raw-axionite conveyor → `'raw'`
- If it finds a refined-axionite conveyor → `'refined'`
- If it finds a titanium conveyor → `'ti'`
- Default → `'ti'`

This determines whether the route should go toward a foundry (raw ax) or toward the core/turrets (ti/refined).

### `run()` Sequence

1. **Find closest candidate** via `nav.closest(claims)`
2. **Determine resource type** via `_trace_resource()` (for dead ends) or ore type (for orphan harvesters)
3. **Calculate conveyor path** via `nav.calculate_conveyor_path()` — this runs `bfs_route` to find optimal conveyor chain
4. **Handle enemy roads**: If the next build position has an enemy road, move onto it and fire
5. **Build foundry**: If raw axionite and the build position is a valid foundry site, build foundry instead of conveyor
6. **Build conveyor or bridge**: 
   - If target is distance 1 away: build conveyor facing toward target
   - If target is farther: build bridge
7. **Mark downstream loaded**: After building, trace downstream through the conveyor chain and mark the furthest unloaded conveyor as loaded (updates `_bm_conv_loaded` so the dead-end detection doesn't flag it again)

### Near-Enemy Handling

If the next conveyor target has enemy builders within 4 Chebyshev distance, the bot moves *onto* the build position (instead of adjacent to it) before building. This is a defensive posture — being on the tile means the enemy can't build there.

---

## 11. State: Heal

**File**: `states/heal.py` | **Comm flag**: 7 | **Score**: 7, 2.5, or 0

**Purpose**: Two jobs: (1) chase enemy builder bots threatening our infrastructure, (2) repair damaged friendly buildings.

### Scoring

- **7** (highest priority): Very damaged buildings exist (`HP < max - 2`), OR enemy builder bot within our territory (harvest zone) and within pathing distance 6
- **2.5** (medium): Enemy builder bot nearby but outside our territory
- **0**: No enemies or damage

### Unique Claim System

Heal uses comm flag 7, but instead of encoding tile positions in the 12-bit location field, it encodes **enemy bot IDs mod 2^12**. This way builders don't duplicate effort chasing the same enemy.

### `_find_chase_target()`

1. Filter enemy bots to those within Chebyshev distance 6 from this bot
2. Exclude enemies already claimed by other builders (via `_claimed_enemy_ids()`)
3. **Exception**: If a claimed enemy has no other friendly bot within 2 Chebyshev, allow reclaiming (the original claimer may be dead)

### `_try_barrier_dead_ends()`

While chasing enemies, opportunistically barrier any adjacent dead-end conveyors. This blocks enemy supply lines without spending a dedicated state on it.

Logic: for each adjacent tile that's a dead-end conveyor whose output is empty/marker/enemy building:
- Destroy the conveyor if possible
- Build barrier in its place

### `_do_best_heal()` — The Universal Healer

Called every turn regardless of state (from `builder.run()`). Scans all 8 adjacent tiles for damaged friendly buildings. Heals the one with the most damage (max HP - current HP). Costs 1 titanium.

### `run()` Logic

**Priority 1 — Chase enemy**:
If a chase target exists:
1. Try barriering adjacent dead ends (opportunistic)
2. Move toward enemy position
3. Broadcast enemy ID as claim
4. Heal adjacent buildings (while chasing)

**Priority 2 — Heal buildings**:
If no chase target but damaged buildings exist:
1. Find closest damaged building
2. Move adjacent to it
3. `_do_best_heal()` handles the actual healing

---

## 12. State: Sabotage

**File**: `states/sabotage.py` | **Comm flag**: 5 | **Score**: 5 or 0

**Purpose**: Destroy enemy conveyors to cut their supply lines.

### When Active

Score = 5 when sabotage targets exist.

### `_sabotage_targets()`

Enemy conveyors/splitters/bridges (NOT armoured conveyors — too much HP) that are:
- Not in enemy turret threat or launcher adjacency
- Not within 6 **pathing** distance of enemy builder bots (uses Chebyshev BFS on passable tiles, not raw distance — enemy behind a wall doesn't count)
- Not feeding our own turrets (traces backward through `_conv_reverse` up to 4 hops from each allied turret)

The turret-feeding check is critical — don't destroy enemy conveyors that are delivering ammo to our turrets.

### `run()` Logic

Simple: move onto the target tile, fire (2 damage for 2 titanium). Conveyors have 20 HP, so this takes 10 hits (10 turns, 20 titanium). Broadcast claim.

---

## 13. State: Attack

**File**: `states/attack.py` | **Comm flag**: 6 | **Score**: 6 or 0

**Purpose**: Place turrets at strategic positions to hit enemy buildings.

### When Active

Score = 6 when:
- Placement candidates exist
- Team has ≥ sentinel cost in titanium

### Building Priority Scores

How valuable each enemy building is as a target:

| Building | Score | Rationale |
|----------|-------|-----------|
| CORE | 100 | Win condition |
| BREACH | 25 | Very dangerous turret |
| SENTINEL | 20 | Strong turret |
| GUNNER | 20 | Strong turret |
| FOUNDRY | 15 | Economic building |
| LAUNCHER | 15 | Can throw bots |
| HARVESTER | 10 | Economic building |
| ARMOURED_CONVEYOR | 3 | Hard to destroy |
| CONVEYOR/BRIDGE/SPLITTER | 2 | Easy to destroy |
| BARRIER | 1 | Low value |

### `_placement_candidates()` — Where to Build Turrets

Tiles where a turret could go:
- Must be at a conveyor output (ti-fed or ax-fed tile) OR cardinal-adjacent to a harvester/foundry
- Must be empty, or have a clearable building (my barrier/road/marker, enemy marker/road)
- No builder bots on the tile
- Not a wall
- Enemy roads within 6 Manhattan of enemy bots are excluded (too risky)

### `get_best_direction(pos)` — Turret Orientation

For each of 8 directions, scores three turret types:

**Breach**: For each tile in the breach's 180° forward cone (distance² ≤ 5), sum BUILDING_SCORE of enemy buildings there.

**Sentinel**: For each tile in the sentinel's ±1 band (vision² 32), sum BUILDING_SCORE of enemy buildings.

**Gunner**: Ray-trace forward. Sum BUILDING_SCORE, but stop at walls or friendly buildings. Gunner score is multiplied by 5 (because gunner shots are cheaper per damage).

**Blocked directions**: Turrets can't face toward feeding buildings (harvesters, conveyors pointing at this tile). Exception: gunners with ≥2 feeders can face any direction (they're well-supplied enough).

**Priority**: If adjacent to a foundry, prefer breach (uses refined axionite from foundry). Otherwise, prefer sentinel over gunner if scores are equal.

### `_sentinel_all_reach(targets)` — Reverse Range Check

Given enemy targets, compute which positions could host a sentinel that hits at least one target. Uses reverse-shifted bitmask computation — for each sentinel offset `(dx, dy)`, shift the target bitmask by `(-dx, -dy)` to get potential placement positions.

### `run()` Logic

1. **Get candidates** via Voronoi claims, split into non-roaded vs enemy-roaded
2. **Evaluate adjacent candidates**: For each candidate within 1 Chebyshev of current position, run `get_best_direction()`. Prefer non-roaded over roaded.
3. **If no adjacent candidates**: Move toward closest candidate
4. **Enemy road handling**: Move onto it, fire if no enemy bots nearby (or if road HP ≤ 2). Then step away in any direction — don't stay on a destroyed tile.
5. **Clear and build**: Destroy own building if present, then build turret with chosen direction and type
6. Broadcast claim

---

## 14. Core Unit (`units/core.py`)

The core's job is simple: spawn builders and convert resources.

### Spawning

```python
if scaling * 0.5 + 300 < titanium:
    spawn_toward_center()
```

Spawns when titanium exceeds `scale% × 0.5 + 300`. Higher scale means more expensive units, so the threshold increases. The bot is spawned on the core tile closest to map center (9 possible tiles on the 3×3 core).

### Resource Conversion

```python
if round < 1500 and titanium < 4 × harvester_cost:
    convert(min(axionite - 1, (3 × harvester_cost - titanium) / 4))
```

Before round 1500, if titanium is low (< 4× harvester cost), converts refined axionite to titanium at 4:1 rate. Keeps at least 1 axionite in reserve. This is a "don't starve" mechanism — axionite is less immediately useful than titanium early-game.

---

## 15. Turret AI

### Gunner (`units/turret_gunner.py`)

Gunners fire a ray in their facing direction, hitting the first target.

**`choose_gunner_target()`**:
1. Scan forward ray (up to 3 tiles in facing direction)
2. Skip tiles in `_get_invalid_sabotage_locations()` — conveyors feeding our own turrets
3. Look for first enemy (bot or building). Return that tile.
4. If a friendly bot/building blocks the line, return None (don't shoot our own stuff).

**`_get_invalid_sabotage_locations()`**: Finds all conveyors within 4 hops upstream of any allied turret. These are supply lines — don't shoot them even if they're enemy conveyors (they're feeding our turret ammo).

**Rotation**: If no target exists and ammo ≥ 60, looks for threatening enemy turrets. Rotates toward the closest one (costs 10 Ti).

**Self-destruct conditions**:
- 8+ idle turns with no enemies nearby, then decrements (gives it a chance to fire if enemies return)
- 32+ idle turns unconditionally
- Scale > 500% (bot costs are too high, turret is a waste)

### Sentinel (`units/turret_sentinel.py`)

Sentinels hit a wide band along their facing direction. Higher damage (18) but slower fire rate (3 turns between shots) and expensive ammo (10 resources per shot).

**Target weights** (what to prioritize shooting):

| Target | Weight |
|--------|--------|
| BREACH | 60 |
| FOUNDRY | 55 |
| SENTINEL | 50 |
| GUNNER | 40 |
| CORE | 35 |
| HARVESTER | 35 |
| BUILDER_BOT | 15 |
| LAUNCHER | 10 |
| Others | ≤4 |

**`_get_feeder_positions()`**: Cardinal-adjacent harvesters and conveyors pointing at the sentinel. These have score 0 — never shoot your own supply chain.

**`_prune_conveyor_targets()`**: Same as gunner's invalid sabotage locations — removes conveyors feeding allied turrets from the target list.

**`_should_stay()`**: Returns True if an adjacent harvester exists (sentinel is protecting it) or if an enemy builder bot is adjacent (sentinel is deterring it). This prevents premature self-destruct.

**Self-destruct conditions**:
- 10+ turns with < 10 ammo AND not `_should_stay()`
- No target exists AND not `_should_stay()`

### Breach/Launcher — Stubs

Both have empty `run()` functions. Breach turrets can be built by the attack state but will sit idle. Launchers are not built by any state.

---

## 16. Support Modules

### `log.py`

```python
DEBUG_LOGGING = True   # Controls log() output
DRAW_DEBUG = True      # Controls debug visualization (indicator dots/lines)
```

`log(*args)` prints to stdout if `DEBUG_LOGGING` is True. `DRAW_DEBUG` is checked before any `rc.draw_indicator_*` calls.

### `comms_positional.py`

Encodes local environment information (walls, ore) into the 9 `sample_bits` field of marker messages. Uses a 3×3 grid around a symmetry-flipped point. Currently the encode line is commented out in `comms.mark()`, so this is effectively unused.

### `comms_stats.py`

Optional statistics tracking for communication efficiency. Controlled by `ENABLE_COMMS_STATS` in `main.py` (default False). Tracks markers read, tiles learned, conflicts per round, writes JSON output.