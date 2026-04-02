from cambc import Controller, Position, Direction, EntityType, Environment, GameError

from enum import Enum
import random
import sys

import map_info
from pathing import Pathing
import comms


class Mode(Enum):
    EXPLORE = (100, 100, 255, "explore")
    HEAL = (0, 255, 0, "healing")
    BUILD_HARVESTER = (0, 180, 180, "build harvester")
    ROUTE = (255, 255, 0, "route to core")
    SABOTAGE = (200, 10, 10, "attack harvester")
    BUILD_TRAP = (193, 154, 107, "launcher trap")
    HEAL_CORE = (255, 165, 0, "heal core")
    def __init__(self, r, g, b, desc):
        self.r = r
        self.g = g
        self.b = b
        self.desc = desc
        
def log(text):
    pass

mode = Mode.EXPLORE
indicator = []
routed = 0
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
                 EntityType.CONVEYOR, EntityType.BRIDGE, EntityType.SENTINEL, EntityType.GUNNER}

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
    globals()[f"check_{mode.name.lower()}"]()
    globals()[f"run_{mode.name.lower()}"]()

    run_post()  # cleanup


# invariant calculations
# Global to track current repair target
repair_target = None
repair_override_turns = 2  # time window after a bot is spawned
heal_idle_turns = 0  # turns spent in heal with nothing to repair

def run_pre():
    """
    Pre-turn logic:
    - Update map info.
    - If not already in HEAL mode, check for newly damaged ally conveyors/bridges.
    - Once a damaged tile is found, set repair_target permanently and switch to HEAL mode.
    - Self-healing is still performed as a fallback.
    """
    global target_ore, blocked_ores, sabotage_ore, opponent_ore, repair_target, mode, heal_idle_turns

    map_info.update()
    my_pos = rc.get_position()

    # --- Step 0: Heal self if possible (fallback) ---
    if map_info.my_core and map_info.building[map_info.my_core.x][map_info.my_core.y] and map_info.building[map_info.my_core.x][map_info.my_core.y].hp < 500 and rc.get_position().distance_squared(map_info.my_core) <= 2:
        if rc.can_heal(my_pos):
            rc.heal(my_pos)
        mode = Mode.HEAL_CORE
        return
    if rc.can_heal(my_pos):
        rc.heal(my_pos)

    # --- Step 1: If we already have a repair target, check if still needed ---
    if repair_target is not None:
        still_damaged = False
        if repair_target.distance_squared(my_pos) <= rc.get_vision_radius_sq():
            try:
                r_id = rc.get_tile_building_id(repair_target)
                if r_id is not None and rc.get_team(r_id) == rc.get_team():
                    if rc.get_hp(r_id) < rc.get_max_hp(r_id):
                        still_damaged = True
            except GameError:
                pass
            if still_damaged:
                heal_idle_turns = 0
                mode = Mode.HEAL
                return
            else:
                heal_idle_turns += 1
                if heal_idle_turns > 30:
                    # Nothing to repair for 30 turns - go back to exploring
                    repair_target = None
                    heal_idle_turns = 0
                else:
                    mode = Mode.HEAL
                    return
        else:
            mode = Mode.HEAL
            return

    # --- Step 2: Scan for damaged allied conveyors/bridges ---
    for pos in rc.get_nearby_tiles():
        building_id = rc.get_tile_building_id(pos)
        if building_id is None:
            continue
        if rc.get_team(building_id) != rc.get_team():
            continue
        b_type = rc.get_entity_type(building_id)
        if b_type not in (EntityType.CONVEYOR, EntityType.ARMOURED_CONVEYOR, EntityType.BRIDGE):
            continue
        if rc.get_hp(building_id) < rc.get_max_hp(building_id):
            # First damaged tile seen becomes permanent repair target
            repair_target = pos
            mode = Mode.HEAL
            return

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
        if rc.get_entity_type(uid) == EntityType.BUILDER_BOT and rc.get_team(uid) == rc.get_team() and uid > 4:
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



def run_post():
    pass

def check_heal():
    pass

def run_heal():
    """
    Permanent HEAL mode:
    - Move toward the permanent repair_target.
    - Heal the tile with the most missing HP in the surrounding area.
    - Place roads on empty tiles around repair target.
    - Attack enemy roads around repair target.
    """
    global repair_target
    if repair_target is None:
        return  # safety check

    my_pos = rc.get_position()

    # --- Step 1: Move toward the repair target ---
    if my_pos != repair_target:
        nav.move_to(repair_target)
        return  # wait until reaching target

    # --- Step 2: Scan surrounding tiles (including target) for most damaged ---
    damaged_candidates = []
    surrounding_tiles = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            pos = Position(repair_target.x + dx, repair_target.y + dy)
            if not map_info.is_on_map(pos):
                continue
            if pos.distance_squared(my_pos) > rc.get_vision_radius_sq():
                continue
            surrounding_tiles.append(pos)
            building_id = rc.get_tile_building_id(pos)
            if building_id is None:
                continue
            if rc.get_team(building_id) != rc.get_team():
                continue
            if rc.get_hp(building_id) < rc.get_max_hp(building_id):
                missing_hp = rc.get_max_hp(building_id) - rc.get_hp(building_id)
                damaged_candidates.append((missing_hp, pos))

    # --- Step 3: Heal the tile with the most missing HP ---
    if damaged_candidates:
        damaged_candidates.sort(reverse=True)  # highest missing HP first
        _, target_pos = damaged_candidates[0]
        if rc.can_heal(target_pos):
            rc.heal(target_pos)
    
    max_missing_hp = 3

    for tile in rc.get_nearby_tiles(rc.get_vision_radius_sq()):
        building_id = rc.get_tile_building_id(tile)
        if building_id is None:
            continue

        # Only consider conveyors (and variants via your helper)
        entity_type = rc.get_entity_type(building_id)
        if not map_info.is_conveyor(entity_type):
            continue

        # Optional: only repair allies (usually what you want)
        if rc.get_team(building_id) != rc.get_team():
            continue

        # Compute missing HP
        hp = rc.get_hp(building_id)
        max_hp = rc.get_max_hp(building_id)
        missing_hp = max_hp - hp

        if missing_hp > max_missing_hp:
            max_missing_hp = missing_hp
            repair_target = tile

    # --- Step 4: Surrounding tile actions ---
    for pos in surrounding_tiles:
        # 1. Place road on empty tiles
        if map_info.is_tile_empty(pos) and rc.can_build_road(pos):
            rc.build_road(pos)
        else:
            building_id = rc.get_tile_building_id(pos)
            if building_id is not None:
                # 2. Attack enemy road
                if rc.get_team(building_id) != rc.get_team() and rc.get_entity_type(building_id) == EntityType.ROAD:
                    if my_pos != pos:
                        nav.move_to(pos)
                    if rc.can_fire(pos):
                        rc.fire(pos)

def force_generate_explore_target():
    global explore_target, turns_since_last_explore_target
    turns_since_last_explore_target = 0

    # Priority: target known titanium ore that doesn't have our harvester
    # Only check 30% of the time to save CPU
    if random.random() < 0.3 and rc.get_cpu_time_elapsed() < 800:
        my_team = rc.get_team()
        best_ore = None
        best_dist = float('inf')
        my_pos = rc.get_position()
        ground_local = map_info.ground
        building_local = map_info.building
        for x in range(map_info.width):
            col_g = ground_local[x]
            col_b = building_local[x]
            for y in range(map_info.height):
                if col_g[y] == Environment.ORE_TITANIUM:
                    b = col_b[y]
                    if b and b.type == EntityType.HARVESTER and b.team == my_team:
                        continue
                    dist = (x - my_pos.x) ** 2 + (y - my_pos.y) ** 2
                    if dist < best_dist:
                        best_dist = dist
                        best_ore = Position(x, y)
        if best_ore:
            explore_target = best_ore
            return

    # Fallback: target unexplored tiles
    for _ in range(5):
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

def check_build_trap():
    """
    Check if the trap at trap_loc is complete.
    - If all 8 surrounding tiles are impassable or out of vision, switch back to EXPLORE mode.
    """
    global mode, trap_loc

    if trap_loc is None:
        return

    my_pos = rc.get_position()
    completed = True

    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            pos = Position(trap_loc.x + dx, trap_loc.y + dy)

            # Skip tiles out of vision; only visible tiles can "fail" the check
            if pos.distance_squared(my_pos) > rc.get_vision_radius_sq():
                continue

            # If a tile is passable, the trap is not yet complete
            if rc.is_tile_passable(pos) or map_info.is_tile_empty(pos) or rc.get_tile_builder_bot_id(pos):
                completed = False
                break
        if not completed:
            break

    if completed:
        mode = Mode.EXPLORE
        trap_loc = None  # reset trap location now that it’s done

def run_build_trap():
    """
    Build a trap around trap_loc:
    1. Pick an "escape tile" around the center (most open).
    2. Place barriers on the other 7 tiles.
    3. Move to the escape tile, then further out.
    4. Place the final barrier on the escape tile.
    """
    global trap_loc
    if trap_loc is None:
        return

    center = trap_loc
    my_pos = rc.get_position()

    # --- Step 1: Identify 8 surrounding tiles ---
    surrounding = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            pos = Position(center.x + dx, center.y + dy)
            if map_info.is_on_map(pos):
                surrounding.append(pos)

    if not surrounding:
        return

    # --- Step 2: Pick escape tile ---
    # choose the tile with the most passable/empty tiles around it (excluding center and its ring)
    def score_escape(tile):
        score = 0
        if not rc.is_tile_passable(tile) and not map_info.is_tile_empty(tile):
            return score
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                neighbor = Position(tile.x + dx, tile.y + dy)
                if not map_info.is_on_map(neighbor):
                    continue
                if neighbor == center or neighbor in surrounding:
                    continue
                if map_info.is_tile_empty(neighbor) or rc.is_tile_passable(neighbor):
                    score += 1
        return score

    escape_tile = max(surrounding, key=score_escape)

    # --- Step 3: Place barriers on the 7 remaining tiles ---
    for tile in surrounding:
        if tile == escape_tile:
            continue
        if map_info.is_tile_empty(tile) and rc.can_build_barrier(tile):
            rc.build_barrier(tile)
            return
        elif rc.is_tile_passable(tile) and rc.can_destroy(tile):
            rc.destroy(tile)
            if (rc.can_build_barrier(tile)):
                rc.build_barrier(tile)
                return

    # --- Step 4: Move to escape tile, then one more step further out ---
    # if my_pos != escape_tile:
    #     # move toward escape tile
    #     nav.move_to(escape_tile)
    #     return
    
    if rc.can_build_road(trap_loc):
        rc.build_road(trap_loc)

    # Step further out: pick a neighbor of escape_tile that is not center or already blocked
    further_out_tile = None
    best_score = -1
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            candidate = Position(escape_tile.x + dx, escape_tile.y + dy)
            if not map_info.is_on_map(candidate):
                continue
            if candidate == center or candidate in surrounding:
                continue
            if not rc.is_tile_passable(candidate) and not map_info.is_tile_empty(candidate):
                continue
            # pick the tile with the most empty neighbors
            score = 0
            for ddx in (-1, 0, 1):
                for ddy in (-1, 0, 1):
                    if ddx == 0 and ddy == 0:
                        continue
                    neighbor = Position(candidate.x + ddx, candidate.y + ddy)
                    if not map_info.is_on_map(neighbor):
                        continue
                    if map_info.is_tile_empty(neighbor) or rc.is_tile_passable(neighbor):
                        score += 1
            if score > best_score:
                best_score = score
                further_out_tile = candidate

    if further_out_tile and my_pos != further_out_tile:
        nav.move_to(further_out_tile)

    # --- Step 5: Place final barrier on the escape tile ---
    if rc.can_build_barrier(escape_tile):
        rc.build_barrier(escape_tile)
    elif rc.is_tile_passable(escape_tile) and rc.can_destroy(escape_tile):
            rc.destroy(escape_tile)
            if (rc.can_build_barrier(escape_tile)):
                rc.build_barrier(escape_tile)




trap_loc = None
def check_explore():
    global mode, explore_target, turns_since_last_explore_target, defended_ores, routed, trap_loc

    my_pos = rc.get_position()

    # # --- Step 0: Check trap condition ---
    # if routed >= 1:
    #     for dx in (-1, 0, 1):
    #         for dy in (-1, 0, 1):
    #             cpos = Position(my_pos.x + dx, my_pos.y + dy)
    #             impassable_count = 0
    #             building_id = rc.get_tile_building_id(cpos)
    #             if not (building_id and map_info.is_conveyor(rc.get_entity_type(building_id))):
    #                 for dx in (-1, 0, 1):
    #                     for dy in (-1, 0, 1):
    #                         if dx == 0 and dy == 0:
    #                             continue
    #                         pos = Position(cpos.x + dx, cpos.y + dy)
    #                         if not map_info.is_on_map(pos):
    #                             continue  # out-of-map tiles are ignored now
    #                         building_id = rc.get_tile_building_id(pos)
    #                         is_impassable_our_building = building_id is not None and rc.get_team(building_id) == rc.get_team() and not rc.is_tile_passable(pos)
    #                         is_wall = map_info.ground[pos.x][pos.y] == Environment.WALL
    #                         if building_id and map_info.is_conveyor(rc.get_entity_type(building_id)):
    #                             impassable_count -= 1000
    #                         if is_impassable_our_building or is_wall:
    #                             impassable_count += 1
    #                 if impassable_count >= 3:
    #                     mode = Mode.BUILD_TRAP
    #                     trap_loc = cpos  # store the current location for trap building
    #                     return

    # --- Step 1: Existing exploration logic ---
    if opponent_ore and opponent_ore not in defended_ores:
        mode = Mode.SABOTAGE
        return

    if target_ore:
        mode = Mode.BUILD_HARVESTER
        return

    if explore_target and my_pos.distance_squared(explore_target) <= 18:
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

last_placed_launcher = None
launcher_count = 0
ore_sentinel_count = {} 
def run_explore():
    global explore_target, turns_since_last_explore_target, last_placed_launcher, launcher_count
    my_pos = rc.get_position()

    # === Launcher placement logic: spread launchers ===
    if (launcher_count < 6):
        for other in rc.get_nearby_buildings():
                if rc.get_team(other) == rc.get_team() and rc.get_entity_type(other) == EntityType.LAUNCHER:
                    if not last_placed_launcher or rc.get_position(other).distance_squared(rc.get_position()) < last_placed_launcher.distance_squared(rc.get_position()):
                        last_placed_launcher = rc.get_position(other)
        for tile in rc.get_nearby_tiles(2):
            if not map_info.is_tile_empty(tile):
                continue

            if not last_placed_launcher:
                # No known launchers? optional: skip placement
                continue

            # Find distance to closest launcher
            closest_dist = tile.distance_squared(last_placed_launcher)

            # Condition: far enough (>16) but still within vision radius
            if closest_dist > 16 and closest_dist <= rc.get_vision_radius_sq() and rc.get_global_resources()[0] > launcher_count * 80:
                if rc.can_build_launcher(tile):
                    rc.build_launcher(tile)
                    last_placed_launcher = tile
                    launcher_count += 1
                    return  # only build one per turn

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



def run_build_harvester():
    global mode, target_ore, blocked_ores, ore_sentinel_count

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
    built_count = 0
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
                built_count += 1
            if rc.get_team(building_id) != rc.get_team():
                opponent_sabotaged = True

        if not is_barrier:
            perimeter_secure = False

    if opponent_sabotaged:
        global blocked_ores
        if target_ore.distance_squared(rc.get_position()) < rc.get_vision_radius_sq():
            if rc.get_tile_building_id(target_ore) and (rc.get_tile_building_id(
                    target_ore) != EntityType.BARRIER and rc.get_tile_building_id(
                    target_ore) != EntityType.SENTINEL) and rc.can_destroy(target_ore) and not map_info.is_turret(rc.get_entity_type(
                    building_id)):
                rc.destroy(target_ore)
            if rc.can_build_barrier(target_ore):
                rc.build_barrier(target_ore)
                blocked_ores[target_ore] = rc.get_current_round() + 150
                target_ore = None
                mode = Mode.EXPLORE
                return
            

    # State 1: Perimeter is not secure. Let's build barriers.

    scale = 1.0
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
                    if building_id and rc.get_team(building_id) == rc.get_team() and not map_info.is_turret(rc.get_entity_type(building_id)):
                        if rc.can_destroy(pos):
                            rc.destroy(pos)
                    
                    my_core = map_info.my_core
                    manhattan_dist = abs(target_ore.x - my_core.x) + abs(target_ore.y - my_core.y)
                    harvester_cost = rc.get_harvester_cost()[0]
                    conveyor_cost = rc.get_conveyor_cost()[0]
                    scale = 1.1
                    expected_finish = 10 + scale * (harvester_cost + manhattan_dist * conveyor_cost - 10 * manhattan_dist) < rc.get_global_resources()[0]
                    
                    # --- Placement logic ---
                    if routed >= 0 and expected_finish:

                        # --- Scan for nearby opponent harvesters ---
                        enemy_harvester_id = None
                        for unit_id in rc.get_nearby_buildings():  # only check 32 distance sq
                            if rc.get_team(unit_id) == rc.get_team():
                                continue  # skip own team

                            if rc.get_entity_type(unit_id) == EntityType.HARVESTER:
                                if pos.distance_squared(rc.get_position(unit_id)) < 32:
                                    enemy_harvester_id = unit_id
                                    break

                        placed_sentinel = False
                        if enemy_harvester_id is not None:
                            enemy_pos = rc.get_position(enemy_harvester_id)
                            dir_to_harvester = pos.direction_to(enemy_pos)
                            dir_to_ore = pos.direction_to(target_ore)

                            # Only place sentinel if directions differ, enemy in range, expected_finish, and we haven't already placed one
                            ore_key = (target_ore.x, target_ore.y)
                            if dir_to_harvester != dir_to_ore and ore_key not in ore_sentinel_count and expected_finish:
                                if rc.can_build_sentinel(pos, dir_to_harvester):
                                    rc.build_sentinel(pos, dir_to_harvester)
                                    ore_sentinel_count[ore_key] = 1
                                    return

                    if routed >= 10 and pos.x % 2 == 0 and expected_finish and built_count != 0:
                        if rc.can_build_sentinel(pos, pos.direction_to(target_ore).rotate_right()):
                            rc.build_sentinel(pos, pos.direction_to(target_ore).rotate_right())
                            return
                    elif rc.can_build_barrier(pos):
                        rc.build_barrier(pos)
                        return

        if (target_ore.distance_squared(rc.get_position()) <= rc.get_vision_radius_sq()):
            if not rc.is_tile_passable(target_ore) and rc.get_entity_type(
                    rc.get_tile_building_id(target_ore)) != EntityType.HARVESTER and rc.can_destroy(target_ore):
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
                    rc.destroy(target_ore)

            if rc.get_tile_building_id(target_ore) is None:
                my_core = map_info.my_core
                if my_core:
                    manhattan_dist = abs(target_ore.x - my_core.x) + abs(target_ore.y - my_core.y)
                    harvester_cost = rc.get_harvester_cost()[0]
                    conveyor_cost = rc.get_conveyor_cost()[0]

                    required_titanium = scale * (harvester_cost + (manhattan_dist) * conveyor_cost - 10 * manhattan_dist)
                    current_titanium = rc.get_global_resources()[0]

                    if current_titanium < required_titanium and target_ore not in ore_sentinel_count:
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
                    rc.destroy(target_ore)
            nav.execute_path()


def check_route():
    global ore_path, launcher_position, route_idx, mode, routed
    if not ore_path:
        ore_path = ore_nav.calculate_conveyor_path(routed_ore)
        route_idx = 0
    if ore_path:
        launcher_position = ore_nav.calculate_launcher_position(ore_path, routed_ore)
    if ore_path and route_idx >= len(ore_path) - 1 and not launcher_position:
        mode = Mode.EXPLORE
        routed += 1
        ore_path = None


def run_route():
    global route_idx, ore_path, launcher_position
    if ore_path:
        if route_idx < len(ore_path) - 1:
            new_path = ore_nav.calculate_conveyor_path(ore_path[route_idx], ore_path[:route_idx], True)
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
            
                if bridge and permissive_can_build_bridge(to_build, ore_path[route_idx + 1]):
                    if rc.can_destroy(to_build):
                        rc.destroy(to_build)
                    rc.build_bridge(to_build, ore_path[route_idx + 1])
                    route_idx += 1
                elif not bridge and permissive_can_build_conveyor(to_build, dir):
                    # if route_idx == len(ore_path)-2 and route_idx != 0 and ore_path[route_idx + 1].distance_squared(map_info.my_core) <= 2:
                    #     if rc.can_build_splitter(to_build, ore_path[route_idx-1].direction_to(ore_path[route_idx])):
                    #         rc.build_splitter(to_build, ore_path[route_idx-1].direction_to(ore_path[route_idx]))
                    #         route_idx += 1
                    # else:
                    if rc.can_destroy(to_build):
                        rc.destroy(to_build)
                    rc.build_conveyor(to_build, dir)
                    route_idx += 1
                elif not bridge:
                    if (rc.can_build_road(to_build) and rc.is_tile_empty(to_build)):
                        rc.build_road(to_build)
            next = ore_path[route_idx]
            if route_idx < len(ore_path) - 1:
                nav.move_to(next)

def permissive_can_build_bridge(to_build, target):
    if rc.can_build_bridge(to_build, target):
        return True
    if rc.can_destroy(to_build) and rc.get_global_resources()[0] >= rc.get_bridge_cost()[0]:
        return True
    return False
def permissive_can_build_conveyor(to_build, dir):
    if rc.can_build_conveyor(to_build, dir):
        return True
    if rc.can_destroy(to_build) and rc.get_global_resources()[0] >= rc.get_conveyor_cost()[0]:
        return True
    return False

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
        # Move toward it
        if rc.get_position() != passable_tile:
            nav.move_to(passable_tile)

        # We're on it → destroy or fire
        if rc.can_destroy(passable_tile):
            rc.destroy(passable_tile)
        elif rc.get_position() == passable_tile:
            if rc.can_fire(rc.get_position()):
                rc.fire(rc.get_position())

        return

def check_heal_core():
    pass
def run_heal_core():
    pass