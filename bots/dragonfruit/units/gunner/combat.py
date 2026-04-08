from cambc import Controller, Direction, EntityType, Position, Team

from globals import INF, TURRET_TYPES

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

def choose_rotate_dir(ct: Controller, my_pos: Position, enemy_units) -> Direction | None:
    current_dir = ct.get_direction()
    rotate_dir = None
    rotate_dist = INF

    for (_eid, etype, pos) in enemy_units:
        if etype not in TURRET_TYPES:
            continue

        dist = my_pos.distance_squared(pos)
        if dist > 2:
            continue

        desired_dir = my_pos.direction_to(pos)
        if desired_dir == current_dir:
            continue

        if dist < rotate_dist:
            rotate_dist = dist
            rotate_dir = desired_dir

    return rotate_dir
