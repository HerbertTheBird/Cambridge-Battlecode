# Lethe Bot - Technical Breakdown

## Design Philosophy

Lethe is built around one core idea: **the entire map is a single integer**. Every tile is a bit in a Python big-int, and every spatial question — "where are the enemy conveyors?", "which ore is reachable?", "what can their turrets hit?" — is answered by ANDing, ORing, and shifting these integers. There are no tile-by-tile loops for strategic decisions. The bot computes over the whole map in constant-ish time using bitwise bulk operations.

This feeds into a **reactive priority system** rather than a planning pipeline. Every turn, all 7 builder states score themselves against the current map state (expressed as bitmasks), and the highest-scoring state runs. There's no multi-turn plan to invalidate — the bot re-evaluates from scratch each tick, which makes it naturally adaptive. When an enemy destroys a conveyor, the route state's score jumps; when a harvester gets damaged, heal's score jumps. The bot pivots instantly.

The third principle is **decentralized coordination**. Units are sandboxed — no shared memory. So Lethe uses encrypted markers as a peer-to-peer broadcast network. Every unit stamps its current task into nearby tiles, and other units decode those stamps to build a shared picture of who's doing what. The "forget" bitmask system ensures units don't duplicate work without needing a central coordinator.

---

## Bitmask-Based Computation

The entire bot represents tile sets as big integers where each bit corresponds to one tile on the map. This enables bulk spatial operations using bitwise logic instead of coordinate loops.

- **Entity/team/environment tracking**: Separate bitmasks per `EntityType`, per `Team`, and per `Environment` — locate all enemy conveyors, all titanium ore, etc. in O(1) bitwise AND.
- **Column-safe shifting**: `_not_left_col` and `_not_right_col` guard masks prevent bits from wrapping across map edges during horizontal shifts.
- **Chebyshev/Manhattan flood fill**: `expand_chebyshev()` and `expand_manhattan()` perform single-step flood fills via 8 or 4 bitwise shifts + OR — used for frontier expansion, threat zones, and nearest-neighbor queries.
- **LSB extraction**: `mask & -mask` isolates the lowest set bit to iterate over tile sets without converting to coordinate lists.

---

## How States Use Whole-Map Bitmasks

Every state's `score()` and `run()` functions build their candidate sets as bitmask expressions over the entire map. No state ever loops over tiles to find targets — they compose masks.

### Harvest: Candidate Ore as a Single Expression

`harvestable_ore()` returns a single integer representing every valid harvest target on the map:

```
ore & ~landlocked & ~has_harvester & ~forget & ~enemy_blocking
    & ~friendly_blocking & ~enemy_hard_adj & ~enemy_turret_threat
    & harvest_zone & ~cant_harvest
```

Each term is a precomputed bitmask. `landlocked` itself is computed in one shot — ore tiles with ore on all 4 cardinal sides, via 4 shifted ANDs:

```python
landlocked = ore & (ore >> 1 & nrc) & (ore << 1 & nlc) & (ore >> w) & (ore << w)
```

This finds every unreachable ore tile on the map in a single line.

### Explore: Flood-Fill Frontier via Shifts

Exploration targets are found by seeding a bitmask with all known unit positions and claimed tiles, then expanding it 10 Chebyshev steps:

```python
expanded = frontier | ((frontier & nrc) << 1) | ((frontier & nlc) >> 1)
         | (frontier << w) | (frontier >> w)
frontier = expanded & passable & ~visited
```

Each step shifts the frontier in all 4+ directions simultaneously — the entire flood fill runs as 10 iterations of bitwise ops, not a queue-based BFS. The final ring is the exploration boundary; a random bit is picked from it using `bit_count()` + iterated `mask &= mask - 1`.

### Attack: Reverse Reachability via Shift-Masks

To find where to place a turret that can hit enemy targets, the attack state uses **reverse shift-masks**. For each (dx, dy) offset in a sentinel's attack pattern, it shifts the target bitmask by (-dx, -dy) to get all positions from which that offset would land on a target:

```python
reachable |= (targets & shift_mask) << offset   # or >> for negative
```

This gives a bitmask of every tile on the map where a sentinel could hit at least one high-value target — computed in bulk, not per-candidate.

### Disrupt: Composing Exclusion Zones

Disruptable ore = all ore minus buildings, minus harvest zone, minus forget, minus turret threat, minus launcher adjacency. Each term is a precomputed whole-map mask:

```python
all_ore & (~has_building | clearable) & ~harvest_zone & ~forget
        & ~enemy_turret_threat & ~enemy_launch_adj
```

### Sabotage & Route: Same Pattern

Sabotage targets = enemy conveyors & ~turret_threat & ~launcher_adj & ~forget & ~danger_zone. Route targets = dead-end conveyors | orphan harvesters (harvesters not adjacent to any network building, computed via `expand_manhattan` of the network mask).

### Heal: Damage Tracking as Bitmasks

`_bm_damaged` and `_bm_very_damaged` are maintained as whole-map masks. The heal state intersects these with `_bm_team[my_idx]` to get friendly damaged buildings, then iterates only the set bits (via LSB extraction) to find the worst damage.

### Harvest Zone: Chebyshev Expansion from Core

The harvest zone is defined as a Chebyshev expansion from the core tile, repeated `(width + height) / 3` times. This creates a diamond-shaped "home territory" mask that all states reference — harvest only targets ore inside it, disrupt only targets ore outside it.

### Enemy Danger Zones: Manhattan Expansion

Multiple states compute enemy builder danger zones by seeding a bitmask with enemy bot positions and expanding it 6 Manhattan steps:

```python
danger = enemy_bots
for _ in range(6):
    danger = expand_manhattan(danger)
candidates &= ~danger
```

Six iterations of 4 bitwise shifts + OR covers the entire map's danger zone in microseconds.

---

## Turret Threat Computation

### Aggregate Attack Range via Shift-Masks

The key insight: for direction-based turrets (breach, sentinel), all turrets facing the same direction have the same attack pattern, just shifted. So instead of computing per-turret:

1. Group turrets by facing direction (8 buckets)
2. OR all same-direction turrets into one mask per bucket
3. For each (dx, dy) offset in that direction's pattern, shift the whole bucket mask at once

This turns O(turrets * pattern_size) into O(8 * pattern_size) — independent of turret count.

- **Gunner**: Per-turret ray-casting with wall blocking — shoots 8 rays until hitting wall or map edge (walls make bulk shifting impossible).
- **Result**: Single aggregate threat bitmask covering all enemy-attackable tiles. Used by every state to exclude danger zones.

---

## Pathfinding

### BFS with Cost-Layered State Space

`bfs()` runs a multi-cost BFS supporting three movement types simultaneously:

1. **Cardinal movement** (4-dir, cost 1)
2. **King movement** (8-dir, cost 1)
3. **Conveyor routing** (includes bridge jumps up to 3 tiles, cost 10)

Cost modifiers:
- Barriers on friendly tiles: +10
- Threat zones (enemy turrets/launchers): +20
- Bridges: +10 per step

Path reconstruction tiebreaks prefer diagonal moves, staying away from map edges, and preserving movement momentum (penalizes direction-family changes).

### Bitmask Nearest-Neighbor

`closest()` does Chebyshev flood fill on bitmasks — pure bitwise BFS with no coordinate math until the result is found. Much faster than coordinate-based search for tactical range queries.

### Stuck Detection & Breaking

Tracks `stuck_turns` and `prev_pos`. After 2-3 stuck turns, forces a move in any valid direction. Destroys blocking barriers and builds roads along its path to reduce future cost.

### Broken Barrier Tracking

When a builder destroys an enemy barrier to open a path, the position + round are logged. The core's `rebuild_broken_barriers()` callback rebuilds them one round later — temporary clearance without permanent map damage.

---

## Communication Protocol

### Marker-Based Encrypted Messaging

Units communicate by placing marker tiles with packed 28-bit values, XOR-encrypted with a SplitMix64-style hash of map dimensions:

| Field | Bits | Purpose |
|-------|------|---------|
| Unit ID | 12 | Source identity (mod 4096) |
| Position | 12 | Target tile (6 bits x + 6 bits y) |
| Turn parity | 1 | Duplicate detection |
| Symmetry | 3 | Team-wide symmetry state broadcast |
| Type | remaining | Message type (explore, harvest, route, sabotage, attack, heal, launch) |

### Placement Strategy

Three-pass marker placement:
1. Empty tiles, avoiding bad spots (adjacent to harvesters, conveyor targets)
2. Overwrite own old markers
3. Destroy own roads if needed to make space

Messages auto-expire after 5 turns out of vision.

### Forget System: Decentralized Task Deconfliction

Each state has a `comm_flag` (0-7). When a unit claims a tile, it broadcasts `(position, flag)` via markers. Other units decode these and set the corresponding bit in `forget[flag]`, preventing them from targeting the same tile. Claims expire after 5 turns out of vision, so if a unit dies or moves on, its claims naturally release. Harvest claims also reserve the 4 cardinal neighbors (for barrier placement), preventing two builders from trying to secure the same ore.

---

## Map Analysis & Symmetry Detection

### Real-Time Symmetry Resolution

Tracks three hypotheses: horizontal flip, vertical flip, and 180-degree rotation. Each newly-seen tile disproves inconsistent symmetries by checking environment match at the reflected position.

Once exactly one symmetry remains:
- Fills unseen half of the map with mirrored environment data
- Predicts enemy core position by flipping own core
- Broadcasts resolved symmetry in every marker for team coordination

### Symmetric Tiebreaking

When multiple symmetries remain valid, breaks the tie toward whichever places the predicted enemy core closer to the current unit. Records the tiebreak decision and communicates it to the team.

### Core Detection

Cores are 3x3; `core_center()` finds the center by checking which orthogonal direction has empty spaces around the core footprint.

---

## Builder Bot State Machine

Seven states, each scored every turn. Highest score runs. Every state's scoring function is a bitmask emptiness check — `return N if mask else 0` — so the entire priority evaluation is just 7 bitmask computations.

### 1. EXPLORE (score 1)
Flood-fill frontier expansion. Seeds all known positions, expands 10 Chebyshev layers via bitwise shifts, picks random unclaimed tile from the outermost ring. When low on resources, avoids empty seen tiles (only explores toward buildings/ore).

### 2. DISRUPT (score 2)
Builds barriers on enemy ore outside the harvest zone. Targets all ore NOT in harvest zone plus clearable enemy roads/markers. Avoids turret threat zones and launcher adjacency.

### 3. HARVEST (score 3)
Four-cardinal-side security check — all sides must have barriers/buildings before placing a harvester. Diagonal ore gets a shortcut if cardinal surroundings are already secured. Tracks cost in `_cost_map` (harvester + conveyor path) so it doesn't repeatedly attempt too-expensive patches. Moves onto enemy roads and fires to clear them.

### 4. ROUTE (score 4)
Connects orphan harvesters (no adjacent conveyor/turret/core) and fixes dead-end conveyors (output not connected to ore-accepting network). Uses bridge-enhanced conveyor pathfinding for long connections. Traces downstream from conveyors to mark furthest unloaded tile as "loaded" for load balancing. Enemy builder override: if enemy within 2 Chebyshev of planned path step, pivots to secure that tile instead.

### 5. HEAL (score 5.5 or 7)
Repairs damaged buildings. Buildings with >2 damage get priority score 7, otherwise 5.5. Also intercepts enemy builders threatening conveyors within Chebyshev distance 4. Heals the most damaged adjacent building by absolute damage value. Every builder also opportunistically heals after its main action — the dedicated heal state handles triage when multiple buildings need attention.

### 6. SABOTAGE (score 5)
Targets enemy conveyor/splitter/bridge NOT in turret threat or launcher adjacency. Avoids 6-Manhattan danger zone around enemy builders. Moves onto tile and fires once.

### 7. ATTACK (score 6)
Places offensive turrets at high-value locations. Uses reverse shift-mask reachability to filter candidates to only tiles that can actually hit an uncovered enemy building. Turret direction optimization:
- Cannot face toward loaders (prevents blocking your own resource chain)
- Gunner exception: can face any direction if 2+ loaders provide redundancy
- Scores each direction by sum of `BUILDING_SCORE` of enemy buildings in attack cone

Two candidate pools: non-enemy-roaded (preferred) and enemy-roaded (fallback, requires fire + step-off before building).

---

## Turret AI

### Gunner
Scans 3-tile forward ray. Target priority: first enemy in ray, then first tile blocked by friendly. When low ammo (<60 titanium), rotates toward closest adjacent enemy turret. Self-destructs after 8+ skipped turns with high cost scaling (>500%) or 32+ skipped turns unconditionally.

### Sentinel
Weighted scoring system for targets:

| Target | Weight |
|--------|--------|
| Breach | 60 |
| Foundry | 55 |
| Gunner | 40 |
| Core | 35 |
| Harvester | 35 |

Avoids shooting feeder buildings (harvesters/conveyors that input to its tile). Self-destructs if ammo <10 AND not protecting a nearby harvester.

---

## Resource Management

### Spawning
Core spawns builder bots when `scale_percent * 2 < titanium`. Prefers spawning toward map center across the 3x3 core footprint.

### Cost Tracking
Harvest and Route states maintain `_cost_map` (tile index to estimated titanium cost). Computed as `base_cost + conveyor_path_cost * (scale_percent/100 + scaling_penalty)`, where scaling penalty is +0.05 per harvester and +0.1 per bridge. Once ore is too expensive, it's added to `cant_harvest` until reconsidered.

### Ore Prioritization
Only harvests titanium ore (not axionite). Skips landlocked ore (surrounded on all 4 cardinal sides — unreachable by conveyor networks).

---

## Defense & Tactical Patterns

- **Harvest zone**: Chebyshev expansion `(width + height) / 3` steps from core — defines "home territory" as a single bitmask
- **Launcher safety**: All states exclude tiles in `_bm_enemy_launch_adj`
- **Enemy builder avoidance**: Sabotage/Attack/Route expand 6 Manhattan distance around each enemy builder as no-go zones
- **Symmetric offensive placement**: Attack state uses predicted enemy core (via symmetry) to guide turret placement toward the enemy
- **Opportunistic healing**: Every builder heals the most-damaged adjacent friendly building at end of turn, regardless of state

---

## Performance

- Caches frequently-accessed functions as locals in hot paths
- Precomputed offset tables for turret attacks (`_BREACH_OFFSETS`, `_SENTINEL_OFFSETS`, `_GUNNER_RAYS`)
- `_sentinel_all_reach_cache` caches union of all sentinel offsets for reverse-reachability
- Bitmask state scoring means the entire 7-state priority evaluation is ~7 bitwise expressions, not 7 tile-scanning loops
- Optional cProfile integration for per-unit performance profiling
- Tracks elapsed microseconds per turn with warnings on >2ms turns
