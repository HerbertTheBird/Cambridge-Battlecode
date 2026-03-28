from cambc import Controller, Position, Direction, EntityType, Environment, GameError

from enum import Enum
import random
import sys

import map_info
from pathing import Pathing
import comms


class Mode(Enum):
    EXPLORE = (0, 255, 0, "explore")
    BUILD_HARVESTER = (0, 180, 180, "build harvester")
    ROUTE = (255, 255, 0, "route to core")
    SABOTAGE = (200, 10, 10, "attack harvester")

    def __init__(self, r, g, b, desc):
        self.r = r
        self.g = g
        self.b = b
        self.desc = desc
        
def log(text : str):
    print(f" <span style='color: #{mode.r:02x}{mode.g:02x}{mode.b:02x}'>|</span> {text}")

mode = Mode.EXPLORE
indicator = []
blocked_ores = {}
defended_ores = set()
cardinal_dirs = [Direction.NORTH, Direction.SOUTH, Direction.EAST, Direction.WEST]
all_dirs = list(Direction)
nav = None
ore_nav = None

# Cache deltas directly to bypass `Enum` and `.add()` overhead
ALL_DIRS_DELTAS = [(d, d.delta()) for d in all_dirs]
CARDINAL_DELTAS = [(d, d.delta()) for d in cardinal_dirs]

OUR_BUILDINGS = {EntityType.BARRIER, EntityType.HARVESTER, EntityType.LAUNCHER,
                 EntityType.CONVEYOR, EntityType.BRIDGE, EntityType.SENTINEL}

# explore state
explore_target = None
turns_since_last_explore_target = 0
target_ore = None
opponent_ore = None
sabotage_ore = None

# route state
routed_ore = None
ore_path = None
launcher_position = None
route_idx = 0

rc = None

MODE_ACTIONS = None


def init(c: Controller):
    global rc, MODE_ACTIONS, nav, ore_nav
    rc = c
    map_info.init(c)
    comms.init(c)
    nav = Pathing(c)
    ore_nav = Pathing(c)


def run():
    global mode
    run_pre()  # preliminary calculations
    print(f"CHECK STATE: <span style='color: #{mode.r:02x}{mode.g:02x}{mode.b:02x}'>{mode.desc}</span>")
    globals()[f"check_{mode.name.lower()}"]()
    log(f"check runtime: {rc.get_cpu_time_elapsed()}")
    print(f"EXEC. STATE: <span style='color: #{mode.r:02x}{mode.g:02x}{mode.b:02x}'>{mode.desc}</span>")
    globals()[f"run_{mode.name.lower()}"]()
    log(f"exec. runtime: {rc.get_cpu_time_elapsed()}")
    run_post()  # cleanup


# invariant calculations
def run_pre():
    global target_ore, blocked_ores, sabotage_ore, opponent_ore
    map_info.update()
    if rc.can_heal(rc.get_position()):
        rc.heal(rc.get_position())
    # Clean up expired blocks
    current_round = rc.get_current_round()
    for ore, unblock_round in list(blocked_ores.items()):
        if current_round >= unblock_round:
            del blocked_ores[ore]

    if map_info.my_core is None:
        return

    closest_ore = None
    min_dist_sq = float('inf')
    min_dist_sq_sabotage = float('inf')
    opponent_ore = None
    min_dist_sq_opponent = float('inf')

    # Find all visible titanium ores without an allied harvester on them
    nearby_units = rc.get_nearby_units(dist_sq=rc.get_vision_radius_sq())

    allied_builders = []
    for uid in nearby_units:
        if rc.get_entity_type(uid) == EntityType.BUILDER_BOT and rc.get_team(uid) == rc.get_team():
            allied_builders.append(uid)
            
    def am_closest_builder(pos):
        my_dist = rc.get_position().distance_squared(pos)

        for uid in allied_builders:
            try:
                other_pos = rc.get_position(uid)
                if other_pos is None:
                    continue

                if other_pos.distance_squared(pos) < my_dist:
                    return False
            except GameError:
                pass

        return True
    
    for pos in rc.get_nearby_tiles():
        if pos in blocked_ores:
            continue

        env = rc.get_tile_env(pos)

        if env == Environment.ORE_TITANIUM:
            building_id = rc.get_tile_building_id(pos)

            blocked = False
            occupied_opponent = False
            if building_id is not None:
                try:
                    building_type = rc.get_entity_type(building_id)
                    building_team = rc.get_team(building_id)
                    if building_type == EntityType.HARVESTER and building_team == rc.get_team() or building_type == EntityType.BARRIER:
                        blocked = True
                    if building_type != EntityType.MARKER and building_team != rc.get_team():
                        occupied_opponent = True
                except GameError:
                    pass

            if building_id is not None and pos not in defended_ores:
                try:
                    building_type = rc.get_entity_type(building_id)
                    building_team = rc.get_team(building_id)

                    if building_type == EntityType.HARVESTER and building_team != rc.get_team():
                        # Check for at least one passable adjacent tile
                        has_passable_adjacent = False
                        for d, (dx, dy) in CARDINAL_DELTAS:
                            if d == Direction.CENTRE:
                                continue
                            adj = Position(pos.x + dx, pos.y + dy)
                            try:
                                if map_info.is_on_map(adj) and rc.is_tile_passable(adj):
                                    has_passable_adjacent = True
                                    break
                            except GameError:
                                pass

                        if has_passable_adjacent:
                            dist_sq = pos.distance_squared(rc.get_position())
                            if dist_sq < min_dist_sq_opponent:
                                min_dist_sq_opponent = dist_sq
                                opponent_ore = pos
                except GameError:
                    pass

            if not blocked and not occupied_opponent:
                if am_closest_builder(pos):
                    dist_sq = pos.distance_squared(map_info.my_core)
                    if dist_sq < min_dist_sq:
                        min_dist_sq = dist_sq
                        closest_ore = pos

            dist_sq_sabotage = pos.distance_squared(rc.get_position())
            if dist_sq_sabotage < min_dist_sq_sabotage:
                min_dist_sq_sabotage = dist_sq_sabotage
                sabotage_ore = pos

    # Update target_ore based on what we can see right now
    if closest_ore is not None:
        if target_ore is None:
            target_ore = closest_ore
        else:
            current_target_dist_sq = target_ore.distance_squared(map_info.my_core)
            if min_dist_sq < current_target_dist_sq:
                target_ore = closest_ore

    if target_ore:
        rc.draw_indicator_dot(target_ore, 255, 255, 0)


def run_post():
    pass


def force_generate_explore_target():
    global explore_target, turns_since_last_explore_target
    turns_since_last_explore_target = 0

    for _ in range(2):  # slightly more aggressive
        random_x = random.randint(0, map_info.width - 1)
        random_y = random.randint(0, map_info.height - 1)
        if map_info.ground[random_x][random_y] is None:
            explore_target = Position(random_x, random_y)
            return

    # If no empty tile found after 100 tries, fallback to completely random
    random_x = random.randint(0, map_info.width - 1)
    random_y = random.randint(0, map_info.height - 1)
    explore_target = Position(random_x, random_y)


# check block
def check_explore_athena():
    pass


def check_explore():
    global mode, explore_target, turns_since_last_explore_target, defended_ores

    if opponent_ore and opponent_ore not in defended_ores:
        mode = Mode.SABOTAGE
        return

    if target_ore:
        mode = Mode.BUILD_HARVESTER
        return

    if explore_target and rc.get_position().distance_squared(explore_target) <= 18:
        force_generate_explore_target()

    if turns_since_last_explore_target > (rc.get_map_width() + rc.get_map_height()) * 2:
        force_generate_explore_target()


def check_build_harvester():
    global mode, target_ore

    if not target_ore:
        mode = Mode.EXPLORE
    if (target_ore.distance_squared(rc.get_position())) <= rc.get_vision_radius_sq():
        building_id = rc.get_tile_building_id(target_ore)
        if building_id and (
                rc.get_entity_type(building_id) == EntityType.HARVESTER or rc.get_team(building_id) != rc.get_team()):
            target_ore = None
            mode = Mode.EXPLORE
            return
    if nav.calculate_path(target_ore):
        pass
    else:
        current_distance = rc.get_position().distance_squared(target_ore)
        if current_distance > 2:
            global blocked_ores
            blocked_ores[target_ore] = rc.get_current_round() + 150
            target_ore = None
            mode = Mode.EXPLORE
            return


# run block
def run_explore_athena():
    pass


def run_explore():
    global explore_target, turns_since_last_explore_target

    if explore_target is None:
        force_generate_explore_target()

    if rc.get_global_resources()[0] < rc.get_bridge_cost()[0]:
        return

    # loop until we find a target we can path to and move.
    moved = False
    attempts = 0
    while not moved and attempts < 1:
        if nav.move_to(explore_target):
            moved = True
        else:
            force_generate_explore_target()  # generates new target for next attempt
        attempts += 1

    turns_since_last_explore_target += 1

    if explore_target:
        rc.draw_indicator_line(rc.get_position(), explore_target, mode.r, mode.g, mode.b)


def run_build_harvester():
    global mode, target_ore, blocked_ores

    if target_ore is None:
        mode = Mode.EXPLORE
        return

    if sabotage_ore and sabotage_ore != target_ore:
        if rc.can_build_barrier(sabotage_ore):
            rc.build_barrier(sabotage_ore)
            return

    adjacent_tiles = [Position(target_ore.x + dx, target_ore.y + dy) for _, (dx, dy) in CARDINAL_DELTAS]

    perimeter_secure = True
    wall_count = 0
    opponent_sabotaged = False
    for pos in adjacent_tiles:
        if (pos.distance_squared(rc.get_position()) > rc.get_vision_radius_sq()):
            perimeter_secure = False
            continue

        if not map_info.is_on_map(pos) or rc.get_tile_env(pos) == Environment.WALL:
            wall_count += 1
            continue

        building_id = rc.get_tile_building_id(pos)
        is_barrier = False
        if building_id is not None:
            if rc.get_entity_type(building_id) in OUR_BUILDINGS and rc.get_team(building_id) == rc.get_team():
                is_barrier = True
            if rc.get_team(building_id) != rc.get_team():
                opponent_sabotaged = True

        if not is_barrier:
            perimeter_secure = False

    if opponent_sabotaged:
        if target_ore.distance_squared(rc.get_position()) < rc.get_vision_radius_sq():
            if rc.get_tile_building_id(target_ore) and rc.get_tile_building_id(
                    target_ore) != EntityType.BARRIER and rc.can_destroy(target_ore) and rc.get_entity_type(
                    building_id) != EntityType.SENTINEL:
                log("destroy1 " + str(target_ore))
                rc.destroy(target_ore)
            if rc.can_build_barrier(target_ore):
                global blocked_ores
                rc.build_barrier(target_ore)
                blocked_ores[target_ore] = rc.get_current_round() + 150
                target_ore = None
                mode = Mode.EXPLORE
            return

    # State 1: Perimeter is not secure. Let's build barriers.
    if not perimeter_secure and wall_count < 4:
        # Try to build from our current position if we are close enough to an insecure spot.
        for pos in adjacent_tiles:
            # Check if this tile needs a barrier and if we are next to it.
            if rc.get_position().distance_squared(pos) <= 2:
                # Check if it needs a barrier
                is_wall = not map_info.is_on_map(pos) or rc.get_tile_env(pos) == Environment.WALL
                if is_wall: continue

                building_id = rc.get_tile_building_id(pos)
                is_our_barrier = False
                if building_id:
                    try:
                        if rc.get_entity_type(building_id) in OUR_BUILDINGS:
                            is_our_barrier = True
                    except GameError:
                        pass

                if not is_our_barrier:
                    # This tile needs a barrier. Can we build/destroy?
                    if building_id and rc.get_team(building_id) == rc.get_team() and rc.get_entity_type(
                            building_id) != EntityType.SENTINEL:
                        if rc.can_destroy(pos):
                            log("destroy2 " + str(pos))
                            rc.destroy(pos)
                    if rc.can_build_barrier(pos):
                        rc.build_barrier(pos)
                        return

        if (target_ore.distance_squared(rc.get_position()) <= rc.get_vision_radius_sq()):
            if not rc.is_tile_passable(target_ore) and rc.get_entity_type(
                    rc.get_tile_building_id(target_ore)) != EntityType.HARVESTER and rc.can_destroy(target_ore):
                log("destroy3 " + str(target_ore))
                rc.destroy(target_ore)
        nav.execute_path()
        return

    # State 2: Perimeter is secure (or all walls). Let's build the harvester.
    else:
        # If we are on the ore, move off.
        if rc.get_position() == target_ore:
            building_id = rc.get_tile_building_id(target_ore)
            if building_id and rc.get_team(building_id) != rc.get_team():
                rc.fire()
                return
            moved = False
            for d in random.sample(all_dirs, len(all_dirs)):
                if rc.can_move(d):
                    rc.move(d)
                    moved = True
            # nowhere to move
            if not moved:
                my_pos = rc.get_position()
                for d in random.sample(all_dirs, len(all_dirs)):
                    dx, dy = d.delta()
                    if map_info.is_tile_empty(Position(my_pos.x + dx, my_pos.y + dy)):
                        nav.move(d)
                        moved = True

        # If adjacent to the ore, clear it and build.
        if rc.get_position().distance_squared(target_ore) <= 2:
            building_id = rc.get_tile_building_id(target_ore)
            if building_id and rc.get_team(building_id) == rc.get_team() and rc.get_entity_type(
                    building_id) != EntityType.HARVESTER:
                if rc.can_destroy(target_ore):
                    log("destroy4 " + str(target_ore))
                    rc.destroy(target_ore)

            if rc.get_tile_building_id(target_ore) is None:
                my_core = map_info.my_core
                if my_core:
                    manhattan_dist = abs(target_ore.x - my_core.x) + abs(target_ore.y - my_core.y)
                    harvester_cost = rc.get_harvester_cost()[0]
                    conveyor_cost = rc.get_conveyor_cost()[0]
                    scale = 1.1

                    required_titanium = scale * (harvester_cost + (manhattan_dist - 3) * conveyor_cost)
                    current_titanium = rc.get_global_resources()[0]

                    if current_titanium < required_titanium:
                        blocked_ores[target_ore] = rc.get_current_round() + 50
                        target_ore = None
                        mode = Mode.EXPLORE
                        return

                if rc.can_build_harvester(target_ore):
                    rc.build_harvester(target_ore)
                    global routed_ore
                    routed_ore = target_ore
                    target_ore = None
                    mode = Mode.ROUTE
                    return
        else:
            if (target_ore.distance_squared(rc.get_position()) <= rc.get_vision_radius_sq()):
                if not rc.is_tile_passable(target_ore) and rc.get_entity_type(
                        rc.get_tile_building_id(target_ore)) != EntityType.HARVESTER and rc.can_destroy(target_ore):
                    log("destroy5 " + str(target_ore))
                    rc.destroy(target_ore)
            nav.execute_path()


def check_route():
    global ore_path, launcher_position, route_idx, mode
    if not ore_path:
        ore_path = ore_nav.calculate_conveyor_path(routed_ore)
        route_idx = 0
    if ore_path:
        launcher_position = ore_nav.calculate_launcher_position(ore_path, routed_ore)
    if ore_path and route_idx >= len(ore_path) - 1 and not launcher_position:
        mode = Mode.EXPLORE
        ore_path = None


def run_route():
    global route_idx, ore_path, launcher_position
    if ore_path:
        for i in range(len(ore_path)):
            if route_idx == i:
                rc.draw_indicator_line(Position(i, -1), ore_path[i], 0, 0, 255)
            else:
                rc.draw_indicator_line(Position(i, -1), ore_path[i], 0, 255, 0)
        if route_idx < len(ore_path) - 1:
            new_path = ore_nav.calculate_conveyor_path(ore_path[route_idx], True)
            if new_path:
                ore_path = ore_path[:route_idx] + new_path
        # for i in range(len(ore_path)):
        #     if route_idx == i:
        #         rc.draw_indicator_line(Position(i, -1), ore_path[i], 0, 0, 255)
        #     else:
        #         rc.draw_indicator_line(Position(i, -1), ore_path[i], 0, 255, 0)
            # rc.draw_indicator_line(ore_path[i], ore_path[i + 1], 0, 255, 0)
            # rc.draw_indicator_dot(ore_path[i], 0, 255, 0)
        if launcher_position:
            launcher = launcher_position
            place = True
            nearby_conv = None
            for i in range(len(ore_path) - 1):
                p = ore_path[i]
                if p.distance_squared(launcher) <= 2:
                    nearby_conv = p
                    if route_idx <= i:
                        place = False

            if place:
                if map_info.building[launcher.x][launcher.y] and map_info.building[launcher.x][
                    launcher.y].team != rc.get_team():
                    nav.move_to(launcher)
                    if rc.get_position() == launcher and rc.can_fire(launcher):
                        rc.fire(launcher)
                elif nearby_conv and nearby_conv != launcher:
                    nav.move_to(nearby_conv)
                    if rc.can_destroy(launcher):
                        log("destroy6 " + str(launcher))
                        rc.destroy(launcher)
                    id = rc.get_tile_building_id(rc.get_position())
                    if rc.get_position() == launcher:
                        if route_idx < len(ore_path) - 1:
                            to_build = ore_path[route_idx]
                            bridge = ore_path[route_idx].distance_squared(ore_path[route_idx + 1]) > 1
                            dir = ore_path[route_idx].direction_to(ore_path[route_idx + 1])
                            if to_build.distance_squared(rc.get_position()) <= 2:
                                if to_build == rc.get_position():
                                    id = rc.get_tile_building_id(rc.get_position())
                                    if id and rc.get_team(id) != rc.get_team() and rc.can_fire(rc.get_position()):
                                        rc.fire(rc.get_position())
                                if rc.can_destroy(to_build):
                                    log("destroy7 " + str(to_build))
                                    rc.destroy(to_build)
                                if bridge and rc.can_build_bridge(to_build, ore_path[route_idx + 1]):
                                    rc.build_bridge(to_build, ore_path[route_idx + 1])
                                    route_idx += 1
                                elif not bridge and rc.can_build_conveyor(to_build, dir):
                                    rc.build_conveyor(to_build, dir)
                                    route_idx += 1
                            next = ore_path[route_idx]
                            if route_idx < len(ore_path) - 1:
                                nav.move_to(next)
                    if rc.can_build_launcher(launcher):
                        rc.build_launcher(launcher)
                return

        if route_idx < len(ore_path) - 1:
            
            to_build = ore_path[route_idx]
            bridge = ore_path[route_idx].distance_squared(ore_path[route_idx + 1]) > 1
            dir = ore_path[route_idx].direction_to(ore_path[route_idx + 1])
            if to_build.distance_squared(rc.get_position()) <= 2:
                if to_build == rc.get_position():
                    id = rc.get_tile_building_id(rc.get_position())
                    if id and rc.get_team(id) != rc.get_team() and rc.can_fire(rc.get_position()):
                        rc.fire(rc.get_position())
                if rc.can_destroy(to_build):
                    log("destroy8 " + str(to_build))
                    rc.destroy(to_build)
                if bridge and rc.can_build_bridge(to_build, ore_path[route_idx + 1]):
                    rc.build_bridge(to_build, ore_path[route_idx + 1])
                    route_idx += 1
                elif not bridge and rc.can_build_conveyor(to_build, dir):
                    rc.build_conveyor(to_build, dir)
                    route_idx += 1
            next = ore_path[route_idx]
            if route_idx < len(ore_path) - 1:
                nav.move_to(next)


def check_sabotage():
    global mode, opponent_ore, defended_ores

    # safety
    if opponent_ore is None:
        mode = Mode.EXPLORE
        return

    building_id = rc.get_tile_building_id(opponent_ore)

    # harvester died
    if building_id is None:
        mode = Mode.EXPLORE
        opponent_ore = None
        return

    building_type = rc.get_entity_type(building_id)
    building_team = rc.get_team(building_id)

    if not (building_type == EntityType.HARVESTER and building_team != rc.get_team()):
        mode = Mode.EXPLORE
        opponent_ore = None
        return

    # we already put a turret
    for d, (dx, dy) in CARDINAL_DELTAS:
        adj = Position(opponent_ore.x + dx, opponent_ore.y + dy)

        # Only consider tiles on map AND in vision
        if not map_info.is_on_map(adj):
            continue
        if rc.get_position().distance_squared(adj) > rc.get_vision_radius_sq():
            continue

        building_id = rc.get_tile_building_id(adj)
        if building_id is None:
            continue
        if (rc.get_entity_type(building_id) == EntityType.SENTINEL and rc.get_team(building_id) == rc.get_team()):
            # Successfully sabotaged → leave
            mode = Mode.EXPLORE
            defended_ores.add(opponent_ore)
            blocked_ores[opponent_ore] = rc.get_current_round() + 100
            opponent_ore = None
            return


def run_sabotage():
    global opponent_ore, mode, defended_ores

    my_pos = rc.get_position()
    for dx in range(-2, 3):
        for dy in range(-2, 3):
            candidate = Position(my_pos.x + dx, my_pos.y + dy)
            if not map_info.is_on_map(candidate):
                continue
            if my_pos.distance_squared(candidate) > 2:
                continue

            # Check all 4 cardinal neighbors of this candidate for enemy harvester
            adjacent_enemy_harvester = False
            for d, (n_dx, n_dy) in CARDINAL_DELTAS:
                neighbor = Position(candidate.x + n_dx, candidate.y + n_dy)
                if not map_info.is_on_map(neighbor):
                    continue
                neighbor_id = rc.get_tile_building_id(neighbor)
                if neighbor_id is not None and rc.get_entity_type(neighbor_id) == EntityType.HARVESTER:
                    if rc.get_team(neighbor_id) != rc.get_team():
                        adjacent_enemy_harvester = True
                        break

            if adjacent_enemy_harvester and rc.can_build_sentinel(candidate, Direction.NORTH):
                direction = map_info.best_sentinel_dir(candidate) or Direction.NORTH
                if rc.can_build_sentinel(candidate, direction):
                    rc.build_sentinel(candidate, direction)
                    rc.draw_indicator_dot(candidate, mode.r, mode.g, mode.b)
                    return  # override done, skip normal sabotage logic

    adjacent_tiles = []
    for d, (dx, dy) in CARDINAL_DELTAS:
        adj = Position(opponent_ore.x + dx, opponent_ore.y + dy)
        if map_info.is_on_map(adj):
            adjacent_tiles.append(adj)

    empty_tile = None
    for pos in adjacent_tiles:
        if rc.get_position().distance_squared(pos) <= rc.get_vision_radius_sq():
            if map_info.is_tile_empty(pos) or rc.can_destroy(pos):
                empty_tile = pos
                break

    # case 1, empty tile exists
    if empty_tile:
        rc.draw_indicator_dot(empty_tile, mode.r, mode.g, mode.b)
        dist_sq = rc.get_position().distance_squared(empty_tile)

        # If we're standing on it, move off
        if rc.get_position() == empty_tile and not rc.get_tile_building_id(empty_tile):
            for d in random.sample(all_dirs, len(all_dirs)):
                if rc.can_move(d):
                    rc.move(d)
                    break

        # Move toward it if not close enough
        if dist_sq > 2:
            nav.move_to(empty_tile)

        # We are within distance ≤ 2 → try placing turret
        dist_sq = rc.get_position().distance_squared(empty_tile)
        if dist_sq <= 2:
            direction = map_info.best_sentinel_dir(empty_tile)
            if direction:
                if rc.get_tile_building_id(empty_tile) and rc.get_entity_type(
                        rc.get_tile_building_id(empty_tile)) != EntityType.SENTINEL:
                    if rc.can_destroy(empty_tile):
                        log("destroy9 " + str(empty_tile))
                        rc.destroy(empty_tile)
                if rc.can_build_sentinel(empty_tile, direction):
                    rc.build_sentinel(empty_tile, direction)

        return

    # case 2, passable
    passable_tile = None
    for pos in adjacent_tiles:
        if rc.get_position().distance_squared(pos) <= rc.get_vision_radius_sq():
            if rc.is_tile_passable(pos):
                passable_tile = pos
                break

    if passable_tile:
        rc.draw_indicator_line(rc.get_position(), passable_tile, mode.r, mode.g, mode.b)
        # Move toward it
        if rc.get_position() != passable_tile:
            nav.move_to(passable_tile)

        # We're on it → destroy or fire
        if rc.can_destroy(passable_tile):
            log("destroy10 " + str(passable_tile))
            rc.destroy(passable_tile)
        elif rc.get_position() == passable_tile:
            if rc.can_fire(rc.get_position()):
                rc.fire(rc.get_position())

        return
