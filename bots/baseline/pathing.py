import heapq
import time
import map_info
from cambc import Controller, Direction, Position, EntityType
import sys
from array import array
weight = 1.5
MAX_ITER = 200
# 4-direction movement

#todo
#make bridges more favorable, weighted a*, no small bridges, prevent revisits, beam search
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
bridge_cost = 4
CONV = [
    (0, -1, 1),
    (0, 1, 1),
    (-1, 0, 1),
    (1, 0, 1),
    (3, 0, bridge_cost),
    (-3, 0, bridge_cost),
    (0, 3, bridge_cost),
    (0, -3, bridge_cost),
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
parent = None
start = None
target = None
avoid = None
width = height = 0
rc = None
run_id = 0
def init(c: Controller):
    global width, height, rc, seen, parent, start, target, avoid
    width = c.get_map_width()
    height = c.get_map_height()
    rc = c
    seen = array('I', [0])*(width*height)
    parent = array('I', [0])*(width*height)
    start = array('I', [0])*(width*height)
    target = array('I', [0])*(width*height)
    avoid = array('I', [0])*(width*height)
def move(dir: Direction):
    new_pos = rc.get_position().add(dir)
    if rc.can_build_road(new_pos):
        rc.build_road(new_pos)
    if rc.can_move(dir):
        rc.move(dir)
    pass
def a_star(start_p:Position, target_p: Position| set[Position], avoid_p: set[Position] = None, dirs=DIRS, adjacent: bool = False) -> list[Position]:
    start_time = time.perf_counter()
    if avoid_p is None:
        avoid_p = set()
    if isinstance(target_p, Position):
        target_p = {target_p}
    global run_id
    run_id += 1
    heappush = heapq.heappush
    heappop = heapq.heappop
    abs_local = abs
    max_local = max
    tx = start_p.x
    ty = start_p.y
    if dirs == DIRS:
        h = lambda pos: (max_local(abs_local(pos%width - tx), abs_local(pos//width - ty)))
    else:
        h = lambda pos: (abs_local(pos%width - tx) + abs_local(pos//width - ty))
    hash = lambda x, y: y*width + x
    heap = []
    for p in target_p:
        t = hash(p.x, p.y)
        target[t] = run_id
        heappush(heap, (h(t), 0, t))
        seen[t] = run_id
    if adjacent:
        for dx, dy in CARD:
            start[hash(start_p.x + dx, start_p.y + dy)] = run_id
    else:
        start[hash(start_p.x, start_p.y)] = run_id
    for a in avoid_p:
        if a == start_p:
            continue
        avoid[hash(a.x, a.y)] = run_id
    iter = 0
    while heap:
        iter += 1
        if iter > MAX_ITER:
            break
        f, g, pos = heappop(heap)
        rc.draw_indicator_dot(Position(pos%width, pos//width), 255, 0, 0)
        g *= -1
        if start[pos] == run_id:
            path = []
            while pos != 0:
                path.append(Position(pos%width, pos//width))
                pos = parent[pos]
            end_time = time.perf_counter()
            print((end_time-start_time)*1000000, "real time")
            return path

        for dx, dy, cost in dirs:
            nx = pos%width + dx
            ny = pos//width + dy
            if nx < 0 or nx >= width or ny < 0 or ny >= height:
                continue
            n = hash(nx, ny)
            if avoid[n] == run_id:
                continue
            if seen[n] == run_id:
                continue
            ng = g+cost
            seen[n] = run_id
            parent[n] = pos
            h0 = h(n)
            new_h = 0 if h0 == 0 else 1.2+(weight-1.2)*max_local(0, 1-g/h0)

            heappush(
                heap,
                (ng + h(n)*new_h, -ng, n)
            )
    return None
def move_to(target: Position):
    avoid = map_info.get_avoid(False, True)
    path = a_star(rc.get_position(), target, avoid)
    if path is None or len(path) < 2:
        return
    dir = path[0].direction_to(path[1])
    move(dir)
    pass
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
        if b.team == rc.get_team() and map_info.is_conveyor(b.type) and b.load is not None and b.load < 4:
            start_positions.add(p)
    return ore_path(start_positions, target, True)
def new_conveyor_path(target: Position):
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
        if b.team == rc.get_team() and map_info.is_conveyor(b.type) and b.load is not None and b.load < 4:
            start_positions.add(p)
    return ore_path(start_positions, target, False)
def ore_path(start_positions: set, target: Position, adjacent: bool):
    avoid = map_info.get_avoid(False, False)
    for p, b in map_info.building.items():
        if b is None:
            continue
        if b.team == rc.get_team() and map_info.is_conveyor(b.type) and b.load is None or b.load == 4:
            avoid.add(p)
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

    def h(pos: Position) -> float:
        return (abs(pos.x - target.x) + abs(pos.y - target.y))*weight

    # Goal = any cardinal tile next to target
    goal_positions = set()
    if adjacent:
        for _, dx, dy in CARD:
            p = Position(target.x + dx, target.y + dy)
            if is_walkable(p):
                goal_positions.add(p)
    else:
        goal_positions.add(target)

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

    iter = 0

    while open_heap:
        iter += 1
        if iter > MAX_ITER:
            return None

        f, g, current = heappop(open_heap)
        g *= -1
        rc.draw_indicator_dot(current, 0, 0, 255)
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
                heappush(open_heap, (new_cost + h(nxt), -new_cost, nxt))

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
                    heappush(open_heap, (new_cost + h(nxt), -new_cost, nxt))

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