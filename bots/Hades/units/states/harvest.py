import map_info
import pathing
from pathing import Pathing
import comms
from cambc import *
import units.builder
from log import log
import sys
rc: Controller = None
nav: Pathing = None

comm_flag = 4

def _my_claims():
    my_pos = map_info._my_pos
    w = map_info._width
    my_mask = 1 << (my_pos.x + my_pos.y * w)
    available = harvestable_ore() & ~_too_expensive()
    return available & ~pathing.voronoi_claim(map_info._bm_friendly_bots, my_mask, available) & ~map_info._bm_friendly_bots

def init(c: Controller):
    global rc, nav
    rc = c
    nav = units.builder.nav

cant_harvest = 0
_cost_map: dict[int, tuple[int, int]] = {}  # tile index -> (min titanium cost, round recorded)
COST_MAP_TTL = 100
def possible_ore():
    w = map_info._width
    ore = map_info._bm_env[map_info._IDX_ENV_ORE_TI]
    if (map_info._bm_team[map_info._my_team_idx] & map_info._bm_et[map_info._IDX_HARVESTER] & map_info._bm_env[map_info._IDX_ENV_ORE_TI]) and rc.get_current_round() >= 750:
        ore |= map_info._bm_env[map_info._IDX_ENV_ORE_AX]

    my_team_idx = map_info._my_team_idx
    enemy_idx = 1 - my_team_idx

    # Enemy buildings that block harvesting (not road/conveyor/bridge/splitter/marker)
    enemy_blocking = (
        map_info._bm_team[enemy_idx]
        & ~map_info._bm_et[map_info._IDX_HARVESTER]
        & ~map_info._bm_et[map_info._IDX_ROAD]
        & ~map_info._bm_et[map_info._IDX_MARKER]
    )
    # Friendly buildings that block harvesting (not road/barrier/marker)
    friendly_blocking = (
        map_info._bm_team[my_team_idx]
        & ~map_info._bm_et[map_info._IDX_CONVEYOR]
        & ~map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR]
        & ~map_info._bm_et[map_info._IDX_ROAD]
        & ~map_info._bm_et[map_info._IDX_BARRIER]
        & ~map_info._bm_et[map_info._IDX_MARKER]
        & ~map_info._bm_et[map_info._IDX_HARVESTER]
    )
    # Ore tiles surrounded on all 4 cardinal sides by ore — unreachable by conveyor
    landlocking = ore | ~map_info._bm_seen&map_info._board_mask
    landlocked = landlocking & (landlocking >> 1 & map_info._not_right_col) & (landlocking << 1 & map_info._not_left_col) & (landlocking >> w) & (landlocking << w)

    enemy_blocked = map_info.expand_manhattan(enemy_blocking)

    return (ore
            & ~landlocked
            & ~enemy_blocked
            & ~friendly_blocking
            & ~map_info._bm_enemy_turret_threat)
def secured():
    my_team_idx = map_info._my_team_idx
    securing = ( map_info._bm_team[my_team_idx]
        & ~map_info._bm_et[map_info._IDX_ROAD]
        & ~map_info._bm_et[map_info._IDX_MARKER]
    | map_info._bm_env[map_info._IDX_ENV_WALL])
    w = map_info._width
    bottom_row = ((1<<w)-1)<<w*(map_info._height-1)
    top_row = ((1<<w)-1)
    secured = (((securing&map_info._not_left_col) >> 1)|~map_info._not_right_col) & (((securing&map_info._not_right_col) << 1)|~map_info._not_left_col) & ((securing>>w)|bottom_row) & ((securing<<w)|top_row)
    return secured
def harvestable_ore():
    ore = possible_ore()
    # units.builder.draw_mask(ore, 255, 0, 0)
    # units.builder.draw_mask(secured(), 0, 255, 0)
    # units.builder.draw_mask(cant_harvest, 0, 0, 255)
    return (ore
            & ~map_info._bm_et[map_info._IDX_HARVESTER]
            & secured()
            & ~cant_harvest)

def _too_expensive():
    """Bitmask of tiles we know we can't afford right now."""
    ti = rc.get_global_resources()[0]
    current = rc.get_current_round()
    result = 0
    stale = []
    for n, (cost, turn) in _cost_map.items():
        if turn + COST_MAP_TTL < current:
            stale.append(n)
            continue
        if cost > ti:
            result |= 1 << n
    for n in stale:
        del _cost_map[n]
    return result

MAX_SCORE = 4
_cached_claims = 0
def score():
    global _cached_claims
    _cached_claims = _my_claims()
    return 4 if _cached_claims else 0

CARD = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]


def run():
    global cant_harvest
    log("HARVEST")

    available = _cached_claims
    if not available:
        return

    best_ore, _ = nav.closest(available)
    log("harvesting", best_ore)
    if best_ore is None:
        cant_harvest |= available
        return

    w = map_info._width
    my_team_idx = map_info._my_team_idx
    best_n = best_ore.x + best_ore.y * w
    is_raw_ax = bool(map_info._bm_env[map_info._IDX_ENV_ORE_AX] & (1 << best_n))
    path = None
    for dir in CARD:
        pos = best_ore.add(dir)
        if not map_info.in_bounds(pos):
            continue
        pn = pos.x + pos.y * w
        pbit = 1 << pn
        if not ((map_info._bm_et[map_info._IDX_CONVEYOR]|map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR])&pbit):
            continue
        d_idx = map_info._building_dir[pn]
        if d_idx < 0:
            continue
        conv_dir = map_info._INT_DIR[d_idx]
        if conv_dir != dir.opposite() and not (map_info._bm_conv_into_open_ore & pbit):
            path = nav.calculate_conveyor_path(pos.add(conv_dir), is_raw_ax, True)
    if not path:
        path = nav.calculate_conveyor_path(best_ore, is_raw_ax)
    if path is not None:
        _cost_map[best_n] = (rc.get_harvester_cost()[0] + nav.conveyor_cost(path[2], rc.get_scale_percent()/100+0.05), rc.get_current_round())
    else:
        cant_harvest |= 1 << (best_ore.x + best_ore.y * w)
        log("cant route")
        return
    if _cost_map[best_n][0] > rc.get_global_resources()[0]:
        log("too expensive")
        return
        
    ore_n = best_ore.x + best_ore.y * w
    ore_bit = 1 << ore_n
    ore_id = map_info._building_id[ore_n]

    if ore_id:
        is_mine = bool(map_info._bm_team[my_team_idx] & ore_bit)
        is_road = bool(map_info._bm_et[map_info._IDX_ROAD] & ore_bit)
        if not is_mine and is_road:
            nav.move_to(best_ore)
            if rc.can_fire(best_ore):
                rc.fire(best_ore)
                map_info.update_at(best_ore)
            comms.mark(best_ore.x + best_ore.y * map_info._width, comm_flag)
            log("firing")
            return
        if is_mine and rc.can_destroy(best_ore) and rc.get_action_cooldown() == 0 and (map_info._my_pos != best_ore or rc.get_move_cooldown() == 0):
            rc.destroy(best_ore)
            map_info.update_at(best_ore)
    targets = set()
    log(path[0])
    for d in Direction:
        p = map_info.pos_add(path[0], d)
        if p == best_ore or not map_info.in_bounds(p):
            continue
        if p.distance_squared(best_ore) > 2:
            continue
        if map_info.is_passable(p):
            targets.add(p)
    if targets:
        nav.move_to(targets)
    log("targets", targets, path[0])
    # Move to any adjacent tile and build harvester
    if rc.can_build_harvester(best_ore):
        rc.build_harvester(best_ore)
        map_info.update_at(best_ore)
    comms.mark(best_ore.x + best_ore.y * map_info._width, comm_flag)
