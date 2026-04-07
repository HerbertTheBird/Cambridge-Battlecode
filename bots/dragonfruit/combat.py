from itertools import chain

from cambc import Controller, EntityType, Position, Team

from vision import VisionCache
from globals import CONVEYOR_TYPES, INF, DIRECTIONS

_PRIORITY = {
    EntityType.CORE: 0,
    EntityType.BUILDER_BOT: 1,
    EntityType.BREACH: 2,
    EntityType.GUNNER: 3,
    EntityType.SENTINEL: 3,
}

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
        prio = _PRIORITY.get(etype, INF)
        dist = my_pos.distance_squared(pos)
        if prio < best_prio or (prio == best_prio and dist < best_dist):
            best_prio = prio
            best_dist = dist
            best_target = fire_pos
    return best_target

# Priority for passive targets (lower = higher priority)
_PASSIVE_PRIORITY = {
    EntityType.LAUNCHER: 0,
    EntityType.BRIDGE: 1,
    EntityType.CONVEYOR: 2,
    EntityType.SPLITTER: 3,
    EntityType.BARRIER: 4,
    EntityType.ARMOURED_CONVEYOR: 5,
    EntityType.ROAD: 6,
}

def choose_passive_target(ct: Controller, my_pos: Position, my_team: Team, vc: VisionCache, map_obj=None) -> Position | None:
    """Pick the best enemy building to shoot at when no enemy units are in range."""
    best_pos = None
    best_prio = INF
    best_dist = INF

    for (bid, etype, pos) in chain(vc.enemy_launchers, vc.enemy_conveyors, vc.enemy_other):
        prio = _PASSIVE_PRIORITY.get(etype)
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
            if map_obj is not None and map_obj.feeds_ally_building(pos, my_team):
                continue
        dist = my_pos.distance_squared(pos)
        if prio < best_prio or (prio == best_prio and dist < best_dist):
            best_prio = prio
            best_dist = dist
            best_pos = pos

    return best_pos

def choose_gunner_target(ct: Controller, my_pos: Position, my_team: Team) -> Position | None:
    """Pick the gunner's shot by scanning its short forward ray."""
    direction = ct.get_direction()
    attackable_tiles = set(ct.get_attackable_tiles())
    ray_tiles = []
    tile = my_pos.add(direction)

    for _ in range(3):
        if tile not in attackable_tiles:
            break
        ray_tiles.append(tile)
        tile = tile.add(direction)

    first_enemy_idx = None
    for i, tile in enumerate(ray_tiles):
        bot_id = ct.get_tile_builder_bot_id(tile)
        if bot_id is not None:
            if ct.get_team(bot_id) != my_team:
                first_enemy_idx = i
                break
            return None

        building_id = ct.get_tile_building_id(tile)
        if building_id is not None:
            etype = ct.get_entity_type(building_id)
            if etype == EntityType.MARKER:
                continue
            if ct.get_team(building_id) != my_team:
                first_enemy_idx = i
                break
            if etype == EntityType.ROAD:
                continue
            return None

        if not ct.is_tile_empty(tile):
            return None

    if first_enemy_idx is None:
        return None

    for i in range(first_enemy_idx):
        tile = ray_tiles[i]
        bot_id = ct.get_tile_builder_bot_id(tile)
        if bot_id is not None:
            return None

        building_id = ct.get_tile_building_id(tile)
        if building_id is None:
            continue

        etype = ct.get_entity_type(building_id)
        if etype == EntityType.MARKER:
            continue
        if ct.get_team(building_id) != my_team or etype == EntityType.ROAD:
            return tile
        return None

    return ray_tiles[first_enemy_idx]
