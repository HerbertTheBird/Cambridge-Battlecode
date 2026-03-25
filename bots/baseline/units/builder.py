from cambc import Controller, Position, Direction, EntityType, Environment

from enum import Enum
import random
import sys

import map_info
import pathing

class Mode(Enum):
    EXPLORE_ATHENA = (100, 255, 100, "preliminary explore (athena)")
    EXPLORE = (0, 255, 0, "explore")
    BUILD_HARVESTER = (0, 180, 180, "build harvester")
    ROUTE = (255, 255, 0, "route to core")

    def __init__(self, r, g, b, desc):
        self.r = r
        self.g = g
        self.b = b
        self.desc = desc

mode = Mode.EXPLORE_ATHENA
indicator = []

rc = None
def init(c : Controller):
    global rc
    rc = c
    map_info.init(c)
    pathing.init(c)
    pass

def run():
    global mode
    run_pre() # preliminary calculations
    pathing.move_to(Position(1, 1))
    print(f"CHECKING STATE: {mode.desc}")
    globals()[f"check_{mode.name.lower()}"]()
    print(f"NEW STATE: {mode.desc}")
    globals()[f"run_{mode.name.lower()}"]()
    run_post() # cleanup


# invariant calculations
def run_pre():
    map_info.update()
    pass

def run_post():
    pass


# check block
def check_explore_athena():
    pass

def check_explore():
    pass

def check_build_harvester():
    pass

def check_route():
    pass


# run block
def run_explore_athena():
    pass

def run_explore():
    pass

def run_build_harvester():
    pass

def run_route():
    pass