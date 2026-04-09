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
    map_info._IDX_BRIDGE: 4,
    map_info._IDX_ARMOURED_CONVEYOR: 4,
    map_info._IDX_HARVESTER: 35,
    map_info._IDX_FOUNDRY: 55,
    map_info._IDX_ROAD: 0,
    map_info._IDX_BARRIER: 4,
    map_info._IDX_GUNNER: 40,
    map_info._IDX_SENTINEL: 50,
    map_info._IDX_BREACH: 60,
    map_info._IDX_LAUNCHER: 10,
    map_info._IDX_CORE: 35,
}

GUNNER_MULTIPLIER = 2
SCORE_THRESHOLD = -30  # negative starting score to prevent random shooting of roads

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

def _my_turret_coverage():
    """Bitmask of all tiles my turrets can attack (regardless of ammo)."""
    my_team_idx = map_info._TM_INT[rc.get_team()]
    my_team_bm = map_info._bm_team[my_team_idx]
    w = map_info._width
    h = map_info._height
    coverage = 0

    # Breach + Sentinel: use shift masks like _compute_enemy_turret_threat
    for turret_idx, offsets_table in ((map_info._IDX_BREACH, map_info._BREACH_OFFSETS),
                                      (map_info._IDX_SENTINEL, map_info._SENTINEL_OFFSETS)):
        turrets = map_info._bm_et[turret_idx] & my_team_bm
        if not turrets:
            continue
        dir_masks = [0] * 8
        m = turrets
        while m:
            lsb = m & -m
            n = lsb.bit_length() - 1
            di = map_info._building_dir[n]
            dir_masks[di] |= lsb
            m ^= lsb
        for di in range(8):
            dm = dir_masks[di]
            if not dm:
                continue
            for dx, dy in offsets_table[di]:
                shift_mask = map_info._turret_shift_masks.get((dx, dy))
                if shift_mask is None:
                    continue
                offset = dx + dy * w
                if offset > 0:
                    coverage |= (dm & shift_mask) << offset
                else:
                    coverage |= (dm & shift_mask) >> (-offset)

    # Gunner: per-turret rays with wall blocking
    gunners = map_info._bm_et[map_info._IDX_GUNNER] & my_team_bm
    if gunners:
        walls = map_info._bm_env[map_info._IDX_ENV_WALL]
        m = gunners
        while m:
            lsb = m & -m
            n = lsb.bit_length() - 1
            px = n % w
            py = n // w
            for ray_di in range(8):
                for dx, dy in map_info._GUNNER_RAYS[ray_di]:
                    nx, ny = px + dx, py + dy
                    if not (0 <= nx < w and 0 <= ny < h):
                        break
                    bit = 1 << (nx + ny * w)
                    if walls & bit:
                        break
                    coverage |= bit
            m ^= lsb

    return coverage

def _sentinel_reachable(targets):
    """Bitmask of positions from which a sentinel (any direction) could hit at least one target tile.
    Computed by shifting targets by reversed sentinel offsets."""
    w = map_info._width
    reachable = 0
    for di in range(8):
        for dx, dy in map_info._SENTINEL_OFFSETS[di]:
            # Reverse: if sentinel at A hits A+(dx,dy)=B, then from B we need A = B+(-dx,-dy)
            rdx, rdy = -dx, -dy
            shift_mask = map_info._turret_shift_masks.get((rdx, rdy))
            if shift_mask is None:
                continue
            offset = rdx + rdy * w
            if offset > 0:
                reachable |= (targets & shift_mask) << offset
            else:
                reachable |= (targets & shift_mask) >> (-offset)
    return reachable

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

def _get_attack_candidates():
    """Return placement candidates filtered to those that can hit uncovered high-value targets."""
    candidates = _placement_candidates()
    if not candidates:
        return 0

    my_team_idx = map_info._TM_INT[rc.get_team()]
    enemy_idx = 1 - my_team_idx
    enemy = map_info._bm_team[enemy_idx]

    # High-value enemy targets only
    high_value = (
        map_info._bm_et[map_info._IDX_HARVESTER]
        | map_info._bm_et[map_info._IDX_FOUNDRY]
        | map_info._bm_et[map_info._IDX_GUNNER]
        | map_info._bm_et[map_info._IDX_SENTINEL]
        | map_info._bm_et[map_info._IDX_BREACH]
        | map_info._bm_et[map_info._IDX_CORE]
    ) & enemy

    if not high_value:
        return 0

    # Remove targets already covered by my turrets
    my_coverage = _my_turret_coverage()
    uncovered = high_value & ~my_coverage
    if not uncovered:
        return 0

    # Positions from which a sentinel could hit any uncovered target
    reachable = _sentinel_reachable(uncovered)

    return candidates & reachable

def score():
    if rc.get_global_resources()[0] < rc.get_sentinel_cost()[0]:
        return 0
    return 7 if _get_attack_candidates() else 0

def run():
    print("ATTACK")
    candidates = _get_attack_candidates()
    if not candidates:
        return

    core = map_info._my_core
    if core is None:
        return

    width = map_info._width

    # Find closest candidate to core via Manhattan expansion
    reached = 1 << (core.x + core.y * width)
    best = None

    for _ in range(width + map_info._height):
        found = candidates & reached
        if found:
            lsb = found & -found
            n = lsb.bit_length() - 1
            best = Position(n % width, n // width)
            break
        reached = map_info.expand_manhattan(reached)

    if best is None:
        return

    # Full scoring on chosen tile
    best_n = best.x + best.y * width
    best_info = _score_position(best_n)
    if best_info is None:
        comms.mark(best, comm_flag)
        return

    _, dir_idx, turret_type = best_info
    direction = map_info._INT_DIR[dir_idx]

    best_bit = 1 << best_n
    best_id = map_info._building_id[best_n]
    my_team_idx = map_info._TM_INT[rc.get_team()]
    is_mine = bool(map_info._bm_team[my_team_idx] & best_bit)

    if best_id and not is_mine:
        # Enemy road/marker — move onto it, fire, then step off to place
        nav.move_to({best})
        if rc.can_fire(best):
            rc.fire(best)
        for d in Direction:
            if d == Direction.CENTRE:
                continue
            if rc.can_move(d):
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
            if rc.can_destroy(best):
                rc.destroy(best)
                map_info.note_destroy(best)

    if turret_type == EntityType.SENTINEL:
        if rc.can_build_sentinel(best, direction):
            rc.build_sentinel(best, direction)
    elif turret_type == EntityType.GUNNER:
        if rc.can_build_gunner(best, direction):
            rc.build_gunner(best, direction)

    comms.mark(best, comm_flag)
