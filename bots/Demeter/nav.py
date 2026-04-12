from cambc import Direction, Position

from globals import DIRECTIONS, INF, DELTAS

def _dist_sq(ax: int, ay: int, bx: int, by: int) -> int:
    dx = ax - bx
    dy = ay - by
    return dx * dx + dy * dy

# target
destination: Position | None = None
destination_type: str | None = None  # 'exact' | 'adjacent' | 'sensed' | 'visited'
original_destination: Position | None = None

# bugnav state
states = None
bug_path_index = 0
rotate_right = None
last_obstacle_found = None
prev_target = None
min_dist_to_target = INF
min_location_to_target = None
turns_moving_to_obstacle = 0

# constants
MAX_TURNS_MOVING_TO_OBSTACLE = 2
MIN_DIST_RESET = 3

width = 0
height = 0
my_id = 0

def init():
    global destination, destination_type, original_destination
    global states, bug_path_index, rotate_right, last_obstacle_found
    global prev_target, min_dist_to_target, min_location_to_target, turns_moving_to_obstacle
    destination = None
    destination_type = None
    original_destination = None
    states = None
    bug_path_index = 0
    rotate_right = None
    last_obstacle_found = None
    prev_target = None
    min_dist_to_target = INF
    min_location_to_target = None
    turns_moving_to_obstacle = 0

def set_statics(w, h, mid):
    global width, height, my_id
    width = w
    height = h
    my_id = mid

def set_destination(target: Position, dest_type: str):
    global destination, destination_type, original_destination
    destination = target
    destination_type = dest_type
    original_destination = target

def clear_destination():
    global destination, destination_type, original_destination
    destination = None
    destination_type = None
    original_destination = None

def refresh_adjacent(ct):
    global destination
    if destination_type != "adjacent" or original_destination is None:
        return
    adj = get_adjacent_target(original_destination, ct)
    if adj is not None:
        destination = adj
    else:
        destination = original_destination

def is_destination_reached(ct):
    import map as map_mod
    if destination is None:
        return True
    if destination_type == "exact":
        return ct.get_position() == destination
    if destination_type == "adjacent":
        ref = original_destination or destination
        my_pos = ct.get_position()
        return 0 < _dist_sq(my_pos.x, my_pos.y, ref.x, ref.y) <= 2
    if destination_type == "sensed":
        return ct.is_in_vision(destination)
    # "visited" or None
    return map_mod.is_visited(destination)

def get_adjacent_target(pos: Position, ct) -> Position | None:
    my_pos = ct.get_position()
    mx, my = my_pos
    px, py = pos
    w, h = width, height
    best: Position | None = None
    best_dist = INF
    for d in DIRECTIONS:
        ddx, ddy = DELTAS[d]
        ax, ay = px + ddx, py + ddy
        if not (0 <= ax < w and 0 <= ay < h):
            continue
        dist = _dist_sq(mx, my, ax, ay)
        if dist >= best_dist:
            continue
        adj = Position(ax, ay)
        if not ct.is_in_vision(adj):
            continue
        if adj != my_pos and not ct.is_tile_passable(adj):
            continue

        best_dist = dist
        best = adj
    return best

def _init_states(ct):
    global states
    if states is None:
        states = [[0] * height for _ in range(width)]

def go_to(ct):
    global prev_target, min_dist_to_target, min_location_to_target
    target = destination
    if target is None:
        return

    _init_states(ct)

    my_loc = ct.get_position()
    mlx, mly = my_loc
    tx, ty = target
    w, h = width, height

    # === TARGET CHANGE HANDLING ===
    if prev_target is None:
        reset_pathfinding()
        _set_rotate_right(None)
    else:
        dist = _dist_sq(tx, ty, prev_target.x, prev_target.y)
        if dist > 0:
            if dist >= MIN_DIST_RESET:
                _set_rotate_right(None)
                reset_pathfinding()
            else:
                soft_reset(target)

    prev_target = target

    check_state(my_loc)

    d = _dist_sq(mlx, mly, tx, ty)
    if d == 0:
        return

    if ct.get_move_cooldown() > 0:
        return

    if d < min_dist_to_target:
        reset_pathfinding()
        min_dist_to_target = d
        min_location_to_target = my_loc

    # === GREEDY ===
    if last_obstacle_found is None:
        if try_greedy_move(ct, my_loc, target):
            reset_pathfinding()
            return

    # === BUG MODE ===
    if last_obstacle_found is not None:
        direction = my_loc.direction_to(last_obstacle_found)
    else:
        direction = my_loc.direction_to(target)

    if can_pass(ct, direction, my_loc):
        execute_move(ct, direction, my_loc)
        if last_obstacle_found is not None:
            _inc_turns_moving()
            ddx, ddy = DELTAS[direction]
            ox, oy = mlx + ddx, mly + ddy
            if (
                turns_moving_to_obstacle >= MAX_TURNS_MOVING_TO_OBSTACLE
                or not (0 <= ox < w and 0 <= oy < h)
            ):
                reset_pathfinding()
            else:
                _set_last_obstacle(Position(ox, oy))
        return
    else:
        _reset_turns_moving()

    check_rotate(ct, my_loc, target, direction)

    # === BUG LOOP ===
    for _ in range(16):
        if can_pass(ct, direction, my_loc):
            execute_move(ct, direction, my_loc)
            return

        ddx, ddy = DELTAS[direction]
        nx, ny = mlx + ddx, mly + ddy

        if not (0 <= nx < w and 0 <= ny < h):
            _set_rotate_right(not rotate_right if rotate_right is not None else True)
        else:
            _set_last_obstacle(Position(nx, ny))

        direction = direction.rotate_right() if rotate_right else direction.rotate_left()

    if can_pass(ct, direction, my_loc):
        execute_move(ct, direction, my_loc)

def try_greedy_move(ct, my_loc, target):
    import map as map_mod
    direction = my_loc.direction_to(target)
    mx, my = my_loc
    tx, ty = target
    dist = _dist_sq(mx, my, tx, ty)

    dir1 = direction.rotate_right()
    dir2 = direction.rotate_left()

    candidates = []
    for turn_cost, candidate_dir in ((0, direction), (1, dir1), (1, dir2)):
        if not can_pass(ct, candidate_dir, my_loc):
            continue
        ddx, ddy = DELTAS[candidate_dir]
        nx, ny = mx + ddx, my + ddy
        next_dist = _dist_sq(nx, ny, tx, ty)
        if next_dist >= dist:
            continue
        risk = map_mod.get_enemy_launcher_adj_count(Position(nx, ny))
        candidates.append(((risk, next_dist, turn_cost), candidate_dir))

    if candidates:
        _, best_dir = min(candidates, key=lambda item: item[0])
        execute_move(ct, best_dir, my_loc)
        return True

    return False

def check_rotate(ct, my_loc, target, direction):
    if rotate_right is not None:
        return

    dir_left = direction
    dir_right = direction

    for _ in range(8):
        if not can_pass(ct, dir_left, my_loc):
            dir_left = dir_left.rotate_left()
        else:
            break

    for _ in range(8):
        if not can_pass(ct, dir_right, my_loc):
            dir_right = dir_right.rotate_right()
        else:
            break

    mx, my = my_loc
    tx, ty = target
    dlx, dly = DELTAS[dir_left]
    drx, dry = DELTAS[dir_right]
    dist_left = _dist_sq(mx + dlx, my + dly, tx, ty)
    dist_right = _dist_sq(mx + drx, my + dry, tx, ty)

    _set_rotate_right(dist_right < dist_left)

def reset_pathfinding():
    global last_obstacle_found, min_dist_to_target, bug_path_index, turns_moving_to_obstacle
    last_obstacle_found = None
    min_dist_to_target = INF
    bug_path_index += 1
    turns_moving_to_obstacle = 0

def soft_reset(target):
    global min_dist_to_target
    if min_location_to_target is not None:
        min_dist_to_target = _dist_sq(
            min_location_to_target.x, min_location_to_target.y,
            target.x, target.y
        )
    else:
        reset_pathfinding()

def check_state(my_loc):
    if states is None:
        return

    if last_obstacle_found is None:
        x, y = 61, 61
    else:
        x, y = last_obstacle_found.x, last_obstacle_found.y

    state = (bug_path_index << 14) | (x << 8) | (y << 2)

    if rotate_right is not None:
        state |= 1 if rotate_right else 2

    if states[my_loc.x][my_loc.y] == state:
        reset_pathfinding()

    states[my_loc.x][my_loc.y] = state

def can_pass(ct, direction, my_pos):
    if direction is None or direction == Direction.CENTRE:
        return False

    ddx, ddy = DELTAS[direction]

    nx, ny = my_pos.x + ddx, my_pos.y + ddy
    if not (0 <= nx < width and 0 <= ny < height):
        return False

    if ct.can_move(direction):
        return True
    next_pos = Position(nx, ny)
    bid = ct.get_tile_builder_bot_id(next_pos)
    return ct.can_build_road(next_pos) and (bid is None or bid == my_id)

def execute_move(ct, direction, my_pos):
    if direction is None or direction == Direction.CENTRE:
        return

    if ct.can_move(direction):
        ct.move(direction)
        return

    ddx, ddy = DELTAS[direction]
    nx, ny = my_pos.x + ddx, my_pos.y + ddy
    if not (0 <= nx < width and 0 <= ny < height):
        return

    next_pos = Position(nx, ny)
    if ct.can_build_road(next_pos):
        ct.build_road(next_pos)

        if ct.can_move(direction):
            ct.move(direction)

# Helper setters for global state
def _set_rotate_right(val):
    global rotate_right
    rotate_right = val

def _set_last_obstacle(pos):
    global last_obstacle_found
    last_obstacle_found = pos

def _inc_turns_moving():
    global turns_moving_to_obstacle
    turns_moving_to_obstacle += 1

def _reset_turns_moving():
    global turns_moving_to_obstacle
    turns_moving_to_obstacle = 0
