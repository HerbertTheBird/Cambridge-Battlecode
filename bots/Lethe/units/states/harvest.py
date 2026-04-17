from cambc import *

import map_info
import pathing
from pathing import Pathing
import comms
import units.builder
from log import log

rc: Controller = None
nav: Pathing = None

comm_flag = 3

def _my_claims():
    my_pos = map_info._my_pos
    w = map_info._width
    my_mask = 1 << (my_pos.x + my_pos.y * w)
    available = harvestable_ore() & ~_too_expensive()
    return available & ~pathing.voronoi_claim(units.builder.claimed_senders[comm_flag], my_mask, available)

def init(c: Controller):
    global rc, nav
    rc = c
    nav = Pathing(rc)

cant_harvest = 0
_cost_map: dict[int, int] = {}  # tile index -> min titanium cost to harvest
def possible_ore():
    ore = map_info._bm_env[map_info._IDX_ENV_ORE_TI]
    if (map_info._bm_team[map_info._my_team_idx] & map_info._bm_et[map_info._IDX_HARVESTER] & map_info._bm_env[map_info._IDX_ENV_ORE_TI]) and rc.get_current_round() >= 1000:
        ore |= map_info._bm_env[map_info._IDX_ENV_ORE_AX]
    return ore
def harvestable_ore():
    """Bitmask of titanium ore tiles without a harvester and not forgotten."""
    my_team_idx = map_info._my_team_idx
    enemy_idx = 1 - my_team_idx

    # Enemy buildings that block harvesting (not road/conveyor/bridge/splitter/marker)
    enemy_blocking = (
        map_info._bm_team[enemy_idx]
        & ~map_info._bm_et[map_info._IDX_ROAD]
        & ~map_info._bm_et[map_info._IDX_CONVEYOR]
        & ~map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR]
        & ~map_info._bm_et[map_info._IDX_BRIDGE]
        & ~map_info._bm_et[map_info._IDX_SPLITTER]
        & ~map_info._bm_et[map_info._IDX_MARKER]
    )

    # Friendly buildings that block harvesting (not road/barrier/marker)
    friendly_blocking = (
        map_info._bm_team[my_team_idx]
        & ~map_info._bm_et[map_info._IDX_ROAD]
        & ~map_info._bm_et[map_info._IDX_BARRIER]
        & ~map_info._bm_et[map_info._IDX_MARKER]
    )
    w = map_info._width
    ore = possible_ore()
    # Ore tiles surrounded on all 4 cardinal sides by ore — unreachable by conveyor
    landlocked = ore & (ore >> 1 & map_info._not_right_col) & (ore << 1 & map_info._not_left_col) & (ore >> w) & (ore << w)

    # Enemy hard buildings (not road/marker) cardinally adjacent — can't harvest next to these
    enemy_hard = (
        map_info._bm_team[enemy_idx]
        & ~map_info._bm_et[map_info._IDX_ROAD]
        & ~map_info._bm_et[map_info._IDX_MARKER]
    )
    enemy_hard_adj = map_info.expand_manhattan(enemy_hard)

    # Axionite ore adjacent to conveyors not carrying raw axionite — can't use those
    non_raw_conveyors = map_info._bm_conveyors & ~map_info._bm_conv_raw_ax & map_info._bm_team[my_team_idx]
    ax_ore_near_non_raw = map_info._bm_env[map_info._IDX_ENV_ORE_AX] & map_info.expand_manhattan(non_raw_conveyors) if non_raw_conveyors else 0

    return (ore
            & ~landlocked
            & ~map_info._bm_et[map_info._IDX_HARVESTER]
            & ~enemy_blocking
            & ~friendly_blocking
            & ~enemy_hard_adj
            & ~map_info._bm_enemy_turret_threat
            & units.builder._harvest_zone
            & ~cant_harvest
            & ~ax_ore_near_non_raw)

def _too_expensive():
    """Bitmask of tiles we know we can't afford right now."""
    ti = rc.get_global_resources()[0]
    result = 0
    for n, cost in _cost_map.items():
        # log(n%map_info._width, n//map_info._width, cost)
        if cost > ti:
            result |= 1 << n
    return result

def score():
    return 3 if _my_claims() else 0


def run():
    global cant_harvest
    log("HARVEST")
    # Quick check: can we build a harvester on a diagonal ore that's already secured?
    w = map_info._width
    my_team_idx = map_info._my_team_idx
    ore_mask = possible_ore()
    wall_mask = map_info._bm_env[map_info._IDX_ENV_WALL]
    road_mask = map_info._bm_et[map_info._IDX_ROAD]
    marker_mask = map_info._bm_et[map_info._IDX_MARKER]
    harvester_mask = map_info._bm_et[map_info._IDX_HARVESTER]
    has_building = map_info._bm_any_building

    my_pos = map_info._my_pos
    harvestable = harvestable_ore() & ~_too_expensive()
    for d in (Direction.NORTHEAST, Direction.SOUTHEAST, Direction.SOUTHWEST, Direction.NORTHWEST):
        dx, dy = map_info._DIRECTION_DELTAS[d]
        p = Position(my_pos.x + dx, my_pos.y + dy)
        if not map_info.in_bounds(p):
            continue
        pn = p.x + p.y * w
        pbit = 1 << pn
        if not (harvestable & pbit):
            continue
        # Check all 4 cardinal sides are secured
        secured = True
        for cd in map_info._CARDINAL:
            cp = map_info.pos_add(p, cd)
            if not map_info.in_bounds(cp):
                continue
            cn = cp.x + cp.y * w
            cbit = 1 << cn
            if wall_mask & cbit:
                continue
            if (has_building & cbit) and not (road_mask & cbit) and not (marker_mask & cbit):
                continue
            secured = False
            break
        if not secured:
            continue
        is_raw_ax = bool(map_info._bm_env[map_info._IDX_ENV_ORE_AX] & pbit)
        path = nav.calculate_conveyor_path(p, is_raw_ax)
        if path is None:
            cant_harvest |= pbit
            continue
        cost = rc.get_harvester_cost()[0] + nav.conveyor_cost(path[2], rc.get_scale_percent()/100+0.05)
        print("diagonal ore at", p, "cost", cost)
        _cost_map[pn] = cost
        if cost > rc.get_global_resources()[0]:
            continue
        if rc.get_action_cooldown() == 0 and rc.can_destroy(p) and (map_info.type_at(p.x, p.y) == EntityType.ROAD or map_info.type_at(p.x, p.y) == EntityType.BARRIER) and not map_info.has_builder_bot(p):
            rc.destroy(p)
            map_info.update_at(p)
        if rc.can_build_harvester(p):
            rc.build_harvester(p)
            map_info.update_at(p)
        comms.mark(pn, comm_flag)
        return

    available = _my_claims()
    if not available:
        return

    best_ore, _ = nav.closest(available)
    if best_ore is None:
        cant_harvest |= available
        return

    w = map_info._width
    my_team_idx = map_info._my_team_idx
    best_n = best_ore.x + best_ore.y * w
    is_raw_ax = bool(map_info._bm_env[map_info._IDX_ENV_ORE_AX] & (1 << best_n))
    path = nav.calculate_conveyor_path(best_ore, is_raw_ax)
    if path is not None:
        _cost_map[best_n] = rc.get_harvester_cost()[0] + nav.conveyor_cost(path[2], rc.get_scale_percent()/100+0.05)
    else:
        cant_harvest |= 1 << (best_ore.x + best_ore.y * w)
        return
    if _cost_map[best_n] > rc.get_global_resources()[0]:
        return
    if map_info._my_pos.distance_squared(best_ore) > 2:
        nav.move_to(best_ore)
        comms.mark(best_ore.x + best_ore.y * map_info._width, comm_flag)
        return
    # --- Secure each cardinal side ---
    all_secured = True
    for d in map_info._CARDINAL:
        p = map_info.pos_add(best_ore, d)
        if not map_info.in_bounds(p):
            continue
        if p == map_info._my_pos and p in pathing.destroyed_barriers:
            continue

        pn = p.x + p.y * w
        pbit = 1 << pn

        # Wall — done
        if map_info._bm_env[map_info._IDX_ENV_WALL] & pbit:
            continue

        pid = map_info._building_id[pn]
        is_mine = bool(map_info._bm_team[my_team_idx] & pbit) if pid else False
        is_road = bool(map_info._bm_et[map_info._IDX_ROAD] & pbit)
        is_marker = bool(map_info._bm_et[map_info._IDX_MARKER] & pbit)

        if pid and not is_mine and is_road:
            # Enemy road — move onto it and fire repeatedly
            all_secured = False
            nav.move_to(p)
            if rc.can_fire(p):
                rc.fire(p)
            comms.mark(best_ore.x + best_ore.y * map_info._width, comm_flag)
            return

        if pid and not is_road and not is_marker:
            # Has a real building (mine or enemy, not road/marker) — side is done
            continue

        # Empty, marker, enemy marker, or my road — needs barrier
        all_secured = False
        nav.move_to(best_ore)
        if pid and is_mine and not map_info.has_builder_bot(p) and rc.can_destroy(p) and rc.get_action_cooldown() == 0:
            rc.destroy(p)
            map_info.update_at(p)
        if rc.can_build_barrier(p):
            rc.build_barrier(p)
            map_info.update_at(p)
        comms.mark(best_ore.x + best_ore.y * map_info._width, comm_flag)
        return

    if not all_secured:
        comms.mark(best_ore.x + best_ore.y * map_info._width, comm_flag)
        return

    # --- All 4 sides covered — place harvester ---
    # Clear ore tile if needed
    ore_n = best_ore.x + best_ore.y * w
    ore_bit = 1 << ore_n
    ore_id = map_info._building_id[ore_n]
    targets = set()
    for d in map_info._ALL_DIRECTIONS:
        p = map_info.pos_add(path[0], d)
        if p == best_ore or not map_info.in_bounds(p):
            continue
        if p.distance_squared(best_ore) > 2:
            continue
        pbit = 1 << (p.x + p.y * w)
        if map_info.is_passable(p):
            targets.add(p)
    log("all secured")
    if targets:
        log("attempt move?", targets)
        nav.move_to(targets)

    if ore_id:
        is_mine = bool(map_info._bm_team[my_team_idx] & ore_bit)
        is_road = bool(map_info._bm_et[map_info._IDX_ROAD] & ore_bit)
        if not is_mine and is_road:
            nav.move_to(best_ore)
            if rc.can_fire(map_info._my_pos):
                rc.fire(map_info._my_pos)
            comms.mark(best_ore.x + best_ore.y * map_info._width, comm_flag)
            return
        if is_mine and not map_info.has_builder_bot(best_ore) and rc.can_destroy(best_ore) and rc.get_action_cooldown() == 0 and map_info._my_pos != best_ore:
            rc.destroy(best_ore)
            map_info.update_at(best_ore)

    # Move to any adjacent tile and build harvester
    if rc.can_build_harvester(best_ore):
        rc.build_harvester(best_ore)
        map_info.update_at(best_ore)
    comms.mark(best_ore.x + best_ore.y * map_info._width, comm_flag)
