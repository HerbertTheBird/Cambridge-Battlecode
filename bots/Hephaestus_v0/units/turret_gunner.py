from cambc import Controller, Position, EntityType, Direction, Team
import math
import map_info

rc: Controller

def init(c: Controller):
    global rc
    rc = c
    map_info.init(c)


def priority(tile: Position, my_team: Team) -> int:
    get_team = rc.get_team
    get_entity_type = rc.get_entity_type
    get_tile_building_id = rc.get_tile_building_id
    get_tile_builder_bot_id = rc.get_tile_builder_bot_id

    # --- Builder bot check ---
    builder_id = get_tile_builder_bot_id(tile)
    if builder_id and get_team(builder_id) != my_team:
        return 3  # builder bots (after turrets)
    elif builder_id:
        return 9

    # --- Building check ---
    building_id = get_tile_building_id(tile)
    if not building_id:
        return 9

    building_type = get_entity_type(building_id)

    if building_type == map_info._ET_ROAD:
        return 8
    
    if get_team(building_id) == my_team:
        return 9

    # Ignore markers
    if building_type == map_info._ET_MARKER:
        return 9

    # Priority order
    if building_type == EntityType.CORE:
        return 0
    if map_info.is_conveyor(building_type):
        return 1
    if map_info.is_turret(building_type):
        return 2

    return 4  # other enemy buildings

# 8-direction order (must match engine ordering)
DIRS = [
    Direction.NORTH,
    Direction.NORTHEAST,
    Direction.EAST,
    Direction.SOUTHEAST,
    Direction.SOUTH,
    Direction.SOUTHWEST,
    Direction.WEST,
    Direction.NORTHWEST,
]

DIR_TO_IDX = {d: i for i, d in enumerate(DIRS)}


def rotate_towards(my_pos: Position, my_team: Team, target_pos: Position):
    desired_dir = my_pos.direction_to(target_pos)

    current_dir = rc.get_direction()

    if current_dir == desired_dir:
        return

    cur_idx = DIR_TO_IDX[current_dir]
    target_idx = DIR_TO_IDX[desired_dir]

    # Compute shortest rotation direction
    diff = (target_idx - cur_idx) % 8

    if diff <= 4:
        # rotate clockwise (+1)
        next_dir = DIRS[(cur_idx + 1) % 8]
    else:
        # rotate counterclockwise (-1)
        next_dir = DIRS[(cur_idx - 1) % 8]

    # Only rotate one step
    if rc.get_action_cooldown() == 0 and rc.get_global_resources()[0] >= 50:
        print("ATTEMPTING ROTATE")
        rc.rotate(next_dir)

def run():
    map_info.update()
    
    if rc.get_action_cooldown() > 0:
        return

    if rc.get_ammo_amount() <= 0:
        return

    my_pos = rc.get_position()
    my_team = rc.get_team()
    can_fire = rc.can_fire


    best_target = None
    best_priority = 999

    pos_x, pos_y = my_pos.x, my_pos.y

    # 8 directions: (dx, dy, max_steps)
    DIRECTIONS = [
        (0, 1, 3),   # N
        (1, 1, 2),   # NE
        (1, 0, 3),   # E
        (1, -1, 2),  # SE
        (0, -1, 3),  # S
        (-1, -1, 2), # SW
        (-1, 0, 3),  # W
        (-1, 1, 2),  # NW
    ]

    # Cache methods (important for speed)
    is_passable = rc.is_tile_passable
    get_building = rc.get_tile_building_id
    is_in_vision = rc.is_in_vision

    # --- Scan only reachable tiles ---
    for dx, dy, max_steps in DIRECTIONS:
        cx, cy = pos_x, pos_y

        for step in range(1, max_steps + 1):
            cx += dx
            cy += dy

            p = Position(cx, cy)

            if not map_info.in_bounds(p):
                break
            if not is_in_vision(p):
                break

            # Blocked BEFORE reaching target
            if step > 1:
                if not map_info.is_tile_empty(p) and not is_passable(p):
                    print(f"Stopped because {map_info.is_tile_empty(p)} {is_passable(p)}")
                    break

            # Evaluate this tile
            pr = priority(p, my_team)

            if pr < best_priority:
                best_priority = pr
                best_target = p

                if pr == 0:
                    break  # best possible

        if best_priority == 0:
            break

    # --- Fire if target exists ---
    if best_target is not None and best_priority < 9 and rc.can_fire(best_target):
        rc.fire(best_target)
        return
    
    print(best_target)
    if (best_priority < 9):
        if (best_target):
            rc.draw_indicator_line(my_pos, best_target, 255, 150, 150)

        # --- Otherwise rotate toward nearest builder ---
            rotate_towards(my_pos, my_team, best_target)