import heapq
import time
import map_info
from cambc import Controller, Direction, Position, EntityType
import comms
import sys
from array import array
import math

weight = 1.5
MAX_ITER = None
TIME_CUTOFF = 1200

# Cache directions and their deltas to completely bypass Enum dictionary
# lookups and `Position.add()` overhead in tight loops.
ALL_DIRS = list(Direction)
ALL_DIRS_DELTAS = [(d, d.delta()) for d in ALL_DIRS]

CARD_DIR = [
    Direction.NORTH,
    Direction.SOUTH,
    Direction.EAST,
    Direction.WEST,
]
CARD = [
    (0, -1, 1),
    (0, 1, 1),
    (-1, 0, 1),
    (1, 0, 1),
]
DIRS = [
    (0, -1, 1),
    (0, 1, 1),
    (-1, 0, 1),
    (1, 0, 1),
    (-1, -1, 1),
    (1, -1, 1),
    (-1, 1, 1),
    (1, 1, 1),
]

bridge_cost = 5
CONV = [
    (0, -1, 1),
    (0, 1, 1),
    (-1, 0, 1),
    (1, 0, 1),
    (3, 0, bridge_cost),
    (-3, 0, bridge_cost),
    (0, 3, bridge_cost),
    (0, -3, bridge_cost),
    (2, 2, bridge_cost),
    (2, -2, bridge_cost),
    (-2, 2, bridge_cost),
    (-2, -2, bridge_cost),
    (2, 1, bridge_cost),
    (2, -1, bridge_cost),
    (-2, 1, bridge_cost),
    (-2, -1, bridge_cost),
    (1, 2, bridge_cost),
    (1, -2, bridge_cost),
    (-1, 2, bridge_cost),
    (-1, -2, bridge_cost),
]

seen = None
best_g = None
adjacent = None
parent = None
target = None
avoid = None

width = height = 0
rc = None
run_id = 1
avoid_id = 1

heap = []
iter = 0
dirs = None

path_dirs = None
path = []
path_idx = 0


def init(c: Controller):
    global width, height, rc, seen, parent, best_g, target, avoid, MAX_ITER
    width = c.get_map_width()
    height = c.get_map_height()
    rc = c
    seen = array('I', [0]) * (width * height)
    parent = array('I', [0]) * (width * height)
    target = array('I', [0]) * (width * height)
    avoid = array('I', [0]) * (width * height)
    best_g = array('I', [0]) * (width * height)
    MAX_ITER = int(math.sqrt(width * height)) * 4


def move(dir: Direction):
    px, py = rc.get_position().x, rc.get_position().y
    dx, dy = dir.delta()
    new_pos = Position(px + dx, py + dy)  # Bypass .add()

    if not map_info.in_bounds(new_pos):
        return False
    id = rc.get_tile_building_id(new_pos)

    if id and rc.get_entity_type(id) == EntityType.BARRIER and rc.can_destroy(new_pos):
        rc.destroy(new_pos)
    if rc.can_build_road(new_pos):
        rc.build_road(new_pos)
    if rc.can_move(dir):
        rc.move(dir)
        return True
    return False


def init_a_star(start_p: Position, target_p: Position | set[Position], input_dirs=DIRS, adjacent_in: bool = False):
    global heap, iter, dirs, adjacent, run_id
    adjacent = adjacent_in
    if isinstance(target_p, Position):
        target_p = {target_p}
    run_id += 1
    heappush = heapq.heappush
    abs_local = abs
    max_local = max
    tx = start_p.x
    ty = start_p.y
    dirs = input_dirs

    is_dirs = (dirs == DIRS)
    hash = lambda x, y: y * width + x
    heap.clear()

    for p in target_p:
        t = hash(p.x, p.y)
        target[t] = run_id

        # Inlined lambda logic
        nx = t % width
        ny = t // width
        if is_dirs:
            h0 = max_local(abs_local(nx - tx), abs_local(ny - ty))
        else:
            h0 = abs_local(nx - tx) + abs_local(ny - ty)

        heappush(heap, (h0, 0, True, t))
        seen[t] = run_id
    iter = 0


def a_star(start_p: Position, avoid_p: set[Position] = None) -> list[Position] | None:
    # Removed print statements for tight-loop speed
    global iter, avoid_id, path_dirs
    heappush = heapq.heappush
    heappop = heapq.heappop
    abs_local = abs
    max_local = max
    hp = heap
    hash = lambda x, y: y * width + x
    start = hash(start_p.x, start_p.y)
    tx = start_p.x
    ty = start_p.y

    is_dirs = (dirs == DIRS)  # Cache this boolean outside the loop

    if adjacent:
        left = -1 if start % width == 0 else start - 1
        right = -1 if start % width == width - 1 else start + 1
        up = -1 if start // width == 0 else start - width
        down = -1 if start // width == height - 1 else start + width

    if avoid_p is None:
        avoid_p = set()
    avoid_id += 1
    max_length = None

    for a in avoid_p:
        if (hash(a.x, a.y) == start and not adjacent) or target[hash(a.x, a.y)] == run_id:
            continue
        avoid[hash(a.x, a.y)] = avoid_id

    if path is not None and len(path) > 0 and dirs == path_dirs:
        if path[0].distance_squared(Position(start % width, start // width)) <= 2 and target[
            hash(path[-1].x, path[-1].y)] == run_id:
            max_length = len(path) - 1

    if seen[start] == run_id:
        heappush(hp, (0, 0, False, start))
    seen[start] = 0

    while hp:
        if rc.get_cpu_time_elapsed() > TIME_CUTOFF:
            return None
        iter += 1
        if iter > MAX_ITER:
            break
        f, g, card, pos = heappop(hp)
        pos = abs_local(pos)
        if avoid[pos] == avoid_id:
            continue

        g *= -1
        if (not adjacent and pos == start) or (adjacent and (pos == left or pos == right or pos == up or pos == down)):
            path_out = []
            path_dirs = dirs
            while pos != -1:
                path_out.append(Position(pos % width, pos // width))
                pos = parent[pos] if target[pos] != run_id else -1
            hp.clear()
            return path_out

        for dx, dy, cost in dirs:
            nx = pos % width + dx
            ny = pos // width + dy
            if nx < 0 or nx >= width or ny < 0 or ny >= height:
                continue
            n = hash(nx, ny)
            if avoid[n] == avoid_id:
                continue
            ng = g + cost
            if ng >= best_g[n] and seen[n] == run_id:
                continue

            best_g[n] = ng
            seen[n] = run_id
            parent[n] = pos

            # INLINED HEURISTIC LAMBDA
            if is_dirs:
                h0 = max_local(abs_local(nx - tx), abs_local(ny - ty))
            else:
                h0 = abs_local(nx - tx) + abs_local(ny - ty)

            new_h = 0 if h0 == 0 else 1.2 + (weight - 1.2) * max_local(0, 1 - g / h0)
            new_f = ng + h0 * new_h

            if max_length is not None and ng + h0 > max_length:
                continue

            card = dx == 0 or dy == 0
            heappush(
                hp,
                (new_f, -ng, card, n if ng % 4 <= 1 else -n)
            )

    hp.clear()
    return []


def moves_through_impassible(path: list[Position], avoid: set[Position] = None) -> bool:
    if avoid is None:
        return False
    for i in range(1, len(path) - 1):
        if path[i] in avoid:
            return True
    return False


def move_to(target: Position, destroy_barrier: bool = False):
    global path, path_idx
    avoid = map_info.get_avoid(False, True, not destroy_barrier, False)
    if len(heap) == 0:
        init_a_star(rc.get_position(), target)
    next_path = a_star(rc.get_position(), avoid)
    if next_path is not None and moves_through_impassible(next_path, avoid):
        init_a_star(rc.get_position(), target)
        next_path = a_star(rc.get_position(), avoid)
    if next_path is not None:
        path = next_path
        path_idx = 0
        for i in range(len(path) - 1):
            rc.draw_indicator_line(path[i], path[i + 1], 0, 50, 0)
    elif path is not None and len(path) > 1:
        for i in range(len(path) - 1):
            rc.draw_indicator_line(path[i], path[i + 1], 0, 0, 50)

    if path is None or len(path) < path_idx + 2:
        if destroy_barrier:
            return False
        else:
            return move_to(target, True)

    move_dir = path[path_idx].direction_to(path[path_idx + 1])
    marked = False

    my_pos = rc.get_position()
    # Bypass `for dir in Direction` and `Position.add`
    for dr, (dx, dy) in ALL_DIRS_DELTAS:
        pos = Position(my_pos.x + dx, my_pos.y + dy)
        if not map_info.in_bounds(pos):
            continue
        id = rc.get_tile_building_id(pos)

        if id and rc.get_entity_type(id) == EntityType.LAUNCHER and rc.get_team(id) == rc.get_team():
            r = int(math.sqrt(rc.get_vision_radius_sq()))
            best = None
            best_dist = 0
            for x in range(pos.x - r, pos.x + r + 1):
                for y in range(pos.y - r, pos.y + r + 1):
                    p = Position(x, y)
                    if rc.is_in_vision(p) and rc.is_tile_passable(p) and target.distance_squared(p) <= 2:
                        dist = max(abs(p.x - target.x), abs(p.y - target.y))
                        if not best or best_dist > dist * weight:
                            best_dist = dist * weight
                            best = p
            if best and best_dist < len(path) - path_idx:
                for dr2, (dx2, dy2) in ALL_DIRS_DELTAS:
                    p2 = Position(pos.x + dx2, pos.y + dy2)
                    if not map_info.in_bounds(p2):
                        continue
                    id2 = rc.get_tile_building_id(p2)
                    if id2 and rc.get_team(id2) == rc.get_team() and rc.get_entity_type(
                            id2) == EntityType.ROAD and rc.can_destroy(p2) and dr != Direction.CENTRE:
                        rc.destroy(p2)
                    if rc.can_place_marker(p2):
                        rc.place_marker(p2, comms.encode_launch(best))
                        marked = True
                        break
        if marked:
            break

    if marked:
        return
    if move(move_dir):
        path_idx += 1
    return True


def calculate_path(target: Position, start=None):
    global path, path_idx
    if start == None:
        start = rc.get_position()

    avoid = map_info.get_avoid(False, True)
    if len(heap) == 0:
        init_a_star(rc.get_position(), target)
    next_path = a_star(rc.get_position(), avoid)
    if next_path is not None and moves_through_impassible(next_path, avoid):
        init_a_star(rc.get_position(), target)
        next_path = a_star(rc.get_position(), avoid)
    if next_path is not None:
        path = next_path
        path_idx = 0
        for i in range(len(path) - 1):
            rc.draw_indicator_line(path[i], path[i + 1], 0, 50, 0)
    elif path is not None and len(path) > 1:
        for i in range(len(path) - 1):
            rc.draw_indicator_line(path[i], path[i + 1], 0, 0, 50)
    if path is None or len(path) < path_idx + 2:
        return None
    return path


def execute_path(sample_path=None, path_idx_in=None):
    global path_idx
    if sample_path == None:
        sample_path = path
        idx = path_idx
    else:
        idx = path_idx_in or 0

    if idx > len(sample_path) - 2:
        return False

    dir = sample_path[idx].direction_to(sample_path[idx + 1])
    if move(dir):
        if sample_path == path:
            path_idx += 1
        return True
    return False


def calculate_conveyor_path(ore: Position, update: bool = False):
    global path, path_idx
    core = map_info.my_core

    # Bypass Enum and `.add()`
    target = {Position(core.x + dx, core.y + dy) for _, (dx, dy) in ALL_DIRS_DELTAS}

    for x in range(map_info.width):
        for y in range(map_info.height):
            b = map_info.building[x][y]
            if b and map_info.is_conveyor(b.type) and b.load and b.load < 3 and b.team == rc.get_team():
                target.add(Position(x, y))

    avoid = map_info.get_avoid(True, False, False, True)
    for dir in CARD_DIR:
        dx, dy = dir.delta()
        pos = Position(ore.x + dx, ore.y + dy)

        if map_info.in_bounds(pos) and map_info.building[pos.x][pos.y] and map_info.building[pos.x][
            pos.y].team == rc.get_team() and map_info.building[pos.x][pos.y].type == EntityType.BARRIER:
            avoid.discard(pos)

    if len(heap) == 0:
        init_a_star(ore, target, CONV, not update)
    next_path = a_star(ore, avoid)
    if next_path and moves_through_impassible(next_path, avoid):
        init_a_star(ore, target, CONV, not update)
        next_path = a_star(ore, avoid)

    if next_path:
        path = next_path
        path_idx = 0
        for i in range(len(path) - 1):
            rc.draw_indicator_line(path[i], path[i + 1], 0, 50, 0)
    elif path and len(path) > 1:
        for i in range(len(path) - 1):
            rc.draw_indicator_line(path[i], path[i + 1], 0, 0, 50)

    if len(path) == 0:
        heap.clear()

    if path is None or len(path) < path_idx + 2:
        return None
    return path


def calculate_launcher_position(path: list[Position], ore: Position) -> Position | None:
    avoid = map_info.get_avoid(True, False)
    avoid.update(path)

    current_pos = rc.get_position()
    width_local = map_info.width
    height_local = map_info.height
    building = map_info.building
    team = rc.get_team()
    path_len = len(path)

    for i in range(path_len - 1):
        possible: set[Position] | None = None
        last_possible: set[Position] | None = None

        for j in range(i, path_len - 1):
            base = path[j]

            here = set()
            has_launcher = False

            # Bypass `.add()` and dynamic Enum lookup
            for _, (dx, dy) in ALL_DIRS_DELTAS:
                x = base.x + dx
                y = base.y + dy

                if x < 0 or x >= width_local or y < 0 or y >= height_local:
                    continue

                candidate = Position(x, y)
                here.add(candidate)

                b = building[x][y]
                if b and b.team == team and b.type == EntityType.LAUNCHER:
                    has_launcher = True

            if has_launcher:
                continue

            if possible is None:
                new_possible = here - avoid
            else:
                new_possible = possible.intersection(here)
                if new_possible:
                    new_possible.difference_update(avoid)

            if not new_possible:
                break

            last_possible = new_possible
            possible = new_possible

        if last_possible:
            best = min(last_possible, key=lambda p: p.distance_squared(current_pos))
            return best

    return None