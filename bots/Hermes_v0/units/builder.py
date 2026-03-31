from cambc import Controller, Position, Direction, EntityType, Environment, GameError

from enum import Enum
import random
import sys

import map_info
from pathing import Pathing
import comms


class Mode(Enum):
    EXPLORE = (100, 100, 255, "explore")
    BUILD_HARVESTER = (0, 180, 180, "build harvester")
    ROUTE = (255, 255, 0, "route to core")
    def __init__(self, r, g, b, desc):
        self.r = r
        self.g = g
        self.b = b
        self.desc = desc
        
def log(text : str):
    print(f" <span style='color: #{mode.r:02x}{mode.g:02x}{mode.b:02x}'>|</span> {text}")

mode = Mode.EXPLORE
blocked_ores = {}
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

def run_pre():
    map_info.update()

def run_post():
    pass


def force_generate_explore_target():
    global explore_target, turns_since_last_explore_target
    turns_since_last_explore_target = 0

    for _ in range(10):  # slightly more aggressive
        random_x = random.randint(0, map_info.width - 1)
        random_y = random.randint(0, map_info.height - 1)
        if map_info.ground[random_x][random_y] is None:
            explore_target = Position(random_x, random_y)
            return

    # If no empty tile found after 100 tries, fallback to completely random
    random_x = random.randint(0, map_info.width - 1)
    random_y = random.randint(0, map_info.height - 1)
    explore_target = Position(random_x, random_y)
def update_target_ore():
    global target_ore
    my_pos = rc.get_position()
    claimed = comms.decode_claim()
    for pos, turn, id in claimed:
        if id == rc.get_id()&comms._ID_MASK:
            continue
        blocked_ores[pos] = max(blocked_ores.get(pos, 0), turn+10)
        for dir in cardinal_dirs:
            new_pos = pos.add(dir)
            if not map_info.in_bounds(new_pos):
                continue
            blocked_ores[new_pos] = max(blocked_ores.get(new_pos, 0), turn+10)
    prev_target_ore = target_ore
    for pos in rc.get_nearby_tiles():
        if map_info.ground[pos.x][pos.y] == Environment.ORE_TITANIUM:
            if not target_ore or my_pos.distance_squared(pos) < my_pos.distance_squared(target_ore) or pos == prev_target_ore:
                if map_info.building[pos.x][pos.y]:
                    if map_info.building[pos.x][pos.y].team == rc.get_team() and map_info.building[pos.x][pos.y].type == EntityType.HARVESTER:
                        if pos == prev_target_ore:
                            prev_target_ore = None
                        continue
                    if map_info.building[pos.x][pos.y].team != rc.get_team():
                        if pos == prev_target_ore:
                            prev_target_ore = None
                        continue
                if pos in blocked_ores and blocked_ores[pos] > rc.get_current_round():
                    if pos == prev_target_ore:
                        prev_target_ore = None
                    continue
                target_ore = pos
    if prev_target_ore:
        target_ore = prev_target_ore
def check_explore():
    global mode, explore_target, turns_since_last_explore_target

    my_pos = rc.get_position()
    update_target_ore()
    if target_ore:
        mode = Mode.BUILD_HARVESTER
        return

    if explore_target and my_pos.distance_squared(explore_target) <= 18:
        force_generate_explore_target()

    if turns_since_last_explore_target > (rc.get_map_width() + rc.get_map_height()) * 2:
        force_generate_explore_target()


def check_build_harvester():
    global mode, target_ore
    update_target_ore()
    if not target_ore:
        mode = Mode.EXPLORE
        return
    if nav.calculate_path(target_ore) == []:
        blocked_ores[target_ore] = rc.get_current_round() + 150
        target_ore = None
        mode = Mode.EXPLORE
        return


def run_explore():
    global explore_target, turns_since_last_explore_target

    if explore_target is None:
        force_generate_explore_target()

    if rc.get_global_resources()[0] < rc.get_harvester_cost()[0]:
        return

    # loop until we find a target we can path to and move.
    if nav.move_to(explore_target) == False:
        force_generate_explore_target()  # generates new target for next attempt

    turns_since_last_explore_target += 1

    if explore_target:
        rc.draw_indicator_line(rc.get_position(), explore_target, mode.r, mode.g, mode.b)


def run_build_harvester():
    global mode, target_ore, blocked_ores
    if target_ore is None or rc.get_global_resources()[0] < rc.get_harvester_cost()[0]*2 + rc.get_barrier_cost()[0]*8:
        mode = Mode.EXPLORE
        return


    adjacent_tiles = [Position(target_ore.x + dx, target_ore.y + dy) for _, (dx, dy) in CARDINAL_DELTAS]

    perimeter_secure = True
    wall_count = 0
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


        if not is_barrier:
            perimeter_secure = False
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
                    
                    my_core = map_info.my_core
                    manhattan_dist = abs(target_ore.x - my_core.x) + abs(target_ore.y - my_core.y)
                    harvester_cost = rc.get_harvester_cost()[0]
                    conveyor_cost = rc.get_conveyor_cost()[0]
                    scale = 1
                    expected_finish = scale * (harvester_cost + (manhattan_dist - 3) * conveyor_cost) < rc.get_global_resources()[0]
                    if rc.can_build_barrier(pos):
                        rc.build_barrier(pos)
                        return

        if (target_ore.distance_squared(rc.get_position()) <= rc.get_vision_radius_sq()):
            if not rc.is_tile_passable(target_ore) and rc.get_entity_type(
                    rc.get_tile_building_id(target_ore)) != EntityType.HARVESTER and rc.can_destroy(target_ore):
                log("destroy3 " + str(target_ore))
                rc.destroy(target_ore)
        nav.move_to(target_ore)
        if rc.can_place_marker(target_ore):
            rc.place_marker(target_ore, comms.encode_claim(target_ore))
        else:
            for dir in cardinal_dirs:
                if rc.can_place_marker(rc.get_position().add(dir)):
                    rc.place_marker(rc.get_position().add(dir), comms.encode_claim(target_ore))
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
                    scale = 1.0

                    required_titanium = scale * (harvester_cost + (manhattan_dist - 3) * conveyor_cost)
                    current_titanium = rc.get_global_resources()[0]

                    if current_titanium < required_titanium:
                        blocked_ores[target_ore] = rc.get_current_round() + 50
                        target_ore = None
                        mode = Mode.EXPLORE
                        return

                if rc.can_build_harvester(target_ore):
                    rc.build_harvester(target_ore)
                    global routed_ore, ore_path
                    routed_ore = target_ore
                    target_ore = None
                    mode = Mode.ROUTE
                    ore_path = []
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
    # print(ore_path)
    if not ore_path:
        ore_path = ore_nav.calculate_conveyor_path(routed_ore)
        if ore_path == []:
            mode = Mode.EXPLORE
            return
        route_idx = 0
    else:
        if route_idx < len(ore_path)-1:
            next_path = ore_nav.calculate_conveyor_path(ore_path[route_idx], ore_path[:route_idx], True)
            if next_path:
                ore_path = ore_path[:route_idx] + next_path
    if ore_path:
        launcher_position = ore_nav.calculate_launcher_position(ore_path, routed_ore)
    if ore_path and route_idx >= len(ore_path) - 1 and not launcher_position:
        mode = Mode.EXPLORE
        ore_path = None


def run_route():
    global route_idx, ore_path, launcher_position, mode
    if ore_path:
        if launcher_position:
            place = True
            nearby_conv = None
            for i in range(len(ore_path) - 1):
                p = ore_path[i]
                if p.distance_squared(launcher_position) <= 2:
                    nearby_conv = p
                    if route_idx <= i:
                        place = False

            if place:
                if map_info.building[launcher_position.x][launcher_position.y] and map_info.building[launcher_position.x][launcher_position.y].team != rc.get_team():
                    if nav.move_to(launcher_position) == False:
                        mode = Mode.EXPLORE
                        return
                    if rc.get_position() == launcher_position and rc.can_fire(launcher_position):
                        rc.fire(launcher_position)
                elif nearby_conv:
                    if nav.move_to(nearby_conv) == False:
                        mode = Mode.EXPLORE
                        return
                    if rc.can_destroy(launcher_position):
                        log("destroy6 " + str(launcher_position))
                        rc.destroy(launcher_position)
                    id = rc.get_tile_building_id(rc.get_position())
                    if rc.can_build_launcher(launcher_position):
                        rc.build_launcher(launcher_position)
                return
        if route_idx < len(ore_path) - 1:
            
            to_build = ore_path[route_idx]
            next = ore_path[route_idx + 1]
            bridge = to_build.distance_squared(next) > 1
            dir = to_build.direction_to(next)
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
            if route_idx < len(ore_path) - 1:
                if nav.move_to(ore_path[route_idx]) == False:
                    mode = Mode.EXPLORE
                    return
                

