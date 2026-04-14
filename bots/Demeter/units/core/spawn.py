import random

from cambc import Controller, Direction, EntityType, Position

import map as map_mod
import vision as vc

from globals import *

def dir_distance(a, b):
    ia = DIRECTIONS.index(a)
    ib = DIRECTIONS.index(b)
    diff = abs(ia - ib)
    return min(diff, 8 - diff)

def get_ray_endpoint(start: Position, direction: Direction, width: int, height: int) -> Position:
    dx, dy = direction.delta()
    x, y = start.x, start.y

    while True:
        nx, ny = x + dx, y + dy
        if nx < 0 or nx >= width or ny < 0 or ny >= height:
            return Position(x, y)
        x, y = nx, ny

def get_valid_directions(ct, core_pos, width, height):
    valid = []
    for d in DIRECTIONS:
        endpoint = get_ray_endpoint(core_pos, d, width, height)
        if not ct.is_in_vision(endpoint):
            valid.append((d, endpoint))
    return valid

def pick_three_directions(core_pos, width, height, valid_dirs):
    if len(valid_dirs) <= 3:
        return valid_dirs

    center = Position(width // 2, height // 2)
    half_w, half_h = width // 2, height // 2
    max_dist_sq = half_w * half_w + half_h * half_h

    best_triplet = (valid_dirs[0], valid_dirs[1], valid_dirs[2])
    best_score = -1

    for i in range(len(valid_dirs)):
        for j in range(i + 1, len(valid_dirs)):
            for k in range(j + 1, len(valid_dirs)):
                sep01 = dir_distance(valid_dirs[i][0], valid_dirs[j][0])
                sep02 = dir_distance(valid_dirs[i][0], valid_dirs[k][0])
                sep12 = dir_distance(valid_dirs[j][0], valid_dirs[k][0])

                # product of pairwise separations: rewards balanced spread
                # e.g. (3,3,2)->18 beats "T" shape (2,2,4)->16
                spread = sep01 * sep02 * sep12

                # center closeness: best of the 3 endpoints (0 to 1)
                best_closeness = max(
                    1.0 - valid_dirs[i][1].distance_squared(center) / max_dist_sq,
                    1.0 - valid_dirs[j][1].distance_squared(center) / max_dist_sq,
                    1.0 - valid_dirs[k][1].distance_squared(center) / max_dist_sq,
                )

                # spread ranges 0-64 (max 4*4*4), closeness 0-1
                score = spread * 10 + best_closeness * 30

                if score > best_score:
                    best_score = score
                    best_triplet = (valid_dirs[i], valid_dirs[j], valid_dirs[k])

    return list(best_triplet)

def choose_spawn_plan(ct: Controller, my_pos: Position):
    valid_dirs = get_valid_directions(ct, my_pos, map_mod.width, map_mod.height)
    rotational_core_dir = my_pos.direction_to(map_mod.get_symmetric_pos(my_pos, Symmetry.ROTATE))

    if len(valid_dirs) == 0:
        return random.sample(DIRECTIONS, 3)

    chosen = pick_three_directions(my_pos, map_mod.width, map_mod.height, valid_dirs)
    return [d for (d, _) in chosen]

def draw_spawn_plan(ct: Controller, my_pos: Position, spawn_plan, width: int, height: int) -> None:
    for d in spawn_plan:
        endpoint = get_ray_endpoint(my_pos, d, width, height)
        ct.draw_indicator_line(my_pos, endpoint, 0, 255, 0)

def should_spawn(player, ct: Controller) -> bool:
    builder_cost = ct.get_builder_bot_cost()[0]
    bridge_cost = ct.get_bridge_cost()[0]
    sees_enemy = len(vc.enemy_units) > 0
    current_round = ct.get_current_round()
    rounds_since_spawn = current_round - player.last_spawn_round

    # Spawn if no units left (core counts as 1, hence <=1)
    no_units = ct.get_unit_count() <= 1
    
    # Spawn some builder bots at start
    initial_units = player.num_spawned < SPAWN_INITIAL_COUNT + NUM_RUSHING
    
    # Spawn more bots once we've found titanium
    resource_intake = (
        player.num_spawned < SPAWN_LATER_COUNT and
        player.global_titanium - builder_cost > 200 and
        current_round - player.last_global_titanium_increase < 10
    )

    # Spawn if we have excess titanium
    wealthy = (
        rounds_since_spawn >= SPAWN_WEALTHY_INTERVAL and
        player.global_titanium >= bridge_cost * SPAWN_WEALTHY_BRIDGE_MULT and
        player.global_titanium >= builder_cost * SPAWN_WEALTHY_BUILDER_MULT and
        player.global_titanium >= SPAWN_WEALTHY_RESOURCE_THRESHOLD and 
        (player.num_spawned < SPAWN_WEALTHY_RESOURCE_THRESHOLD_NUM_BOTS or player.global_titanium >= SPAWN_WEALTHY_RESOURCE_THRESHOLD_EXTRA)
    )
    
    # Spawn if enemy spotted and no ally builder bots nearby
    threatened = (
        sees_enemy and
        (player.global_titanium >= builder_cost * SPAWN_THREATENED_BUILDER_MULT or player.health - player.prev_health < 0) and
        len(vc.ally_builder_bots) < 2
    )
    
    # Spawn if we haven't seen a builder bot in a while
    builder_drought = (
        current_round - player.last_spawn_round > 40
    )
    
    # if (current_round > 500):
    #     return True

    return (
        no_units or
        initial_units or
        resource_intake or
        wealthy or
        threatened or
        builder_drought
    )

def choose_enemy_facing_spawn(my_pos: Position, enemy_units) -> Position | None:
    if not enemy_units:
        return None

    nearest_enemy_pos = min(
        (pos for (_eid, _etype, pos) in enemy_units),
        key=lambda p: my_pos.distance_squared(p)
    )
    spawn_dir = my_pos.direction_to(nearest_enemy_pos)
    return my_pos.add(spawn_dir)

def choose_rushing_spawn(ct: Controller, my_pos: Position, predicted_core: Position) -> Position | None:
    spawn_dir = my_pos.direction_to(predicted_core)
    bbid = ct.get_tile_builder_bot_id(my_pos.add(spawn_dir))
    if bbid is not None:
        return my_pos.add(spawn_dir.rotate_left())
    return my_pos.add(spawn_dir)
