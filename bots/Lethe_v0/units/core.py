from cambc import Controller, Position, Environment, EntityType, GameError, ResourceType
import random
import map_info

rc: Controller
num_spawned = 0
prev_unit_count = 0
last_titanium = 500
last_scaling = 100.
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


def count_incoming_titanium():
    core_pos = rc.get_position()
    count = 0
    for dx in range(-2, 3):
        for dy in range(-2, 3):
            if abs(dx) <= 1 and abs(dy) <= 1:
                continue
            pos = Position(core_pos.x + dx, core_pos.y + dy)
            if not map_info.in_bounds(pos) or not rc.is_in_vision(pos):
                continue
            bid = rc.get_tile_building_id(pos)
            if bid is None:
                continue
            try:
                if rc.get_team(bid) != rc.get_team():
                    continue
                etype = rc.get_entity_type(bid)
                if etype not in (EntityType.CONVEYOR, EntityType.ARMOURED_CONVEYOR,
                                 EntityType.SPLITTER, EntityType.BRIDGE):
                    continue
                res = rc.get_stored_resource(bid)
                if res is not None and res == ResourceType.TITANIUM:
                    # check it points toward core
                    d = rc.get_direction(bid)
                    ddx, ddy = d.delta()
                    out_x = pos.x + ddx
                    out_y = pos.y + ddy
                    if abs(out_x - core_pos.x) <= 1 and abs(out_y - core_pos.y) <= 1:
                        count += 1
            except GameError:
                pass
    return count * 10


def run():
    global num_spawned, prev_unit_count, last_titanium, last_scaling

    titanium = rc.get_global_resources()[0]
    scaling = rc.get_scale_percent()
    round_num = rc.get_current_round()
    # if round_num == 100:
    #     rc.resign()
    passive = 10 if round_num % 4 == 0 else 0
    scaling_delta = scaling - last_scaling
    titanium_delta = (titanium - last_titanium) - passive
    lost_conveyors = False
    # Counts the incoming titanium at the end of this function. Doesn't take bridges into account.
    if -1. <= scaling_delta <= 1. and -2 <= titanium_delta <= 0.:
        roads_built = -titanium_delta
        if roads_built > 0:
            scaling_delta -= roads_built * 0.5
        if scaling_delta < -0.5:
            lost_conveyors = bool(scaling_delta < -0.99)

    max_spawn = 3
    
    if titanium > 800:
        max_spawn = 8
    elif titanium > 600:
        max_spawn = 6

    current_count = rc.get_unit_count()
    if prev_unit_count > current_count:
        num_spawned -= (prev_unit_count - current_count)


    core_pos = rc.get_position()
    # rc.convert(rc.get_global_resources()[1])
    if rc.get_current_round() == 0:
        dx = max(-1, min(1, map_info._MAP_CENTER.x - core_pos.x))
        dy = max(-1, min(1, map_info._MAP_CENTER.y - core_pos.y))
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

    # # Normal spawning logic
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
    prev_unit_count = rc.get_unit_count()
    last_titanium = rc.get_global_resources()[0] + count_incoming_titanium()
    last_scaling = rc.get_scale_percent()


def init(c: Controller):
    global rc, num_spawned, prev_unit_count, last_titanium, last_scaling
    rc = c
    num_spawned = 0
    prev_unit_count = 0
    last_titanium = 500
    last_scaling = 100.
    map_info.init(c)
