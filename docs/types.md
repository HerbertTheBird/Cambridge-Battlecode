> ## Documentation Index
> Fetch the complete documentation index at: https://docs.battlecode.cam/llms.txt
> Use this file to discover all available pages before exploring further.

# Types & Enums

> All game types available from `from cambc import *`.

All types are imported from the `cambc` module:

```python  theme={"dark"}
from cambc import *
```

This gives you: `Team`, `EntityType`, `ResourceType`, `Environment`, `Direction`, `Position`, [`GameConstants`](/api/constants), `GameError`, and [`Controller`](/api/controller).

## Team

```python  theme={"dark"}
class Team(Enum):
    A = "a"
    B = "b"
```

## EntityType

```python  theme={"dark"}
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

```python  theme={"dark"}
class ResourceType(Enum):
    TITANIUM = "titanium"
    RAW_AXIONITE = "raw_axionite"
    REFINED_AXIONITE = "refined_axionite"
```

## Environment

```python  theme={"dark"}
class Environment(Enum):
    EMPTY = "empty"
    WALL = "wall"
    ORE_TITANIUM = "ore_titanium"
    ORE_AXIONITE = "ore_axionite"
```

## Direction

```python  theme={"dark"}
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

```python  theme={"dark"}
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

```python  theme={"dark"}
pos = Position(5, 10)
new_pos = pos.add(Direction.NORTH)      # Position(5, 9)
dist = pos.distance_squared(new_pos)    # 1
dir = pos.direction_to(Position(8, 7))  # Direction.NORTHEAST
```

## GameError

```python  theme={"dark"}
class GameError(Exception):
    pass
```

Raised when a player issues an invalid action (e.g., building on an occupied tile, moving with cooldown > 0).


Built with [Mintlify](https://mintlify.com).