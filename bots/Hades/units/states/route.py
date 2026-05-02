import map_info
import pathing
from pathing import Pathing
from cambc import *
import units.builder
from log import log
import sys
rc: Controller = None
nav: Pathing = None
_cost_map: dict[int, tuple[int, int]] = {}  # tile index -> (min titanium cost, round recorded)
COST_MAP_TTL = 100
ATTACK_ROUTE_DISTANCE_NUMERATOR = 3
ATTACK_ROUTE_DISTANCE_DENOMINATOR = 2

unpathable = 0


def _prefer_armoured_conveyor() -> bool:
    ti, ax = rc.get_global_resources()
    armoured_ti, armoured_ax = rc.get_armoured_conveyor_cost()
    return ti >= armoured_ti * 5 and ax >= armoured_ax


def _can_build_preferred_conveyor(pos: Position, direction: Direction) -> bool:
    ti = rc.get_global_resources()[0]
    reserve = map_info.builder_ti_reserve()
    if _prefer_armoured_conveyor():
        return (
            rc.can_build_armoured_conveyor(pos, direction)
            and ti >= rc.get_armoured_conveyor_cost()[0] + reserve
        )
    return (
        rc.can_build_conveyor(pos, direction)
        and ti >= rc.get_conveyor_cost()[0] + reserve
    )


def _build_preferred_conveyor(pos: Position, direction: Direction) -> EntityType:
    if _prefer_armoured_conveyor():
        rc.build_armoured_conveyor(pos, direction)
        return EntityType.ARMOURED_CONVEYOR
    rc.build_conveyor(pos, direction)
    return EntityType.CONVEYOR

def init(c: Controller):
    global rc, nav
    rc = c
    nav = units.builder.nav

def _core_distance(a: Position, b: Position) -> int:
    return abs(a.x - b.x) + abs(a.y - b.y)

def _core_bfs_distance(pos: Position, core: Position) -> int:
    target = 1 << (core.x + core.y * map_info._width)
    _closest, dist = nav.closest(target, pos=pos)
    return dist if dist >= 0 else _core_distance(pos, core)

def _should_attack_route(pos: Position) -> bool:
    my_core = map_info._my_core
    enemy_core = map_info._their_core or map_info._predicted_enemy_core
    if my_core is None or enemy_core is None:
        return False
    enemy_dist = _core_bfs_distance(pos, enemy_core)
    my_dist = _core_bfs_distance(pos, my_core)
    return (
        enemy_dist * ATTACK_ROUTE_DISTANCE_DENOMINATOR
        <= my_dist * ATTACK_ROUTE_DISTANCE_NUMERATOR
    )

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
        log("cost of", n%map_info._width, n//map_info._width, cost)
        if cost > ti:
            result |= 1 << n
    for n in stale:
        del _cost_map[n]
    return result

def _dead_end_conveyors():
    """Bitmask of routable conveyors whose output is not connected to my ore-accepting network."""
    return map_info._bm_dead_end & ~map_info._bm_enemy_turret_threat
def not_blocked():
    '''
    it is not blocked if
    it does not have a conveyor taking it
    and it does have a place to put a conveyor
    '''
    my_team_idx = map_info._my_team_idx
    my_connected = (
        map_info._bm_et[map_info._IDX_BRIDGE]
        | map_info._bm_et[map_info._IDX_SPLITTER]
        | map_info._bm_et[map_info._IDX_CORE]
    ) & map_info._bm_team[my_team_idx]
    w = map_info._width
    conveyors = (map_info._bm_et[map_info._IDX_CONVEYOR]|map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR])&map_info._bm_team[my_team_idx] & ~map_info._bm_guard_conveyor
    left_conveyors = ((conveyors&~map_info._bm_conv_by_dir[map_info._DIR_E])&map_info._not_right_col)<<1
    right_conveyors = ((conveyors&~map_info._bm_conv_by_dir[map_info._DIR_W])&map_info._not_left_col)>>1
    up_conveyors = ((conveyors&~map_info._bm_conv_by_dir[map_info._DIR_S]))<<w
    down_conveyors = ((conveyors&~map_info._bm_conv_by_dir[map_info._DIR_N]))>>w
    blocking = (
        (map_info._bm_team[1-my_team_idx]
        & ~map_info._bm_et[map_info._IDX_MARKER]
        & ~map_info._bm_et[map_info._IDX_ROAD])
        | ~map_info._bm_env[map_info._IDX_ENV_EMPTY]
        | (map_info._bm_team[my_team_idx]
        & ~map_info._bm_et[map_info._IDX_MARKER]
        & ~map_info._bm_et[map_info._IDX_ROAD]
        & ~map_info._bm_et[map_info._IDX_CONVEYOR]
        & ~map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR])
    )
    already_routed = map_info.expand_manhattan(my_connected) | left_conveyors | right_conveyors | up_conveyors | down_conveyors
    bottom_row = ((1 << w) - 1) << (w * (map_info._height - 1))
    top_row = (1 << w) - 1
    blocked = (
        (((blocking & map_info._not_left_col) >> 1) | ~map_info._not_right_col)
        & (((blocking & map_info._not_right_col) << 1) | ~map_info._not_left_col)
        & ((blocking >> w) | bottom_row)
        & ((blocking << w) | top_row)
    )
    return map_info._board_mask & ~already_routed & ~blocked & ~map_info._bm_enemy_turret_threat

def _orphan_harvesters(not_blocked_mask: int):
    my_harvesters = map_info._bm_et[map_info._IDX_HARVESTER]
    if not my_harvesters:
        return 0
    return my_harvesters & not_blocked_mask
def _orphan_foundries(not_blocked_mask: int):
    my_foundries = map_info._bm_et[map_info._IDX_FOUNDRY]
    if not my_foundries:
        return 0
    return my_foundries & not_blocked_mask
def cant_claim():
    w = map_info._width
    my_pos = map_info._my_pos
    my_bit = 1 << (my_pos.x + my_pos.y * w)
    cant = map_info._bm_others_3x3 & ~map_info.expand_chebyshev(my_bit)
    return cant
def _my_claims():
    w = map_info._width
    my_mask = 1 << (map_info._my_pos.x + map_info._my_pos.y * w)
    avoid = _too_expensive() | cant_claim() | unpathable
    avoid &= ~(map_info._bm_feeding_enemy&~unpathable)
    not_blocked_mask = not_blocked()
    candidates = (
        _dead_end_conveyors()
        | _orphan_harvesters(not_blocked_mask)
        | _orphan_foundries(not_blocked_mask)
    ) & ~avoid
    if units.builder._stay_near_core:
        candidates &= units.builder.near_core_mask()
    return pathing.claim_subset(my_mask, map_info._bm_friendly_bots, candidates, tie_self=True)

_cached_claims = 0

MAX_SCORE = 7.75
def score():
    global _cached_claims
    units.builder.draw_mask(map_info._bm_dead_end, 0, 0, 255)
    _cached_claims = _my_claims()

    important = map_info.expand_chebyshev(map_info._bm_enemy_bots, 5)&~(map_info._bm_team[map_info._my_team_idx]&(map_info._bm_et[map_info._IDX_HARVESTER]|map_info._bm_et[map_info._IDX_FOUNDRY]))|map_info._bm_feeding_enemy
    if important&_cached_claims:
        log("IMPORTANT")
        _cached_claims &= important
        return 7.75
    return 5 if _cached_claims else 0

def run():

    global unpathable
    log("ROUTE")
    candidates = _cached_claims
    high_priority = False
    important = map_info.expand_chebyshev(map_info._bm_enemy_bots, 5)&~(map_info._bm_team[map_info._my_team_idx]&(map_info._bm_et[map_info._IDX_HARVESTER]|map_info._bm_et[map_info._IDX_FOUNDRY]))|map_info._bm_feeding_enemy

    if important & candidates:
        high_priority = True
        candidates &= important
    if not candidates:
        log("no candidates?")
        return
    width = map_info._width

    _BARRIER_DESTROYABLE = (
        EntityType.ROAD,
        EntityType.MARKER,
        EntityType.CONVEYOR,
        EntityType.ARMOURED_CONVEYOR,
    )

    def _try_barrier_output(target_n):
        output_n = map_info._building_conv_target[target_n]
        if output_n < 0:
            return False
        output_bit = 1 << output_n
        if output_bit & (map_info._bm_friendly_bots | map_info._bm_enemy_bots):
            return False
        output = Position(output_n % width, output_n // width)
        output_type = map_info.type_at(output.x, output.y)
        is_my_road = (
            output_type == EntityType.ROAD
            and map_info.team_at(output.x, output.y) == map_info._my_team
        )
        if output_type is not None and not is_my_road:
            return False

        my_pos = map_info._my_pos
        if max(abs(my_pos.x - output.x), abs(my_pos.y - output.y)) > 1:
            if not nav.move_to_adjacent(output):
                return False

        if is_my_road and rc.can_destroy(output):
            rc.destroy(output)
            map_info.update_at(output)
        if rc.can_build_barrier(output) and rc.get_global_resources()[0] >= rc.get_barrier_cost()[0] + map_info.builder_ti_reserve():
            rc.build_barrier(output)
            map_info.update_at(output)
        return True

    def fallback_barrier(target):
        log("barrier fallback at", target)
        target_n = target.x + target.y * width
        target_feeds_enemy = bool((1 << target_n) & map_info._bm_feeding_enemy)
        barrier_ready = (
            rc.get_action_cooldown() == 0
            and rc.get_global_resources()[0] >= rc.get_barrier_cost()[0] + map_info.builder_ti_reserve()
        )

        if barrier_ready and _try_barrier_output(target_n):
            return

        nav.move_adjacent(target)
        existing = map_info.type_at(target.x, target.y)
        # Only destroy the routed tile for free when it immediately cuts an
        # enemy feed, or when we can still turn it into a barrier this turn.
        if (
            existing in _BARRIER_DESTROYABLE
            and rc.can_destroy(target)
            and (target_feeds_enemy or barrier_ready)
        ):
            rc.destroy(target)
            map_info.update_at(target)
        if rc.can_build_barrier(target) and rc.get_global_resources()[0] >= rc.get_barrier_cost()[0] + map_info.builder_ti_reserve():
            rc.build_barrier(target)
            map_info.update_at(target)

    best = None
    path = None
    target_conveyor = [None]*2
    is_raw_ax = False
    is_refined = False
    is_harvester = False
    is_foundry = False
    is_ti_harvester = False

    while candidates:
        candidate, _ = nav.closest(candidates)
        if candidate is None:
            log("no closest???")
            unpathable |= candidates
            return
        cand_n = candidate.x + candidate.y * width
        cand_bit = 1 << cand_n
        cand_is_harvester = bool(map_info._bm_et[map_info._IDX_HARVESTER] & cand_bit)
        cand_is_foundry = bool(map_info._bm_et[map_info._IDX_FOUNDRY] & cand_bit)
        cand_raw_ax = False
        cand_refined = False
        cand_is_ti_harvester = False
        if cand_is_foundry:
            cand_raw_ax = False
            cand_refined = True
        if cand_is_harvester:
            if map_info._bm_env[map_info._IDX_ENV_ORE_AX] & cand_bit:
                cand_raw_ax = True
                cand_refined = False
            elif map_info._bm_env[map_info._IDX_ENV_ORE_TI] & cand_bit:
                cand_raw_ax = False
                cand_refined = False
                cand_is_ti_harvester = True
        if cand_is_harvester or cand_is_foundry:
            cand_path = nav.calculate_conveyor_path(candidate, cand_raw_ax, update=False)
        else:
            prev_bit = map_info._conv_reverse[cand_n]&-map_info._conv_reverse[cand_n]
            cand_raw_ax = bool(map_info._bm_raw_ax_carrying & prev_bit) or bool(map_info._bm_raw_ax_carrying & cand_bit)
            cand_refined = bool(map_info._bm_refined_carrying & prev_bit) or bool(map_info._bm_refined_carrying & cand_bit)
            cand_path = nav.calculate_conveyor_path(candidate, cand_raw_ax, update=True)
            log("PATH", cand_path, bool(cand_raw_ax))
        if cand_path is None:
            if high_priority:
                fallback_barrier(candidate)
                return
            unpathable |= cand_bit
            candidates &= ~cand_bit
            continue
        cost = nav.conveyor_cost(cand_path[2])
        if not cand_refined:
            _cost_map[cand_n] = (cost, rc.get_current_round())
            if rc.get_global_resources()[0] < cost:
                log("can't afford", cost)
                if high_priority:
                    fallback_barrier(candidate)
                    return
                candidates &= ~cand_bit
                continue
        best = candidate
        path = cand_path
        target_conveyor = [path[0], path[1]]
        is_raw_ax = cand_raw_ax
        is_refined = cand_refined
        is_harvester = cand_is_harvester
        is_foundry = cand_is_foundry
        is_ti_harvester = cand_is_ti_harvester
        break

    if best is None:
        return

    # Final-pick attack-route override: if the chosen candidate is closer
    # to the enemy core than to ours, try routing toward the enemy core
    # instead. Only applies when the route doesn't carry raw axionite.
    if is_harvester or is_foundry:
        attack_eligible = is_foundry or is_ti_harvester
    else:
        attack_eligible = not is_raw_ax
    if attack_eligible and _should_attack_route(best):
        attack_path = nav.calculate_attack_conveyor_path(
            best, update=not (is_harvester or is_foundry)
        )
        if attack_path is not None:
            attack_cost = nav.conveyor_cost(attack_path[2])
            if is_refined or rc.get_global_resources()[0] >= attack_cost:
                path = attack_path
                target_conveyor = [path[0], path[1]]

    best_n = best.x + best.y * width
    best_bit = 1 << best_n
    foundry_sites = nav.raw_ax_foundry_sites() if is_raw_ax else 0
    tc0_bit = 1 << (target_conveyor[0].x + target_conveyor[0].y * width)
    if is_raw_ax and (foundry_sites & tc0_bit) and target_conveyor[0] == target_conveyor[1]:
        nav.move_adjacent(target_conveyor[0])
        if rc.get_action_cooldown() == 0 and rc.get_global_resources()[0] >= rc.get_foundry_cost()[0] + map_info.builder_ti_reserve():
            if rc.can_destroy(target_conveyor[0]):
                rc.destroy(target_conveyor[0])
                map_info.update_at(target_conveyor[0])
            if rc.can_build_foundry(target_conveyor[0]) and rc.get_global_resources()[0] >= rc.get_foundry_cost()[0] + map_info.builder_ti_reserve():
                rc.build_foundry(target_conveyor[0])
                map_info.update_at(target_conveyor[0])
        return
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
            map_info.update_at(target)
        return
    if near_enemy and not (map_info.team_at(target_conveyor[1].x, target_conveyor[1].y) == rc.get_team() and map_info.type_at(target_conveyor[1].x, target_conveyor[1].y) != EntityType.MARKER) and (map_info.team_at(target_conveyor[0].x, target_conveyor[0].y) == rc.get_team() and map_info.type_at(target_conveyor[0].x, target_conveyor[0].y) != EntityType.MARKER):
        nav.move_to(target_conveyor[1])
        if map_info._my_pos == target_conveyor[1]:
            if map_info.team_at(target_conveyor[1].x, target_conveyor[1].y) != map_info._my_team and rc.can_fire(target_conveyor[1]):
                rc.fire(target_conveyor[1])
                map_info.update_at(target_conveyor[0])
        if rc.can_build_road(target_conveyor[0]) and rc.get_global_resources()[0] >= rc.get_road_cost()[0] + map_info.builder_ti_reserve():
            rc.build_road(target_conveyor[0])
            map_info.update_at(target_conveyor[0])
        return
    def attempt_build():
        destroy = target_conveyor[0]
        next = target_conveyor[1]
        bridge = destroy.distance_squared(next) > 1
        cost = rc.get_bridge_cost()[0] if bridge else rc.get_conveyor_cost()[0]
        cost += map_info.builder_ti_reserve()
        if rc.can_destroy(destroy) and rc.get_action_cooldown() == 0 and rc.get_global_resources()[0] >= cost:
            rc.destroy(destroy)
            map_info.update_at(destroy)
        if bridge and rc.can_build_bridge(destroy, next) and rc.get_global_resources()[0] >= rc.get_bridge_cost()[0] + map_info.builder_ti_reserve():
            rc.build_bridge(destroy, next)
            map_info.update_at(destroy)
        elif not bridge:
            direction = map_info.direction_to(destroy, next)
            if _can_build_preferred_conveyor(destroy, direction):
                _build_preferred_conveyor(destroy, direction)
                map_info.update_at(destroy)
    attempt_build()
    nav.move_to(target_conveyor[0])
    attempt_build()
