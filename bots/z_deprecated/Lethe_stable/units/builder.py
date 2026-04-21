from cambc import *
import random

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
LEARN_MAP_COMM_FLAG = 0
HEAL_COMM_FLAG = 7
states = [explore, disrupt, harvest, route, heal, sabotage, attack]
def init(c: Controller):
    global rc, harvest_radius
    rc = c
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

def handle_comms():
    current_round = rc.get_current_round()
    comms_positional.start_round_stats()
    w = map_info._width
    for v, marker_pos, sender_pos, estimated_turn in comms.get_new_messages():
        flag = comms.decode_type(v)
        if estimated_turn + 3 < current_round:
            continue
        if flag == LEARN_MAP_COMM_FLAG:
            corresponding_pos = comms.decode_learn_map_corresponding_pos(v)
            sample = comms.decode_learn_map_sample_bits(v)
            env_bit = comms.decode_learn_map_env_bit(v)
            comms_positional.apply_learn_map_message(marker_pos, corresponding_pos, env_bit, sample)
            continue

        sym = comms.decode_sym(v)
        map_info.update_symmetry_from_comms(sym)
        idx = comms.decode_location(v)
        sample = comms.decode_sample_bits(v)
        comms_positional.apply_message(marker_pos, sym, sample)

        claimed_targets[flag] |= 1 << idx
        _target_rounds[flag][idx] = estimated_turn
        if map_info.in_bounds(sender_pos):
            sn = sender_pos.x + sender_pos.y * w
            claimed_senders[flag] |= 1 << sn
            _sender_rounds[flag][sn] = estimated_turn
    # Tile-based prune: 3-turn expiry inside vision, 50-turn expiry outside.
    vision_mask = 0
    for p in map_info._nearby_tiles:
        vision_mask |= 1 << (p.x + p.y * w)
    for i in range(len(claimed_targets)):
        if i in (LEARN_MAP_COMM_FLAG, HEAL_COMM_FLAG):
            pass
        else:
            # Heal flag stores enemy UIDs, not tile indices, so skip it here.
            stale = [
                k for k, r in _target_rounds[i].items()
                if r + (3 if (vision_mask >> k) & 1 else 50) < current_round
            ]
            for k in stale:
                del _target_rounds[i][k]
                claimed_targets[i] &= ~(1 << k)
        stale = [
            k for k, r in _sender_rounds[i].items()
            if r + (3 if (vision_mask >> k) & 1 else 50) < current_round
        ]
        for k in stale:
            del _sender_rounds[i][k]
            claimed_senders[i] &= ~(1 << k)
    # Age-based prune for heal flag target claims (UIDs, not tiles).
    stale_heal = [k for k, r in _target_rounds[HEAL_COMM_FLAG].items() if r + 3 < current_round]
    for k in stale_heal:
        del _target_rounds[HEAL_COMM_FLAG][k]
        claimed_targets[HEAL_COMM_FLAG] &= ~(1 << k)
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

def _maybe_mark_learn_map():
    width = map_info._width
    height = map_info._height
    my_pos = map_info._my_pos
    my_x = my_pos.x
    my_y = my_pos.y
    for _ in range(20):
        x = random.randint(0, width - 1)
        y = random.randint(0, height - 1)
        dx = x - my_x
        dy = y - my_y
        if dx * dx + dy * dy <= 13:
            continue
        if not map_info.seen_at(x, y):
            continue
        comms.mark(0, LEARN_MAP_COMM_FLAG, Position(x, y))
        return

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
    _maybe_mark_learn_map()
    # if rc.get_tile_building_id(rc.get_position()) and rc.get_team(rc.get_tile_building_id(rc.get_position())) != rc.get_team() and rc.can_fire(rc.get_position()):
    #     rc.fire(rc.get_position())
