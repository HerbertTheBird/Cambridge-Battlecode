from cambc import Direction, Position

from globals import DIRECTIONS, INF, DELTAS

def _dist_sq(ax: int, ay: int, bx: int, by: int) -> int:
    dx = ax - bx
    dy = ay - by
    return dx * dx + dy * dy

class Navigator:
    def __init__(self):
        # target
        self.destination: Position | None = None
        self.destination_type: str | None = None  # 'exact' | 'adjacent' | 'sensed' | 'visited'
        self.original_destination: Position | None = None  # set when destination_type == "adjacent"

        # bugnav state
        self.states = None
        self.bug_path_index = 0
        self.rotate_right = None
        self.last_obstacle_found = None
        self.prev_target = None
        self.min_dist_to_target = INF
        self.min_location_to_target = None
        self.turns_moving_to_obstacle = 0

        # constants
        self.MAX_TURNS_MOVING_TO_OBSTACLE = 2
        self.MIN_DIST_RESET = 3
        
        self.width = 0
        self.height = 0

    def set_statics(self, width, height, my_id):
        self.width = width
        self.height = height
        self.my_id = my_id

    def set_destination(self, target: Position, destination_type: str):
        self.destination = target
        self.destination_type = destination_type
        self.original_destination = target

    def clear_destination(self):
        self.destination = None
        self.destination_type = None
        self.original_destination = None

    def refresh_adjacent(self, ct, map_obj):
        """If destination_type is 'adjacent', update destination to the nearest passable adjacent tile."""
        if self.destination_type != "adjacent" or self.original_destination is None:
            return
        adj = self.get_adjacent_target(self.original_destination, ct)
        if adj is not None:
            self.destination = adj
        else:
            self.destination = self.original_destination

    def is_destination_reached(self, ct, map_obj):
        if self.destination is None:
            return True
        if self.destination_type == "exact":
            return ct.get_position() == self.destination
        if self.destination_type == "adjacent":
            ref = self.original_destination or self.destination
            my_pos = ct.get_position()
            return 0 < _dist_sq(my_pos.x, my_pos.y, ref.x, ref.y) <= 2
        if self.destination_type == "sensed":
            return ct.is_in_vision(self.destination)
        # "visited" or None
        return map_obj is not None and map_obj.is_visited(self.destination)

    def get_adjacent_target(self, pos: Position, ct) -> Position | None:
        """Return the passable tile adjacent to pos that is closest to our current position."""
        my_pos = ct.get_position()
        mx, my = my_pos
        px, py = pos
        w, h = self.width, self.height
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

    def init(self, ct, map_obj):
        if self.states is None:
            width = map_obj.width
            height = map_obj.height
            self.states = [[0] * height for _ in range(width)]

    def go_to(self, ct, map_obj):
        target = self.destination
        if target is None:
            return

        self.init(ct, map_obj)

        my_loc = ct.get_position()
        mlx, mly = my_loc
        tx, ty = target
        w, h = self.width, self.height

        # === TARGET CHANGE HANDLING ===
        if self.prev_target is None:
            self.reset_pathfinding()
            self.rotate_right = None
        else:
            dist = _dist_sq(tx, ty, self.prev_target.x, self.prev_target.y)
            if dist > 0:
                if dist >= self.MIN_DIST_RESET:
                    self.rotate_right = None
                    self.reset_pathfinding()
                else:
                    self.soft_reset(target)

        self.prev_target = target

        self.check_state(my_loc)

        d = _dist_sq(mlx, mly, tx, ty)
        if d == 0:
            return
        
        if ct.get_move_cooldown() > 0:
            return

        if d < self.min_dist_to_target:
            self.reset_pathfinding()
            self.min_dist_to_target = d
            self.min_location_to_target = my_loc

        # === GREEDY ===
        if self.last_obstacle_found is None:
            if self.try_greedy_move(ct, my_loc, target, map_obj):
                self.reset_pathfinding()
                return

        # === BUG MODE ===
        if self.last_obstacle_found is not None:
            direction = my_loc.direction_to(self.last_obstacle_found)
        else:
            direction = my_loc.direction_to(target)

        if self.can_pass(ct, direction, my_loc):
            self.execute_move(ct, direction, my_loc)
            if self.last_obstacle_found is not None:
                self.turns_moving_to_obstacle += 1
                ddx, ddy = DELTAS[direction]
                ox, oy = mlx + ddx, mly + ddy
                if (
                    self.turns_moving_to_obstacle >= self.MAX_TURNS_MOVING_TO_OBSTACLE
                    or not (0 <= ox < w and 0 <= oy < h)
                ):
                    self.reset_pathfinding()
                else:
                    self.last_obstacle_found = Position(ox, oy)
            return
        else:
            self.turns_moving_to_obstacle = 0

        self.check_rotate(ct, my_loc, target, direction)

        # === BUG LOOP ===
        for _ in range(16):
            if self.can_pass(ct, direction, my_loc):
                self.execute_move(ct, direction, my_loc)
                return

            ddx, ddy = DELTAS[direction]
            nx, ny = mlx + ddx, mly + ddy

            if not (0 <= nx < w and 0 <= ny < h):
                self.rotate_right = not self.rotate_right
            else:
                self.last_obstacle_found = Position(nx, ny)

            direction = direction.rotate_right() if self.rotate_right else direction.rotate_left()

        if self.can_pass(ct, direction, my_loc):
            self.execute_move(ct, direction, my_loc)

    def try_greedy_move(self, ct, my_loc, target, map_obj):
        direction = my_loc.direction_to(target)
        mx, my = my_loc
        tx, ty = target
        dist = _dist_sq(mx, my, tx, ty)

        dir1 = direction.rotate_right()
        dir2 = direction.rotate_left()

        candidates = []
        for turn_cost, candidate_dir in ((0, direction), (1, dir1), (1, dir2)):
            if not self.can_pass(ct, candidate_dir, my_loc):
                continue
            ddx, ddy = DELTAS[candidate_dir]
            nx, ny = mx + ddx, my + ddy
            next_dist = _dist_sq(nx, ny, tx, ty)
            if next_dist >= dist:
                continue
            risk = map_obj.get_enemy_launcher_adj_count(Position(nx, ny))
            candidates.append(((risk, next_dist, turn_cost), candidate_dir))

        if candidates:
            _, best_dir = min(candidates, key=lambda item: item[0])
            self.execute_move(ct, best_dir, my_loc)
            return True

        return False

    def check_rotate(self, ct, my_loc, target, direction):
        if self.rotate_right is not None:
            return

        dir_left = direction
        dir_right = direction

        for _ in range(8):
            if not self.can_pass(ct, dir_left, my_loc):
                dir_left = dir_left.rotate_left()
            else:
                break

        for _ in range(8):
            if not self.can_pass(ct, dir_right, my_loc):
                dir_right = dir_right.rotate_right()
            else:
                break

        mx, my = my_loc
        tx, ty = target
        dlx, dly = DELTAS[dir_left]
        drx, dry = DELTAS[dir_right]
        dist_left = _dist_sq(mx + dlx, my + dly, tx, ty)
        dist_right = _dist_sq(mx + drx, my + dry, tx, ty)

        self.rotate_right = dist_right < dist_left

    def reset_pathfinding(self):
        self.last_obstacle_found = None
        self.min_dist_to_target = INF
        self.bug_path_index += 1
        self.turns_moving_to_obstacle = 0

    def soft_reset(self, target):
        if self.min_location_to_target is not None:
            self.min_dist_to_target = _dist_sq(
                self.min_location_to_target.x, self.min_location_to_target.y,
                target.x, target.y
            )
        else:
            self.reset_pathfinding()

    def check_state(self, my_loc):
        if self.states is None:
            return

        if self.last_obstacle_found is None:
            x, y = 61, 61
        else:
            x, y = self.last_obstacle_found.x, self.last_obstacle_found.y

        state = (self.bug_path_index << 14) | (x << 8) | (y << 2)

        if self.rotate_right is not None:
            state |= 1 if self.rotate_right else 2

        if self.states[my_loc.x][my_loc.y] == state:
            self.reset_pathfinding()

        self.states[my_loc.x][my_loc.y] = state

    def can_pass(self, ct, direction, my_pos):
        if direction is None or direction == Direction.CENTRE:
            return False

        ddx, ddy = DELTAS[direction]

        nx, ny = my_pos.x + ddx, my_pos.y + ddy
        if not (0 <= nx < self.width and 0 <= ny < self.height):
            return False

        if ct.can_move(direction):
            return True
        next_pos = Position(nx, ny)
        bid = ct.get_tile_builder_bot_id(next_pos)
        return ct.can_build_road(next_pos) and (bid is None or bid == self.my_id)

    def execute_move(self, ct, direction, my_pos):
        if direction is None or direction == Direction.CENTRE:
            return

        # Case 1: already movable
        if ct.can_move(direction):
            ct.move(direction)
            return

        # Case 2: need to build road first
        ddx, ddy = DELTAS[direction]
        nx, ny = my_pos.x + ddx, my_pos.y + ddy
        if not (0 <= nx < self.width and 0 <= ny < self.height):
            return

        next_pos = Position(nx, ny)
        if ct.can_build_road(next_pos):
            ct.build_road(next_pos)

            # after building, try to move
            if ct.can_move(direction):
                ct.move(direction)
