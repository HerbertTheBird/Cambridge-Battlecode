import map_info
import pathing
from cambc import *
import units.builder
from log import log
from units.states.harvest import possible_ore, secured
rc: Controller = None
nav = None

def _my_claims():
    my_pos = map_info._my_pos
    w = map_info._width
    my_mask = 1 << (my_pos.x + my_pos.y * w)
    available = securable_ore() & ~((_too_expensive()) & ~(map_info._bm_et[map_info._IDX_HARVESTER]|map_info._bm_et[map_info._IDX_FOUNDRY])) & ~cant_secure()
    return pathing.claim_subset(my_mask, map_info._bm_friendly_bots, available, tie_self=False)


def init(c: Controller):
    global rc, nav
    rc = c
    nav = units.builder.nav

_cant_secure_map: dict[int, int] = {}  # tile index -> round recorded
CANT_SECURE_TTL = 100
_cost_map: dict[int, tuple[int, int]] = {}  # tile index -> (min titanium cost, round recorded)
COST_MAP_TTL = 100


def cant_secure():
    """Bitmask of tiles we recently failed to secure; entries expire after CANT_SECURE_TTL rounds."""
    current = rc.get_current_round()
    result = 0
    stale = []
    for n, turn in _cant_secure_map.items():
        if turn + CANT_SECURE_TTL < current:
            stale.append(n)
            continue
        result |= 1 << n
    for n in stale:
        del _cant_secure_map[n]
    return result


def _mark_cant_secure(mask):
    if not mask:
        return
    current = rc.get_current_round()
    m = mask
    while m:
        lsb = m & -m
        n = lsb.bit_length() - 1
        _cant_secure_map[n] = current
        m ^= lsb
def securable_ore():
    """Bitmask of titanium ore tiles without a harvester and not forgotten."""

    ore = possible_ore()
    return (ore
            & ~secured()) | map_info._bm_et[map_info._IDX_FOUNDRY]

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

MAX_SCORE = 7.5
_cached_claims = 0
def score():
    global _cached_claims
    _cached_claims = _my_claims()
    # units.builder.draw_mask(securable_ore(), 0, 255, 0)
    # units.builder.draw_mask(_cached_claims, 0, 0, 255)
    if _cached_claims & (map_info._bm_et[map_info._IDX_HARVESTER]|map_info._bm_et[map_info._IDX_FOUNDRY]):
        # units.builder.draw_mask(_cached_claims & map_info._bm_et[map_info._IDX_HARVESTER], 0, 255, 0)
        return 7.5
    return 3 if _cached_claims else 0

CARD = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]

def run():
    log("SECURE")
    available = _cached_claims
    secure_now = False
    if _cached_claims & (map_info._bm_et[map_info._IDX_HARVESTER]|map_info._bm_et[map_info._IDX_FOUNDRY]):
        available = _cached_claims & (map_info._bm_et[map_info._IDX_HARVESTER]|map_info._bm_et[map_info._IDX_FOUNDRY])
        secure_now = True
    log("secure now?", secure_now)
    # units.builder.draw_mask(cant_secure(), 255, 255, 255)
    if not available:
        log("secure exit: no available, claims=", _cached_claims.bit_count())
        return
    w = map_info._width
    my_team_idx = map_info._my_team_idx
    best_ore, _ = nav.closest(available)
    if not best_ore:
        log("secure exit: nav.closest returned None over", available.bit_count(), "tiles")
        _mark_cant_secure(available)
        return
    log("dist", _)
    log("best secure", best_ore)
    if best_ore.distance_squared(rc.get_position()) <= 5:
        check_region = map_info.expand_chebyshev(1<<(rc.get_position().x+rc.get_position().y*w), 2)
        securing = ( map_info._bm_team[my_team_idx]
            & ~map_info._bm_et[map_info._IDX_ROAD]
            & ~map_info._bm_et[map_info._IDX_MARKER]
            & ~map_info._bm_guard_conveyor
        | map_info._bm_env[map_info._IDX_ENV_WALL]) |  map_info._bm_team[1-my_team_idx] & map_info._bm_et[map_info._IDX_HARVESTER]
        bottom_row = ((1<<w)-1)<<w*(map_info._height-1)
        top_row = ((1<<w)-1)
        
        to_check = check_region&available
        loc_best = None
        def dirs_covered(n_bit):
            score = 0
            if n_bit & ~map_info._not_left_col:
                score += 1
            if n_bit & ~map_info._not_right_col:
                score += 1
            if n_bit & bottom_row:
                score += 1
            if n_bit & top_row:
                score += 1
            if (n_bit & map_info._not_left_col)<<1 & securing:
                score += 1
            if (n_bit & map_info._not_right_col)>>1 & securing:
                score += 1
            if n_bit>>w & securing:
                score += 1
            if n_bit<<w & securing:
                score += 1
            return score
        mx_score = dirs_covered(1<<(best_ore.x+best_ore.y*w))
        log("initial score", mx_score)
        while to_check:
            n_bit = (to_check&-to_check)
            n = n_bit.bit_length()-1
            score = dirs_covered(n_bit)
            log("new score", score, n%w, n//w)
            if score > mx_score:
                mx_score = score
                loc_best = Position(n%w, n//w)
            to_check ^= n_bit
        if loc_best:
            best_ore = loc_best
    log(best_ore)
    is_foundry = bool(map_info._bm_et[map_info._IDX_FOUNDRY]&(1<<(best_ore.x+best_ore.y*w)))
    log("is foundry", is_foundry)
    if best_ore is None:
        _mark_cant_secure(available)
        return

    best_n = best_ore.x + best_ore.y * w
    is_raw_ax = bool(map_info._bm_env[map_info._IDX_ENV_ORE_AX] & (1 << best_n))
    if map_info._my_pos.distance_squared(best_ore) > 13:
        log("secure: dist", map_info._my_pos.distance_squared(best_ore), "> 13, moving to", best_ore)
        nav.move_to(best_ore)
        return
    # --- Secure each cardinal side ---
    unsecured = 0
    done_conveyor = None
    for d in CARD:
        p = map_info.pos_add(best_ore, d)
        if not map_info.in_bounds(p):
            continue

        pn = p.x + p.y * w
        pbit = 1 << pn

        # Wall — done
        if map_info._bm_env[map_info._IDX_ENV_WALL] & pbit:
            continue

        is_mine = bool(map_info._bm_team[my_team_idx] & pbit) if map_info._building_id[pn] else False
        is_road = bool(map_info._bm_et[map_info._IDX_ROAD] & pbit)
        is_marker = bool(map_info._bm_et[map_info._IDX_MARKER] & pbit)
        log("checking", p, is_mine, is_road, is_marker, map_info._building_id[pn])
        if is_mine and (map_info._bm_et[map_info._IDX_CONVEYOR]&pbit or map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR]&pbit or map_info._bm_et[map_info._IDX_BRIDGE]&pbit) and map_info._building_dir[pn] != map_info._DIR_INT[d.opposite()]:
            done_conveyor = p
        if is_mine and map_info._bm_et[map_info._IDX_BRIDGE]&pbit:
            done_conveyor = p
        if is_mine and not is_road and not is_marker:
            # Has a real building (mine or enemy, not road/marker) — side is done
            continue
        unsecured |= pbit
    # units.builder.draw_mask(map_info._bm_team[my_team_idx], 255, 0, 0)
    closest, _ = nav.closest(unsecured)
    if not closest:
        _mark_cant_secure(unsecured)
        log("exit 3")
        return
    closest_n = closest.x+closest.y*w
    if map_info._building_id[closest_n] and not (map_info._bm_team[my_team_idx]&(1<<closest_n)) and not (map_info._bm_et[map_info._IDX_MARKER]&(1<<closest_n)):
        nav.move_to(closest)
        if rc.can_fire(closest):
            rc.fire(closest)
            map_info.update_at(closest)
        log("exit 2")
        return

    is_enemy_harvester = bool(
        map_info._bm_et[map_info._IDX_HARVESTER]
        & map_info._bm_team[1 - my_team_idx]
        & (1 << best_n)
    )
    if is_enemy_harvester:
        # Enemy-owned harvester at the target — wrap it in barriers instead of
        # paving conveyors. Skips the path conveyor entirely.
        log("enemy harvester at", best_ore, "— barrier wrap")
        def _build_barrier_at(p):
            if rc.can_destroy(p) and rc.get_action_cooldown() == 0:
                rc.destroy(p)
                map_info.update_at(p)
            if rc.can_build_barrier(p) and rc.get_global_resources()[0] >= rc.get_barrier_cost()[0] + map_info.builder_ti_reserve():
                rc.build_barrier(p)
                map_info.update_at(p)
                return True
            return False
        if rc.get_position().distance_squared(closest) <= 2 and _build_barrier_at(closest):
            unsecured ^= (1 << closest_n)
            next_closest, _ = nav.closest(unsecured)
            if next_closest:
                nav.move_to(next_closest)
        else:
            nav.move_to(closest)
            _build_barrier_at(closest)
        return

    if done_conveyor:
        path = nav.calculate_conveyor_path(done_conveyor, is_raw_ax, True)
    else:
        path = nav.calculate_conveyor_path(best_ore, is_raw_ax)
    if path is not None:
        is_conveyor = path[0].distance_squared(path[1]) == 1

        unsecured_conv_cost = rc.get_conveyor_cost()[0]*(unsecured.bit_count()-1)
        harvester_cost = rc.get_harvester_cost()[0]
        cost_estimate = unsecured_conv_cost + harvester_cost
        scale_estimate = (unsecured.bit_count()-1)*0.01 + 0.05
        if is_conveyor:
            start_piece_cost = rc.get_conveyor_cost()[0]
            scale_estimate += 0.01
        else:
            start_piece_cost = rc.get_bridge_cost()[0]
            scale_estimate += 0.1
        cost_estimate += start_piece_cost
        reserve_cost = map_info.builder_ti_reserve()
        cost_estimate += reserve_cost
        path_cost = nav.conveyor_cost(path[2], rc.get_scale_percent()/100+scale_estimate)
        total_cost = cost_estimate + path_cost
        _cost_map[best_n] = (total_cost, rc.get_current_round())
        log("secure cost: path_len", path[2], "unsecured_conv", unsecured_conv_cost, "harvester", harvester_cost, "start_piece", start_piece_cost, "reserve", reserve_cost, "path", path_cost, "total", total_cost, "ti", rc.get_global_resources()[0], "unsecured", unsecured.bit_count(), "is_conveyor", is_conveyor)
    elif not secure_now:
        log("CANT SECURE", best_ore, done_conveyor)
        _mark_cant_secure(1 << (best_ore.x + best_ore.y * w))
        return
    if not secure_now and _cost_map[best_n][0] > rc.get_global_resources()[0]:
        log("too expensive", best_ore, _cost_map[best_n][0], rc.get_global_resources()[0])
        return
    if path and not is_foundry:
        tn = path[1].x + path[1].y * w
        if not done_conveyor and is_conveyor and path[0] == closest and rc.get_position().distance_squared(path[1]) <= 2 and not (map_info._bm_team[my_team_idx] & (1 << tn) and not (map_info._bm_et[map_info._IDX_MARKER] & (1 << tn))):
            if rc.can_build_road(path[1]) and rc.get_global_resources()[0] >= rc.get_road_cost()[0] + map_info.builder_ti_reserve():
                rc.build_road(path[1])
                map_info.update_at(path[1])
                log("Exit 1")
                return
    # units.builder.draw_mask(unsecured, 255, 0, 0)
    def build_stuff():
        log("build stuff", closest)
        if rc.can_destroy(closest) and rc.get_action_cooldown() == 0:
            rc.destroy(closest)
            map_info.update_at(closest)
        if path and not done_conveyor and closest == path[0]:
            if is_conveyor:
                dir = map_info.direction_to(path[0], path[1])
                if rc.can_build_conveyor(path[0], dir) and rc.get_global_resources()[0] >= rc.get_conveyor_cost()[0] + map_info.builder_ti_reserve():
                    rc.build_conveyor(path[0], dir)
                    map_info.update_at(path[0])
                    return True
            else:
                if rc.can_build_bridge(path[0], path[1]) and rc.get_global_resources()[0] >= rc.get_bridge_cost()[0] + map_info.builder_ti_reserve():
                    rc.build_bridge(path[0], path[1])
                    map_info.update_at(path[0])
                    return True
        else:
            dir = map_info.direction_to(closest, best_ore)
            if rc.can_build_conveyor(closest, dir) and rc.get_global_resources()[0] >= rc.get_conveyor_cost()[0] + map_info.builder_ti_reserve():
                rc.build_conveyor(closest, dir)
                map_info.update_at(closest)
                return True
        return False
    if rc.get_position().distance_squared(closest) <= 2 and build_stuff():
        unsecured ^= (1<<closest_n)
        next_closest, _ = nav.closest(unsecured)
        if next_closest:
            log("move to next")
            nav.move_to(next_closest)
    else:
        if secure_now:
            nav.move_to(closest)
        else:
            nav.move_to(best_ore)
        build_stuff()
