import map_info
import pathing
from cambc import *
import units.builder
from log import log
from units.states.harvest import possible_ore, secured
from units.states import attack
rc: Controller = None
nav = None

def _my_claims():
    my_pos = map_info._my_pos
    w = map_info._width
    my_mask = 1 << (my_pos.x + my_pos.y * w)
    available = securable_ore() & ~cant_secure()
    # units.builder.draw_mask(available, 0, 255, 0)
    available &= ~(_too_expensive() & map_info._bm_et[map_info._IDX_BARRIER])
    return pathing.claim_subset(my_mask, map_info._bm_friendly_bots&map_info._bm_visible, available, tie_self=True)


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

    ore = possible_ore(allow_partial=True)
    return (ore | map_info._bm_et[map_info._IDX_FOUNDRY]) & ~secured()

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

MAX_SCORE = 8.5
_cached_claims = 0
def score():
    global _cached_claims
    _cached_claims = _my_claims()
    # units.builder.draw_mask(securable_ore(), 0, 255, 0)
    # units.builder.draw_mask(_cached_claims, 0, 0, 255)
    important = _cached_claims & (map_info._bm_et[map_info._IDX_HARVESTER]|map_info._bm_et[map_info._IDX_FOUNDRY])
    # units.builder.draw_mask(important, 0, 255, 0)

    if important:
        closest, dist = nav.closest(important, map_info._my_pos)
        if closest:
            if dist <= 2:
                return 8.5
            else:
                return 7.5
    if _cached_claims & ~_too_expensive():
        return 3
    # elif _cached_claims:
    #     return 2
    return 0

CARD = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]

def run():
    log("SECURE")
    available = _cached_claims
    secure_now = False
    if _cached_claims & (map_info._bm_et[map_info._IDX_HARVESTER]|map_info._bm_et[map_info._IDX_FOUNDRY]):
        available = _cached_claims & (map_info._bm_et[map_info._IDX_HARVESTER]|map_info._bm_et[map_info._IDX_FOUNDRY])
        secure_now = True
    else:
        too_expensive = _too_expensive()
        if _cached_claims&~_too_expensive():
            available &= ~too_expensive
    log("secure now?", secure_now)
    if not available:
        return
    w = map_info._width
    my_team_idx = map_info._my_team_idx
    best_ore, _ = nav.closest(available)
    if not best_ore:
        _mark_cant_secure(available)
        return
    if best_ore.distance_squared(rc.get_position()) <= 5:
        check_region = map_info.expand_chebyshev(1<<(rc.get_position().x+rc.get_position().y*w), 2)
        securing = ( map_info._bm_team[my_team_idx]
            & ~map_info._bm_et[map_info._IDX_ROAD]
            & ~map_info._bm_et[map_info._IDX_MARKER]
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
    is_foundry = bool(map_info._bm_et[map_info._IDX_FOUNDRY]&(1<<(best_ore.x+best_ore.y*w)))
    if best_ore is None:
        _mark_cant_secure(available)
        return

    best_n = best_ore.x + best_ore.y * w
    is_raw_ax = bool(map_info._bm_env[map_info._IDX_ENV_ORE_AX] & (1 << best_n))
    if map_info._my_pos.distance_squared(best_ore) > 13:
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
        is_enemy = bool(map_info._bm_team[1-my_team_idx] & pbit) if map_info._building_id[pn] else False
        is_road = bool(map_info._bm_et[map_info._IDX_ROAD] & pbit)
        is_marker = bool(map_info._bm_et[map_info._IDX_MARKER] & pbit)
        if is_mine and (map_info._bm_et[map_info._IDX_CONVEYOR]&pbit or map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR]&pbit or map_info._bm_et[map_info._IDX_BRIDGE]&pbit) and map_info._building_dir[pn] != map_info._DIR_INT[d.opposite()]:
            done_conveyor = p
        if is_mine and map_info._bm_et[map_info._IDX_BRIDGE]&pbit:
            done_conveyor = p
        if is_mine and not is_road and not is_marker:
            # Has a real building (mine or enemy, not road/marker) — side is done
            continue
        if is_enemy and not is_road and not is_marker:
            # Enemy real building — can't build here, leave unsecured
            continue
        unsecured |= pbit
    # units.builder.draw_mask(map_info._bm_team[my_team_idx], 255, 0, 0)
    if is_foundry:
        path = None
    elif done_conveyor:
        path = nav.calculate_conveyor_path(done_conveyor, is_raw_ax, True)
    else:
        path = nav.calculate_conveyor_path(best_ore, is_raw_ax)
    if unsecured.bit_count() == 1:
        closest = Position((unsecured.bit_length()-1)%w, (unsecured.bit_length()-1)//w)
    else:
        if path and path[0].distance_squared(path[1]) > 1:
            unsecured &= ~(1<<(path[0].x+path[0].y*w))
        closest, _ = nav.closest(unsecured)
    if not closest:
        _mark_cant_secure(unsecured)
        return
    closest_n = closest.x+closest.y*w
    if map_info._building_id[closest_n] and not (map_info._bm_team[my_team_idx]&(1<<closest_n)) and not (map_info._bm_et[map_info._IDX_MARKER]&(1<<closest_n)):
        nav.move_to(closest)
        if rc.can_fire(closest):
            rc.fire(closest)
            map_info.update_at(closest)
        return
    if path is not None:
        is_conveyor = path[0].distance_squared(path[1]) == 1

        cost_estimate = rc.get_conveyor_cost()[0]*(unsecured.bit_count()-1) + rc.get_harvester_cost()[0]
        scale_estimate = (unsecured.bit_count()-1)*0.01 + 0.05
        if (1<<(path[0].x+path[0].y*w))&unsecured and is_conveyor:
            cost_estimate += rc.get_bridge_cost()[0]
            scale_estimate += 0.1
        else:
            cost_estimate += rc.get_conveyor_cost()[0]
            scale_estimate += 0.01
        is_attack_adjacent = bool((1 << best_n) & map_info.expand_manhattan(attack.wanted_attack_tiles()))
        if is_attack_adjacent:
            _cost_map[best_n] = (cost_estimate, rc.get_current_round())
        else:
            _cost_map[best_n] = (cost_estimate + nav.conveyor_cost(path[2], rc.get_scale_percent()/100+scale_estimate), rc.get_current_round())
    elif not secure_now:
        _mark_cant_secure(1 << (best_ore.x + best_ore.y * w))
        return
    if not secure_now and _cost_map[best_n][0] > rc.get_global_resources()[0]:
        # nav.move_adjacent(best_ore)
        # if rc.can_build_barrier(best_ore):
        #     rc.build_barrier(best_ore)
        #     map_info.update_at(best_ore)
        #     _mark_cant_secure(1 << best_n)
        return
    if path and not is_foundry:
        tn = path[1].x + path[1].y * w
        if not done_conveyor and is_conveyor and path[0] == closest and not (map_info._bm_team[my_team_idx] & (1 << tn) and not (map_info._bm_et[map_info._IDX_MARKER] & (1 << tn))):
            nav.move_to(path[1])
            if rc.can_build_road(path[1]):
                rc.build_road(path[1])
                map_info.update_at(path[1])
                log("Exit 1")
                return
    def build_stuff():
        if rc.can_destroy(closest) and rc.get_action_cooldown() == 0:
            rc.destroy(closest)
            map_info.update_at(closest)
        if path and not done_conveyor and closest == path[0]:
            if is_conveyor:
                dir = map_info.direction_to(path[0], path[1])
                if rc.can_build_conveyor(path[0], dir):
                    rc.build_conveyor(path[0], dir)
                    map_info.update_at(path[0])
                    return True
            else:
                if rc.can_build_bridge(path[0], path[1]):
                    rc.build_bridge(path[0], path[1])
                    map_info.update_at(path[0])
                    return True
        else:
            dir = map_info.direction_to(closest, best_ore)
            if rc.can_build_conveyor(closest, dir):
                rc.build_conveyor(closest, dir)
                map_info.update_at(closest)
                return True
        return False
    if rc.get_position().distance_squared(closest) <= 2 and build_stuff():
        unsecured ^= (1<<closest_n)
        next_closest, _ = nav.closest(unsecured)
        if next_closest:
            nav.move_to(next_closest)
    else:
        nav.move_to(closest)
        build_stuff()
