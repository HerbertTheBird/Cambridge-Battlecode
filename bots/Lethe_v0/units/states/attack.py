import map_info
from pathing import Pathing
import comms
import units.builder
from cambc import *

rc: Controller = None
nav: Pathing = None

comm_flag = 7

# Score weights per entity type index (adjustable)
_WEIGHTS = {
    map_info._IDX_CONVEYOR: 1,
    map_info._IDX_SPLITTER: 2,
    map_info._IDX_BRIDGE: 5,
    map_info._IDX_ARMOURED_CONVEYOR: 5,
    map_info._IDX_HARVESTER: 20,
    map_info._IDX_FOUNDRY: 50,
    map_info._IDX_ROAD: 0,
    map_info._IDX_BARRIER: 5,
    map_info._IDX_GUNNER: 10,
    map_info._IDX_SENTINEL: 30,
    map_info._IDX_BREACH: 65,
    map_info._IDX_LAUNCHER: 20,
    map_info._IDX_CORE: 100,
}

GUNNER_MULTIPLIER = 3
SCORE_THRESHOLD = -15  # negative starting score to prevent random shooting of roads

def init(c: Controller):
    global rc, nav
    rc = c
    nav = Pathing(rc)

_CARD_FEED = [(0, -1, 0), (1, 0, 2), (0, 1, 4), (-1, 0, 6)]  # (dx, dy, dir_idx toward neighbor)

def _score_position(pos_n):
    """Evaluate a position for turret placement.
    Returns (score, best_dir_idx, best_type) or None if no positive score."""
    my_team_idx = map_info._TM_INT[rc.get_team()]
    enemy_idx = 1 - my_team_idx
    enemy = map_info._bm_team[enemy_idx]

    w = map_info._width
    h = map_info._height
    x = pos_n % w
    y = pos_n // w

    # Forbidden facing directions: can't face toward feeding conveyor or harvester
    my_conveyors = (
        map_info._bm_et[map_info._IDX_CONVEYOR]
        | map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR]
        | map_info._bm_et[map_info._IDX_SPLITTER]
        | map_info._bm_et[map_info._IDX_BRIDGE]
    ) & map_info._bm_team[my_team_idx]
    my_harvesters = map_info._bm_et[map_info._IDX_HARVESTER] & map_info._bm_team[my_team_idx]
    forbidden = 0
    for dx, dy, di in _CARD_FEED:
        nx, ny = x + dx, y + dy
        if 0 <= nx < w and 0 <= ny < h:
            nn = nx + ny * w
            nbit = 1 << nn
            if (nbit & my_conveyors and map_info._building_conv_target[nn] == pos_n) \
                    or (nbit & my_harvesters):
                forbidden |= (1 << di)

    best_score = SCORE_THRESHOLD
    best_dir = 0
    best_type = None

    for dir_idx in range(8):
        if (1 << dir_idx) & forbidden:
            continue

        # Sentinel
        sentinel_mask = map_info.turret_attack_mask(pos_n, dir_idx, map_info._IDX_SENTINEL)
        s_score = SCORE_THRESHOLD
        hittable = sentinel_mask & enemy
        for idx, weight in _WEIGHTS.items():
            overlap = map_info._bm_et[idx] & hittable
            s_score += overlap.bit_count() * weight
        if s_score > best_score:
            best_score = s_score
            best_dir = dir_idx
            best_type = EntityType.SENTINEL

        # Gunner
        gunner_mask = map_info.turret_attack_mask(pos_n, dir_idx, map_info._IDX_GUNNER)
        g_score = SCORE_THRESHOLD
        hittable = gunner_mask & enemy
        for idx, weight in _WEIGHTS.items():
            overlap = map_info._bm_et[idx] & hittable
            g_score += overlap.bit_count() * weight * GUNNER_MULTIPLIER
        if g_score > best_score:
            best_score = g_score
            best_dir = dir_idx
            best_type = EntityType.GUNNER

    if best_score <= 0 or best_type is None:
        return None
    return (best_score, best_dir, best_type)

def _placement_candidates():
    """Bitmask of tiles where a turret could be placed.
    Location: all conveyor outputs + cardinally adjacent to harvesters.
    Tile must be: empty, or my conveyor/barrier/road/marker, or enemy marker/road.
    Excluded: enemy turret threat, enemy launcher adjacency, walls."""
    my_team_idx = map_info._TM_INT[rc.get_team()]
    enemy_idx = 1 - my_team_idx
    my_team = map_info._bm_team[my_team_idx]
    enemy_team = map_info._bm_team[enemy_idx]

    # Location filter: conveyor outputs + cardinal adj to harvesters
    candidates = map_info._bm_conveyor_targets
    harvesters = map_info._bm_et[map_info._IDX_HARVESTER]
    if harvesters:
        candidates |= map_info.expand_manhattan(harvesters)

    # Tile content filter: empty, or clearable
    has_building = 0
    for i in range(map_info._NUM_ET):
        has_building |= map_info._bm_et[i]
    empty = ~has_building

    my_clearable = (
        map_info._bm_et[map_info._IDX_CONVEYOR]
        | map_info._bm_et[map_info._IDX_BARRIER]
        | map_info._bm_et[map_info._IDX_ROAD]
        | map_info._bm_et[map_info._IDX_MARKER]
    ) & my_team

    enemy_clearable = (
        map_info._bm_et[map_info._IDX_MARKER]
        | map_info._bm_et[map_info._IDX_ROAD]
    ) & enemy_team

    candidates &= (empty | my_clearable | enemy_clearable)

    # Exclusions
    candidates &= ~map_info._bm_enemy_turret_threat
    candidates &= ~map_info._bm_enemy_launch_adj
    candidates &= ~map_info._bm_env[map_info._IDX_ENV_WALL]
    candidates &= ~units.builder.forget[comm_flag]

    return candidates

def _get_attack_targets():
    """Return bitmask of candidate positions that have a positive score."""
    candidates = _placement_candidates()
    if not candidates:
        return 0

    w = map_info._width
    result = 0
    mask = candidates
    while mask:
        lsb = mask & -mask
        n = lsb.bit_length() - 1
        res = _score_position(n)
        if res is not None:
            result |= lsb
        mask ^= lsb
    return result

def score():
    if rc.get_global_resources()[0] < rc.get_sentinel_cost()[0]:
        return 0
    return 7 if _get_attack_targets() else 0

def run():
    print("ATTACK")
    candidates = _placement_candidates()
    if not candidates:
        return

    core = map_info._my_core
    if core is None:
        return

    width = map_info._width

    # Find closest candidate to core, then pick the one with best score
    reached = 1 << (core.x + core.y * width)
    best = None
    best_info = None

    for _ in range(width + map_info._height):
        found = candidates & reached
        if found:
            # Evaluate all candidates at this distance, pick highest score
            m = found
            while m:
                lsb = m & -m
                n = lsb.bit_length() - 1
                res = _score_position(n)
                if res is not None:
                    if best_info is None or res[0] > best_info[0]:
                        best = Position(n % width, n // width)
                        best_info = res
                m ^= lsb
            if best is not None:
                break
        reached = map_info.expand_manhattan(reached)

    if best is None or best_info is None:
        return

    _, dir_idx, turret_type = best_info
    direction = map_info._INT_DIR[dir_idx]
    width = map_info._width

    best_n = best.x + best.y * width
    best_bit = 1 << best_n
    best_id = map_info._building_id[best_n]
    my_team_idx = map_info._TM_INT[rc.get_team()]
    is_mine = bool(map_info._bm_team[my_team_idx] & best_bit)

    if best_id and not is_mine:
        # Enemy road/marker — move onto it, fire, then step off to place
        nav.move_to({best})
        if rc.can_fire(rc.get_position()):
            rc.fire(rc.get_position())
        # Try to step off to an adjacent tile so we can build
        for d in Direction:
            if d == Direction.CENTRE:
                continue
            p = best.add(d)
            if map_info.in_bounds(p) and rc.can_move(d):
                rc.move(d)
                break
    else:
        # Move adjacent to build position
        adj = set()
        for d in Direction:
            if d == Direction.CENTRE:
                continue
            p = best.add(d)
            if map_info.in_bounds(p) and map_info.is_passable(p):
                adj.add(p)
        if not adj:
            return
        nav.move_to(adj)

        if best_id and is_mine:
            # My conveyor/barrier/road/marker — destroy it
            if rc.can_destroy(best):
                rc.destroy(best)
                map_info.note_destroy(best)

    # Place turret (marker gets overwritten automatically)
    if turret_type == EntityType.SENTINEL:
        if rc.can_build_sentinel(best, direction):
            rc.build_sentinel(best, direction)
    elif turret_type == EntityType.GUNNER:
        if rc.can_build_gunner(best, direction):
            rc.build_gunner(best, direction)

    comms.mark(best, comm_flag)
