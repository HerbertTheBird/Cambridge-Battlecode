> ## Documentation Index
> Fetch the complete documentation index at: https://docs.battlecode.cam/llms.txt
> Use this file to discover all available pages before exploring further.

# Controller

> Complete reference for all Controller methods available to your bot.

The `Controller` object is passed to your `Player.run()` method each round. It provides all queries and actions for interacting with the game.

```python  theme={"dark"}
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
  cone within distance squared 5. For launchers, this is every in-bounds tile
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
  Return True if a builder bot belonging to this team could stand on the tile
  (conveyor, road, or allied core, and no other builder bot).
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

```python  theme={"dark"}
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

```python  theme={"dark"}
c.build_conveyor(pos, direction)          c.can_build_conveyor(pos, direction)
c.build_splitter(pos, direction)          c.can_build_splitter(pos, direction)
c.build_armoured_conveyor(pos, direction) c.can_build_armoured_conveyor(pos, direction)
c.build_gunner(pos, direction)            c.can_build_gunner(pos, direction)
c.build_sentinel(pos, direction)          c.can_build_sentinel(pos, direction)
c.build_breach(pos, direction)            c.can_build_breach(pos, direction)
```

### Bridge

Takes `(position: Position, target: Position)` — the bridge's output target tile (within distance² 9):

```python  theme={"dark"}
c.build_bridge(pos, target)               c.can_build_bridge(pos, target)
```

### Non-directional buildings

Take only `(position: Position)`:

```python  theme={"dark"}
c.build_harvester(pos)                    c.can_build_harvester(pos)
c.build_road(pos)                         c.can_build_road(pos)
c.build_barrier(pos)                      c.can_build_barrier(pos)
c.build_foundry(pos)                      c.can_build_foundry(pos)
c.build_launcher(pos)                     c.can_build_launcher(pos)
```

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

<ResponseField name="resign()" type="None">
  Forfeit the game immediately. Destroys this team's core, ending the game as a
  loss. **Terminates this unit's execution immediately** — no code after
  `resign()` will run.
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
  False if the current unit is not a gunner, if the gunner cannot act this turn,
  or if the team cannot afford the global 10 Ti rotate cost. Unlike `rotate()`,
  this does not raise on non-gunners.
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


Built with [Mintlify](https://mintlify.com).