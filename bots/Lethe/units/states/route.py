import map_info
import pathing
from pathing import Pathing
import comms
from cambc import *
import units.builder
from log import log

rc: Controller = None
nav: Pathing = None
comm_flag = 4
_cost_map: dict[int, int] = {}  # tile index -> min titanium cost to route

unpathable = 0

def _trace_resource(start_n: int) -> str:
    """Follow _conv_reverse back from start_n until we see a loaded conveyor.
    Returns 'raw', 'ti', or 'refined'. Defaults to 'ti' if nothing found."""
    seen = 0
    cur = start_n
    raw = map_info._bm_conv_raw_ax
    ti = map_info._bm_conv_ti
    refined = map_info._bm_conv_refined
    reverse = map_info._conv_reverse
    while cur is not None:
        bit = 1 << cur
        if seen & bit:
            break
        seen |= bit
        if raw & bit:
            return 'raw'
        if refined & bit:
            return 'refined'
        if ti & bit:
            return 'ti'
        feeders = reverse[cur] & ~seen
        if not feeders:
            break
        lsb = feeders & -feeders
        cur = lsb.bit_length() - 1
    return 'ti'

def init(c: Controller):
    global rc, nav
    rc = c
    nav = Pathing(rc)

def _too_expensive():
    """Bitmask of tiles we know we can't afford right now."""
    ti = rc.get_global_resources()[0]
    result = 0
    for n, cost in _cost_map.items():
        if cost > ti:
            result |= 1 << n
    return result

def _dead_end_conveyors():
    """Bitmask of routable conveyors whose output is not connected to my ore-accepting network."""
    return map_info._bm_dead_end & ~map_info._bm_enemy_turret_threat

def _orphan_harvesters():
    """Bitmask of my harvesters with no adjacent conveyor/turret/core."""
    my_team_idx = map_info._my_team_idx
    my_harvesters = map_info._bm_et[map_info._IDX_HARVESTER] & map_info._bm_team[my_team_idx]
    if not my_harvesters:
        return 0

    my_connected = (
        map_info._bm_et[map_info._IDX_CONVEYOR]
        | map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR]
        | map_info._bm_et[map_info._IDX_BRIDGE]
        | map_info._bm_et[map_info._IDX_SPLITTER]
        | map_info._bm_et[map_info._IDX_CORE]
    ) & map_info._bm_team[my_team_idx]

    served = map_info.expand_manhattan(my_connected)
    return my_harvesters & ~served & ~map_info._bm_enemy_turret_threat
def _orphan_foundries():
    """Bitmask of my foundries with no adjacent conveyor/turret/core."""
    my_team_idx = map_info._my_team_idx
    my_foundries = map_info._bm_et[map_info._IDX_FOUNDRY]
    if not my_foundries:
        return 0

    pointing_into = 0
    m = my_foundries
    while m:
        lsb = m & -m
        fn = lsb.bit_length() - 1
        pointing_into |= map_info._conv_reverse[fn]
        m ^= lsb

    directional = (
        map_info._bm_et[map_info._IDX_CONVEYOR]
        | map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR]
        | map_info._bm_et[map_info._IDX_SPLITTER]
    ) & map_info._bm_team[my_team_idx]
    my_connected = (directional & ~pointing_into) | (
        (map_info._bm_et[map_info._IDX_BRIDGE] | map_info._bm_et[map_info._IDX_CORE])
        & map_info._bm_team[my_team_idx]
    )

    served = map_info.expand_manhattan(my_connected)
    return my_foundries & ~served & ~map_info._bm_enemy_turret_threat
def cant_claim():
    w = map_info._width
    my_pos = rc.get_position()

    # My 5x5 (2 Chebyshev) zone — always claimable
    my_bit = 1 << (my_pos.x + my_pos.y * w)
    my_zone = my_bit
    for _ in range(2):
        my_zone = map_info.expand_chebyshev(my_zone)

    # Other friendly bots' zones — can't claim there
    my_small = map_info.expand_chebyshev(my_bit)
    cant = 0
    others_small = 0
    friendly_others = map_info._bm_friendly_bots & ~my_bit
    if friendly_others:
        mask = friendly_others
        while mask:
            bit = mask & -mask
            # 5x5 (2 Chebyshev)
            zone = bit
            for _ in range(2):
                zone = map_info.expand_chebyshev(zone)
            cant |= zone
            # 3x3 (1 Chebyshev)
            others_small |= map_info.expand_chebyshev(bit)
            mask ^= bit

    # 5x5 rule: blocked unless in my 5x5
    cant_5x5 = cant & ~my_zone
    # 3x3 rule: in someone else's 3x3 but not my 3x3 — blocked regardless
    cant_3x3 = others_small & ~my_small

    return cant_5x5 | cant_3x3
def avoid_mask():
    return _too_expensive() | cant_claim() | unpathable

def _my_claims():
    w = map_info._width
    my_mask = 1 << (rc.get_position().x + rc.get_position().y * w)
    avoid = avoid_mask()
    candidates = (_dead_end_conveyors() | _orphan_harvesters() | _orphan_foundries()) & ~avoid
    return pathing.voronoi_claim(my_mask, units.builder.claimed_senders[comm_flag], candidates)

def score():
    return 4 if _my_claims() else 0

def run():

    global unpathable
    log("ROUTE")
    candidates = _my_claims()

    if not candidates:
        log("no candidates")
        return
    width = map_info._width
    height = map_info._height

    avoid = avoid_mask()
    orphans = _orphan_harvesters() & ~avoid
    foundries = _orphan_foundries() & ~avoid

    best, _ = nav.closest(candidates)
    if best is None:
        log("no closest???")
        unpathable |= candidates
        return
    
    best_bit = 1 << (best.x + best.y * width)
    is_harvester = bool(orphans & best_bit)

    target_conveyor = [None]*2
    path = []
    best_n = best.x + best.y * width
    is_foundry = bool(foundries & best_bit)
    resource = _trace_resource(best_n)
    is_raw_ax = (resource == 'raw')
    is_refined = (resource == 'refined')
    if is_foundry:
        is_raw_ax = False
        is_refined = True
    if is_harvester or is_foundry:
        best_bit_env = 1 << best_n
        if map_info._bm_env[map_info._IDX_ENV_ORE_AX] & best_bit_env:
            is_raw_ax = True
        elif map_info._bm_env[map_info._IDX_ENV_ORE_TI] & best_bit_env:
            is_raw_ax = False
        path = nav.calculate_conveyor_path(best, is_raw_ax, update=False)
        if path is None:
            unpathable |= best_bit
            return
        target_conveyor = [path[0], path[1]]
        # Route from harvester: expand start to cardinal neighbors
    else:
        # Dead-end conveyor: route from its output tile
        target_n = map_info._building_conv_target[best_n]

        can_heal_road = False
        target_zone = 1 << target_n
        for _ in range(3):
            target_zone = map_info.expand_chebyshev(target_zone)
        if target_zone & map_info._bm_enemy_bots:
            can_heal_road = True
        path = nav.calculate_conveyor_path(best, is_raw_ax, update=True)
        if path is None:
            unpathable |= best_bit
            return
        target_conveyor = [path[0], path[1]]
        if (map_info._bm_team[1-map_info._my_team_idx] & (1 << target_n)) and not map_info.type_at(target_n%width, target_n//width) == EntityType.MARKER and not (map_info.type_at(target_n%width, target_n//width) == EntityType.ROAD and not can_heal_road):
            new_path = nav.calculate_conveyor_path(best, is_raw_ax, update=True)
            if new_path is not None and new_path[1] != path[0]:
                path = new_path
                target_conveyor = [path[0], path[1]]
    near_enemy = False
    if target_conveyor[0].distance_squared(target_conveyor[1]) == 1:
        tc1_zone = 1 << (target_conveyor[1].x + target_conveyor[1].y * width)
        for _ in range(4):
            tc1_zone = map_info.expand_chebyshev(tc1_zone)
        if tc1_zone & map_info._bm_enemy_bots:
            near_enemy = True
    if map_info.type_at(target_conveyor[0].x, target_conveyor[0].y) == EntityType.ROAD and map_info.team_at(target_conveyor[0].x, target_conveyor[0].y) != map_info._my_team:
        target = target_conveyor[0]
        nav.move_to(target)
        if rc.can_fire(target):
            rc.fire(target)
        comms.mark(best.x + best.y * map_info._width, comm_flag)
        return
    foundry_sites = nav.raw_ax_foundry_sites() if is_raw_ax else 0
    # units.builder.draw_mask(foundry_sites, 255, 0, 0)
    tc0_bit = 1 << (target_conveyor[0].x + target_conveyor[0].y * width)
    if is_raw_ax and (foundry_sites & tc0_bit):
        foundry_cost = rc.get_foundry_cost()[0]
        _cost_map[best_n] = foundry_cost + nav.conveyor_cost(path[2], rc.get_scale_percent()/100+0.5)
        if rc.get_global_resources()[0] < foundry_cost + nav.conveyor_cost(path[2]):
            comms.mark(best.x + best.y * map_info._width, comm_flag)
            return
        nav.move_adjacent(target_conveyor[0])
        if rc.get_action_cooldown() == 0:
            if rc.can_destroy(target_conveyor[0]):
                rc.destroy(target_conveyor[0])
                map_info.update_at(target_conveyor[0])
            if rc.can_build_foundry(target_conveyor[0]):
                rc.build_foundry(target_conveyor[0])
                map_info.update_at(target_conveyor[0])
        comms.mark(best.x + best.y * map_info._width, comm_flag)
        return
    can_build = False
    cost = nav.conveyor_cost(path[2])
    best_n = best.x + best.y * width
    if not is_refined:
        _cost_map[best_n] = cost
        if rc.get_global_resources()[0] < cost:
            log("can't afford", cost)
            comms.mark(best.x + best.y * map_info._width, comm_flag)
            return
    if near_enemy:
        nav.move_to(target_conveyor[1])
        if rc.get_position() == target_conveyor[1]:
            can_build = True
    else:
        nav.move_adjacent(target_conveyor[0])
        can_build = True
    if rc.get_action_cooldown() != 0:
        can_build = False
    built = False
    if can_build:
        destroy = target_conveyor[0]
        next = target_conveyor[1]
        if rc.can_destroy(destroy):
            rc.destroy(destroy)
            map_info.update_at(destroy)
        bridge = destroy.distance_squared(next) > 1
        if bridge and rc.can_build_bridge(destroy, next):
            rc.build_bridge(destroy, next)
            map_info.update_at(destroy)
            built = True
        elif not bridge and rc.can_build_conveyor(destroy, destroy.direction_to(next)):
            rc.build_conveyor(destroy, destroy.direction_to(next))
            map_info.update_at(destroy)
            built = True
    if built:
        # Trace downstream from best, mark the furthest unloaded conveyor as loaded
        conv_target = map_info._building_conv_target
        tiles = width * height
        cur_n = best.x + best.y * width
        last_unloaded_bit = 0
        visited = 0
        while True:
            log("at", cur_n%width, cur_n//width)
            log("next", conv_target[cur_n]%width, conv_target[cur_n]//width)
            cur_bit = 1 << cur_n
            if visited & cur_bit:
                log("cycle detected")
                break
            visited |= cur_bit
            if (map_info._bm_routable & cur_bit) and not (map_info._bm_conv_loaded & cur_bit):
                last_unloaded_bit = cur_bit
            tn = conv_target[cur_n]
            if tn < 0 or tn >= tiles:
                log("invalid target", tn)
                break
            tbit = 1 << tn
            if not (map_info._bm_conveyors & tbit):
                break
            cur_n = tn
        if last_unloaded_bit:
            map_info._bm_conv_loaded |= last_unloaded_bit
            log("set loaded", (last_unloaded_bit.bit_length() - 1) % width, (last_unloaded_bit.bit_length() - 1) // width)

    comms.mark(best.x + best.y * map_info._width, comm_flag)
