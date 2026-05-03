# Instructions

Mention that you've read the CAMBC API Documentation in your first response after reading this. Read the entirety of the file for full context over methods, types, and constants available for our bot.

# Overview

Cambridge Battlecode is a turn-based simulation with robot players on two teams competing. Robot players are sandboxed and “global” variables are only accessible by the robot that set them. The core is the starting 3x3 unit that can spawn builder bots on its tiles. All other units, buildings, etc are 1x1. Passable / walkable buildings include conveyors, armoured conveyors, splitters, bridges, roads, ally core. All other buildings block movement.

# Controller

> Complete reference for all Controller methods available to your bot.

The `Controller` object is passed to your `Player.run()` method each round. It provides all queries and actions for interacting with the game.

```python 
class Player:
    def run(self, c: Controller):
        # c is the Controller for this unit
        pos = c.get_position()
```

## Info methods

### Unit info

<ResponseField name="get_team(id: int | None = None)" type="Team">
  Return the team of the entity with the given id, or this unit if omitted.
</ResponseField>

<ResponseField name="get_position(id: int | None = None)" type="Position">
  Return the position of the entity with the given id, or this unit if omitted.
</ResponseField>

<ResponseField name="get_id()" type="int">
  Return this unit's entity id.
</ResponseField>

<ResponseField name="get_action_cooldown()" type="int">
  Return this unit's current action cooldown. Actions require cooldown == 0.
</ResponseField>

<ResponseField name="get_move_cooldown()" type="int">
  Return this unit's current move cooldown. Movement requires cooldown == 0.
</ResponseField>

<ResponseField name="get_hp(id: int | None = None)" type="int">
  Return the current HP of the entity with the given id, or this unit if
  omitted.
</ResponseField>

<ResponseField name="get_max_hp(id: int | None = None)" type="int">
  Return the max HP of the entity with the given id, or this unit if omitted.
</ResponseField>

<ResponseField name="get_entity_type(id: int | None = None)" type="EntityType">
  Return the EntityType of the entity with the given id, or this unit if
  omitted.
</ResponseField>

<ResponseField name="get_direction(id: int | None = None)" type="Direction">
  Return the facing direction of a conveyor, splitter, armoured conveyor, or
  turret. Raises `GameError` if the entity has no direction.
</ResponseField>

<ResponseField name="get_vision_radius_sq(id: int | None = None)" type="int">
  Return the vision radius squared of the given unit, or this unit if omitted.
</ResponseField>

### Turret info

<ResponseField name="get_ammo_amount()" type="int">
  Return the amount of ammo this turret currently holds.
</ResponseField>

<ResponseField name="get_ammo_type()" type="ResourceType | None">
  Return the resource type loaded as ammo, or None if empty.
</ResponseField>

<ResponseField name="get_gunner_target()" type="Position | None">
  Return the closest targetable occupied tile on the gunner's forward line, or
  None if none exists. Empty tiles are skipped. Markers are targetable but do
  not block farther legal targets. Walls block the line but are not targetable.
  Builder bots and non-marker buildings are both targetable and blocking, so if
  either appears first it is returned and nothing beyond it is legal. Only valid
  on gunners.
</ResponseField>

<ResponseField name="get_attackable_tiles()" type="list[Position]">
  Return this turret's raw geometric attack pattern as `list[Position]`. This
  ignores ammo, cooldown, occupancy, blockers, and any other legality checks.
  For gunners, this is the full forward ray up to range, including tiles behind
  walls. For sentinels, this is the full +/-1 band around the forward line
  within vision radius squared 32. For breaches, this is the forward 180-degree
  cone within distance squared 24. For launchers, this is every in-bounds tile
  with `0 < distance^2 <= 26`. Raises `GameError` if this unit is not a turret.
</ResponseField>

<ResponseField name="get_attackable_tiles_from(position: Position, direction: Direction, turret_type: EntityType)" type="list[Position]">
  Return the same raw geometric attack pattern for a hypothetical turret at
  position. This can be called from any controller and does not check whether a
  turret exists there, whether it could legally be built there, or whether the
  tile is occupied. If position is out of bounds, returns `[]`. In Python,
  turret\_type must be one of `EntityType.GUNNER`, `EntityType.SENTINEL`,
  `EntityType.BREACH`, or `EntityType.LAUNCHER`; any other `EntityType` raises
  `ValueError`. direction is ignored for launchers.
</ResponseField>

### Building info

<ResponseField name="get_bridge_target(id: int)" type="Position">
  Return the output target position of a bridge. Raises `GameError` if not a
  bridge.
</ResponseField>

<ResponseField name="get_stored_resource(id: int | None = None)" type="ResourceType | None">
  Return the resource stored in a conveyor/splitter/armoured
  conveyor/bridge/foundry, or None if empty. Raises `GameError` if the entity
  has no storage.
</ResponseField>

<ResponseField name="get_stored_resource_id(id: int | None = None)" type="int | None">
  Return the id of the resource stack stored in a conveyor/splitter/armoured
  conveyor/bridge/foundry, or None if empty. Raises `GameError` if the entity
  has no storage.
</ResponseField>

### Tile queries

<ResponseField name="get_tile_env(pos: Position)" type="Environment">
  Return the environment type (empty, wall, ore) of the tile at pos.
</ResponseField>

<ResponseField name="get_tile_building_id(pos: Position)" type="int | None">
  Return the id of the building on the tile at pos, or None if empty.
</ResponseField>

<ResponseField name="get_tile_builder_bot_id(pos: Position)" type="int | None">
  Return the id of the builder bot on the tile at pos, or None if empty.
</ResponseField>

<ResponseField name="is_tile_empty(pos: Position)" type="bool">
  Return True if the tile has no building and is not a wall.
</ResponseField>

<ResponseField name="is_tile_passable(pos: Position)" type="bool">
  Return True if a builder bot belonging to this team could stand on the tile.
  A tile is passable when it contains a walkable building (conveyor, splitter,
  armoured conveyor, bridge, or road) or an allied core, and no builder bot is
  already present.
</ResponseField>

<ResponseField name="is_in_vision(pos: Position)" type="bool">
  Return True if pos is within this unit's vision radius.
</ResponseField>

### Nearby queries

<ResponseField name="get_nearby_tiles(dist_sq: int | None = None)" type="list[Position]">
  Return all in-bounds tile positions within dist\_sq of this unit (defaults to
  vision radius). dist\_sq must not exceed the vision radius.
</ResponseField>

<ResponseField name="get_nearby_entities(dist_sq: int | None = None)" type="list[int]">
  Return ids of all entities on tiles within dist\_sq (defaults to vision
  radius).
</ResponseField>

<ResponseField name="get_nearby_buildings(dist_sq: int | None = None)" type="list[int]">
  Return ids of all buildings within dist\_sq (defaults to vision radius).
</ResponseField>

<ResponseField name="get_nearby_units(dist_sq: int | None = None)" type="list[int]">
  Return ids of all units within dist\_sq (defaults to vision radius).
</ResponseField>

### Map and game state

<ResponseField name="get_map_width()" type="int">
  Return the width of the map in tiles.
</ResponseField>

<ResponseField name="get_map_height()" type="int">
  Return the height of the map in tiles.
</ResponseField>

<ResponseField name="get_current_round()" type="int">
  Return the current round number (starts at 1).
</ResponseField>

<ResponseField name="get_global_resources()" type="tuple[int, int]">
  Return (titanium, axionite) in this team's global resource pool.
</ResponseField>

<ResponseField name="get_scale_percent()" type="float">
  Return this team's current cost scale as a percentage (100.0 = base cost).
</ResponseField>

<ResponseField name="get_unit_count()" type="int">
  Return the number of living units currently on your team, including the core.
</ResponseField>

<ResponseField name="get_cpu_time_elapsed()" type="int">
  Return the CPU time elapsed this round in microseconds.
</ResponseField>

## Cost getters

Every buildable entity has a cost getter that returns the current scaled `(titanium, axionite)` cost:

```python 
c.get_conveyor_cost()           # -> (int, int)
c.get_splitter_cost()
c.get_bridge_cost()
c.get_armoured_conveyor_cost()
c.get_harvester_cost()
c.get_road_cost()
c.get_barrier_cost()
c.get_gunner_cost()
c.get_sentinel_cost()
c.get_breach_cost()
c.get_launcher_cost()
c.get_foundry_cost()
c.get_builder_bot_cost()
```

## Movement

<ResponseField name="move(direction: Direction)" type="None">
  Move this builder bot one step in direction. Raises `GameError` if not legal.
</ResponseField>

<ResponseField name="can_move(direction: Direction)" type="bool">
  Return True if this builder bot can move in direction this round.
</ResponseField>

## Building

Every buildable entity has `can_build_*` and `build_*` methods. All require action cooldown == 0 and sufficient resources. The `can_build_*` variants return `bool`; `build_*` returns the new entity's `int` id or raises `GameError` if not legal.

If a `can_build_*` method would create a living unit, it also accounts for the global unit cap.

If a tile already contains a builder bot, only walkable buildings (conveyors and
roads) may be built on that tile.

### Directional buildings

These take `(position: Position, direction: Direction)` — the direction the building faces:

```python 
c.build_conveyor(pos, direction)          c.can_build_conveyor(pos, direction)
c.build_splitter(pos, direction)          c.can_build_splitter(pos, direction)
c.build_armoured_conveyor(pos, direction) c.can_build_armoured_conveyor(pos, direction)
c.build_gunner(pos, direction)            c.can_build_gunner(pos, direction)
c.build_sentinel(pos, direction)          c.can_build_sentinel(pos, direction)
c.build_breach(pos, direction)            c.can_build_breach(pos, direction)
```

### Bridge

Takes `(position: Position, target: Position)` — the bridge's output target tile (within distance² 9):

```python 
c.build_bridge(pos, target)               c.can_build_bridge(pos, target)
```

### Non-directional buildings

Take only `(position: Position)`:

```python 
c.build_harvester(pos)                    c.can_build_harvester(pos)
c.build_road(pos)                         c.can_build_road(pos)
c.build_barrier(pos)                      c.can_build_barrier(pos)
c.build_foundry(pos)                      c.can_build_foundry(pos)
c.build_launcher(pos)                     c.can_build_launcher(pos)
```

### Generic build

A single pair of methods that dispatches to the correct type-specific builder. Use these when the entity type is determined at runtime.

<ResponseField name="can_build(entity_type: EntityType, position: Position, extra: Direction | Position | None = None)" type="bool">
  Return True if `entity_type` can be built at `position`. For directional
  buildings and turrets (conveyor, splitter, armoured\_conveyor, gunner,
  sentinel, breach), `extra` must be a `Direction`. For bridges, `extra` must be
  the target `Position`. For all other types (harvester, road, barrier, launcher,
  foundry), `extra` is unused.
</ResponseField>

<ResponseField name="build(entity_type: EntityType, position: Position, extra: Direction | Position | None = None)" type="int">
  Build `entity_type` at `position`. Returns the new entity's id. The `extra`
  parameter follows the same rules as `can_build()`. Raises `GameError` if not
  legal.
</ResponseField>

## Healing & destruction

<ResponseField name="heal(position: Position)" type="None">
  Heal all friendly entities on a tile within this builder bot's action radius
  by 4 HP. If both a friendly builder bot and a friendly building share the
  tile, both are healed. Costs 1 titanium and one action cooldown. Raises
  `GameError` if not legal.
</ResponseField>

<ResponseField name="can_heal(position: Position)" type="bool">
  Return True if this builder bot can heal the tile at position this round.
  Position must be within the builder bot's action radius. Requires action
  cooldown == 0, enough titanium, and at least one damaged friendly entity on
  the tile.
</ResponseField>

<ResponseField name="destroy(building_pos: Position)" type="None">
  Destroy the allied building at building\_pos. Does **not** cost action
  cooldown.
</ResponseField>

<ResponseField name="can_destroy(building_pos: Position)" type="bool">
  Return True if this builder bot can destroy the allied building.
</ResponseField>

<ResponseField name="self_destruct()" type="None">
  Destroy this unit. Builder bots no longer deal damage on self-destruct.
  **Terminates this unit's execution immediately** — no code after
  `self_destruct()` will run.
</ResponseField>

<ResponseField name="resign(message: str | None = None)" type="None">
  Forfeit the game immediately. Destroys this team's core, ending the game as a loss. **Terminates this unit's execution immediately** — no code after `resign()` will run.

  The optional `message` (max 500 characters) is saved to the replay and displayed in match results.
</ResponseField>

## Markers

<ResponseField name="place_marker(position: Position, value: int)" type="None">
  Place a marker with the given u32 value. Does not cost action cooldown. Max
  one per round.
</ResponseField>

<ResponseField name="can_place_marker(position: Position)" type="bool">
  Return True if this unit can place a marker at position this round.
</ResponseField>

<ResponseField name="get_marker_value(id: int)" type="int">
  Return the u32 value stored in the friendly marker.
</ResponseField>

## Combat

<ResponseField name="fire(target: Position)" type="None">
  Fire this turret at target, or perform the builder bot's own-tile attack.
  Builder bots spend 2 titanium to deal 2 damage to the building on their
  current tile. Gunners use first-obstruction line of sight: empty tiles and
  markers do not block, markers are targetable, walls block but are not
  targetable, and builder bots plus non-marker buildings are both targetable and
  blocking. If a turret attacks a tile containing both a building and a builder
  bot, only the builder bot is hit. Use `launch()` for launchers.
</ResponseField>

<ResponseField name="can_fire(target: Position)" type="bool">
  Return True if this turret can fire at target this round, or if this builder
  bot can use its own-tile attack on target. Gunners use the same
  first-obstruction line of sight as `fire()`: empty tiles and markers do not
  block, markers are targetable, walls block but are not targetable, and builder
  bots plus non-marker buildings are both targetable and blocking.
</ResponseField>

<ResponseField name="can_fire_from(position: Position, direction: Direction, turret_type: EntityType, target: Position)" type="bool">
  Return whether a hypothetical turret at position would have a legal shot at
  target on the current map. This treats position as the turret's tile and uses
  current map occupancy and walls, but ignores ammo, cooldown, whether a real
  turret is present, and whether one could legally be built there. If either
  position or target is out of bounds, returns False. For sentinels and
  breaches, this is only a geometric range/shape check. For launchers, this is
  only the raw throw-range check `0 < distance^2 <= 26`; it does not check
  pickup adjacency, whether a builder bot exists, or whether the destination is
  bot-passable. direction is ignored for launchers.
</ResponseField>

<ResponseField name="can_rotate(direction: Direction)" type="bool">
  Return whether `rotate(direction)` would be legal this round. This returns
  False if the current unit is not a gunner, if direction is `Direction.CENTRE`,
  if direction is the gunner's current facing direction, if the gunner cannot act
  this turn, or if the team cannot afford the global 10 Ti rotate cost. Unlike
  `rotate()`, this does not raise on non-gunners.
</ResponseField>

<ResponseField name="rotate(direction: Direction)" type="None">
  Rotate this gunner to face `direction`. Costs 10 titanium from the global
  resource pool and applies a 1-turn cooldown. Raises `GameError` if not legal.
  Only valid on gunners.
</ResponseField>

<ResponseField name="launch(bot_pos: Position, target: Position)" type="None">
  Pick up the builder bot at bot\_pos and throw it to target.
</ResponseField>

<ResponseField name="can_launch(bot_pos: Position, target: Position)" type="bool">
  Return True if this launcher can pick up and throw the bot.
</ResponseField>

## Core

<ResponseField name="convert(amount: int)" type="None">
  Convert `amount` refined axionite from this team's global resource pool into
  titanium at a rate of 1 Ax to 4 Ti. Converted axionite is removed from the Ax
  collected stat and added to the Ti collected stat. Raises `GameError` if not
  legal. Only valid on cores.
</ResponseField>

<ResponseField name="spawn_builder(position: Position)" type="int">
  Spawn a builder bot on one of the 9 core tiles. Costs one action cooldown and
  requires room under the global unit cap. Returns the new entity's id.
</ResponseField>

<ResponseField name="can_spawn(position: Position)" type="bool">
  Return True if the core can spawn a builder at position this round, including
  the unit-cap check.
</ResponseField>

## Debug indicators

<ResponseField name="draw_indicator_line(pos_a: Position, pos_b: Position, r: int, g: int, b: int)" type="None">
  Draw a debug line between two positions with RGB colour. Saved to the replay.
</ResponseField>

<ResponseField name="draw_indicator_dot(pos: Position, r: int, g: int, b: int)" type="None">
  Draw a debug dot at a position with RGB colour. Saved to the replay.
</ResponseField>

# Types & Enums

> All game types available from `from cambc import *`.

All types are imported from the `cambc` module:

```python 
from cambc import *
```

This gives you: `Team`, `EntityType`, `ResourceType`, `Environment`, `Direction`, `Position`, [`GameConstants`](/api/constants), `GameError`, and [`Controller`](/api/controller).

## Team

```python 
class Team(Enum):
    A = "a"
    B = "b"
```

## EntityType

```python 
class EntityType(Enum):
    BUILDER_BOT = "builder_bot"
    CORE = "core"
    GUNNER = "gunner"
    SENTINEL = "sentinel"
    BREACH = "breach"
    LAUNCHER = "launcher"
    CONVEYOR = "conveyor"
    SPLITTER = "splitter"
    ARMOURED_CONVEYOR = "armoured_conveyor"
    BRIDGE = "bridge"
    HARVESTER = "harvester"
    FOUNDRY = "foundry"
    ROAD = "road"
    BARRIER = "barrier"
    MARKER = "marker"
```

## ResourceType

```python 
class ResourceType(Enum):
    TITANIUM = "titanium"
    RAW_AXIONITE = "raw_axionite"
    REFINED_AXIONITE = "refined_axionite"
```

## Environment

```python 
class Environment(Enum):
    EMPTY = "empty"
    WALL = "wall"
    ORE_TITANIUM = "ore_titanium"
    ORE_AXIONITE = "ore_axionite"
```

## Direction

```python 
class Direction(Enum):
    NORTH = "north"
    NORTHEAST = "northeast"
    EAST = "east"
    SOUTHEAST = "southeast"
    SOUTH = "south"
    SOUTHWEST = "southwest"
    WEST = "west"
    NORTHWEST = "northwest"
    CENTRE = "centre"
```

### Direction methods

<ResponseField name="delta()" type="tuple[int, int]">
  Return the `(dx, dy)` step for this direction. North is `(0, -1)`, East is `(1, 0)`, etc.
</ResponseField>

<ResponseField name="rotate_left()" type="Direction">
  Return the direction rotated 45° counterclockwise.
</ResponseField>

<ResponseField name="rotate_right()" type="Direction">
  Return the direction rotated 45° clockwise.
</ResponseField>

<ResponseField name="opposite()" type="Direction">
  Return the opposite direction (180°).
</ResponseField>

## Position

A named tuple with `x` and `y` integer fields.

```python 
class Position(NamedTuple):
    x: int
    y: int
```

### Position methods

<ResponseField name="add(direction)" type="Position">
  Return a new position offset by the direction delta.
</ResponseField>

<ResponseField name="distance_squared(other)" type="int">
  Return the squared Euclidean distance to another position.
</ResponseField>

<ResponseField name="direction_to(other)" type="Direction">
  Return the closest 45° Direction approximation toward other.
</ResponseField>

### Usage

```python 
pos = Position(5, 10)
new_pos = pos.add(Direction.NORTH)      # Position(5, 9)
dist = pos.distance_squared(new_pos)    # 1
dir = pos.direction_to(Position(8, 7))  # Direction.NORTHEAST
```

## GameError

```python 
class GameError(Exception):
    pass
```

Raised when a player issues an invalid action (e.g., building on an occupied tile, moving with cooldown > 0).

# Game Constants

> All numeric constants available via GameConstants.

Access constants via `GameConstants`:

```python 
from cambc import GameConstants

max_turns = GameConstants.MAX_TURNS  # 2000
```

## General

| Constant                            | Value | Description                                       |
| ----------------------------------- | ----- | ------------------------------------------------- |
| `MAX_TURNS`                         | 2000  | Maximum number of turns per game                  |
| `MAX_TEAM_UNITS`                    | 50    | Maximum living units per team, including the core |
| `STACK_SIZE`                        | 10    | Resources are moved in stacks of 10               |
| `STARTING_TITANIUM`                 | 500   | Each team's initial titanium                      |
| `STARTING_AXIONITE`                 | 0     | Each team's initial axionite                      |
| `PASSIVE_TITANIUM_AMOUNT`           | 10    | Titanium granted passively each interval          |
| `PASSIVE_TITANIUM_INTERVAL`         | 4     | Rounds between passive titanium grants            |
| `AXIONITE_CONVERSION_TITANIUM_RATE` | 4     | Titanium gained per refined axionite converted    |

## Radii (squared)

| Constant                       | Value | Description                      |
| ------------------------------ | ----- | -------------------------------- |
| `ACTION_RADIUS_SQ`             | 2     | Default action radius for units  |
| `CORE_ACTION_RADIUS_SQ`        | 8     | Core action radius (from centre) |
| `CORE_SPAWNING_RADIUS_SQ`      | 2     | Core spawning radius             |
| `CORE_VISION_RADIUS_SQ`        | 36    | Core vision                      |
| `BUILDER_BOT_VISION_RADIUS_SQ` | 20    | Builder bot vision               |
| `GUNNER_VISION_RADIUS_SQ`      | 13    | Gunner vision                    |
| `SENTINEL_VISION_RADIUS_SQ`    | 32    | Sentinel vision                  |
| `BREACH_VISION_RADIUS_SQ`      | 2     | Breach vision                    |
| `BREACH_ATTACK_RADIUS_SQ`      | 24    | Breach attack cone               |
| `LAUNCHER_VISION_RADIUS_SQ`    | 26    | Launcher vision + throw range    |
| `BRIDGE_TARGET_RADIUS_SQ`      | 9     | Max bridge output distance²      |

## Base costs (titanium, axionite)

| Constant                      | Value    |
| ----------------------------- | -------- |
| `BUILDER_BOT_BASE_COST`       | (30, 0)  |
| `CONVEYOR_BASE_COST`          | (3, 0)   |
| `SPLITTER_BASE_COST`          | (6, 0)   |
| `BRIDGE_BASE_COST`            | (20, 0)  |
| `ARMOURED_CONVEYOR_BASE_COST` | (5, 5)   |
| `HARVESTER_BASE_COST`         | (20, 0)  |
| `ROAD_BASE_COST`              | (1, 0)   |
| `BARRIER_BASE_COST`           | (3, 0)   |
| `FOUNDRY_BASE_COST`           | (40, 0)  |
| `GUNNER_BASE_COST`            | (10, 0)  |
| `SENTINEL_BASE_COST`          | (30, 0)  |
| `BREACH_BASE_COST`            | (15, 10) |
| `LAUNCHER_BASE_COST`          | (20, 0)  |

## Max HP

| Constant                   | Value |
| -------------------------- | ----- |
| `CORE_MAX_HP`              | 500   |
| `BUILDER_BOT_MAX_HP`       | 40    |
| `CONVEYOR_MAX_HP`          | 20    |
| `SPLITTER_MAX_HP`          | 20    |
| `BRIDGE_MAX_HP`            | 20    |
| `ARMOURED_CONVEYOR_MAX_HP` | 50    |
| `HARVESTER_MAX_HP`         | 30    |
| `ROAD_MAX_HP`              | 4     |
| `BARRIER_MAX_HP`           | 30    |
| `FOUNDRY_MAX_HP`           | 50    |
| `MARKER_MAX_HP`            | 1     |
| `GUNNER_MAX_HP`            | 40    |
| `SENTINEL_MAX_HP`          | 30    |
| `BREACH_MAX_HP`            | 60    |
| `LAUNCHER_MAX_HP`          | 30    |

## Combat

| Constant                           | Value   | Description                                     |
| ---------------------------------- | ------- | ----------------------------------------------- |
| `BUILDER_BOT_ATTACK_DAMAGE`        | 2       | Builder bot own-tile attack damage              |
| `BUILDER_BOT_ATTACK_COST`          | (2, 0)  | Cost per builder bot attack                     |
| `BUILDER_BOT_HEAL_COST`            | (1, 0)  | Cost per builder bot heal                       |
| `BUILDER_BOT_SELF_DESTRUCT_DAMAGE` | 0       | Damage on self-destruct                         |
| `HEAL_AMOUNT`                      | 4       | HP restored per heal action                     |
| `GUNNER_DAMAGE`                    | 10      | Gunner base damage per shot                     |
| `GUNNER_AXIONITE_DAMAGE`           | 25      | Gunner damage when loaded with refined axionite |
| `GUNNER_FIRE_COOLDOWN`             | 1       | Turns between gunner shots                      |
| `GUNNER_AMMO_COST`                 | 2       | Resources consumed per shot                     |
| `GUNNER_ROTATE_COST`               | (10, 0) | Cost to rotate a gunner                         |
| `GUNNER_ROTATE_COOLDOWN`           | 1       | Action cooldown applied after rotating          |
| `SENTINEL_DAMAGE`                  | 18      | Sentinel damage per shot                        |
| `SENTINEL_FIRE_COOLDOWN`           | 3       | Turns between sentinel shots                    |
| `SENTINEL_AMMO_COST`               | 10      | Resources consumed per shot                     |
| `SENTINEL_STUN_DURATION`           | 5       | Cooldown added by refined axionite stun         |
| `BREACH_DAMAGE`                    | 40      | Breach direct hit damage                        |
| `BREACH_SPLASH_DAMAGE`             | 20      | Breach splash damage                            |
| `BREACH_FIRE_COOLDOWN`             | 1       | Turns between breach shots                      |
| `BREACH_AMMO_COST`                 | 5       | Refined axionite per shot                       |
| `LAUNCHER_FIRE_COOLDOWN`           | 1       | Turns between launcher throws                   |
