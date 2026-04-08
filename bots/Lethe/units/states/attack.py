import map_info
from pathing import Pathing
import comms
import units.builder
from cambc import *
from log import log


rc: Controller = None
nav: Pathing = None

comm_flag = 7

DIRECTIONS = [
    Direction.NORTH, Direction.NORTHEAST, Direction.EAST, Direction.SOUTHEAST,
    Direction.SOUTH, Direction.SOUTHWEST, Direction.WEST, Direction.NORTHWEST,
]
CARDINAL_DIRECTIONS = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]


def init(c: Controller):
    global rc, nav
    rc = c
    nav = Pathing(rc)


def on_map(pos: Position, width: int, height: int) -> bool:
    return 0 <= pos.x < width and 0 <= pos.y < height


def get_blocked_sentinel_directions(intercept_pos: Position) -> set:
    # Simplified version from dragonfruit, assuming we can't inspect input chains easily
    # This part might need more complex map analysis if available in Lethe
    return set()

def get_sentinel_direction(intercept_pos: Position, enemy_pos: Position) -> Direction | None:
    """Pick the best direction for a sentinel at intercept_pos facing enemy_pos."""
    blocked = get_blocked_sentinel_directions(intercept_pos)

    desired = intercept_pos.direction_to(enemy_pos)
    if desired not in blocked:
        return desired
    # Try rotating to find a non-blocked direction
    for rot in [desired.rotate_left(), desired.rotate_right(),
                desired.rotate_left().rotate_left(), desired.rotate_right().rotate_right()]:
        if rot not in blocked:
            return rot
    return None


def is_gunner_position(
    core_pos: Position | None,
    pos: Position,
    primary_threat: Position | None,
) -> bool:
    """
    True if pos is a good gunner location.
    Adapted from dragonfruit.
    """
    if core_pos is not None:
        dist = core_pos.distance_squared(pos)
        if 2 < dist <= 18:
            return True

    if primary_threat is None or not rc.is_in_vision(primary_threat):
        return False

    my_team = rc.get_team()
    width = map_info._width
    height = map_info._height

    for d in DIRECTIONS:
        dx, dy = d.delta()
        max_range = 3 if d in CARDINAL_DIRECTIONS else 2

        x, y = pos.x, pos.y
        for _ in range(max_range):
            x += dx
            y += dy

            cur = Position(x, y)

            if not on_map(cur, width, height):
                break

            if map_info.ground_at(x, y) == map_info._IDX_ENV_WALL:
                break

            if cur == primary_threat:
                return True
            
            bbid = None
            if rc.is_in_vision(cur):
                bbid = rc.get_tile_builder_bot_id(cur)
            if bbid is not None:
                if rc.get_team(bbid) == my_team:
                    break
                continue

            bid = None
            if rc.is_in_vision(cur):
                bid = rc.get_tile_builder_bot_id(cur)
            if bid is not None:
                etype = rc.get_entity_type(bid)
                team = rc.get_team(bid)

                if etype == EntityType.MARKER or etype == EntityType.ROAD:
                    continue

                if team == my_team:
                    break
                continue

    return False

def get_best_turret_type(pos: Position, enemy_core_pos: Position | None, primary_threat: Position | None = None) -> EntityType:
    """Return the preferred turret type for an intercept build at pos."""
    if is_gunner_position(enemy_core_pos, pos, primary_threat):
        return EntityType.GUNNER
    return EntityType.SENTINEL


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

def _get_uncovered_high_value_targets():
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
    return high_value & ~my_coverage


def _get_attack_candidates():
    """Return placement candidates filtered to those that can hit uncovered high-value targets."""
    candidates = _placement_candidates()
    if not candidates:
        return 0

    uncovered = _get_uncovered_high_value_targets()
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
    log("ATTACK")
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

    # --- New logic from dragonfruit ---
    uncovered = _get_uncovered_high_value_targets()
    primary_threat = None
    if uncovered:
        primary_threat = min(map_info.iter_mask(uncovered), key=lambda t: t.distance_squared(best))

    turret_type = get_best_turret_type(best, map_info._their_core, primary_threat)

    direction = get_sentinel_direction(best, primary_threat) if primary_threat else Direction.NORTH
    if direction is None:
        direction = Direction.NORTH # Fallback

    log(f"Attack state: best_pos={best}, threat={primary_threat}, type={turret_type}, dir={direction}")
    # --- End new logic ---

    best_bit = 1 << (best.x + best.y * width)
    best_id = map_info._building_id[best.x + best.y * width]
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
            gunner_direction = best.direction_to(primary_threat) if primary_threat is not None else direction
            rc.build_gunner(best, gunner_direction)

    comms.mark(best, comm_flag)
