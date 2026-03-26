from cambc import Controller, Position, Direction, EntityType, Environment, GameError

from enum import Enum
import random
import sys

import map_info
import pathing

class Mode(Enum):
    EXPLORE_ATHENA = (100, 255, 100, "preliminary explore (athena)")
    EXPLORE = (0, 255, 0, "explore")
    BUILD_HARVESTER = (0, 180, 180, "build harvester")
    ROUTE = (255, 255, 0, "route to core")

    def __init__(self, r, g, b, desc):
        self.r = r
        self.g = g
        self.b = b
        self.desc = desc

mode = Mode.EXPLORE
indicator = []

# explore state
explore_target = None
turns_since_last_explore_target = 0
target_ore = None

rc = None
def init(c : Controller):
    global rc
    rc = c
    map_info.init(c)
    pathing.init(c)
    pass

def run():
    global mode
    run_pre() # preliminary calculations
    print(f"CHECKING STATE: <span style='color: #{mode.r:02x}{mode.g:02x}{mode.b:02x}'>{mode.desc}</span>")
    globals()[f"check_{mode.name.lower()}"]()
    print(f"NEW STATE: <span style='color: #{mode.r:02x}{mode.g:02x}{mode.b:02x}'>{mode.desc}</span>")
    globals()[f"run_{mode.name.lower()}"]()
    run_post() # cleanup

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
    global target_ore
    map_info.update()

    if map_info.my_core is None:
        return

    closest_ore = None
    min_dist_sq = float('inf')

    # Find all visible titanium ores without an allied harvester on them
    for pos in rc.get_nearby_tiles():
        env = rc.get_tile_env(pos)
        if env == Environment.ORE_TITANIUM:
            building_id = rc.get_tile_building_id(pos)
            
            has_allied_harvester = False
            if building_id is not None:
                try:
                    building_type = rc.get_entity_type(building_id)
                    building_team = rc.get_team(building_id)
                    if building_type == EntityType.HARVESTER and building_team == rc.get_team():
                        has_allied_harvester = True
                except GameError:
                    pass

            if not has_allied_harvester:
                dist_sq = pos.distance_squared(map_info.my_core)
                if dist_sq < min_dist_sq:
                    min_dist_sq = dist_sq
                    closest_ore = pos

    # Update target_ore based on what we can see right now
    if closest_ore is not None:
        path = pathing.calculate_conveyor_path(closest_ore)
        launchers = pathing.calculate_launcher_positions(path, closest_ore)
        for i in launchers:
            rc.draw_indicator_line(Position(0, 0),i, 0, 255, 255)
        if target_ore is None:
            target_ore = closest_ore
        else:
            current_target_dist_sq = target_ore.distance_squared(map_info.my_core)
            if min_dist_sq < current_target_dist_sq:
                target_ore = closest_ore
    
    if target_ore:
        rc.draw_indicator_dot(target_ore, 255, 255, 0)

def run_post():
    # pick a random empty tile surrounding the builder and place a marker
    surrounding_tiles = []
    for d in list(Direction):
        if d == Direction.CENTRE:
            continue
        
        pos = rc.get_position().add(d)
        try:
            if rc.can_place_marker(pos):
                surrounding_tiles.append(pos)
        except GameError:
            # position is off map
            pass

    if surrounding_tiles:
        target_pos = random.choice(surrounding_tiles)
        value = generate_encoded_int(rc.get_id())
        rc.place_marker(target_pos, value)

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
    
    if target_ore:
        mode = Mode.BUILD_HARVESTER
    
    if explore_target and rc.get_position().distance_squared(explore_target) <= 18:
        force_generate_explore_target()
    
    if turns_since_last_explore_target > (rc.get_map_width() + rc.get_map_height()) * 2:
        force_generate_explore_target()

def check_build_harvester():
    if pathing.calculate_path(target_ore):
        print(" | Path to mine found")
    else:
        print(" | Can't reach mine")
        

def check_route():
    pass


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
    global mode, target_ore

    if target_ore is None:
        mode = Mode.EXPLORE
        return

    cardinal_dirs = [Direction.NORTH, Direction.SOUTH, Direction.EAST, Direction.WEST]
    adjacent_tiles = [target_ore.add(d) for d in cardinal_dirs]
    
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
            try:
                if rc.get_entity_type(building_id) in [EntityType.BARRIER, EntityType.HARVESTER] and rc.get_team(building_id) == rc.get_team():
                    is_barrier = True
            except GameError: pass
        
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
                        if rc.get_entity_type(building_id) in [EntityType.BARRIER, EntityType.HARVESTER]:
                            is_our_barrier = True
                    except GameError: pass

                if not is_our_barrier:
                    # This tile needs a barrier. Can we build/destroy?
                    if building_id and rc.get_team(building_id) == rc.get_team():
                        if rc.can_destroy(pos):
                            rc.destroy(pos)
                            return
                    elif not building_id:
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
        if rc.get_position() == target_ore:
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
            if building_id and rc.get_team(building_id) == rc.get_team():
                if rc.can_destroy(target_ore):
                    rc.destroy(target_ore)
            
            if rc.get_tile_building_id(target_ore) is None and rc.can_build_harvester(target_ore):
                rc.build_harvester(target_ore)
                target_ore = None
                mode = Mode.EXPLORE # works really well, but we want to avoid changing states in run code, refactor later
                return

def run_route():
    pass