import map_info
from pathing import Pathing
import comms
import units.builder
from cambc import *
import random

rc: Controller = None
nav: Pathing = None

explore_target = None
comm_flag = 1
forget = None

def init(c: Controller):
    global rc, nav, forget
    rc = c
    nav = Pathing(rc)
    forget = units.builder.forget[comm_flag]
def score():
    return 1
def generate_explore_target():
    global explore_target

    for _ in range(2):  # slightly more aggressive
        random_x = random.randint(0, map_info._width - 1)
        random_y = random.randint(0, map_info._height - 1)
        if not map_info.seen_at(random_x, random_y):
            explore_target = Position(random_x, random_y)
            return

    # If no empty tile found after 2 tries, fallback to completely random
    if (random.randint(0, 1) == 0):
        explore_target = random.choice(list(map_info._conveyors))
    random_x = random.randint(0, map_info._width - 1)
    random_y = random.randint(0, map_info._height - 1)
    explore_target = Position(random_x, random_y)
def run():
    print("EXPLORE")
    if explore_target is None:
        generate_explore_target()
    if explore_target and rc.get_position().distance_squared(explore_target) <= 8:
         generate_explore_target()
    if rc.get_global_resources()[0] < rc.get_harvester_cost()[0]:
        return

    # loop until we find a target we can path to and move.
    moved = False
    attempts = 0
    while not moved and attempts < 2:
        if not nav.move_to(explore_target):
            generate_explore_target()
            moved = True
        else:
            break
        attempts += 1
    comms.mark(explore_target, comm_flag)
