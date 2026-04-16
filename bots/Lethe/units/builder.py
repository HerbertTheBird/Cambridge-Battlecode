from cambc import Controller, Position, Direction, EntityType, Environment, GameError

from enum import Enum
import random
import sys

import map_info
import pathing
import comms
import comms_positional
import comms_stats

import units.states.explore  as explore
import units.states.disrupt  as disrupt
import units.states.harvest  as harvest
import units.states.route    as route
import units.states.heal     as heal
import units.states.sabotage as sabotage
import units.states.attack   as attack

from log import DRAW_DEBUG, log


rc: Controller
harvest_radius = 0
_harvest_zone = 0
states = [explore, disrupt, harvest, route, heal, sabotage, attack]
def init(c: Controller):
    global rc, harvest_radius
    rc = c
    harvest_radius = (c.get_map_width() + c.get_map_height()) // 3
    if comms_stats.is_enabled():
        comms_stats.init(c)
    for s in states:
        s.init(c)

claimed_targets = [0] * (len(states) + 1)   # target bitmask per comm flag
claimed_senders = [0] * (len(states) + 1)   # sender position bitmask per comm flag
_target_rounds = [dict() for _ in range(len(states) + 1)]
_sender_rounds = [dict() for _ in range(len(states) + 1)]

def handle_comms():
    current_round = rc.get_current_round()
    comms_positional.start_round_stats()
    w = map_info._width
    for v, sender_pos, estimated_turn in comms.get_new_messages():
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
            if idx in _sender_rounds[i] and _sender_rounds[i][idx] + 3 < current_round:
                del _sender_rounds[i][idx]
                claimed_senders[i] &= ~(1 << idx)
    comms_positional.flush_round_stats(current_round)
def draw_mask(mask, r, g, b):
    if not DRAW_DEBUG:
        return
    for p in map_info.iter_mask(mask):
        rc.draw_indicator_dot(p, r, g, b)

_harvest_zone_final = False

def _compute_voronoi_harvest_zone():
    """Flood-fill Manhattan from both cores simultaneously.
    Tiles reached by my core first are my harvest zone."""
    w = map_info._width
    board = map_info._board_mask
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
    global _harvest_zone, _harvest_zone_final
    map_info.update(recompute=False)
    handle_comms()
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
