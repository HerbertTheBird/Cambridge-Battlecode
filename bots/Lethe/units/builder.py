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
        pos = comms.decode_location(v)
        idx = pos.x + pos.y * map_info._width
        flag = comms.decode_type(v)
        forget[flag] |= 1 << idx
        _forget_rounds[flag][idx] = current_round
        # print("forget", pos, flag, comms.decode_id(v))
        # Harvest claims also reserve cardinal neighbors (for barriers)
        if flag == 3:
            w = map_info._width
            px, py = pos.x, pos.y
            for dx, dy in [(0, -1), (1, 0), (0, 1), (-1, 0)]:
                nx, ny = px + dx, py + dy
                if 0 <= nx < w and 0 <= ny < map_info._height:
                    ni = nx + ny * w
                    forget[flag] |= 1 << ni
                    _forget_rounds[flag][ni] = current_round
    for p in rc.get_nearby_tiles():
        idx = p.x + p.y * map_info._width
        for i in range(len(forget)):
            if idx in _forget_rounds[i] and _forget_rounds[i][idx] + 5 < current_round:
                del _forget_rounds[i][idx]
                forget[i] &= ~(1 << idx)
def draw_mask(mask, r, g, b):
    for p in map_info.iter_mask(mask):
        rc.draw_indicator_dot(p, r, g, b)

def run():
    global _harvest_zone
    map_info.update()
    handle_comms()
    pathing.rebuild_broken_barriers(rc)
    if map_info._my_core and not _harvest_zone:
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
