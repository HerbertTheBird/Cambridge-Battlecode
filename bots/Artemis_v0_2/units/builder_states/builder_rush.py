from cambc import Controller, Position, Direction, EntityType, Environment, GameError

from enum import Enum
import random
import sys

import map_info
import pathing
import comms

class Mode(Enum):
    RUSH_CORE = (255, 100, 100, "rush opponent core")
    BUILD_HARVESTER = (0, 180, 180, "build harvester")

    def __init__(self, r, g, b, desc):
        self.r = r
        self.g = g
        self.b = b
        self.desc = desc

mode = Mode.RUSH_CORE
indicator = []
blocked_ores = {}
defended_ores = set()
cardinal_dirs = [Direction.NORTH, Direction.SOUTH, Direction.EAST, Direction.WEST]
all_dirs = list(Direction)

# Cache deltas directly to bypass `Enum` and `.add()` overhead
ALL_DIRS_DELTAS = [(d, d.delta()) for d in all_dirs]
CARDINAL_DELTAS = [(d, d.delta()) for d in cardinal_dirs]
OUR_BUILDINGS = {EntityType.BARRIER, EntityType.HARVESTER, EntityType.LAUNCHER,
                 EntityType.CONVEYOR, EntityType.BRIDGE, EntityType.SENTINEL}

# explore state
explore_target = None
turns_since_last_explore_target = 0

rc = None
MODE_ACTIONS = None

def init(c: Controller):
    global rc, MODE_ACTIONS
    rc = c
    map_info.init(c)
    comms.init(c)
    pathing.init(c)

def run():
    global mode
    globals()[f"run_pre"]()  # preliminary calculations
    globals()[f"print"](f"CHECKING STATE: <span style='color: #{mode.r:02x}{mode.g:02x}{mode.b:02x}'>{mode.desc}</span>")
    globals()[f"check_{mode.name.lower()}"]()
    globals()[f"print"](f"NEW STATE: <span style='color: #{mode.r:02x}{mode.g:02x}{mode.b:02x}'>{mode.desc}</span>")
    globals()[f"run_{mode.name.lower()}"]()
    globals()[f"run_post"]()  # cleanup


    