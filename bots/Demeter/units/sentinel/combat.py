from itertools import chain

from cambc import Controller, EntityType, GameConstants, Position, Team

import map as map_mod
import vision as vc

from globals import (
    CONVEYOR_TYPES, 
    INF, 
    DIRECTIONS, 
    TURRET_PRIORITY, 
    TURRET_PASSIVE_PRIORITY,
)

_SENTINEL_DAMAGE = GameConstants.SENTINEL_DAMAGE
_KILLING_BLOW_TYPE_PRIORITY = {
    EntityType.CORE: 0,
    EntityType.BUILDER_BOT: 1,
    EntityType.BREACH: 2,
    EntityType.GUNNER: 2,
    EntityType.SENTINEL: 2,
    EntityType.LAUNCHER: 2,
}

def _killing_blow_type_rank(etype: EntityType) -> int:
    return _KILLING_BLOW_TYPE_PRIORITY.get(etype, 3)

def _get_shot_target_id(ct: Controller, pos: Position) -> int | None:
    bot_id = ct.get_tile_builder_bot_id(pos)
    if bot_id is not None:
        return bot_id
    return map_mod.get_tile_entity_id(pos)

def _is_killing_blow(ct: Controller, pos: Position) -> bool:
    target_id = _get_shot_target_id(ct, pos)
    return target_id is not None and ct.get_hp(target_id) <= _SENTINEL_DAMAGE

def _has_enemy_healer_nearby(target: Position) -> bool:
    for (_eid, etype, pos) in vc.enemy_units:
        if etype == EntityType.BUILDER_BOT and pos.distance_squared(target) <= 2:
            return True
    return False

def _has_other_ally_sentinel_covering(ct: Controller, target: Position) -> bool:
    my_id = ct.get_id()
    for (eid, etype, pos) in vc.ally_turrets:
        if eid <= my_id or etype != EntityType.SENTINEL:
            continue
        if ct.can_fire_from(pos, ct.get_direction(eid), EntityType.SENTINEL, target):
            return True
    return False

def _has_ally_gunner_covering(ct: Controller, target: Position) -> bool:
    for (eid, etype, pos) in vc.ally_turrets:
        if etype != EntityType.GUNNER:
            continue
        if ct.can_fire_from(pos, ct.get_direction(eid), EntityType.GUNNER, target):
            return True
    return False

def should_wait_to_sync_shot(ct: Controller, target: Position) -> bool:
    if map_mod.get_tile_entity_type(target) == EntityType.CORE:
        return False
    if _is_killing_blow(ct, target):
        return False
    if ct.get_current_round() % 3 == 0:
        return False
    if not _has_other_ally_sentinel_covering(ct, target):
        return False
    if _has_ally_gunner_covering(ct, target):
        return False
    if not _has_enemy_healer_nearby(target):
        return False
    return True

def _is_better_target(
    etype: EntityType,
    hp: int,
    prio: int | float,
    dist: int,
    best_etype: EntityType | None,
    best_hp: int | float,
    best_prio: int | float,
    best_dist: int | float,
) -> bool:
    killing_blow = hp <= _SENTINEL_DAMAGE
    best_killing_blow = best_etype is not None and best_hp <= _SENTINEL_DAMAGE

    if killing_blow != best_killing_blow:
        return killing_blow

    if killing_blow:
        type_rank = _killing_blow_type_rank(etype)
        best_type_rank = _killing_blow_type_rank(best_etype) if best_etype is not None else INF
        if type_rank != best_type_rank:
            return type_rank < best_type_rank
        if hp != best_hp:
            return hp > best_hp
        if prio != best_prio:
            return prio < best_prio
        return dist < best_dist

    if prio != best_prio:
        return prio < best_prio
    if hp != best_hp:
        return hp < best_hp
    return dist < best_dist

def choose_target(ct: Controller, my_pos: Position) -> Position | None:
    """Return the position of the best target to attack, or None if there are no targets."""
    best_target = None
    best_etype = None
    best_hp = INF
    best_prio = INF
    best_dist = INF
    _CORE = EntityType.CORE
    for (_eid, etype, pos) in vc.enemy_units:
        if etype is _CORE:
            # Core is 3x3 — find any tile we can fire on
            fire_pos = None
            if ct.can_fire(pos):
                fire_pos = pos
            else:
                for d in DIRECTIONS:
                    tile = pos.add(d)
                    if ct.can_fire(tile):
                        fire_pos = tile
                        break
            if fire_pos is None:
                continue
        else:
            if not ct.can_fire(pos):
                continue
            fire_pos = pos
        target_id = _get_shot_target_id(ct, fire_pos)
        if target_id is None:
            continue
        hp = ct.get_hp(target_id)
        prio = TURRET_PRIORITY.get(etype, INF)
        dist = my_pos.distance_squared(pos)
        if _is_better_target(etype, hp, prio, dist, best_etype, best_hp, best_prio, best_dist):
            best_etype = etype
            best_hp = hp
            best_prio = prio
            best_dist = dist
            best_target = fire_pos
    return best_target

def choose_passive_target(ct: Controller, my_pos: Position, my_team: Team) -> Position | None:
    """Pick the best enemy building to shoot at when no enemy units are in range."""
    best_pos = None
    best_etype = None
    best_hp = INF
    best_prio = INF
    best_dist = INF

    for (bid, etype, pos) in chain(vc.enemy_launchers, vc.enemy_conveyors, vc.enemy_other):
        prio = TURRET_PASSIVE_PRIORITY.get(etype)
        if prio is None:
            continue
        if not ct.can_fire(pos):
            continue
        bot_id = ct.get_tile_builder_bot_id(pos)
        if bot_id is not None and ct.get_team(bot_id) == my_team:
            continue
        if etype == EntityType.ROAD:
            continue
        if etype == EntityType.MARKER:
            continue
        # Skip conveyors/bridges/splitters/armoured conveyors if output chain feeds ally building
        if etype in CONVEYOR_TYPES:
            if map_mod.feeds_ally_building_idx(map_mod.pos_to_idx(pos), my_team):
                continue
        hp = ct.get_hp(bid)
        dist = my_pos.distance_squared(pos)
        if _is_better_target(etype, hp, prio, dist, best_etype, best_hp, best_prio, best_dist):
            best_etype = etype
            best_hp = hp
            best_prio = prio
            best_dist = dist
            best_pos = pos

    return best_pos
