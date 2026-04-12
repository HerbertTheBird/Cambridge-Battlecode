from cambc import Controller, Direction, EntityType, Environment, Position, Team

import map as map_mod

from globals import INF, TURRET_TYPES, DIRECTIONS, CARDINAL_DIRECTIONS, DELTAS
from map import on_map, on_map_coords

def choose_gunner_target(ct: Controller, my_pos: Position, my_team: Team) -> Position | None:
    """Pick the gunner's shot by scanning its short forward ray."""
    direction = ct.get_direction()
    dx, dy = DELTAS[direction]
    max_range = 3 if direction in CARDINAL_DIRECTIONS else 2
    width = ct.get_map_width()
    height = ct.get_map_height()
    ray_tiles = []
    x, y = my_pos.x, my_pos.y

    for _ in range(max_range):
        x += dx
        y += dy
        if not on_map_coords(x, y, width, height):
            break
        ray_tiles.append(Position(x, y))

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

def get_gunner_threat_mask(ct: Controller, tpos: Position, my_team) -> int:
    threat_mask = 0

    width = map_mod.width
    height = map_mod.height
    bm_wall = map_mod.get_env_mask(Environment.WALL)

    for d in DIRECTIONS:
        dx, dy = DELTAS[d]
        max_range = 3 if d in CARDINAL_DIRECTIONS else 2

        x, y = tpos.x, tpos.y
        for _ in range(max_range):
            x += dx
            y += dy
            if not on_map_coords(x, y, width, height):
                break
            idx = y * width + x
            cur = Position(x, y)
            if not ct.is_in_vision(cur):
                continue

            # Wall blocks
            if (bm_wall >> idx) & 1:
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
            
            threat_mask |= 1 << idx

    return threat_mask

def choose_rotate_dir(ct: Controller, my_pos: Position, enemy_units, my_team) -> Direction | None:
    current_dir = ct.get_direction()
    rotate_dir = None
    rotate_dist = 14

    threat_mask = get_gunner_threat_mask(ct, my_pos, my_team)

    for (eid, etype, tpos) in enemy_units:
        if etype not in TURRET_TYPES:
            continue

        # --- core check ---
        tile_idx = tpos.y * map_mod.width + tpos.x
        if not ((threat_mask >> tile_idx) & 1):
            continue

        dist = my_pos.distance_squared(tpos)
        desired_dir = my_pos.direction_to(tpos)

        if desired_dir == current_dir:
            continue

        if dist < rotate_dist:
            rotate_dist = dist
            rotate_dir = desired_dir

    return rotate_dir
