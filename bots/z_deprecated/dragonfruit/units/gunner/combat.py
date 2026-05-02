from cambc import Controller, Direction, EntityType, Position, Team, Environment

from globals import INF, TURRET_TYPES, DIRECTIONS, CARDINAL_DIRECTIONS, DELTAS
from map import on_map

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

def get_gunner_threat_tiles(ct: Controller, tpos: Position, map_obj, my_team) -> set[Position]:
    threat_tiles = set()

    width = map_obj.width
    height = map_obj.height

    for d in DIRECTIONS:
        dx, dy = DELTAS[d]
        max_range = 3 if d in CARDINAL_DIRECTIONS else 2

        x, y = tpos.x, tpos.y
        for _ in range(max_range):
            x += dx
            y += dy
            cur = Position(x, y)
            
            if not ct.is_in_vision(cur):
                continue

            if not on_map(cur, width, height):
                break

            # Wall blocks
            if map_obj.get_tile_env(cur) == Environment.WALL:
                break

            # builder blocks
            bbid = ct.get_tile_builder_bot_id(cur)
            if bbid is not None:
                if ct.get_team(bbid) == my_team:
                    break  # ally blocks

            # buildings
            bid = ct.get_tile_building_id(cur)
            if bid is not None:
                etype = ct.get_entity_type(bid)
                team = ct.get_team(bid)
                if etype != EntityType.MARKER and etype != EntityType.ROAD and team == my_team:
                    break  # ally blocks
            
            threat_tiles.add(cur)

    return threat_tiles

def choose_rotate_dir(ct: Controller, my_pos: Position, enemy_units, map_obj, my_team) -> Direction | None:
    current_dir = ct.get_direction()
    rotate_dir = None
    rotate_dist = 14

    threat_tiles = get_gunner_threat_tiles(ct, my_pos, map_obj, my_team)

    for (eid, etype, tpos) in enemy_units:
        if etype not in TURRET_TYPES:
            continue

        # --- core check ---
        if tpos not in threat_tiles:
            continue

        dist = my_pos.distance_squared(tpos)
        desired_dir = my_pos.direction_to(tpos)

        if desired_dir == current_dir:
            continue

        if dist < rotate_dist:
            rotate_dist = dist
            rotate_dir = desired_dir

    return rotate_dir