from cambc import Controller, Position, Direction, EntityType, Environment, ResourceType

from enum import Enum
import random
import sys

import map_info
from pathing import Pathing
import comms

indicator = []
blocked_ores = {}
defended_ores = set()
cardinal_dirs = [Direction.NORTH, Direction.SOUTH, Direction.EAST, Direction.WEST]
all_dirs = list(Direction)

# Cache deltas directly to bypass `Enum` and `.add()` overhead
ALL_DIRS_DELTAS = [(d, d.delta()) for d in all_dirs]
CARDINAL_DELTAS = [(d, d.delta()) for d in cardinal_dirs]
OUR_BUILDINGS = {EntityType.BARRIER, EntityType.HARVESTER, EntityType.LAUNCHER,
                 EntityType.CONVEYOR, EntityType.BRIDGE, EntityType.SENTINEL}

# explore state
explore_target = None
turns_since_last_explore_target = 0
next_attack_tile = None

rc = None
MODE_ACTIONS = None
nav = None
class Mode(Enum):
    RUSH_CORE = (255, 165, 0, "rush opponent core")
    PREPARE_LAUNCHER = (0, 180, 180, "set up for launcher")
    ATTACK = (200, 10, 10, "attack opponent")

    def __init__(self, r, g, b, desc):
        self.r = r
        self.g = g
        self.b = b
        self.desc = desc

mode = Mode.RUSH_CORE

def init(c: Controller):
    global rc, MODE_ACTIONS, nav
    rc = c
    map_info.init(c)
    comms.init(c)
    nav = Pathing(c)

def run():
    global mode
    run_pre()  # preliminary calculations
    print(f"CHECK STATE: <span style='color: #{mode.r:02x}{mode.g:02x}{mode.b:02x}'>{mode.desc}</span>")
    globals()[f"check_{mode.name.lower()}"]()
    print(f"EXEC. STATE: <span style='color: #{mode.r:02x}{mode.g:02x}{mode.b:02x}'>{mode.desc}</span>")
    globals()[f"run_{mode.name.lower()}"]()
    run_post()  # cleanup

def log(text : str):
    print(f" <span style='color: #{mode.r:02x}{mode.g:02x}{mode.b:02x}'>|</span> {text}")

def check_rush_core():
    global mode

    # find stuff to sabotage
    for pos in rc.get_nearby_tiles():
        building_id = rc.get_tile_building_id(pos)
        if building_id is None:
            continue

        # Check if it's enemy
        if rc.get_team(building_id) == rc.get_team():
            continue

        entity_type = rc.get_entity_type(building_id)

        # Case 1: Enemy harvester on titanium ore
        if entity_type == EntityType.HARVESTER:
            if map_info.ground[pos.x][pos.y] == Environment.ORE_TITANIUM:
                
                # Check for cardinally adjacent passable tiles
                passable_found = False
                empty_adjacent_found = False

                for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    adj = Position(pos.x + dx, pos.y + dy)
                    if adj.distance_squared(rc.get_position()) > rc.get_vision_radius_sq():
                        continue
                    if rc.is_tile_passable(adj):
                        passable_found = True
                        break  # No need to check further

                # If no passable tile, check empty-adjacent tiles that have passable neighbors
                if not passable_found:
                    for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                        adj = Position(pos.x + dx, pos.y + dy)
                        if adj.distance_squared(rc.get_position()) > rc.get_vision_radius_sq():
                            continue
                        if map_info.is_tile_empty(adj):
                            # Check if this empty tile has any passable neighbor
                            for ddx, ddy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                                neighbor = Position(adj.x + ddx, adj.y + ddy)
                                if neighbor.distance_squared(rc.get_position()) > rc.get_vision_radius_sq():
                                    continue
                                if rc.is_tile_passable(neighbor):
                                    empty_adjacent_found = True
                                    break
                            if empty_adjacent_found:
                                break

                if passable_found or empty_adjacent_found:
                    log("Prepare launcher: titanium harvester detected")
                    mode = Mode.PREPARE_LAUNCHER
                    return

        # Case 2: Enemy conveyor carrying titanium
        elif entity_type in (
            EntityType.CONVEYOR,
            EntityType.ARMOURED_CONVEYOR,
            EntityType.SPLITTER,
            EntityType.BRIDGE,
        ):
            stored = rc.get_stored_resource(building_id)
            if stored and stored == ResourceType.TITANIUM:
                log("Prepare launcher: connected conveyer detected")
                mode = Mode.PREPARE_LAUNCHER
                return

    # route to core
    path = nav.calculate_path(map_info.predicted_enemy_core)
    if path is not None:
        if len(path) > 0:
            log("Path to core found.")
        else:
            log("Opponent core is unreachable")
    else:
        log("A* TLE - assuming safe state, ignoring")
        
def run_rush_core():
    nav.execute_path()

def check_prepare_launcher():
    global mode

    my_pos = rc.get_position()
    # Only need to check very close range (adjacent = dist_sq <= 2)
    for unit_id in rc.get_nearby_units(2):
        # Must be allied
        if rc.get_team(unit_id) != rc.get_team():
            continue

        # Must be a launcher
        if rc.get_entity_type(unit_id) != EntityType.LAUNCHER:
            continue

        if rc.get_position(unit_id).distance_squared(my_pos) <= 2:
            log("Launcher placed, waiting")
            mode = Mode.ATTACK
            return

def run_prepare_launcher():
    print("Prepare launcher ran", file=sys.stderr)
    log("Setup")
    global mode
    my_pos = rc.get_position()
    
     # Check for nearby allied launchers
    launcher_adjacent_tiles = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            check_pos = Position(my_pos.x + dx, my_pos.y + dy)
            if not map_info.is_on_map(check_pos):
                continue
            b_id = rc.get_tile_building_id(check_pos)
            if b_id is not None and rc.get_team(b_id) == rc.get_team() and rc.get_entity_type(b_id) == EntityType.LAUNCHER:
                launcher_adjacent_tiles.append(check_pos)

    # If already adjacent to a launcher, continue with normal logic
    if not launcher_adjacent_tiles:
        log("Looking for close launcher")
        # Try moving to a tile surrounding an allied launcher
        nearest_launcher_tile = None
        nearest_dist = float('inf')
        for pos in rc.get_nearby_tiles(rc.get_vision_radius_sq()):
            # pos = Position(x, y)
            if pos.distance_squared(my_pos) > rc.get_vision_radius_sq():
                continue
            b_id = rc.get_tile_building_id(pos)
            if b_id is None or rc.get_team(b_id) != rc.get_team() or rc.get_entity_type(b_id) != EntityType.LAUNCHER:
                continue

            # For each launcher, check surrounding tiles
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    adj = Position(pos.x + dx, pos.y + dy)
                    
                    if adj.distance_squared(my_pos) > rc.get_vision_radius_sq():
                        continue
                    if not map_info.is_on_map(adj) or (not map_info.is_tile_empty(adj) and not rc.is_tile_passable(adj)):
                        continue
                    dist = my_pos.distance_squared(adj)
                    if dist < nearest_dist:
                        nearest_dist = dist
                        nearest_launcher_tile = adj

        # Move to nearest empty tile surrounding an allied launcher
        if nearest_launcher_tile and (rc.is_tile_passable(nearest_launcher_tile) or map_info.is_tile_empty(nearest_launcher_tile)):
            path = nav.calculate_path(nearest_launcher_tile)
            if path and len(path) > 0:
                if len(path) <= 2:
                    log("Choosing existing launcher")
                    nav.execute_path()
                    if rc.get_position() == nearest_launcher_tile:
                        mode = Mode.ATTACK
                    return  # Skip building a new launcher

    best_empty = None
    best_empty_dist = float('inf')

    best_restrict = None
    best_restrict_dist = float('inf')
    log("Building new launcher")

    # check all 8 surrounding tiles
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue

            pos = Position(my_pos.x + dx, my_pos.y + dy)

            if not map_info.is_on_map(pos):
                continue

            dist = pos.distance_squared(map_info.predicted_enemy_core)

            # Priority 1: empty tiles
            if map_info.is_tile_empty(pos):
                if dist < best_empty_dist:
                    best_empty = pos
                    best_empty_dist = dist

            # Priority 2: restrictive, owned tiles
            elif map_info.can_place_at_restrictive(pos):
                if dist < best_restrict_dist:
                    best_restrict = pos
                    best_restrict_dist = dist

    launcher_pos = None

    # try placing on best empty tile
    if best_empty is not None and rc.can_build_launcher(best_empty):
        rc.build_launcher(best_empty)
        launcher_pos = best_empty

    # otherwise clear restrictive tile and place
    elif best_restrict is not None:
        if rc.can_destroy(best_restrict):
            rc.destroy(best_restrict)

        if rc.can_build_launcher(best_restrict):
            rc.build_launcher(best_restrict)
            launcher_pos = best_restrict

    # --- Place marker on a second empty tile surrounding the launcher ---
    if launcher_pos is not None:
        second_tile = None
        second_dist = float('inf')

        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue

                pos = Position(launcher_pos.x + dx, launcher_pos.y + dy)

                # skip the launcher tile itself
                if pos == launcher_pos:
                    continue

                if not map_info.is_on_map(pos):
                    continue

                if not map_info.is_tile_empty(pos):
                    continue

                # choose the closest one to the launcher (or first one found)
                dist = pos.distance_squared(launcher_pos)
                if dist < second_dist:
                    second_tile = pos
                    second_dist = dist

        # place marker if found
        if second_tile is not None and rc.can_place_marker(second_tile):
            rc.place_marker(second_tile, comms.encode_centralized_launch())

def check_attack():
    global next_attack_tile
    my_pos = rc.get_position()

    # List of nearby valid attack tiles
    attack_tiles = []

    # Check adjacent tiles for enemy conveyors/bridges (Case 2)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            adj = Position(my_pos.x + dx, my_pos.y + dy)
            if not map_info.is_on_map(adj) or not map_info.is_tile_empty(adj):
                continue

            for ddx in (-1, 0, 1):
                for ddy in (-1, 0, 1):
                    check_pos = Position(adj.x + ddx, adj.y + ddy)
                    if not map_info.is_on_map(check_pos):
                        continue
                    b_id = rc.get_tile_building_id(check_pos)
                    if b_id is None or rc.get_team(b_id) == rc.get_team():
                        continue
                    b_type = rc.get_entity_type(b_id)
                    if b_type in (EntityType.CONVEYOR, EntityType.ARMOURED_CONVEYOR):
                        if rc.get_position(b_id).add(rc.get_direction(b_id)) == adj:
                            attack_tiles.append(adj)
                    elif b_type == EntityType.BRIDGE:
                        if rc.get_bridge_target(b_id) == adj:
                            attack_tiles.append(adj)

    # Case 3: standing on enemy conveyor/bridge
    building_id = rc.get_tile_building_id(my_pos)
    if building_id and rc.get_team(building_id) != rc.get_team() and rc.get_entity_type(building_id) in (
        EntityType.CONVEYOR, EntityType.ARMOURED_CONVEYOR, EntityType.BRIDGE
    ):
        attack_tiles.append(my_pos)

    # If at least one attack tile is nearby or on current tile → stay in ATTACK
    global mode
    if attack_tiles:
        # store the closest attack tile for run_attack to move to
        next_attack_tile = min(attack_tiles, key=lambda t: my_pos.distance_squared(t))
        path = nav.calculate_path(next_attack_tile)
        if not path:
            mode = Mode.RUSH_CORE
            check_rush_core()
    else:
        mode = Mode.RUSH_CORE
        check_rush_core()

def run_attack():
    # place down sentinel if possible
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
                
    # If there's a next_attack_tile set, move to it first
    target_tile = next_attack_tile
    if target_tile and my_pos != target_tile:
        nav.execute_path()
        return  # wait until we reach the tile before continuing
    
    # Helper: find adjacent empty tile
    adjacent_empty = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            adj = Position(my_pos.x + dx, my_pos.y + dy)
            if map_info.is_on_map(adj) and map_info.is_tile_empty(adj):
                adjacent_empty.append(adj)

    # Helper: find adjacent empty tiles
    adjacent_empty = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            adj = Position(my_pos.x + dx, my_pos.y + dy)
            if map_info.is_on_map(adj) and map_info.is_tile_empty(adj):
                adjacent_empty.append(adj)

    # Case 1: Standing on an empty tile that an enemy conveyor/bridge leads into
    if map_info.is_tile_empty(my_pos):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                adj = Position(my_pos.x + dx, my_pos.y + dy)
                b_id = rc.get_tile_building_id(adj)
                if b_id is None:
                    continue
                if rc.get_team(b_id) == rc.get_team():
                    continue
                b_type = rc.get_entity_type(b_id)
                # Check if this building points at our tile
                points_at_tile = False
                if b_type in (EntityType.CONVEYOR, EntityType.ARMOURED_CONVEYOR):
                    dir = rc.get_direction(b_id)
                    b_pos = rc.get_position(b_id)
                    if b_pos.add(dir) == my_pos:
                        points_at_tile = True
                elif b_type == EntityType.BRIDGE:
                    if rc.get_bridge_target(b_id) == my_pos:
                        points_at_tile = True

                if points_at_tile and adjacent_empty:
                    rc.move(random.choice(adjacent_empty))
                    return

    # --- Case 2: Place sentinel on empty tile an enemy conveyor/bridge leads into ---
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            adj = Position(my_pos.x + dx, my_pos.y + dy)
            if not map_info.is_on_map(adj) or not map_info.is_tile_empty(adj):
                continue
            for ddx in (-1, 0, 1):
                for ddy in (-1, 0, 1):
                    if ddx == 0 and ddy == 0:
                        continue
                    check_pos = Position(adj.x + ddx, adj.y + ddy)
                    if not map_info.is_on_map(check_pos):
                        continue
                    b_id = rc.get_tile_building_id(check_pos)
                    if b_id is None:
                        continue
                    if rc.get_team(b_id) == rc.get_team():
                        continue
                    b_type = rc.get_entity_type(b_id)
                    points_at_tile = False
                    if b_type in (EntityType.CONVEYOR, EntityType.ARMOURED_CONVEYOR):
                        dir = rc.get_direction(b_id)
                        b_pos = rc.get_position(b_id)
                        if b_pos.add(dir) == adj:
                            points_at_tile = True
                    elif b_type == EntityType.BRIDGE:
                        if rc.get_bridge_target(b_id) == adj:
                            points_at_tile = True

                    if points_at_tile and rc.can_build_sentinel(adj, Direction.NORTH):
                        direction = map_info.best_sentinel_dir(adj) or Direction.NORTH
                        rc.build_sentinel(adj, direction)
                        return

    # Case 3: Standing on an enemy conveyor/bridge, fire
    building_id = rc.get_tile_building_id(my_pos)
    if building_id is not None:
        if rc.get_team(building_id) != rc.get_team() and rc.get_entity_type(building_id) in (
            EntityType.CONVEYOR, EntityType.ARMOURED_CONVEYOR, EntityType.BRIDGE
        ):
            if rc.can_fire(my_pos):
                rc.fire(my_pos)
                return
    

def run_pre():
    map_info.update()

def run_post():
    pass