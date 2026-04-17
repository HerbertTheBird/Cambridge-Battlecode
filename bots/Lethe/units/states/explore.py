from cambc import *

import map_info
from pathing import Pathing
import comms
import units.builder
import random
from log import log

rc: Controller = None
nav: Pathing = None

explore_target = None
comm_flag = 1
_first_explore = True # New global flag

def init(c: Controller):
    global rc, nav
    rc = c
    nav = Pathing(rc)
    global _first_explore # Reset for each unit
    _first_explore = True

def score():
    return 1

def generate_explore_target():
    global explore_target
    global _first_explore

    if _first_explore:
        _first_explore = False
        core_pos = rc.get_position()
        attempts = 0
        while attempts < 10: # Try a few times
            dx = random.randint(-10, 10)
            dy = random.randint(-10, 10)
            # Check distance squared (dx*dx + dy*dy <= 100)
            if dx*dx + dy*dy <= 100:
                potential_target = Position(core_pos.x + dx, core_pos.y + dy)
                if map_info.in_bounds(potential_target): # Use map_info.in_bounds
                    explore_target = potential_target
                    log(f"First explore target: {explore_target}")
                    return
            attempts += 1
        log("Couldn't find nearby target, falling back to random logic.")

    # Subsequent runs: Pick a random location at least 3 units from map edges.
    w = map_info._width
    h = map_info._height
    
    min_x = 3
    max_x = w - 4 # w - 1 (last index) - 3 (margin)
    min_y = 3
    max_y = h - 4 # h - 1 (last index) - 3 (margin)

    if min_x > max_x or min_y > max_y: # Handle very small maps
        log("Map too small for 3-unit edge margin, picking random on whole map.")
        explore_target = Position(random.randint(0, w - 1), random.randint(0, h - 1))
    else:
        rand_x = random.randint(min_x, max_x)
        rand_y = random.randint(min_y, max_y)
        explore_target = Position(rand_x, rand_y)
    
    log(f"New explore target: {explore_target}")


def run():
    log("EXPLORE")
    if explore_target is None or map_info._my_pos.distance_squared(explore_target) <= 18:
        generate_explore_target()

    attempts = 0
    while attempts < 1:
        if not nav.move_to(explore_target):
            generate_explore_target()
        else:
            break
        attempts += 1
    if rc.get_global_resources()[0] >= rc.get_harvester_cost()[0]*5:
        comms.mark(explore_target.x + explore_target.y * map_info._width, comm_flag)
