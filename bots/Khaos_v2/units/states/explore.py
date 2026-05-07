from collections import deque

import chokepoint
import map_info
from pathing import Pathing
import units.builder
from cambc import *
import random
from log import log

rc: Controller = None
nav: Pathing = None

explore_target = None
_explore_target_from_initial = False


_CHOKEPOINT_OWN_DESTROYABLE = frozenset({
    EntityType.ROAD,
})
_CHOKEPOINT_ENEMY_CLEARABLE = frozenset({
    EntityType.ROAD,
    EntityType.CONVEYOR,
    EntityType.BRIDGE,
})
_CHOKEPOINT_LAUNCHER_CLEARANCE = 4

def init(c: Controller):
    global rc, nav
    rc = c
    nav = units.builder.nav

MAX_SCORE = 1
def score():
    return 1

def generate_explore_target():
    global explore_target
    w = map_info._width
    nlc = map_info._not_left_col
    nrc = map_info._not_right_col
    board = (1 << (w * map_info._height)) - 1
    if units.builder._stay_near_core:
        near = units.builder.near_core_mask()
        avoid = map_info.get_avoid(False, False, False)
        candidates = near & ~avoid
        if not candidates:
            candidates = near
        if candidates:
            count = candidates.bit_count()
            pick = random.randint(0, count - 1)
            mask = candidates
            for _ in range(pick):
                mask &= mask - 1
            lsb = mask & -mask
            n = lsb.bit_length() - 1
            explore_target = Position(n % w, n // w)
            return
    avoid = map_info.get_avoid(False, False, False)
    if rc.get_global_resources()[0] < rc.get_harvester_cost()[0]*2:
        avoid |= map_info._bm_seen & ~map_info._bm_any_building & ~map_info._bm_env[map_info._IDX_ENV_WALL]
    passable = ~avoid & board

    # Seed with all other builders' claimed tiles + incremental steps from
    # the nearest friendly bot toward each claim, plus my own position.
    seeds = 0
    my_pos = map_info._my_pos
    my_n = my_pos.x + my_pos.y * w
    seeds |= 1 << my_n
    seeds |= map_info._bm_friendly_bots

    # Seed tiles every 5 Chebyshev steps from my position toward each claim.
    bx, by = my_pos.x, my_pos.y
    mask = seeds
    while mask:
        lsb = mask & -mask
        n = lsb.bit_length() - 1
        tx, ty = n % w, n // w
        steps = max(abs(bx - tx), abs(by - ty))
        for s in range(5, steps, 5):
            ix = bx + (tx - bx) * s // steps
            iy = by + (ty - by) * s // steps
            seeds |= 1 << (ix + iy * w)
        mask ^= lsb

    # Keep the trailing 6 frontiers so we can recover the ring at iteration (c-5) once the fill terminates.
    visited = seeds
    frontier = seeds
    recent_frontiers = deque([seeds], maxlen=6)
    c = 0
    while frontier and c < 100:
        h = frontier | ((frontier & nrc) << 1) | ((frontier & nlc) >> 1)
        expanded = h | (h << w) | (h >> w)
        frontier = expanded & passable & ~visited
        visited |= frontier
        c += 1
        recent_frontiers.append(frontier)
    frontier = recent_frontiers[0]
    count = frontier.bit_count()
    if count == 0:
        explore_target = Position(random.randint(0, map_info._width - 1),
                                  random.randint(0, map_info._height - 1))
        return
    pick = random.randint(0, count - 1)
    mask = frontier
    for _ in range(pick):
        mask &= mask - 1
    lsb = mask & -mask
    n = lsb.bit_length() - 1
    explore_target = Position(n % w, n // w)


def _can_afford_chokepoint(kind):
    ti, ax = rc.get_global_resources()
    if kind == chokepoint.BLOCKER_WALL:
        cost_ti, cost_ax = rc.get_barrier_cost()
    else:
        cost_ti, cost_ax = rc.get_launcher_cost()
    return ti >= cost_ti + map_info.builder_ti_reserve() and ax >= cost_ax


def _launcher_too_close(target):
    target_bit = 1 << (target.x + target.y * map_info._width)
    nearby = map_info.expand_chebyshev(target_bit, _CHOKEPOINT_LAUNCHER_CLEARANCE)
    return bool(nearby & map_info._bm_et[map_info._IDX_LAUNCHER])


def _enemy_clearable_chokepoint_blocker(target):
    etype = map_info.type_at(target.x, target.y)
    if etype not in _CHOKEPOINT_ENEMY_CLEARABLE:
        return False
    team = map_info.team_at(target.x, target.y)
    return team is not None and team != map_info._my_team


def _wait_for_chokepoint_if_enabled():
    if units.builder.WAIT_FOR_CHOKEPOINT:
        units.builder.wait_for_chokepoint()
        return True
    return False


def _target_unavailable(target, kind):
    n = target.x + target.y * map_info._width
    bit = 1 << n
    if map_info._bm_env[map_info._IDX_ENV_WALL] & bit:
        chokepoint.abandon_target(target)
        return True
    if (map_info._bm_env[map_info._IDX_ENV_ORE_TI] | map_info._bm_env[map_info._IDX_ENV_ORE_AX]) & bit:
        chokepoint.abandon_target(target)
        return True

    etype = map_info.type_at(target.x, target.y)
    team = map_info.team_at(target.x, target.y)
    if team == map_info._my_team and etype in (EntityType.BARRIER, EntityType.LAUNCHER):
        chokepoint.mark_completed(target)
        return True

    if kind == chokepoint.BLOCKER_LAUNCHER and _launcher_too_close(target):
        chokepoint.abandon_target(target)
        return True

    if etype is None:
        return False

    if etype in _CHOKEPOINT_OWN_DESTROYABLE and team == map_info._my_team:
        return False

    if _enemy_clearable_chokepoint_blocker(target) and units.builder.WAIT_FOR_CHOKEPOINT:
        return False

    chokepoint.abandon_target(target)
    return True


def _clear_replaceable(target):
    etype = map_info.type_at(target.x, target.y)
    if etype in _CHOKEPOINT_OWN_DESTROYABLE and map_info.team_at(target.x, target.y) == map_info._my_team:
        if rc.can_destroy(target):
            rc.destroy(target)
            map_info.update_at(target)


def _try_clear_enemy_chokepoint_blocker(target):
    if not _enemy_clearable_chokepoint_blocker(target):
        return False
    if map_info._my_pos != target:
        return False

    if rc.can_fire(target):
        rc.fire(target)
        map_info.update_at(target)

    return _wait_for_chokepoint_if_enabled()


def _try_place_chokepoint_at(target, kind):
    reserve = map_info.builder_ti_reserve()
    ti_have = rc.get_global_resources()[0]
    if kind == chokepoint.BLOCKER_WALL:
        if rc.can_build_barrier(target) and ti_have >= rc.get_barrier_cost()[0] + reserve:
            rc.build_barrier(target)
            map_info.update_at(target)
            chokepoint.mark_completed(target)
            if chokepoint.CHOKEPOINT_DEBUG_PRINTS:
                chokepoint.debug(rc, f"passive build: built barrier at ({target.x},{target.y})")
            return True
    elif kind == chokepoint.BLOCKER_LAUNCHER:
        if rc.can_build_launcher(target) and ti_have >= rc.get_launcher_cost()[0] + reserve:
            rc.build_launcher(target)
            map_info.update_at(target)
            chokepoint.mark_completed(target)
            if chokepoint.CHOKEPOINT_DEBUG_PRINTS:
                chokepoint.debug(rc, f"passive build: built launcher at ({target.x},{target.y})")
            return True

    return False


def _try_build_chokepoint_at(target, kind):
    if _target_unavailable(target, kind):
        return False

    if _try_clear_enemy_chokepoint_blocker(target):
        return True

    _clear_replaceable(target)
    return _try_place_chokepoint_at(target, kind)


def _try_step_toward_chokepoint_target(target, kind, my_pos):
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            stand = Position(target.x + dx, target.y + dy)
            if not map_info.in_bounds(stand):
                continue
            if max(abs(stand.x - my_pos.x), abs(stand.y - my_pos.y)) > 1:
                continue
            if stand == my_pos:
                continue
            if stand == target and not _enemy_clearable_chokepoint_blocker(target):
                continue

            move_dir = map_info.direction_to(my_pos, stand)
            if not rc.can_move(move_dir):
                continue

            rc.move(move_dir)
            map_info.update_move()
            built = _try_build_chokepoint_at(target, kind)
            if chokepoint.CHOKEPOINT_DEBUG_PRINTS:
                chokepoint.debug(
                    rc,
                    f"passive build: moved toward {kind} target ({target.x},{target.y}); built={built}",
                )
            return True

    return False


def _try_passive_chokepoint_build():
    if not chokepoint.analysis_complete():
        if chokepoint.CHOKEPOINT_DEBUG_PRINTS:
            chokepoint.debug(
                rc,
                "passive build: waiting for chokepoint analysis",
                key="passive:waiting_analysis",
                interval=chokepoint.CHOKEPOINT_DEBUG_INTERVAL_ROUNDS,
            )
        return False

    my_pos = map_info._my_pos
    claims = chokepoint.claim_targets_near(my_pos, 2)
    if not claims:
        if chokepoint.CHOKEPOINT_DEBUG_PRINTS:
            chokepoint.debug(
                rc,
                "passive build: analysis complete, no local claimed chokepoint targets",
                key="passive:no_claims",
                interval=chokepoint.CHOKEPOINT_DEBUG_INTERVAL_ROUNDS,
            )
        return False

    w = map_info._width
    targets = []
    mask = claims
    while mask:
        lsb = mask & -mask
        n = lsb.bit_length() - 1
        target = Position(n % w, n // w)
        kind = chokepoint.blocker_kind_at(target)
        if kind is not None and not _target_unavailable(target, kind):
            targets.append((max(abs(target.x - my_pos.x), abs(target.y - my_pos.y)), target, kind))
        mask ^= lsb

    targets.sort(key=lambda item: item[0])
    if not targets:
        if chokepoint.CHOKEPOINT_DEBUG_PRINTS:
            chokepoint.debug(
                rc,
                "passive build: local targets exist but none are usable",
                key="passive:none_usable",
                interval=chokepoint.CHOKEPOINT_DEBUG_INTERVAL_ROUNDS,
            )
        return False

    blocked_targets = [
        item
        for item in targets
        if _enemy_clearable_chokepoint_blocker(item[1])
    ]
    if units.builder.WAIT_FOR_CHOKEPOINT:
        for _dist, target, kind in blocked_targets:
            if _try_build_chokepoint_at(target, kind):
                return True
            if _try_step_toward_chokepoint_target(target, kind, my_pos):
                return True

    if rc.get_action_cooldown() != 0:
        return _wait_for_chokepoint_if_enabled()

    affordable_targets = [
        item
        for item in targets
        if _can_afford_chokepoint(item[2])
    ]
    if not affordable_targets:
        if chokepoint.CHOKEPOINT_DEBUG_PRINTS:
            ti, ax = rc.get_global_resources()
            chokepoint.debug(
                rc,
                f"passive build: waiting on resources for {len(targets)} local chokepoint targets; resources=({ti},{ax})",
                key="passive:waiting_resources",
                interval=chokepoint.CHOKEPOINT_DEBUG_INTERVAL_ROUNDS,
            )
        return _wait_for_chokepoint_if_enabled()

    for _dist, target, kind in affordable_targets:
        if _target_unavailable(target, kind):
            continue
        if _try_build_chokepoint_at(target, kind):
            return True

        if _try_step_toward_chokepoint_target(target, kind, my_pos):
            return True

    return False


def try_passive_chokepoint_action_only():
    if rc.get_action_cooldown() != 0:
        return False
    if not chokepoint.analysis_complete():
        return False

    my_pos = map_info._my_pos
    claims = chokepoint.claim_targets_near(my_pos, 2)
    if not claims:
        return False

    w = map_info._width
    targets = []
    mask = claims
    while mask:
        lsb = mask & -mask
        n = lsb.bit_length() - 1
        target = Position(n % w, n // w)
        kind = chokepoint.blocker_kind_at(target)
        if kind is not None and not _target_unavailable(target, kind):
            targets.append((max(abs(target.x - my_pos.x), abs(target.y - my_pos.y)), target, kind))
        mask ^= lsb

    targets.sort(key=lambda item: item[0])
    for _dist, target, kind in targets:
        if _enemy_clearable_chokepoint_blocker(target):
            if my_pos == target and rc.can_fire(target):
                rc.fire(target)
                map_info.update_at(target)
                return True
            continue

        if not _can_afford_chokepoint(kind):
            continue
        if _try_build_chokepoint_at(target, kind):
            return True

    return False


def run():
    global explore_target, _explore_target_from_initial
    log("EXPLORE")
    
    if _try_passive_chokepoint_build():
        return

    if units.builder._initial_explore_target is not None:
        if map_info._my_pos.distance_squared(units.builder._initial_explore_target) <= 18:
            units.builder._initial_explore_target = None
        else:
            explore_target = units.builder._initial_explore_target
            _explore_target_from_initial = True
    elif _explore_target_from_initial:
        # initial target was cleared externally (e.g. timeout); don't trust the stale copy
        explore_target = None
        _explore_target_from_initial = False
    if explore_target is None or map_info._my_pos.distance_squared(explore_target) <= 18:
        generate_explore_target()
        _explore_target_from_initial = False
    attempts = 0
    while attempts < 1:
        if not nav.move_to(explore_target):
            generate_explore_target()
        else:
            break
        attempts += 1
