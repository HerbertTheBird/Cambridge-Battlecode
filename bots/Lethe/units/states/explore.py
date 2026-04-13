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

def init(c: Controller):
    global rc, nav
    rc = c
    nav = Pathing(rc)

def score():
    return 1

def generate_explore_target():
    global explore_target
    w = map_info._width
    nlc = map_info._not_left_col
    nrc = map_info._not_right_col
    board = (1 << (w * map_info._height)) - 1
    avoid = map_info.get_avoid(False, False, False)
    if rc.get_global_resources()[0] < rc.get_harvester_cost()[0]*5:
        has_building = 0
        for i in range(map_info._NUM_ET):
            has_building |= map_info._bm_et[i]
        avoid |= map_info._bm_seen & ~has_building & ~map_info._bm_env[map_info._IDX_ENV_WALL]
    passable = ~avoid & board

    # Seed with all other builders' claimed tiles (all flags) + my position
    seeds = 0
    for i, f in enumerate(units.builder.forget):
        if i == 7:  # heal flag uses enemy IDs, not tile positions
            continue
        seeds |= f

    visited = seeds
    frontier = seeds
    prev_frontier = frontier
    c = 0
    while frontier and c < 100:
        prev_frontier = frontier
        expanded = frontier | ((frontier & nrc) << 1) | ((frontier & nlc) >> 1) | (frontier << w) | (frontier >> w)
        frontier = expanded & passable & ~visited
        visited |= frontier
        c += 1

    # prev_frontier is the last ring before flood filled everything.
    # Pick a random unset bit from that ring (tiles NOT claimed by anyone).
    unclaimed = prev_frontier & ~units.builder.forget[comm_flag]
    pool = unclaimed if unclaimed else prev_frontier
    count = pool.bit_count()
    if count == 0:
        explore_target = Position(random.randint(0, map_info._width - 1),
                                  random.randint(0, map_info._height - 1))
        return
    pick = random.randint(0, count - 1)
    mask = pool
    for _ in range(pick):
        mask &= mask - 1
    lsb = mask & -mask
    n = lsb.bit_length() - 1
    explore_target = Position(n % w, n // w)

def run():
    print("EXPLORE")
    if explore_target is None or rc.get_position().distance_squared(explore_target) <= 18:
        generate_explore_target()

    attempts = 0
    while attempts < 2:
        if not nav.move_to(explore_target, rc.get_global_resources()[0] < rc.get_harvester_cost()[0]*5):
            generate_explore_target()
        else:
            break
        attempts += 1
    comms.mark(explore_target.x + explore_target.y * map_info._width, comm_flag)
