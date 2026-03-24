from heapq import heappush, heappop
import time
import map_info
from cambc import Controller, Direction, Position, EntityType
import sys

# 4-direction movement
CARD = [
    (Direction.NORTH, 0, -1),
    (Direction.SOUTH, 0, 1),
    (Direction.WEST, -1, 0),
    (Direction.EAST, 1, 0),
]
DIRS = [
    (Direction.NORTH, 0, -1),
    (Direction.SOUTH, 0, 1),
    (Direction.WEST, -1, 0),
    (Direction.EAST, 1, 0),
    (Direction.NORTHWEST, -1, -1),
    (Direction.NORTHEAST, 1, -1),
    (Direction.SOUTHWEST, -1, 1),
    (Direction.SOUTHEAST, 1, 1),
]
width = height = 0
rc = None
def init(c: Controller):
    global width, height, rc
    width = c.get_map_width()
    height = c.get_map_height()
    rc = c
def _key(pos: Position) -> tuple[int, int]:
    return (pos.x, pos.y)

def move_adjacent(
    start: Position,
    target: Position,
    adjacent: Position,
    avoid: set[Position] | None = None,
):
    if avoid is None:
        avoid = set()

    best_dir = None
    best_score = None

    for _, dx, dy in CARD:
        cand = Position(adjacent.x + dx, adjacent.y + dy)
        if not (0 <= cand.x < width and 0 <= cand.y < height):
            continue
        if cand in avoid:
            continue

        first_dir, dist1 = move_card(start, cand, avoid)
        if first_dir is None:
            continue
        # estimate future distance from that adjacent tile to the final target
        second_dir, dist2 = move_card(cand, target, avoid)
        if second_dir is None:
            continue
        score = dist1 + dist2
        if best_score is None or score < best_score:
            best_score = score
            best_dir = first_dir

    if best_dir is None:
        return None, 0
    return best_dir, best_score
def move_toward(start:Position, target: Position, avoid: set[Position] | None = None):
    dirs = [
        (d, *d.delta())
        for d in Direction
        if d != Direction.CENTRE
    ]
    return move_card(start, target, avoid, dirs)
def move_card(start:Position, target: Position, avoid: set[Position] | None = None, dirs=CARD):
    start_time = time.perf_counter()
    
    if avoid is None:
        avoid = set()

    sx, sy = start.x, start.y
    tx, ty = target.x, target.y

    if sx == tx and sy == ty:
        return None, 0

    avoid_keys = {(p.x, p.y) for p in avoid}

    # Same kind of blocking logic your BFS uses
    if (tx, ty) in avoid_keys:
        return None, 0

    # local bindings for speed
    heappush_local = heappush
    heappop_local = heappop
    abs_local = abs

    def h(x: int, y: int) -> int:
        return abs_local(x - tx) + abs_local(y - ty)
    # heap item:
    # (f, g, x, y, first_dir)
    #
    # first_dir is the direction taken from the start to reach this node.
    # We keep it in the heap item so we do not need a separate parent map.
    open_heap = []
    heappush_local(open_heap, (h(sx, sy), 0, sx, sy, None))

    # best known g for each tile
    best_g = {(sx, sy): 0}

    while open_heap:
        f, g, x, y, first_dir = heappop_local(open_heap)
        g *= -1
        # stale entry check
        if best_g.get((x, y)) != g:
            continue

        if x == tx and y == ty:
            end_time = time.perf_counter()
            return first_dir, g

        ng = g + 1

        for direction, dx, dy in dirs:
            nx = x + dx
            ny = y + dy

            if nx < 0 or nx >= width or ny < 0 or ny >= height:
                continue

            nkey = (nx, ny)
            if nkey in avoid_keys:
                continue

            old_g = best_g.get(nkey)
            if old_g is not None and ng >= old_g:
                continue

            best_g[nkey] = ng
            next_first_dir = direction if first_dir is None else first_dir
            heappush_local(
                open_heap,
                (ng + h(nx, ny), -ng, nx, ny, next_first_dir)
            )
    return None, 0
def conveyor_path(target: Position):
    if target is None or map_info.my_core is None:
        return None

    core = map_info.my_core


        # Start = 8 tiles around core
    start_positions = set()
    core = map_info.my_core

    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            p = Position(core.x + dx, core.y + dy)
            start_positions.add(p)

    # Also start from my existing conveyors
    for p, b in map_info.building.items():
        if b is None:
            continue

        # replace these checks with your actual API fields if needed
        if getattr(b, "team", None) == rc.get_team() and getattr(b, "type", None) == EntityType.CONVEYOR:
            start_positions.add(p)
    return ore_path(start_positions, target)

def ore_path(start_positions: set, target: Position):
    avoid = map_info.get_avoid(False, False)
    if not start_positions:
        return None
    def is_walkable(pos: Position) -> bool:
        if not map_info.in_bounds(pos):
            return False
        if not pos in map_info.ground:
            return False
        if pos in avoid and pos not in start_positions:
            return False
        return True

    def h(pos: Position) -> int:
        return abs(pos.x - target.x) + abs(pos.y - target.y)

    # Goal = any cardinal tile next to target
    goal_positions = set()
    for _, dx, dy in CARD:
        p = Position(target.x + dx, target.y + dy)
        if is_walkable(p):
            goal_positions.add(p)

    if not goal_positions:
        return None

    # Already there
    for p in start_positions:
        if p in goal_positions:
            return [p]

    open_heap = []
    best_cost = {}
    parent_map = {}

    for start in start_positions:
        best_cost[start] = 0
        parent_map[start] = None
        heappush(open_heap, (h(start), 0, start))

    iteration_count = 0
    max_iterations = 2000

    while open_heap:
        iteration_count += 1
        if iteration_count > max_iterations:
            return None

        f, g, current = heappop(open_heap)

        if g != best_cost.get(current):
            continue

        if current in goal_positions:
            path = []
            while current is not None:
                path.append(current)
                current = parent_map[current]

            if len(path) > 100:
                return None

            for i in range(len(path) - 1):
                rc.draw_indicator_line(path[i], path[i + 1], 0, 0, 255)
                rc.draw_indicator_dot(path[i], 0, 0, 255*i//len(path))

            return path

        # Cardinal moves
        for _, dx, dy in CARD:
            nxt = Position(current.x + dx, current.y + dy)
            if not is_walkable(nxt):
                continue

            new_cost = g + 1
            if new_cost < best_cost.get(nxt, float("inf")):
                best_cost[nxt] = new_cost
                parent_map[nxt] = current
                heappush(open_heap, (new_cost + h(nxt), new_cost, nxt))

        # Jump moves
        for dx in range(-3, 4):
            for dy in range(-3, 4):
                if dx == 0 and dy == 0:
                    continue
                if dx * dx + dy * dy > 9:
                    continue

                nxt = Position(current.x + dx, current.y + dy)
                if not is_walkable(nxt):
                    continue

                new_cost = g + 5
                if new_cost < best_cost.get(nxt, float("inf")):
                    best_cost[nxt] = new_cost
                    parent_map[nxt] = current
                    heappush(open_heap, (new_cost + h(nxt), new_cost, nxt))

    return None
def explore_move(target: Position):
    on = rc.get_tile_building_id(rc.get_position())
    if rc.get_entity_type(on) == EntityType.CONVEYOR and rc.get_team(on) != rc.get_team():
        if rc.can_fire(rc.get_position()):
            rc.fire(rc.get_position())
        return False
    avoid = map_info.get_avoid(False, True)
    for tile in avoid:
        rc.draw_indicator_dot(tile, 255, 0, 0)
    if target in avoid:
        if target.distance_squared(rc.get_position()) <= 2:
            return False
        avoid.remove(target)
    move = move_toward(rc.get_position(), target, avoid)[0]
    if not move:
        return False
    rc.draw_indicator_line(rc.get_position(), rc.get_position().add(move), 255, 255, 0)
    destroy = rc.get_position()
    next = rc.get_position().add(move)
    if rc.get_entity_type(rc.get_tile_building_id(destroy)) != EntityType.ROAD:
        destroy = False
    if rc.can_move(move):
        rc.move(move)
    else:
        if rc.can_build_road(next):
            rc.build_road(next)
        if rc.can_move(move):
            rc.move(move)
        else:
            return False
    # if destroy and rc.can_destroy(destroy):
    #     rc.destroy(destroy)
    return True


def build_path(path: list[Position], path_i: int):
    if not path or path_i >= len(path) - 1:
        return 0  # No path, path is too short, or index is out of bounds

    current_pos = rc.get_position()

    # Ensure we are at the correct position in the path
    if current_pos.distance_squared(path[path_i]) > 2:
        explore_move(path[path_i])
        return 0

    start = path[path_i]
    if rc.get_team(rc.get_tile_building_id(start)) != rc.get_team():
        if current_pos == start:
            if rc.can_fire(start):
                rc.fire(start)
        else:
            explore_move(start)
        return 0
    
    end = path[path_i + 1]

    # Calculate the Manhattan distance between the current and next tile
    dist = abs(start.x - end.x) + abs(start.y - end.y)
    existing_id = rc.get_tile_building_id(start)
    if existing_id and (rc.get_entity_type(existing_id) == EntityType.CONVEYOR or rc.get_entity_type(existing_id) == EntityType.BRIDGE) and rc.get_team(existing_id) == rc.get_team():
        return 1000
    if dist == 1:  # Cardinal move (cost 1)
        direction = start.direction_to(end)
        if rc.can_destroy(start):
            rc.destroy(start)
        if rc.can_build_conveyor(start, direction):
            rc.build_conveyor(start, direction)
            explore_move(end)
            return 1
    else:
        if rc.can_destroy(start):
            rc.destroy(start)
        if rc.can_build_bridge(start, end):
            rc.build_bridge(start, end)
            explore_move(end)
            return 1
    return 0