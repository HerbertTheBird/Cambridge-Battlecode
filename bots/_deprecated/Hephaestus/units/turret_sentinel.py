from cambc import Controller, Position, EntityType, Direction, Team
import math
import map_info

rc: Controller

# 1. Pre-calculate integer offsets to bypass enum hashing overhead entirely
CARDINAL_OFFSETS = [
    (0, 1),  # NORTH
    (0, -1),  # SOUTH
    (-1, 0),  # WEST
    (1, 0)  # EAST
]


def init(c: Controller):
    global rc
    rc = c
    map_info.init(c)


def priority(tile: Position, my_pos: Position, my_team: Team) -> int:
    # Localize methods for faster lookup
    get_team = rc.get_team
    get_entity_type = rc.get_entity_type
    get_tile_building_id = rc.get_tile_building_id

    # Check builder
    builder_id = rc.get_tile_builder_bot_id(tile)
    enemy_builder = False
    if builder_id and get_team(builder_id) != my_team:
        enemy_builder = True

    # Check building
    building_id = get_tile_building_id(tile)
    building_type = None
    my_building = False

    if building_id:
        building_type = get_entity_type(building_id)
        if get_team(building_id) == my_team and building_type != map_info._ET_ROAD and building_type != map_info._ET_MARKER:
            my_building = True
        else:
            building_type = get_entity_type(building_id)

    if map_info.is_conveyor(building_type) and get_team(building_id) == my_team:
        return 9
    # Resolve simplest priorities first
    if enemy_builder and building_type is not None and not my_building:
        return 2

    if building_type is not None:
        if map_info.is_conveyor(building_type) and not my_building:
            return 3
        if map_info.is_turret(building_type) and not my_building:
            return 0

    if enemy_builder and not my_building:
        return 4

    if building_type == EntityType.CORE and not my_building:
        return 1

    # 2. Lazy Evaluation: Only check adjacent tiles IF it's an enemy harvester.
    # This avoids the 4-direction loop 95% of the time.
    if building_type == EntityType.HARVESTER and my_pos.distance_squared(tile) > 1 and not my_building:
        adjacent_sentinel = False
        is_in_vision = rc.is_in_vision
        in_bounds = map_info.in_bounds

        pos_x, pos_y = tile.x, tile.y
        for dx, dy in CARDINAL_OFFSETS:
            adj_pos = Position(pos_x + dx, pos_y + dy)

            if not in_bounds(adj_pos) or not is_in_vision(adj_pos):
                continue

            adj_id = get_tile_building_id(adj_pos)
            if adj_id and get_team(adj_id) == my_team and get_entity_type(adj_id) == EntityType.SENTINEL:
                adjacent_sentinel = True
                break  # Found one, stop checking other directions

        return 9 if adjacent_sentinel else 5

    if building_type == EntityType.LAUNCHER and not my_building:
        return 6
    if building_type == EntityType.BARRIER and not my_building:
        return 7
    if building_type == EntityType.ROAD and not get_team(building_id) == my_team:
        return 8

    return 9


def run():
    get_tile_building_id = rc.get_tile_building_id
    get_hp = rc.get_hp
    get_tile_builder_bot_id = rc.get_tile_builder_bot_id
    print("i am a sentinel")
    if rc.get_action_cooldown() > 0:
        return
    if rc.get_ammo_amount() < 5:
        print(f"Ammo: {rc.get_ammo_amount()}")
        return
    print("i am a shooting sentinel")

    vision_r = int(math.sqrt(rc.get_vision_radius_sq()))
    my_pos = rc.get_position()
    my_team = rc.get_team()
    can_fire = rc.can_fire

    best_target = None
    best_priority = 999  # 3. Cache the best priority score

    pos_x, pos_y = my_pos.x, my_pos.y
    best_hp = math.inf  # NEW

    for x in range(pos_x - vision_r, pos_x + vision_r + 1):
        for y in range(pos_y - vision_r, pos_y + vision_r + 1):
            p = Position(x, y)
            if can_fire(p):
                current_priority = priority(p, my_pos, my_team)

                # Get target id (prefer builder if exists, else building)
                target_id = get_tile_builder_bot_id(p)
                if not target_id:
                    target_id = get_tile_building_id(p)

                current_hp = get_hp(target_id) if target_id else math.inf

                # Compare
                if (
                    current_priority < best_priority or
                    (current_priority == best_priority and current_hp < best_hp)
                ):
                    best_priority = current_priority
                    best_target = p
                    best_hp = current_hp

                    if best_priority == 0 and best_hp == 0:
                        break
        if best_priority == 0 and best_hp == 0:
            break

    if best_priority == 9 or best_target is None:
        return

    rc.fire(best_target)