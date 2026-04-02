from cambc import Controller, Position, Environment, EntityType, GameError
import random
import map_info

rc = None
num_spawned = 0
defended = set()

def random_spawn_tile() -> Position | None:
    """Return a random adjacent tile that can be spawned on."""
    core_pos = rc.get_position()
    candidates = [
        Position(core_pos.x + dx, core_pos.y + dy)
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
        if not (dx == 0 and dy == 0)
    ]
    random.shuffle(candidates)
    for p in candidates:
        if rc.can_spawn(p):
            return p
    return None

def get_closest_titanium_tile() -> Position | None:
    """Return the closest visible titanium ore without an allied harvester."""
    core_pos = rc.get_position()
    min_dist_sq = float('inf')
    closest_ore = None

    for pos in rc.get_nearby_tiles():
        if rc.get_tile_env(pos) != Environment.ORE_TITANIUM:
            continue

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
            dist_sq = pos.distance_squared(core_pos)
            if dist_sq < min_dist_sq:
                min_dist_sq = dist_sq
                closest_ore = pos

    return closest_ore

def run():
    global num_spawned

    max_spawn = 4
    if rc.get_current_round() < 100:
        max_spawn = 3
    if rc.get_current_round() == 100:
        rc.resign()
    core_pos = rc.get_position()
    rc.convert(rc.get_global_resources()[1])
    if rc.get_current_round() == 0:
        dx = max(-1, min(1, map_info.MAP_CENTER.x - core_pos.x))
        dy = max(-1, min(1, map_info.MAP_CENTER.y - core_pos.y))
        spawn_pos = Position(core_pos.x + dx, core_pos.y + dy)
        if rc.can_spawn(spawn_pos):
            rc.spawn_builder(spawn_pos)
            num_spawned += 1
            return  # Only spawn 1 builder for turn 0

    # --- Spawn towards closest titanium on turn 1 ---
    if rc.get_current_round() == 1:
        titanium_pos = get_closest_titanium_tile()
        if titanium_pos is not None:
            dx = max(-1, min(1, titanium_pos.x - core_pos.x))
            dy = max(-1, min(1, titanium_pos.y - core_pos.y))
            spawn_pos = Position(core_pos.x + dx, core_pos.y + dy)
            if rc.can_spawn(spawn_pos):
                rc.spawn_builder(spawn_pos)
                num_spawned += 1
                return  # Only spawn 1 builder for turn 1
    
    # Normal spawning logic
    if num_spawned < max_spawn or rc.get_global_resources()[0] > 900 + rc.get_scale_percent() or rc.get_hp() < 500:
        spawn_pos = random_spawn_tile()
        if spawn_pos is not None:
            rc.spawn_builder(spawn_pos)
            num_spawned += 1

    # --- Spawn builders to repair damaged ally conveyors/bridges ---
    for pos in rc.get_nearby_tiles():
        building_id = rc.get_tile_building_id(pos)
        if building_id is None or building_id in defended:
            continue

        # Only consider allied conveyors/bridges
        if rc.get_team(building_id) != rc.get_team():
            continue

        b_type = rc.get_entity_type(building_id)
        if b_type not in (EntityType.CONVEYOR, EntityType.ARMOURED_CONVEYOR, EntityType.BRIDGE):
            continue

        # Check if building is damaged
        if rc.get_hp(building_id) < rc.get_max_hp(building_id):
            # Determine spawn direction from core toward damaged tile
            dx = max(-1, min(1, pos.x - core_pos.x))
            dy = max(-1, min(1, pos.y - core_pos.y))
            spawn_pos = Position(core_pos.x + dx, core_pos.y + dy)

            if rc.can_spawn(spawn_pos):
                defended.add(building_id)
                rc.spawn_builder(spawn_pos)
                num_spawned += 1
                print(f"Spawned builder toward damaged {b_type} at {pos}")

def init(c: Controller):
    global rc, num_spawned
    rc = c
    num_spawned = 0
    map_info.init(c)