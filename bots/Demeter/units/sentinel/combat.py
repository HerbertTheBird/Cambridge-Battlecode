from itertools import chain

from cambc import Controller, EntityType, Position, Team

from vision import VisionCache
from globals import CONVEYOR_TYPES, INF, DIRECTIONS, TURRET_PRIORITY, TURRET_PASSIVE_PRIORITY

def choose_target(ct: Controller, my_pos: Position, vc: VisionCache) -> Position | None:
    """Return the position of the best target to attack, or None if there are no targets."""
    best_target = None
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
        prio = TURRET_PRIORITY.get(etype, INF)
        dist = my_pos.distance_squared(pos)
        if prio < best_prio or (prio == best_prio and dist < best_dist):
            best_prio = prio
            best_dist = dist
            best_target = fire_pos
    return best_target

def choose_passive_target(ct: Controller, my_pos: Position, my_team: Team, vc: VisionCache, map_obj=None) -> Position | None:
    """Pick the best enemy building to shoot at when no enemy units are in range."""
    best_pos = None
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
            if map_obj is not None and map_obj.feeds_ally_building_idx(map_obj.pos_to_idx(pos), my_team):
                continue
        dist = my_pos.distance_squared(pos)
        if prio < best_prio or (prio == best_prio and dist < best_dist):
            best_prio = prio
            best_dist = dist
            best_pos = pos

    return best_pos
