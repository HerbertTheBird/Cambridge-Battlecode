import map_info
import pathing
from pathing import Pathing
import comms
import units.builder
from cambc import *
from log import log


rc: Controller = None
nav: Pathing = None

comm_flag = 6

DIRECTIONS = (
    Direction.NORTH, Direction.NORTHEAST, Direction.EAST, Direction.SOUTHEAST,
    Direction.SOUTH, Direction.SOUTHWEST, Direction.WEST, Direction.NORTHWEST,
)


def init(c: Controller):
    global rc, nav
    rc = c
    nav = Pathing(rc)


BUILDING_SCORE = [0] * map_info._NUM_ET
BUILDING_SCORE[map_info._IDX_CORE] = 100
BUILDING_SCORE[map_info._IDX_HARVESTER] = 10
BUILDING_SCORE[map_info._IDX_FOUNDRY] = 15
BUILDING_SCORE[map_info._IDX_GUNNER] = 20
BUILDING_SCORE[map_info._IDX_SENTINEL] = 20
BUILDING_SCORE[map_info._IDX_BREACH] = 25
BUILDING_SCORE[map_info._IDX_LAUNCHER] = 15
BUILDING_SCORE[map_info._IDX_CONVEYOR] = 2
BUILDING_SCORE[map_info._IDX_ARMOURED_CONVEYOR] = 3
BUILDING_SCORE[map_info._IDX_BARRIER] = 1
BUILDING_SCORE[map_info._IDX_BRIDGE] = 2
BUILDING_SCORE[map_info._IDX_SPLITTER] = 2


def _get_loaders(pos):
    """Return list of direction indices (0-7) from pos toward buildings that feed it."""
    w = map_info._width
    h = map_info._height
    px, py = pos.x, pos.y
    pos_n = px + py * w
    loaders = []

    harvesters = map_info._bm_et[map_info._IDX_HARVESTER]
    conveyors = (map_info._bm_et[map_info._IDX_CONVEYOR]
                 | map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR])

    # Cardinal-adjacent harvesters
    for di, (dx, dy) in zip([0, 2, 4, 6], [(0, -1), (1, 0), (0, 1), (-1, 0)]):
        nx, ny = px + dx, py + dy
        if 0 <= nx < w and 0 <= ny < h:
            if harvesters & (1 << (nx + ny * w)):
                loaders.append(di)

    # Any neighbor conveyor whose output targets this tile
    for di in range(8):
        dx, dy = map_info._DIR_VECS[di]
        nx, ny = px + dx, py + dy
        if 0 <= nx < w and 0 <= ny < h:
            nn = nx + ny * w
            if (conveyors & (1 << nn)) and map_info._building_conv_target[nn] == pos_n:
                if di not in loaders:
                    loaders.append(di)

    return loaders


def get_best_direction(pos):
    """Pick the best (direction, turret_type) for a turret at pos.
    Blocked: turret cannot face toward a loading building.
    Exception: gunner with 2+ loaders can face any direction.
    Score = sum of BUILDING_SCORE for enemy buildings the turret can hit."""
    w = map_info._width
    h = map_info._height
    px, py = pos.x, pos.y

    my_team_idx = map_info._my_team_idx
    enemy_buildings = map_info._bm_team[1 - my_team_idx]
    my_buildings = map_info._bm_team[my_team_idx]
    walls = map_info._bm_env[map_info._IDX_ENV_WALL]

    loaders = _get_loaders(pos)
    loader_dirs = set(loaders)
    sentinel_blocked = loader_dirs
    breach_blocked = loader_dirs
    gunner_blocked = set() if len(loaders) >= 2 else loader_dirs

    my_foundries = map_info._bm_et[map_info._IDX_FOUNDRY] & my_buildings
    adj_foundry = False
    for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
        nx, ny = px + dx, py + dy
        if 0 <= nx < w and 0 <= ny < h and (my_foundries & (1 << (nx + ny * w))):
            adj_foundry = True
            break

    best_b_dir, best_b_score = Direction.NORTH, -1
    best_s_dir, best_s_score = Direction.NORTH, -1
    best_g_dir, best_g_score = Direction.NORTH, -1

    for di in range(8):
        # Breach score
        if di not in breach_blocked:
            core_counted = False
            b_score = 0
            for dx, dy in map_info._BREACH_OFFSETS[di]:
                sx, sy = px + dx, py + dy
                if 0 <= sx < w and 0 <= sy < h:
                    sbit = 1 << (sx + sy * w)
                    if enemy_buildings & sbit:
                        et_idx = map_info._building_et_idx[sx + sy * w]
                        if et_idx >= 0 and (not core_counted or et_idx != map_info._IDX_CORE):
                            b_score += BUILDING_SCORE[et_idx]
                            if et_idx == map_info._IDX_CORE:
                                core_counted = True
            if b_score > best_b_score:
                best_b_score = b_score
                best_b_dir = DIRECTIONS[di]

        # Sentinel score
        if di not in sentinel_blocked:
            core_counted = False
            s_score = 0
            for dx, dy in map_info._SENTINEL_OFFSETS[di]:
                sx, sy = px + dx, py + dy
                if 0 <= sx < w and 0 <= sy < h:
                    sbit = 1 << (sx + sy * w)
                    if enemy_buildings & sbit:
                        et_idx = map_info._building_et_idx[sx + sy * w]
                        if et_idx >= 0 and (not core_counted or et_idx != map_info._IDX_CORE):
                            s_score += BUILDING_SCORE[et_idx]
                            if et_idx == map_info._IDX_CORE:
                                core_counted = True
            if s_score > best_s_score:
                best_s_score = s_score
                best_s_dir = DIRECTIONS[di]

        # Gunner score — single ray, wall/friendly-blocked
        if di not in gunner_blocked:
            g_score = 0
            for dx, dy in map_info._GUNNER_RAYS[di]:
                sx, sy = px + dx, py + dy
                if not (0 <= sx < w and 0 <= sy < h):
                    break
                sbit = 1 << (sx + sy * w)
                if walls & sbit:
                    break
                if my_buildings & sbit:
                    if not map_info._bm_et[map_info._IDX_ROAD] & sbit:
                        break
                if enemy_buildings & sbit:
                    et_idx = map_info._building_et_idx[sx + sy * w]
                    if et_idx >= 0:
                        g_score += BUILDING_SCORE[et_idx]
            g_score *= 5
            if g_score > best_g_score:
                best_g_score = g_score
                best_g_dir = DIRECTIONS[di]

    if adj_foundry:
        if best_b_score > 0:
            return best_b_dir, EntityType.BREACH, best_b_score
        if best_s_score > 0:
            return best_s_dir, EntityType.SENTINEL, best_s_score
        return best_g_dir, EntityType.GUNNER, best_g_score

    if best_s_score >= best_g_score:
        return best_s_dir, EntityType.SENTINEL, best_s_score
    return best_g_dir, EntityType.GUNNER, best_g_score


def _my_turret_coverage():
    """Bitmask of all tiles my turrets can attack (regardless of ammo)."""
    my_team_idx = map_info._my_team_idx
    my_team_bm = map_info._bm_team[my_team_idx]
    w = map_info._width
    h = map_info._height
    coverage = 0

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


def _high_value_targets():
    """Bitmask of enemy high-value buildings not already covered by my turrets."""
    my_team_idx = map_info._my_team_idx
    enemy_idx = 1 - my_team_idx
    enemy = map_info._bm_team[enemy_idx]

    high_value = (
        map_info._bm_et[map_info._IDX_FOUNDRY]
        | map_info._bm_et[map_info._IDX_GUNNER]
        | map_info._bm_et[map_info._IDX_SENTINEL]
        | map_info._bm_et[map_info._IDX_BREACH]
        | map_info._bm_et[map_info._IDX_CORE]
        | map_info._bm_et[map_info._IDX_LAUNCHER]
        | map_info._bm_et[map_info._IDX_HARVESTER]
    ) & enemy
    if not high_value:
        return 0

    my_coverage = _my_turret_coverage()
    return high_value & ~my_coverage


def _placement_candidates():
    """Bitmask of tiles where a turret could be placed."""
    my_team_idx = map_info._my_team_idx
    enemy_idx = 1 - my_team_idx
    my_team = map_info._bm_team[my_team_idx]
    enemy_team = map_info._bm_team[enemy_idx]

    # Location filter: conveyor outputs + cardinal adj to harvesters
    candidates = map_info._bm_ti_fed | map_info._bm_ax_fed
    harvesters = (map_info._bm_et[map_info._IDX_HARVESTER]&map_info._bm_env[map_info._IDX_ENV_ORE_TI]) | map_info._bm_et[map_info._IDX_FOUNDRY]  # double for safety margin
    if harvesters:
        candidates |= map_info.expand_manhattan(harvesters)

    # Tile content filter: empty, or clearable
    empty = ~map_info._bm_any_building

    my_clearable = (
        map_info._bm_et[map_info._IDX_BARRIER]
        | map_info._bm_et[map_info._IDX_ROAD]
        | map_info._bm_et[map_info._IDX_MARKER]
    ) & my_team

    enemy_clearable = (
        map_info._bm_et[map_info._IDX_MARKER]
        | map_info._bm_et[map_info._IDX_ROAD]
    ) & enemy_team

    candidates &= (empty | my_clearable | enemy_clearable)

    # Exclusions
    candidates &= ~map_info._bm_env[map_info._IDX_ENV_WALL]

    # Exclude tiles with any builder bots (except me)
    my_bit = 1 << (rc.get_position().x + rc.get_position().y * map_info._width)
    all_bots = (map_info._bm_friendly_bots | map_info._bm_enemy_bots) & ~my_bit
    candidates &= ~all_bots

    # Avoid enemy builder bots within 6 manhattan — only for enemy road candidates
    enemy_bots = map_info._bm_enemy_bots
    if enemy_bots:
        danger = enemy_bots
        for _ in range(6):
            danger = map_info.expand_manhattan(danger)
        enemy_roads = map_info._bm_et[map_info._IDX_ROAD] & enemy_team
        candidates &= ~(danger & enemy_roads)

    return candidates


def _sentinel_all_offsets():
    """Union of all sentinel offsets across all 8 directions as (dx, dy) set."""
    offsets = set()
    for di in range(8):
        for dx, dy in map_info._SENTINEL_OFFSETS[di]:
            offsets.add((dx, dy))
    return offsets

_sentinel_all_reach_cache = None

def _sentinel_all_reach(targets):
    """Bitmask of positions from which a sentinel (any direction) could hit at least one target.
    Uses reverse-shift of the union of all direction offsets."""
    global _sentinel_all_reach_cache
    if _sentinel_all_reach_cache is None:
        _sentinel_all_reach_cache = list(_sentinel_all_offsets())
    w = map_info._width
    reachable = 0
    for dx, dy in _sentinel_all_reach_cache:
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


def _get_attack_candidates():
    """Return (non_roaded, roaded) candidate bitmasks."""
    candidates = _placement_candidates()
    if not candidates:
        return 0, 0

    targets = _high_value_targets()
    if not targets:
        return 0, 0

    # Filter to candidates that can hit at least one target in some direction
    reachable = _sentinel_all_reach(targets)
    filtered = candidates & reachable

    if not filtered:
        return 0, 0

    # Split into non-enemy-roaded vs enemy-roaded
    my_team_idx = map_info._my_team_idx
    enemy_idx = 1 - my_team_idx
    enemy_roads = map_info._bm_et[map_info._IDX_ROAD] & map_info._bm_team[enemy_idx]

    roaded = filtered & enemy_roads
    non_roaded = filtered & ~enemy_roads

    return non_roaded, roaded


def _my_claims():
    w = map_info._width
    my_mask = 1 << (rc.get_position().x + rc.get_position().y * w)
    non_roaded, roaded = _get_attack_candidates()
    combined = non_roaded | roaded
    claimed = pathing.voronoi_claim(my_mask, units.builder.claimed_senders[comm_flag], combined)
    return claimed & non_roaded, claimed & roaded

def score():
    if rc.get_global_resources()[0] < rc.get_sentinel_cost()[0]:
        return 0
    non_roaded, roaded = _my_claims()
    return 6 if (non_roaded or roaded) else 0


def run():
    log("ATTACK")
    non_roaded, roaded = _my_claims()

    if not non_roaded and not roaded:
        return

    width = map_info._width
    my_team_idx = map_info._my_team_idx
    candidates = non_roaded | roaded

    # Evaluate all adjacent candidate tiles and pick highest scoring
    my_pos = rc.get_position()
    best = None
    best_score = -1
    best_direction = Direction.NORTH
    best_turret_type = EntityType.SENTINEL
    best_is_enemy_road = False

    enemy_roads = map_info._bm_et[map_info._IDX_ROAD] & map_info._bm_team[1 - my_team_idx]

    mask = candidates
    while mask:
        lsb = mask & -mask
        n = lsb.bit_length() - 1
        px, py = n % width, n // width
        if max(abs(px - my_pos.x), abs(py - my_pos.y)) <= 1:
            pos = Position(px, py)
            direction, turret_type, dir_score = get_best_direction(pos)
            # Prefer non-roaded tiles
            is_er = bool(enemy_roads & lsb)
            adj_score = (0 if is_er else 1, dir_score)
            if adj_score > (0 if best_is_enemy_road else 1, best_score):
                best = pos
                best_score = dir_score
                best_direction = direction
                best_turret_type = turret_type
                best_is_enemy_road = is_er
        mask ^= lsb

    if best is None:
        # No adjacent candidates, move toward closest
        if non_roaded:
            best, _ = nav.closest(non_roaded)
        if best is None and roaded:
            best, _ = nav.closest(roaded)
        if best is None:
            return
        best_direction, best_turret_type, _ = get_best_direction(best)
        best_n = best.x + best.y * width
        best_is_enemy_road = bool(enemy_roads & (1 << best_n))

    best_n = best.x + best.y * width
    best_bit = 1 << best_n
    best_id = map_info._building_id[best_n]
    is_mine = bool(map_info._bm_team[my_team_idx] & best_bit)

    direction = best_direction
    turret_type = best_turret_type
    is_enemy_road = best_is_enemy_road
    log(f"Attack: best={best}, dir={direction}, type={turret_type}, enemy_road={is_enemy_road}")

    my_team = map_info._my_team

    count = 0
    for uid in rc.get_nearby_units(4):
        if rc.get_entity_type(uid) != map_info._ET_BUILDER_BOT or rc.get_team(uid) == my_team:
            continue
        count += 1

    if is_enemy_road:
        # Move onto enemy road, fire it, step off
        nav.move_to(best)
        if rc.can_fire(best):
            if count == 0 or rc.get_hp(best_id) <= 2: # bait them to move away
                rc.fire(best)
        for d in Direction:
            if d == Direction.CENTRE:
                continue
            if rc.can_move(d):
                rc.move(d)
                map_info.update_move()
                break
    else:
        # Move adjacent and destroy own building if needed
        nav.move_adjacent(best)
        if best_id and is_mine:
            if rc.can_destroy(best) and rc.get_action_cooldown() == 0:
                log(f"Attack destroy own building at {best}")
                rc.destroy(best)
                map_info.update_at(best)

    # Place turret
    if turret_type == EntityType.GUNNER:
        if rc.can_build_gunner(best, direction):
            rc.build_gunner(best, direction)
            map_info.update_at(best)
    elif turret_type == EntityType.BREACH:
        if rc.can_build_breach(best, direction):
            rc.build_breach(best, direction)
            map_info.update_at(best)
    else:
        if rc.can_build_sentinel(best, direction):
            rc.build_sentinel(best, direction)
            map_info.update_at(best)

    comms.mark(best.x + best.y * map_info._width, comm_flag)
