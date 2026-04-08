from cambc import Controller, Position, EntityType, Direction
import math
import map_info

rc = None
_no_ammo_turns = 0

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


def priority(tile: Position, my_pos: Position, my_team: int) -> int:
    get_team = rc.get_team
    get_entity_type = rc.get_entity_type
    get_tile_building_id = rc.get_tile_building_id

    builder_id = rc.get_tile_builder_bot_id(tile)
    building_id = get_tile_building_id(tile)

    building_type = None
    my_building = False

    if building_id:
        building_type = get_entity_type(building_id)
        if get_team(building_id) == my_team and building_type not in (map_info._ET_ROAD, map_info._ET_MARKER):
            my_building = True

    # ---- 0. CORE (highest priority) ----
    if building_type == EntityType.CORE and not my_building:
        return 1

    # ---- 1. Enemy conveyors / bridges / splitters ----
    if building_type and map_info.is_conveyor(building_type) and not my_building:
        return 2

    # ---- 2. Enemy builder ON our building ----
    if builder_id and get_team(builder_id) != my_team and my_building:
        return 3

    # ---- 8. Enemy builder NOT on our building ----
    if builder_id and get_team(builder_id) != my_team:
        return 8

    # ---- Keep your other priorities (shifted if needed) ----
    if building_type:
        if building_type == EntityType.LAUNCHER and not my_building:
            return 4
        if map_info.is_turret(building_type) and not my_building:
            return 0
        if building_type == EntityType.BARRIER and not my_building:
            return 5
        if building_type == EntityType.ROAD and get_team(building_id) != my_team:
            return 6

    # ---- Optional: your harvester + sentinel logic ----
    if building_type == EntityType.HARVESTER and not my_building:
        if my_pos.distance_squared(tile) > 1:
            for dx, dy in CARDINAL_OFFSETS:
                adj = Position(tile.x + dx, tile.y + dy)
                if map_info.in_bounds(adj) and rc.is_in_vision(adj):
                    adj_id = get_tile_building_id(adj)
                    if adj_id and get_team(adj_id) == my_team and get_entity_type(adj_id) == EntityType.SENTINEL:
                        return 9
        return 7

    return 9



def run():
    global _no_ammo_turns
    get_tile_building_id = rc.get_tile_building_id
    get_hp = rc.get_hp
    get_tile_builder_bot_id = rc.get_tile_builder_bot_id

    if rc.get_ammo_amount() < 5:
        _no_ammo_turns += 1
        if _no_ammo_turns >= 10:
            # Don't self-destruct if cardinally adjacent to a harvester
            my_pos = rc.get_position()
            adj_harvester = False
            for dx, dy in CARDINAL_OFFSETS:
                p = Position(my_pos.x + dx, my_pos.y + dy)
                if map_info.in_bounds(p):
                    bid = rc.get_tile_building_id(p)
                    if bid and rc.get_entity_type(bid) == EntityType.HARVESTER:
                        adj_harvester = True
                        break
            if not adj_harvester:
                rc.self_destruct()
                return
    else:
        _no_ammo_turns = 0

    if rc.get_action_cooldown() > 0:
        return
    if rc.get_ammo_amount() < 5:
        return
    print("i am a shooting sentinel")
    
    nearby_units = rc.get_nearby_units()
    enemy_builders = []
    my_team = rc.get_team()

    get_team = rc.get_team
    get_entity_type = rc.get_entity_type
    get_position = rc.get_position

    for uid in nearby_units:
        if get_team(uid) != my_team and get_entity_type(uid) == EntityType.BUILDER_BOT:
            enemy_builders.append(get_position(uid))
            
    def dist_to_nearest_builder_sq(p):
        best = 999999
        px, py = p.x, p.y
        for b in enemy_builders:
            dx = b.x - px
            dy = b.y - py
            d = dx*dx + dy*dy
            if d < best:
                best = d
        return best

    vision_r = int(math.sqrt(rc.get_vision_radius_sq()))
    my_pos = rc.get_position()
    can_fire = rc.can_fire

    best_target = None
    best_priority = 999  # 3. Cache the best priority score

    pos_x, pos_y = my_pos.x, my_pos.y
    best_dist = -1
    best_hp = math.inf

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
                dist = dist_to_nearest_builder_sq(p) if current_priority == 1 else 0

                if (
                    current_priority < best_priority or
                    (
                        current_priority == best_priority and (
                            dist > best_dist or
                            (dist == best_dist and current_hp < best_hp)
                        )
                    )
                ):
                    best_priority = current_priority
                    best_target = p
                    best_hp = current_hp
                    best_dist = dist

                if best_priority == 0 and best_hp == 0:
                    break

    if best_priority == 9 or best_target is None:
        adj_harvester = False
        for dx, dy in CARDINAL_OFFSETS:
            p = Position(my_pos.x + dx, my_pos.y + dy)
            if map_info.in_bounds(p):
                bid = get_tile_building_id(p)
                if bid and get_entity_type(bid) == EntityType.HARVESTER:
                    adj_harvester = True
                    break
        if not adj_harvester:
            rc.self_destruct()
        return

    rc.fire(best_target)