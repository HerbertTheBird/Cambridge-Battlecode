from cambc import Controller, Position, Direction, EntityType, Environment, GameError

from enum import Enum
import random
import sys

import map_info
import pathing
import comms

import units.states.explore  as explore
import units.states.disrupt  as disrupt
import units.states.harvest  as harvest
import units.states.route    as route
import units.states.heal     as heal
import units.states.sabotage as sabotage
import units.states.attack   as attack




rc: Controller
harvest_radius = 0
_harvest_zone = 0
states = [explore, disrupt, harvest, route, heal, sabotage, attack]
def init(c: Controller):
    global rc, harvest_radius
    rc = c
    harvest_radius = (c.get_map_width() + c.get_map_height()) // 3
    map_info.init(c)
    comms.init(c)
    for s in states:
        s.init(c)

forget = [0] * (len(states) + 1)            # bitmask per comm flag
_forget_rounds = [dict() for _ in range(len(states) + 1)]  # idx -> round for expiry

def handle_comms():
    current_round = rc.get_current_round()
    for v in comms.get_new_messages():
        sym = comms.decode_sym(v)
        map_info.update_symmetry_from_comms(sym)
        idx = comms.decode_location(v)
        flag = comms.decode_type(v)
        forget[flag] |= 1 << idx
        _forget_rounds[flag][idx] = current_round
        # Harvest claims also reserve cardinal neighbors (for barriers)
        if flag == 3:
            w = map_info._width
            px = idx % w
            py = idx // w
            for dx, dy in [(0, -1), (1, 0), (0, 1), (-1, 0)]:
                nx, ny = px + dx, py + dy
                if 0 <= nx < w and 0 <= ny < map_info._height:
                    ni = nx + ny * w
                    forget[flag] |= 1 << ni
                    _forget_rounds[flag][ni] = current_round
    for p in rc.get_nearby_tiles():
        idx = p.x + p.y * map_info._width
        for i in range(len(forget)):
            if idx in _forget_rounds[i] and _forget_rounds[i][idx] + 3 < current_round:
                del _forget_rounds[i][idx]
                forget[i] &= ~(1 << idx)
def draw_mask(mask, r, g, b):
    for p in map_info.iter_mask(mask):
        rc.draw_indicator_dot(p, r, g, b)

_harvest_zone_final = False

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
    global _harvest_zone, _harvest_zone_final
    map_info.update()
    handle_comms()
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
    best_heal = None
    best_heal_damage = -1
    w = map_info._width
    my_team_idx = map_info._TM_INT[rc.get_team()]
    healable = map_info._bm_team[my_team_idx] & map_info._bm_damaged
    for d in Direction:
        p = rc.get_position().add(d)
        if not map_info.in_bounds(p):
            continue
        pbit = 1 << (p.x + p.y * w)
        if not (healable & pbit):
            continue
        if not rc.can_heal(p):
            continue
        n = p.x + p.y * w
        hp = map_info._building_hp[n]
        for i in range(map_info._NUM_ET):
            if map_info._bm_et[i] & pbit:
                damage = map_info._MAX_HP_BY_IDX[i] - hp
                if damage > best_heal_damage:
                    best_heal_damage = damage
                    best_heal = p
                break
    if best_heal is not None:
        rc.heal(best_heal)
    elif rc.can_heal(rc.get_position()):
        rc.heal(rc.get_position())
    # if rc.get_tile_building_id(rc.get_position()) and rc.get_team(rc.get_tile_building_id(rc.get_position())) != rc.get_team() and rc.can_fire(rc.get_position()):
    #     rc.fire(rc.get_position())