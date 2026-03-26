from cambc import Controller, Position, Direction, EntityType, Environment, GameError

from enum import Enum
import random
import sys

import map_info
import pathing
import comms

class Mode(Enum):
    EXPLORE_ATHENA = (100, 255, 100, "preliminary explore (athena)")
    EXPLORE = (0, 255, 0, "explore")
    BUILD_HARVESTER = (0, 180, 180, "build harvester")
    ROUTE = (255, 255, 0, "route to core")
    SABOTAGE = (200, 10, 10, "attack harvester")

    def __init__(self, r, g, b, desc):
        self.r = r
        self.g = g
        self.b = b
        self.desc = desc

mode = Mode.EXPLORE
indicator = []
blocked_ores = {}
defended_ores = set()
cardinal_dirs = [Direction.NORTH, Direction.SOUTH, Direction.EAST, Direction.WEST]

# explore state
explore_target = None
turns_since_last_explore_target = 0
target_ore = None
opponent_ore = None
sabotage_ore = None

#route state
routed_ore = None
ore_path = None
launcher_positions = None
route_idx = 0
launcher_idx = 0

rc = None
def init(c : Controller):
    global rc
    rc = c
    map_info.init(c)
    comms.init(c)
    pathing.init(c)
    pass

def run():
    global mode
    run_pre() # preliminary calculations
    print(f"CHECKING STATE: <span style='color: #{mode.r:02x}{mode.g:02x}{mode.b:02x}'>{mode.desc}</span>")
    globals()[f"check_{mode.name.lower()}"]()
    print(f"NEW STATE: <span style='color: #{mode.r:02x}{mode.g:02x}{mode.b:02x}'>{mode.desc}</span>")
    globals()[f"run_{mode.name.lower()}"]()
    # run_post() # cleanup

def generate_encoded_int(builder_id: int) -> int:
    if not (0 <= builder_id <= 31):
        builder_id %= 32
    
    # Step 1: rightmost 3 bits
    right_bits = random.randint(0, 6)  # 3 bits
    
    # Step 2: next 5 bits for builder_id
    builder_bits = builder_id & 0b11111  # ensure 5 bits
    
    # Step 3: next 9 bits random number
    random_bits = random.randint(0, 511)  # 9 bits
    
    # Combine everything
    encoded = (random_bits << (5 + 3)) | (builder_bits << 3) | right_bits
    return encoded

# invariant calculations
def run_pre():
    global target_ore, blocked_ores, sabotage_ore, opponent_ore
    map_info.update()
    if rc.get_hp() < rc.get_max_hp() and rc.can_heal(rc.get_position()):
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
                        for d in cardinal_dirs:
                            if d == Direction.CENTRE:
                                continue
                            adj = pos.add(d)
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
    # # pick a random empty tile surrounding the builder and place a marker
    # surrounding_tiles = []
    # for d in list(Direction):
    #     if d == Direction.CENTRE:
    #         continue
        
    #     pos = rc.get_position().add(d)
    #     try:
    #         if rc.can_place_marker(pos):
    #             surrounding_tiles.append(pos)
    #     except GameError:
    #         # position is off map
    #         pass

    # if surrounding_tiles:
    #     target_pos = random.choice(surrounding_tiles)
    #     value = generate_encoded_int(rc.get_id())
    #     rc.place_marker(target_pos, value)
    pass
def force_generate_explore_target():
    global explore_target, turns_since_last_explore_target
    print(" | Forcing new explore")
    turns_since_last_explore_target = 0
    random_x = random.randint(0, map_info.width - 1)
    random_y = random.randint(0, map_info.height - 1)
    explore_target = Position(random_x, random_y)

# check block
def check_explore_athena():
    pass

def check_explore():
    global mode, explore_target, turns_since_last_explore_target
    
    if opponent_ore:
        mode = Mode.SABOTAGE
    if target_ore:
        mode = Mode.BUILD_HARVESTER
    
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
        if building_id and (rc.get_entity_type(building_id) == EntityType.HARVESTER or rc.get_team(building_id) != rc.get_team()):
            target_ore = None
            mode = Mode.EXPLORE
            return
    if pathing.calculate_path(target_ore):
        print(" | Path to mine found")
    else:
        print(" | Can't reach mine")
        current_distance = rc.get_position().distance_squared(target_ore)
        if current_distance > 2:
            global blocked_ores
            print(f" | Blocking mine at {target_ore}")
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

    # loop until we find a target we can path to and move.
    moved = False
    attempts = 0
    while not moved and attempts < 1:
        if pathing.move_to(explore_target):
            moved = True
        else:
            force_generate_explore_target() # generates new target for next attempt
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
        print(" | Sabotaging titanium ore")
        if rc.can_build_barrier(sabotage_ore):
            rc.build_barrier(sabotage_ore)
            return

    adjacent_tiles = [target_ore.add(d) for d in cardinal_dirs]
    
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
            if rc.get_entity_type(building_id) in [EntityType.BARRIER, EntityType.HARVESTER, EntityType.LAUNCHER, EntityType.CONVEYOR, EntityType.BRIDGE, EntityType.SENTINEL] and rc.get_team(building_id) == rc.get_team():
                is_barrier = True
            if rc.get_team(building_id) != rc.get_team():
                print(" | Opponent sabotaged")
                opponent_sabotaged = True
        
        if not is_barrier:
            perimeter_secure = False

    if opponent_sabotaged:
        if rc.get_tile_building_id(target_ore) and rc.get_tile_building_id(target_ore) != EntityType.BARRIER and rc.can_destroy(target_ore):
            rc.destroy(target_ore)
        if rc.can_build_barrier(target_ore):
            global blocked_ores
            rc.build_barrier(target_ore)
            target_ore = None
            blocked_ores[target_ore] = rc.get_current_round() + 2000
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
                        if rc.get_entity_type(building_id) in [EntityType.BARRIER, EntityType.HARVESTER, EntityType.LAUNCHER, EntityType.CONVEYOR, EntityType.BRIDGE, EntityType.SENTINEL]:
                            is_our_barrier = True
                    except GameError: pass

                if not is_our_barrier:
                    # This tile needs a barrier. Can we build/destroy?
                    if building_id and rc.get_team(building_id) == rc.get_team():
                        if rc.can_destroy(pos):
                            rc.destroy(pos)
                    if rc.can_build_barrier(pos):
                        rc.build_barrier(pos)
                        return
        
        print(" | Moving to mine")
        if (target_ore.distance_squared(rc.get_position()) <= rc.get_vision_radius_sq()):
            if not rc.is_tile_passable(target_ore) and rc.get_entity_type(rc.get_tile_building_id(target_ore)) != EntityType.HARVESTER and rc.can_destroy(target_ore):
                rc.destroy(target_ore)
        pathing.execute_path()
        return

    # State 2: Perimeter is secure (or all walls). Let's build the harvester.
    else:
        # If we are on the ore, move off.
        print(" | Perimeter done")
        if rc.get_position() == target_ore:
            building_id = rc.get_tile_building_id(target_ore)
            if building_id and rc.get_team(building_id) != rc.get_team():
                rc.fire()
                return
            moved = False
            for d in random.sample(list(Direction), len(list(Direction))):
                if rc.can_move(d):
                    rc.move(d)
                    moved = True
            # nowhere to move
            if not moved:
                for d in random.sample(list(Direction), len(list(Direction))):
                    if map_info.is_tile_empty(rc.get_position().add(d)):
                        pathing.move(d)
                        moved = True
                    

        # If adjacent to the ore, clear it and build.
        if rc.get_position().distance_squared(target_ore) <= 2:
            building_id = rc.get_tile_building_id(target_ore)
            if building_id and rc.get_team(building_id) == rc.get_team() and rc.get_entity_type(building_id) != EntityType.HARVESTER:
                if rc.can_destroy(target_ore):
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
                        print(f" | Not enough titanium for harvester.")
                        print(f" | Estimate: {current_titanium} / {required_titanium}")
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
            print(" | Moving to harvester loc")
            if (target_ore.distance_squared(rc.get_position()) <= rc.get_vision_radius_sq()):
                if not rc.is_tile_passable(target_ore) and rc.get_entity_type(rc.get_tile_building_id(target_ore)) != EntityType.HARVESTER and rc.can_destroy(target_ore):
                    rc.destroy(target_ore)
            pathing.execute_path()

def check_route():
    global ore_path, launcher_positions, route_idx, mode, launcher_idx
    if not ore_path:
        ore_path = pathing.calculate_conveyor_path(routed_ore)
        route_idx = 0
        launcher_idx = 0
        if ore_path:
            launcher_positions = pathing.calculate_launcher_positions(ore_path, routed_ore)
    if ore_path and route_idx >= len(ore_path)-1 and launcher_idx >= len(launcher_positions):
        mode = Mode.EXPLORE
        ore_path = None
        launcher_positions = None

def run_route():
    global route_idx, launcher_idx, ore_path, launcher_positions
    print("route idx", route_idx, "launcher idx", launcher_idx)
    if ore_path:
        launcher_positions = pathing.calculate_launcher_positions(ore_path, routed_ore)
        launcher_idx = 0
        if route_idx < len(ore_path)-1 and pathing.moves_through_impassible(ore_path, map_info.get_avoid(False, False, False, True)):
            new_path = pathing.calculate_conveyor_path(ore_path[route_idx], True)
            if new_path:
                ore_path = new_path
                route_idx = 0
        for i in range(len(ore_path)-1):
            rc.draw_indicator_line(ore_path[i], ore_path[i+1], 0, 255, 0)
            rc.draw_indicator_dot(ore_path[i], 0, 255, 0)
        for i in launcher_positions:
            rc.draw_indicator_dot(i, 255, 0, 0)
        if launcher_idx < len(launcher_positions):
            launcher = launcher_positions[launcher_idx]
            place = True
            nearby_conv = None
            for i in range(len(ore_path)-1):
                p = ore_path[i]
                if p.distance_squared(launcher) <= 2:
                    nearby_conv = p
                    if route_idx <= i:
                        place = False

            if place:
                if launcher in map_info.building and map_info.building[launcher] and map_info.building[launcher].team != rc.get_team():
                    pathing.move_to(launcher)
                    if rc.get_position() == launcher and rc.can_fire(launcher):
                        rc.fire(launcher)
                elif nearby_conv:
                    pathing.move_to(nearby_conv)
                    if rc.can_destroy(launcher):
                        rc.destroy(launcher)
                    id = rc.get_tile_building_id(rc.get_position())
                    if rc.can_build_launcher(launcher):
                        rc.build_launcher(launcher)
                        launcher_idx += 1
                return
        if route_idx < len(ore_path)-1:
            to_build = ore_path[route_idx]
            bridge = ore_path[route_idx].distance_squared(ore_path[route_idx+1]) > 1
            dir = ore_path[route_idx].direction_to(ore_path[route_idx+1])
            if to_build.distance_squared(rc.get_position()) <= 2:
                if to_build == rc.get_position():
                    id = rc.get_tile_building_id(rc.get_position())
                    if id and rc.get_team(id) != rc.get_team() and rc.can_fire(rc.get_position()):
                        rc.fire(rc.get_position())
                if rc.can_destroy(to_build):
                    rc.destroy(to_build)
                if bridge and rc.can_build_bridge(to_build, ore_path[route_idx+1]):
                    rc.build_bridge(to_build, ore_path[route_idx+1])
                    route_idx += 1
                elif not bridge and rc.can_build_conveyor(to_build, dir):
                    rc.build_conveyor(to_build, dir)
                    route_idx += 1
            next = ore_path[route_idx]
            pathing.move_to(next)
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

    try:
        building_type = rc.get_entity_type(building_id)
        building_team = rc.get_team(building_id)

        if not (building_type == EntityType.HARVESTER and building_team != rc.get_team()):
            mode = Mode.EXPLORE
            opponent_ore = None
            return
    except GameError:
        mode = Mode.EXPLORE
        opponent_ore = None
        return

    # we already put a turret
    for d in Direction:
        if d == Direction.CENTRE:
            continue

        adj = opponent_ore.add(d)

        # Only consider tiles on map AND in vision
        if not map_info.is_on_map(adj):
            continue
        if rc.get_position().distance_squared(adj) > rc.get_vision_radius_sq():
            continue

        try:
            building_id = rc.get_tile_building_id(adj)
            if building_id is None:
                continue
            if (rc.get_entity_type(building_id) == EntityType.SENTINEL and
                rc.get_team(building_id) == rc.get_team()):
                # Successfully sabotaged → leave
                mode = Mode.EXPLORE
                opponent_ore = None
                defended_ores.add(opponent_ore)
                blocked_ores[opponent_ore] = rc.get_current_round() + 100
                return
        except GameError:
            # Safety: shouldn't happen now, but skip just in case
            continue

def run_sabotage():
    global opponent_ore, mode, defended_ores
    
    adjacent_tiles = []
    for d in cardinal_dirs:
        adj = opponent_ore.add(d)
        if map_info.is_on_map(adj):
            adjacent_tiles.append(adj)

    empty_tile = None
    for pos in adjacent_tiles:
        if rc.get_position().distance_squared(pos) <= rc.get_vision_radius_sq():
            if map_info.is_tile_empty(pos):
                empty_tile = pos
                break

    # case 1, empty tile exists
    if empty_tile:
        rc.draw_indicator_dot(empty_tile, mode.r, mode.g, mode.b)
        dist_sq = rc.get_position().distance_squared(empty_tile)

        # If we're standing on it, move off
        if rc.get_position() == empty_tile and not rc.get_tile_building_id(empty_tile):
            for d in random.sample(list(Direction), len(list(Direction))):
                if rc.can_move(d):
                    rc.move(d)
                    break

        # Move toward it if not close enough
        if dist_sq > 2:
            pathing.move_to(empty_tile)

        # We are within distance ≤ 2 → try placing turret
        direction = map_info.best_sentinel_dir(empty_tile)
        if direction:
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
            pathing.move_to(passable_tile)

        # We're on it → destroy or fire
        if rc.can_destroy(passable_tile):
            rc.destroy(passable_tile)
        elif rc.get_position() == passable_tile:
            if rc.can_fire(rc.get_position()):
                rc.fire(rc.get_position())

        return