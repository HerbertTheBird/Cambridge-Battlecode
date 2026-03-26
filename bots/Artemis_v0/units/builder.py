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

mode = Mode.EXPLORE
indicator = []

# explore state
explore_target = None
turns_since_last_explore_target = 0

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

def force_generate_explore_target():
    global explore_target, turns_since_last_explore_target
    turns_since_last_explore_target = 0
    random_x = random.randint(0, map_info.width - 1)
    random_y = random.randint(0, map_info.height - 1)
    explore_target = Position(random_x, random_y)

# check block
def check_explore_athena():
    pass

def check_explore():
    global mode, explore_target, turns_since_last_explore_target
    
    if explore_target and rc.get_position().distance_squared(explore_target) <= 18:
        force_generate_explore_target()
    
    if turns_since_last_explore_target > (rc.get_map_width() + rc.get_map_height()) * 2:
        force_generate_explore_target()

def check_build_harvester():
    pass

def check_route():
    pass


# run block
def run_explore_athena():
    pass

def run_explore():
    global explore_target, turns_since_last_explore_target
    
    if explore_target is None:
        force_generate_explore_target()

    # loop until we find a target we can path to and move.
    moved = False
    attempts = 0
    while not moved and attempts < 1:
        if pathing.move_to(explore_target):

            moved = True
        else:
            force_generate_explore_target() # generates new target for next attempt
        attempts += 1

    turns_since_last_explore_target += 1
    
    if explore_target:
        rc.draw_indicator_line(rc.get_position(), explore_target, mode.r, mode.g, mode.b)

def run_build_harvester():
    pass

def run_route():
    pass