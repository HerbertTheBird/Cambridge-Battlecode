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
    print(f" <span style='color: #{mode.r:02x}{mode.g:02x}{mode.b:02x}'>|</span> {str(text)}")

mode = Mode.EXPLORE
indicator = []
routed = 0
blocked_ores = {}
defended_ores = set()
cardinal_dirs = [Direction.NORTH, Direction.SOUTH, Direction.EAST, Direction.WEST]
all_dirs = list(Direction)
nav = None
ore_nav = None

axionite_after = 500

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
build_foundry = None

target_foundry = set()
target_splitters = set()
splitter_dir = None

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
# Global to track current repair target
repair_target = None
repair_override_turns = 2  # time window after a bot is spawned

def run_pre():
    """
    Pre-turn logic:
    - Update map info.
    - If not already in HEAL mode, check for newly damaged ally conveyors/bridges.
    - Once a damaged tile is found, set repair_target permanently and switch to HEAL mode.
    - Self-healing is still performed as a fallback.
    """
    global target_ore, blocked_ores, sabotage_ore, opponent_ore, repair_target, mode, target_foundry, target_splitters

    map_info.update()
    if not map_info._my_core:
        rc.self_destruct()
    nav.rebuild_broken_barriers()
    if len(target_foundry) == 0 and map_info._my_core is not None:
        core = map_info._my_core
        for dir, (dx, dy) in CARDINAL_DELTAS:
            tx = core.x + 2 * dx
            ty = core.y + 2 * dy
            target = Position(tx, ty)
            if not map_info.in_bounds(target):
                continue

            if dx == 0:
                side1 = Position(tx - 1, ty)
                side2 = Position(tx + 1, ty)
            else:
                side1 = Position(tx, ty - 1)
                side2 = Position(tx, ty + 1)
            if not map_info.in_bounds(side1) or not map_info.in_bounds(side2):
                continue

            if map_info.ground_at(tx, ty) is Environment.WALL:
                continue
            if map_info.ground_at(side1.x, side1.y) is Environment.WALL:
                continue
            if map_info.ground_at(side2.x, side2.y) is Environment.WALL:
                continue
            target_foundry.add(target)
            target_splitters.add(side1)
            target_splitters.add(side2)
            
    my_pos = rc.get_position()

    # --- Step 0: Heal self if possible (fallback) ---
    if map_info._my_core and map_info.id_at(map_info._my_core.x, map_info._my_core.y) and map_info.hp_at(map_info._my_core.x, map_info._my_core.y) < 500 and rc.get_position().distance_squared(map_info._my_core) <= 2:
        if rc.can_heal(my_pos):
            rc.heal(my_pos)
        mode = Mode.HEAL_CORE
        return
    if rc.can_heal(my_pos):
        rc.heal(my_pos)

    # --- Step 1: If we already have a repair target, switch to HEAL mode ---
    if repair_target is not None:
        mode = Mode.HEAL
        return  # permanent state, run() or run_heal() will handle movement/healing

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
            log(f"Repair target set at {repair_target}, switching to HEAL mode")
            return

    # Clean up expired blocks
    current_round = rc.get_current_round()
    for ore, unblock_round in list(blocked_ores.items()):
        if current_round >= unblock_round:
            del blocked_ores[ore]

    if map_info._my_core is None:
        return

    # closest_ore = None
    # min_dist_sq = float('inf')
    # min_dist_sq_sabotage = float('inf')
    # opponent_ore = None
    # min_dist_sq_opponent = float('inf')

    # # Find all visible titanium ores without an allied harvester on them
    # nearby_units = rc.get_nearby_units(dist_sq=rc.get_vision_radius_sq())

    # allied_builders = []
    # for uid in nearby_units:
    #     if rc.get_entity_type(uid) == EntityType.BUILDER_BOT and rc.get_team(uid) == rc.get_team() and uid > 4:
    #         allied_builders.append(uid)
            
    # def am_closest_builder(pos):
    #     my_dist = rc.get_position().distance_squared(pos)

    #     for uid in allied_builders:
    #         try:
    #             other_pos = rc.get_position(uid)
    #             if other_pos is None:
    #                 continue

    #             if other_pos.distance_squared(pos) < my_dist:
    #                 return False
    #         except GameError:
    #             pass

    #     return True
    
    # for pos in rc.get_nearby_tiles():
    #     if pos in blocked_ores:
    #         continue

    #     env = rc.get_tile_env(pos)

    #     if env == Environment.ORE_TITANIUM:
    #         building_id = rc.get_tile_building_id(pos)

    #         blocked = False
    #         occupied_opponent = False
    #         if building_id is not None:
    #             try:
    #                 building_type = rc.get_entity_type(building_id)
    #                 building_team = rc.get_team(building_id)
    #                 if building_type == EntityType.HARVESTER and building_team == rc.get_team() or building_type == EntityType.BARRIER:
    #                     blocked = True
    #                 if building_type != EntityType.MARKER and building_team != rc.get_team():
    #                     occupied_opponent = True
    #             except GameError:
    #                 pass

    #         if building_id is not None and pos not in defended_ores:
    #             try:
    #                 building_type = rc.get_entity_type(building_id)
    #                 building_team = rc.get_team(building_id)

    #                 if building_type == EntityType.HARVESTER and building_team != rc.get_team():
    #                     # Check for at least one passable adjacent tile
    #                     has_passable_adjacent = False
    #                     for d, (dx, dy) in CARDINAL_DELTAS:
    #                         if d == Direction.CENTRE:
    #                             continue
    #                         adj = Position(pos.x + dx, pos.y + dy)
    #                         try:
    #                             if map_info.in_bounds(adj) and rc.is_tile_passable(adj):
    #                                 has_passable_adjacent = True
    #                                 break
    #                         except GameError:
    #                             pass

    #                     if has_passable_adjacent:
    #                         dist_sq = pos.distance_squared(rc.get_position())
    #                         if dist_sq < min_dist_sq_opponent:
    #                             min_dist_sq_opponent = dist_sq
    #                             opponent_ore = pos
    #             except GameError:
    #                 pass

    #         if not blocked and not occupied_opponent:
    #             if am_closest_builder(pos):
    #                 dist_sq = pos.distance_squared(map_info._my_core)
    #                 if dist_sq < min_dist_sq:
    #                     min_dist_sq = dist_sq
    #                     closest_ore = pos

    #         dist_sq_sabotage = pos.distance_squared(rc.get_position())
    #         if dist_sq_sabotage < min_dist_sq_sabotage:
    #             min_dist_sq_sabotage = dist_sq_sabotage
    #             sabotage_ore = pos

    # Update target_ore based on what we can see right now
    update_target_ore()
    if target_ore:
        rc.draw_indicator_dot(target_ore, 255, 255, 0)


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
            if not map_info.in_bounds(pos):
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
            rc.draw_indicator_dot(target_pos, 0, 255, 0)  # optional visual indicator
    
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

    for _ in range(2):  # slightly more aggressive
        random_x = random.randint(0, map_info._width - 1)
        random_y = random.randint(0, map_info._height - 1)
        if not map_info.seen_at(random_x, random_y):
            explore_target = Position(random_x, random_y)
            return

    # If no empty tile found after 100 tries, fallback to completely random
    random_x = random.randint(0, map_info._width - 1)
    random_y = random.randint(0, map_info._height - 1)
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
            if map_info.in_bounds(pos):
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
                if not map_info.in_bounds(neighbor):
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
            rc.draw_indicator_dot(tile, 255, 0, 0)
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
            if not map_info.in_bounds(candidate):
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
                    if not map_info.in_bounds(neighbor):
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
        rc.draw_indicator_dot(escape_tile, 255, 0, 0)
    elif rc.is_tile_passable(escape_tile) and rc.can_destroy(escape_tile):
            rc.destroy(escape_tile)
            if (rc.can_build_barrier(escape_tile)):
                rc.build_barrier(escape_tile)




trap_loc = None
def update_target_ore():
    global target_ore
    my_pos = rc.get_position()
    claimed = comms.decode_claim()
    for pos, turn, id in claimed:
        if id == rc.get_id()&comms._ID_MASK:
            continue
        blocked_ores[pos] = max(blocked_ores.get(pos, 0), turn+10)
        print("blocked", pos, turn, id)
        for dir in cardinal_dirs:
            new_pos = pos.add(dir)
            if not map_info.in_bounds(new_pos):
                continue
            blocked_ores[new_pos] = max(blocked_ores.get(new_pos, 0), turn+10)

    prev_target_ore = target_ore
    target_ore = None
    core = map_info._my_core
    for pos in rc.get_nearby_tiles():
        if map_info.ground_at(pos.x, pos.y) == Environment.ORE_TITANIUM or map_info.ground_at(pos.x, pos.y) == Environment.ORE_AXIONITE and rc.get_current_round() > axionite_after:
            if not target_ore or core.distance_squared(pos) < core.distance_squared(target_ore) or pos == prev_target_ore:
                fail = False
                if map_info.id_at(pos.x, pos.y) != 0:
                    if pos == prev_target_ore:
                        print(map_info.team_at(pos.x, pos.y), rc.get_team(), map_info.type_at(pos.x, pos.y))
                    if map_info.team_at(pos.x, pos.y) == rc.get_team() and map_info.type_at(pos.x, pos.y) == EntityType.HARVESTER:
                        fail = True
                    if map_info.team_at(pos.x, pos.y) != rc.get_team():
                        fail = True
                card_d = [[0, 1], [0, -1], [1, 0], [-1, 0]]
                for d in card_d:
                    if map_info.in_bounds(Position(pos.x+d[0], pos.y+d[1])) and map_info.id_at(pos.x+d[0], pos.y+d[1]) != 0 and map_info.type_at(pos.x+d[0], pos.y+d[1]) != EntityType.ROAD and map_info.team_at(pos.x+d[0], pos.y+d[1]) != rc.get_team():
                        fail = True
                if pos in blocked_ores and blocked_ores[pos] > rc.get_current_round():
                    fail = True
                if fail:
                    if pos == prev_target_ore:
                        prev_target_ore = None
                    continue
                target_ore = pos
    if prev_target_ore:
        target_ore = prev_target_ore
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
    # update_target_ore()
    if not target_ore:
        mode = Mode.EXPLORE
        return
    if (target_ore.distance_squared(rc.get_position())) <= rc.get_vision_radius_sq():
        building_id = rc.get_tile_building_id(target_ore)
        if building_id and (
                rc.get_entity_type(building_id) == EntityType.HARVESTER or rc.get_team(building_id) != rc.get_team()):
            target_ore = None
            mode = Mode.EXPLORE
            return
    if nav.calculate_path(target_ore) != []:
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
                    log(f"Placed launcher at {tile}")
                    return  # only build one per turn

    if explore_target is None:
        force_generate_explore_target()

    if rc.get_global_resources()[0] < rc.get_harvester_cost()[0]:
        return

    # loop until we find a target we can path to and move.
    moved = False
    attempts = 0
    while not moved and attempts < 2:
        if nav.move_to(explore_target) == False:
            force_generate_explore_target()
            moved = True
        else:
            break
        attempts += 1

    turns_since_last_explore_target += 1

    if explore_target:
        rc.draw_indicator_line(rc.get_position(), explore_target, mode.r, mode.g, mode.b)


def run_build_harvester():
    global mode, target_ore, blocked_ores, ore_sentinel_count, build_foundry
    log("try build on " + str(target_ore))
    if target_ore is None or rc.get_global_resources()[0] < rc.get_harvester_cost()[0]*2 + rc.get_barrier_cost()[0]*8:
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

        if not map_info.in_bounds(pos) or rc.get_tile_env(pos) == Environment.WALL:
            wall_count += 1
            continue

        building_id = rc.get_tile_building_id(pos)
        is_barrier = False
        if building_id is not None:
            if rc.get_entity_type(building_id) in OUR_BUILDINGS and rc.get_team(building_id) == rc.get_team():
                is_barrier = True
                built_count += 1
            if rc.get_entity_type(building_id) == EntityType.ROAD and rc.get_team(building_id) != rc.get_team():
                if rc.can_move(rc.get_position().direction_to(pos)):
                    rc.move(rc.get_position().direction_to(pos))
                if rc.get_position() == pos and rc.can_fire(rc.get_position()):
                    rc.fire(rc.get_position())
            elif rc.get_team(building_id) != rc.get_team():
                opponent_sabotaged = True
        if not is_barrier:
            perimeter_secure = False

    if opponent_sabotaged:
        global blocked_ores
        if target_ore.distance_squared(rc.get_position()) < rc.get_vision_radius_sq():
            building_id = rc.get_tile_building_id(target_ore)
            if building_id and rc.get_entity_type(
                    building_id) != EntityType.BARRIER and rc.can_destroy(target_ore) and not map_info.is_turret(rc.get_entity_type(
                    building_id)):
                log("destroy1 " + str(target_ore))
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
                is_wall = not map_info.in_bounds(pos) or rc.get_tile_env(pos) == Environment.WALL
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
        nav.move_to(target_ore)
        if rc.can_place_marker(target_ore):
            rc.place_marker(target_ore, comms.encode_claim(target_ore))
            bid = rc.get_tile_building_id(rc.get_position())
            if bid and rc.get_entity_type(bid) == EntityType.ROAD and rc.get_team(bid) == rc.get_id():
                rc.destroy(rc.get_position())
        else:
            for dir in all_dirs:
                if not map_info.in_bounds(rc.get_position().add(dir)):
                    continue
                bid = rc.get_tile_building_id(rc.get_position().add(dir))
                if bid and rc.get_entity_type(bid) == EntityType.ROAD and rc.get_team(bid) == rc.get_team():

                    rc.destroy(rc.get_position().add(dir))
                if rc.can_place_marker(rc.get_position().add(dir)):

                    rc.place_marker(rc.get_position().add(dir), comms.encode_claim(target_ore))
                    break
        return

    # State 2: Perimeter is secure (or all walls). Let's build the harvester.
    else:
        # If we are on the ore, move off.
        if rc.get_position() == target_ore:
            def is_blocking_neighbor(pos: Position) -> bool:
                if pos == target_ore:
                    return True
                if not map_info.in_bounds(pos):
                    return True
                if rc.get_tile_env(pos) == Environment.WALL:
                    return True

                building_id = rc.get_tile_building_id(pos)
                if building_id is None:
                    return False

                building_type = rc.get_entity_type(building_id)
                building_team = rc.get_team(building_id)
                if building_team == rc.get_team():
                    return (
                        building_type is EntityType.HARVESTER
                        or building_type is EntityType.FOUNDRY
                        or map_info.is_turret(building_type)
                    )

                if map_info.is_conveyor(building_type):
                    return False
                return building_type is not EntityType.ROAD and building_type is not EntityType.MARKER

            def is_fully_surrounded(pos: Position) -> bool:
                for d in all_dirs:
                    if d is Direction.CENTRE:
                        continue
                    dx, dy = d.delta()
                    neighbor = Position(pos.x + dx, pos.y + dy)
                    if not is_blocking_neighbor(neighbor):
                        return False
                return True

            building_id = rc.get_tile_building_id(target_ore)
            if building_id and rc.get_team(building_id) != rc.get_team():
                rc.fire()
                return
            moved = False
            for d in random.sample(all_dirs, len(all_dirs)):
                if d is Direction.CENTRE:
                    continue
                dx, dy = d.delta()
                next_pos = Position(rc.get_position().x + dx, rc.get_position().y + dy)
                if is_fully_surrounded(next_pos):
                    continue
                if rc.can_move(d):
                    rc.move(d)
                    moved = True
                    break
            # nowhere to move
            if not moved:
                my_pos = rc.get_position()
                for d in random.sample(all_dirs, len(all_dirs)):
                    if d is Direction.CENTRE:
                        continue
                    dx, dy = d.delta()
                    next_pos = Position(my_pos.x + dx, my_pos.y + dy)
                    if is_fully_surrounded(next_pos):
                        continue
                    if map_info.is_tile_empty(next_pos):
                        nav.move(d)
                        moved = True
                        break

        # If adjacent to the ore, clear it and build.
        if rc.get_position().distance_squared(target_ore) <= 2:
            building_id = rc.get_tile_building_id(target_ore)
            if building_id and rc.get_team(building_id) == rc.get_team() and rc.get_entity_type(
                    building_id) != EntityType.HARVESTER:
                if rc.can_destroy(target_ore):
                    log("destroy4 " + str(target_ore))
                    rc.destroy(target_ore)

            if rc.get_tile_building_id(target_ore) is None:
                my_core = map_info._my_core
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
                    global routed_ore, ore_path
                    routed_ore = target_ore
                    target_ore = None
                    mode = Mode.ROUTE
                    build_foundry = None
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
    global ore_path, launcher_position, route_idx, mode, routed
    # print(ore_path)
    if not ore_path:
        ore_path = ore_nav.calculate_conveyor_path(routed_ore, routed_ore, None, False)
        if ore_path == []:
            mode = Mode.EXPLORE
            return
        route_idx = 0
    else:
        if route_idx < len(ore_path)-1:
            next_path = ore_nav.calculate_conveyor_path(ore_path[route_idx], routed_ore, ore_path[:route_idx], True)
            if next_path:
                ore_path = ore_path[:route_idx] + next_path
    if ore_path:
        launcher_position = ore_nav.calculate_launcher_position(ore_path if ore_path[-1] != Position(-1, -1) else ore_path[:-1], routed_ore)
    if ore_path and route_idx >= len(ore_path) - 1 and not launcher_position and not build_foundry:
        mode = Mode.EXPLORE
        routed += 1
        ore_path = None


def run_route():
    global route_idx, ore_path, launcher_position, mode, build_foundry
    log(str(ore_path))
    if not ore_path:
        ore_path = ore_nav.calculate_conveyor_path(routed_ore, routed_ore, None, False)
        log("new path " + str(ore_path))

        if ore_path == []:
            mode = Mode.EXPLORE
            return
        route_idx = 0
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
                if map_info.id_at(launcher_position.x, launcher_position.y) != 0 and map_info.team_at(launcher_position.x, launcher_position.y) != rc.get_team():
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
        def attempt_build():
            global route_idx, ore_path, build_foundry
            to_build = ore_path[route_idx]
            next = ore_path[route_idx + 1]
            bridge = to_build.distance_squared(next) > 1
            dir = to_build.direction_to(next)
            print("next", next, to_build, target_foundry)
            if next == Position(-1, -1):
                if map_info.id_at(to_build.x, to_build.y) != 0 and map_info.type_at(to_build.x, to_build.y) == EntityType.SPLITTER and map_info.team_at(to_build.x, to_build.y) == rc.get_team():
                    print("hi here", to_build)
                    route_idx += 1
                    if map_info.ground_at(routed_ore.x, routed_ore.y) == Environment.ORE_AXIONITE:
                        for f in target_foundry:
                            if to_build.distance_squared(f) == 1:
                                build_foundry = f
                                if map_info.id_at(f.x, f.y) != 0 and map_info.type_at(f.x, f.y) == EntityType.FOUNDRY:
                                    build_foundry = None
                                    break
                    return True
            if to_build.distance_squared(rc.get_position()) <= 2:
                print("hi close enough")
                if to_build == rc.get_position():
                    id = rc.get_tile_building_id(rc.get_position())
                    if id and rc.get_team(id) != rc.get_team() and rc.can_fire(rc.get_position()):
                        rc.fire(rc.get_position())
                if rc.can_destroy(to_build):
                    log("destroy8 " + str(to_build))
                    rc.destroy(to_build)
                if next == Position(-1, -1):
                    if to_build.x == map_info._my_core.x-2:
                        splitter_dir = Direction.EAST
                    elif to_build.x == map_info._my_core.x+2:
                        splitter_dir = Direction.WEST
                    elif to_build.y == map_info._my_core.y+2:
                        splitter_dir = Direction.NORTH
                    else:
                        splitter_dir = Direction.SOUTH
                    print("hi", to_build, splitter_dir)
                    if rc.can_build_splitter(to_build, splitter_dir):
                        rc.build_splitter(to_build, splitter_dir)
                        if map_info.ground_at(routed_ore.x, routed_ore.y) == Environment.ORE_AXIONITE:
                            for f in target_foundry:
                                if to_build.distance_squared(f) == 1:
                                    build_foundry = f
                                    if map_info.id_at(f.x, f.y) != 0 and map_info.type_at(f.x, f.y) == EntityType.FOUNDRY:
                                        build_foundry = None
                                        break
                        route_idx += 1
                        return True
                elif bridge and rc.can_build_bridge(to_build, ore_path[route_idx + 1]):
                    rc.build_bridge(to_build, ore_path[route_idx + 1])
                    if route_idx == 0:
                        map_info.my_conveyors.add((to_build, routed_ore))
                    route_idx += 1
                    return True
                elif not bridge and rc.can_build_conveyor(to_build, dir):
                    rc.build_conveyor(to_build, dir)
                    if route_idx == 0:
                        map_info.my_conveyors.add((to_build, routed_ore))
                    route_idx += 1
                    return True
            return False
        if route_idx < len(ore_path) - 1:
            if route_idx >= len(ore_path) - 1:
                return
            attempt_build()

            if nav.move_to(ore_path[route_idx]) == False:
                mode = Mode.EXPLORE
                return
        if build_foundry:
            adjacent = set()
            for dir in all_dirs:
                if dir == Direction.CENTRE:
                    continue
                if map_info.is_passable(build_foundry.add(dir)):
                    adjacent.add(build_foundry.add(dir))
            if nav.move_to(adjacent) == False:
                mode = Mode.EXPLORE
                return
            if rc.can_destroy(build_foundry):
                rc.destroy(build_foundry)
            if rc.can_build_foundry(build_foundry):
                rc.build_foundry(build_foundry)
                build_foundry = None

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
        if not map_info.in_bounds(adj):
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
            if not map_info.in_bounds(candidate):
                continue
            if my_pos.distance_squared(candidate) > 2:
                continue

            # Check all 4 cardinal neighbors of this candidate for enemy harvester
            adjacent_enemy_harvester = False
            for d, (n_dx, n_dy) in CARDINAL_DELTAS:
                neighbor = Position(candidate.x + n_dx, candidate.y + n_dy)
                if not map_info.in_bounds(neighbor):
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
        if map_info.in_bounds(adj):
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

def check_heal_core():
    pass
def run_heal_core():
    pass
