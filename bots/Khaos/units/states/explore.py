from collections import deque

import map_info
from pathing import Pathing
import units.builder
from cambc import *
import random
from log import log

rc: Controller = None
nav: Pathing = None

explore_target = None
_explore_target_from_initial = False

def init(c: Controller):
    global rc, nav
    rc = c
    nav = units.builder.nav

MAX_SCORE = 1
def score():
    return 1

def generate_explore_target():
    global explore_target
    w = map_info._width
    nlc = map_info._not_left_col
    nrc = map_info._not_right_col
    board = (1 << (w * map_info._height)) - 1
    avoid = map_info.get_avoid(False, False, False)
    if rc.get_global_resources()[0] < rc.get_harvester_cost()[0]*2:
        avoid |= map_info._bm_seen & ~map_info._bm_any_building & ~map_info._bm_env[map_info._IDX_ENV_WALL]
    passable = ~avoid & board

    # Seed with all other builders' claimed tiles + incremental steps from
    # the nearest friendly bot toward each claim, plus my own position.
    seeds = 0
    my_pos = map_info._my_pos
    my_n = my_pos.x + my_pos.y * w
    seeds |= 1 << my_n
    seeds |= map_info._bm_friendly_bots

    # Seed tiles every 5 Chebyshev steps from my position toward each claim.
    bx, by = my_pos.x, my_pos.y
    mask = seeds
    while mask:
        lsb = mask & -mask
        n = lsb.bit_length() - 1
        tx, ty = n % w, n // w
        steps = max(abs(bx - tx), abs(by - ty))
        for s in range(5, steps, 5):
            ix = bx + (tx - bx) * s // steps
            iy = by + (ty - by) * s // steps
            seeds |= 1 << (ix + iy * w)
        mask ^= lsb

    # Keep the trailing 6 frontiers so we can recover the ring at iteration (c-5) once the fill terminates.
    visited = seeds
    frontier = seeds
    recent_frontiers = deque([seeds], maxlen=6)
    c = 0
    while frontier and c < 100:
        h = frontier | ((frontier & nrc) << 1) | ((frontier & nlc) >> 1)
        expanded = h | (h << w) | (h >> w)
        frontier = expanded & passable & ~visited
        visited |= frontier
        c += 1
        recent_frontiers.append(frontier)
    frontier = recent_frontiers[0]
    count = frontier.bit_count()
    if count == 0:
        explore_target = Position(random.randint(0, map_info._width - 1),
                                  random.randint(0, map_info._height - 1))
        return
    pick = random.randint(0, count - 1)
    mask = frontier
    for _ in range(pick):
        mask &= mask - 1
    lsb = mask & -mask
    n = lsb.bit_length() - 1
    explore_target = Position(n % w, n // w)

def run():
    global explore_target, _explore_target_from_initial
    log("EXPLORE")
    if units.builder._initial_explore_target is not None:
        if map_info._my_pos.distance_squared(units.builder._initial_explore_target) <= 18:
            units.builder._initial_explore_target = None
        else:
            explore_target = units.builder._initial_explore_target
            _explore_target_from_initial = True
    elif _explore_target_from_initial:
        # initial target was cleared externally (e.g. timeout); don't trust the stale copy
        explore_target = None
        _explore_target_from_initial = False
    if explore_target is None or map_info._my_pos.distance_squared(explore_target) <= 18:
        generate_explore_target()
        _explore_target_from_initial = False

    attempts = 0
    while attempts < 1:
        if not nav.move_to(explore_target):
            generate_explore_target()
        else:
            break
        attempts += 1
