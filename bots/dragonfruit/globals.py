from enum import Enum

from cambc import Direction, EntityType

USE_LAUNCHERS = False
RUSH_CORE = False

INF = float('inf')

DIRECTIONS = tuple(d for d in Direction if d is not Direction.CENTRE)
ALL_DIRECTIONS = (Direction.CENTRE, *DIRECTIONS) # We want centre first to prioritize current tile when healing, etc
CARDINAL_DIRECTIONS = (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST)

DELTAS: dict[Direction, tuple[int, int]] = {d: d.delta() for d in Direction}

CONVEYOR_TYPES = (EntityType.CONVEYOR, EntityType.ARMOURED_CONVEYOR, EntityType.BRIDGE, EntityType.SPLITTER)
TURRET_TYPES = (EntityType.GUNNER, EntityType.SENTINEL, EntityType.BREACH)

class State(Enum):
    EXPLORE = 0
    START_HARVEST_CHAIN = 1
    EXTEND_HARVEST_CHAIN = 2
    INTERCEPT = 3
    SABOTAGE = 4
    REROUTE_TITANIUM = 5
    DEFEND = 6
    HEAL = 7

class Symmetry(Enum):
    UNKNOWN = 0
    FLIP_X = 1
    FLIP_Y = 2
    ROTATE = 3
    
TIMEOUT_TURNS = 3

# Core spawn thresholds
SPAWN_INITIAL_COUNT = 3
SPAWN_LATER_COUNT = 5
SPAWN_WEALTHY_RESOURCE_THRESHOLD = 2800
SPAWN_WEALTHY_BRIDGE_MULT = 10
SPAWN_WEALTHY_BUILDER_MULT = 5
SPAWN_THREATENED_RESOURCE_MIN = 500
SPAWN_THREATENED_BUILDER_MULT = 2
SPAWN_WEALTHY_INTERVAL = 2