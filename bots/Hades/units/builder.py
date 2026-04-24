from cambc import Controller, Position, Direction, EntityType, Environment, GameError

from enum import Enum
import random
import sys

import map_info
import pathing
from pathing import Pathing
import comms
import comms_positional
import comms_stats
from units.spawn_plan import get_ray_endpoint, INITIAL_EXPLORE_MAX_STEPS, INITIAL_SPAWN_COUNT

import units.states.explore  as explore
import units.states.disrupt  as disrupt
import units.states.harvest  as harvest
import units.states.route    as route
import units.states.heal     as heal
import units.states.sabotage as sabotage
import units.states.attack   as attack
import units.states.secure   as secure

from log import DRAW_DEBUG, log


rc: Controller
nav: Pathing = None
harvest_radius = 0
_harvest_zone = 0
# states = [explore, disrupt, harvest, route, heal, sabotage, attack]
states = [explore, secure, harvest, disrupt, route]
def init(c: Controller):
    global rc, harvest_radius, nav
    rc = c
    nav = Pathing(c)
    harvest_radius = (c.get_map_width() + c.get_map_height()) // 3
    if comms_stats.is_enabled():
        comms_stats.init(c)
    for s in states:
        s.init(c)
    states.sort(key=lambda s: s.MAX_SCORE, reverse=True)

claimed_targets = [0] * (len(states) + 1)   # target bitmask per comm flag
claimed_senders = [0] * (len(states) + 1)   # sender position bitmask per comm flag
_target_rounds = [dict() for _ in range(len(states) + 1)]
_sender_rounds = [dict() for _ in range(len(states) + 1)]
USE_CLAIM_VISION = True
crowded_claims = [0] * (len(states) + 1)    # locally observed crowded targets per comm flag
_crowded_seen_rounds = [dict() for _ in range(len(states) + 1)]
_crowded_claim_rounds = [dict() for _ in range(len(states) + 1)]
_active_target_flag = 0
_active_target_idx = -1


def _clear_crowded_claim(flag: int, idx: int):
    if not USE_CLAIM_VISION:
        return
    crowded_claims[flag] &= ~(1 << idx)
    _crowded_seen_rounds[flag].pop(idx, None)
    _crowded_claim_rounds[flag].pop(idx, None)


def _adjacent_friendly_builder_count(target_idx: int) -> int:
    w = map_info._width
    h = map_info._height
    tx = target_idx % w
    ty = target_idx // w
    count = 0

    my_pos = map_info._my_pos
    if max(abs(my_pos.x - tx), abs(my_pos.y - ty)) <= 1:
        count += 1

    friendly = map_info._bm_friendly_bots
    for dy in (-1, 0, 1):
        ny = ty + dy
        if ny < 0 or ny >= h:
            continue
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            nx = tx + dx
            if nx < 0 or nx >= w:
                continue
            if friendly & (1 << (nx + ny * w)):
                count += 1
                if count >= 2:
                    return count
    return count


def _update_crowded_claims():
    if not USE_CLAIM_VISION:
        return
    current_round = rc.get_current_round()

    for flag in range(len(crowded_claims)):
        stale = [idx for idx, seen_round in _crowded_claim_rounds[flag].items() if seen_round + 2 < current_round]
        for idx in stale:
            _clear_crowded_claim(flag, idx)

    if _active_target_flag == 0 or _active_target_idx < 0 or _active_target_flag == heal.comm_flag:
        return

    bit = 1 << _active_target_idx
    if not (map_info._bm_visible & bit):
        return

    flag = _active_target_flag
    idx = _active_target_idx
    if _adjacent_friendly_builder_count(idx) >= 2:
        prev_round = _crowded_seen_rounds[flag].get(idx)
        _crowded_seen_rounds[flag][idx] = current_round
        if prev_round == current_round - 1 or (crowded_claims[flag] & bit):
            crowded_claims[flag] |= bit
            _crowded_claim_rounds[flag][idx] = current_round
    else:
        _clear_crowded_claim(flag, idx)


def exclude_crowded_claims(flag: int, mask: int) -> int:
    if not USE_CLAIM_VISION:
        return mask
    return mask & ~crowded_claims[flag]


def register_active_target(flag: int, target: Position | None):
    global _active_target_flag, _active_target_idx
    if not USE_CLAIM_VISION:
        return
    if target is None or flag == heal.comm_flag:
        return
    _active_target_flag = flag
    _active_target_idx = target.x + target.y * map_info._width

def handle_comms():
    current_round = rc.get_current_round()
    comms_positional.start_round_stats()
    w = map_info._width
    for v, sender_pos, _marker_pos, _marker_id, estimated_turn in comms.get_new_messages():
        sym = comms.decode_sym(v)
        map_info.update_symmetry_from_comms(sym)
        if estimated_turn + 3 < current_round:
            continue
        idx = comms.decode_location(v)
        flag = comms.decode_type(v)
        claimed_targets[flag] |= 1 << idx
        _target_rounds[flag][idx] = estimated_turn
        if map_info.in_bounds(sender_pos):
            sn = sender_pos.x + sender_pos.y * w
            claimed_senders[flag] |= 1 << sn
            _sender_rounds[flag][sn] = estimated_turn
    for p in map_info._nearby_tiles:
        idx = p.x + p.y * w
        for i in range(len(claimed_targets)):
            if idx in _target_rounds[i] and _target_rounds[i][idx] + 3 < current_round:
                del _target_rounds[i][idx]
                claimed_targets[i] &= ~(1 << idx)
    for i in range(len(claimed_senders)):
        expired = [idx for idx, t in _sender_rounds[i].items() if t + 20 < current_round]
        for idx in expired:
            del _sender_rounds[i][idx]
            claimed_senders[i] &= ~(1 << idx)
    comms_positional.flush_round_stats(current_round)
def draw_mask(mask, r, g, b):
    if not DRAW_DEBUG:
        return
    for p in map_info.iter_mask(mask):
        rc.draw_indicator_dot(p, r, g, b)

_harvest_zone_final = False

# First-tick ray explore target (derived from spawn tile relative to core).
# Cleared by explore state once reached.
_initial_explore_target: Position | None = None
_initial_explore_done = False
_initial_explore_round = -1  # round the target was set; used for timeout
INITIAL_EXPLORE_TIMEOUT = 30

def _compute_voronoi_harvest_zone():
    """Flood-fill Manhattan from both cores simultaneously.
    Tiles reached by my core first are my harvest zone."""
    w = map_info._width
    h = map_info._height
    board = (1 << (w * h)) - 1
    walls = map_info._bm_env[map_info._IDX_ENV_WALL]
    passable = board & ~walls

    my_core = map_info._my_core
    enemy_core = map_info._predicted_enemy_core

    my_front = 1 << (my_core.x + my_core.y * w)
    enemy_front = 1 << (enemy_core.x + enemy_core.y * w)

    my_claimed = my_front
    enemy_claimed = enemy_front
    claimed = my_claimed | enemy_claimed

    while my_front or enemy_front:
        if my_front:
            my_expand = map_info.expand_manhattan(my_front) & passable & ~claimed
            my_claimed |= my_expand
            claimed |= my_expand
            my_front = my_expand
        if enemy_front:
            enemy_expand = map_info.expand_manhattan(enemy_front) & passable & ~claimed
            enemy_claimed |= enemy_expand
            claimed |= enemy_expand
            enemy_front = enemy_expand

    return my_claimed

def run():
    global _harvest_zone, _harvest_zone_final, _active_target_flag, _active_target_idx
    global _initial_explore_target, _initial_explore_done, _initial_explore_round
    map_info.update(recompute=False)
    if not _initial_explore_done:
        if rc.get_current_round() > INITIAL_SPAWN_COUNT + 1:
            _initial_explore_done = True
        elif map_info._my_core is not None:
            spawn_dir = map_info.direction_to(map_info._my_core, map_info._my_pos)
            _initial_explore_target = get_ray_endpoint(
                map_info._my_pos, spawn_dir, map_info._width, map_info._height,
                max_steps=INITIAL_EXPLORE_MAX_STEPS,
            )
            _initial_explore_round = rc.get_current_round()
            _initial_explore_done = True
    # Auto-clear stale initial target if we couldn't reach it in time
    if (
        _initial_explore_target is not None
        and rc.get_current_round() - _initial_explore_round >= INITIAL_EXPLORE_TIMEOUT
    ):
        _initial_explore_target = None
    handle_comms()
    _update_crowded_claims()
    _active_target_flag = 0
    _active_target_idx = -1
    map_info.recompute_derived()
    pathing.rebuild_broken_barriers(rc)
    if map_info._my_core and not _harvest_zone_final:
        if map_info._solved_sym and map_info._predicted_enemy_core is not None:
            # Symmetry solved — compute Voronoi partition once
            _harvest_zone = _compute_voronoi_harvest_zone()
            _harvest_zone_final = True
        elif not _harvest_zone:
            # Fallback: radius-based until symmetry is solved
            w = map_info._width
            zone = 1 << (map_info._my_core.x + map_info._my_core.y * w)
            for _ in range(harvest_radius):
                zone = map_info.expand_chebyshev(zone)
            _harvest_zone = zone
    best_state = None
    best_score = 0
    for i in states:
        if best_score >= i.MAX_SCORE:
            break
        score = i.score()
        if score > best_score:
            best_score = score
            best_state = i
    best_state.run()
    # Heal the most damaged adjacent building, fall back to self
    heal._do_best_heal()
    if rc.can_heal(map_info._my_pos):
        rc.heal(map_info._my_pos)
    # if rc.get_tile_building_id(rc.get_position()) and rc.get_team(rc.get_tile_building_id(rc.get_position())) != rc.get_team() and rc.can_fire(rc.get_position()):
    #     rc.fire(rc.get_position())
