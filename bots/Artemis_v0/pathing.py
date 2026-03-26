import heapq
import time
import map_info
from cambc import Controller, Direction, Position, EntityType
import sys
from array import array
import time
weight = 1.5
MAX_ITER = 500
TIME_CUTOFF = 1600
# 4-direction movement

#todo
#make bridges more favorable, weighted a*, no small bridges, prevent revisits, beam search
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
    global width, height, rc, seen, parent, best_g, target, avoid
    width = c.get_map_width()
    height = c.get_map_height()
    rc = c
    seen = array('I', [0])*(width*height)
    parent = array('I', [0])*(width*height)
    target = array('I', [0])*(width*height)
    avoid = array('I', [0])*(width*height)
    best_g = array('I', [0])*(width*height)
def move(dir: Direction):
    new_pos = rc.get_position().add(dir)
    if new_pos in map_info.building and map_info.building[new_pos] and map_info.building[new_pos].type == EntityType.BARRIER and rc.can_destroy(new_pos):
        rc.destroy(new_pos)
    if rc.can_build_road(new_pos):
        rc.build_road(new_pos)
    if rc.can_move(dir):
        rc.move(dir)
        return True
    return False
def init_a_star(start_p: Position, target_p: Position| set[Position], input_dirs=DIRS, adjacent_in: bool = False):
    global heap, iter, dirs, adjacent, run_id
    adjacent = adjacent_in
    if isinstance(target_p, Position):
        target_p = {target_p}
    run_id += 1
    heappush = heapq.heappush
    heappop = heapq.heappop
    abs_local = abs
    max_local = max
    tx = start_p.x
    ty = start_p.y
    dirs = input_dirs
    if dirs == DIRS:
        h = lambda pos: (max_local(abs_local(pos%width - tx), abs_local(pos//width - ty)))
    else:
        h = lambda pos: (abs_local(pos%width - tx) + abs_local(pos//width - ty))
    hash = lambda x, y: y*width + x
    heap = []
    for p in target_p:
        t = hash(p.x, p.y)
        target[t] = run_id
        heappush(heap, (h(t), 0, True, t))
        seen[t] = run_id
    iter = 0
def a_star(start_p: Position, avoid_p: set[Position] = None) -> list[Position] | None:
    global iter, avoid_id, path_dirs
    heappush = heapq.heappush
    heappop = heapq.heappop
    abs_local = abs
    max_local = max
    hp = heap
    hash = lambda x, y: y*width + x
    start = hash(start_p.x, start_p.y)
    tx = start_p.x
    ty = start_p.y

    if adjacent:
        left = -1 if start%width == 0 else start-1
        right = -1 if start%width == width-1 else start+1
        up = -1 if start//width == 0 else start-width
        down = -1 if start//width == height-1 else start+width
    if avoid_p is None:
        avoid_p = set()
    avoid_id += 1
    avoid_changed = False
    max_length = None
    for a in avoid_p:
        if (hash(a.x, a.y) == start and not adjacent) or target[hash(a.x, a.y)] == run_id:
            continue
        if avoid[hash(a.x, a.y)] != avoid_id - 1:
            avoid_changed = True
        avoid[hash(a.x, a.y)] = avoid_id
    if not avoid_changed and path is not None and len(path) > 0 and dirs == path_dirs:
        if path[0].distance_squared(Position(start%width, start//width)) <= 2 and target[hash(path[-1].x, path[-1].y)] == run_id:
            max_length = len(path)-1
    if dirs == DIRS:
        h = lambda pos: (max_local(abs_local(pos%width - tx), abs_local(pos//width - ty)))
    else:
        h = lambda pos: (abs_local(pos%width - tx) + abs_local(pos//width - ty))
    if seen[start] == run_id:
        heappush(hp, (0, 0, False, start))
    seen[start] = 0
    while hp:
        # time.sleep(0.0001)
        if rc.get_cpu_time_elapsed() > TIME_CUTOFF:
            return None
        iter += 1
        if iter > MAX_ITER:
            break
        f, g, card, pos = heappop(hp)
        pos = abs_local(pos)
        if avoid[pos] == avoid_id:
            continue
        rc.draw_indicator_dot(Position(pos%width, pos//width), 50, 0, 0)
        g *= -1
        if (not adjacent and pos == start) or (adjacent and (pos == left or pos == right or pos == up or pos == down)):
            path_out = []
            path_dirs = dirs
            while pos != -1:
                path_out.append(Position(pos%width, pos//width))
                pos = parent[pos] if target[pos] != run_id else -1
            hp.clear()
            return path_out

        for dx, dy, cost in dirs:
            nx = pos%width + dx
            ny = pos//width + dy
            if nx < 0 or nx >= width or ny < 0 or ny >= height:
                continue
            n = hash(nx, ny)
            if avoid[n] == avoid_id:
                continue
            ng = g+cost
            if ng >= best_g[n] and seen[n] == run_id:
                continue
            best_g[n] = ng
            seen[n] = run_id
            parent[n] = pos
            h0 = h(n)
            new_h = 0 if h0 == 0 else 1.2+(weight-1.2)*max_local(0, 1-g/h0)
            new_f = ng + h(n)*new_h
            if max_length is not None and ng + h(n) >= max_length:
                continue
            card = dx == 0 or dy == 0
            heappush(
                hp,
                (new_f, -ng, card, n if ng%4 <= 1 else -n)
            )
    hp.clear()
    return []
def moves_through_impassible(path: list[Position], avoid: set[Position] = None) -> bool:
    if avoid is None:
        return False
    for i in range(1, len(path)-1):
        if path[i] in avoid:
            return True
    return False

def move_to(target: Position, destroy_barriers: bool = False):
    global path, path_idx
    avoid = map_info.get_avoid(False, True, not destroy_barriers)
    if len(heap) == 0:
        init_a_star(rc.get_position(), target)
    next_path = a_star(rc.get_position(), avoid)
    if next_path is not None and moves_through_impassible(next_path, avoid):
        init_a_star(rc.get_position(), target)
        next_path = a_star(rc.get_position(), avoid)
    if next_path is not None:
        path = next_path
        path_idx = 0
        for i in range(len(path)-1):
            rc.draw_indicator_line(path[i], path[i+1], 0, 50, 0)
    elif path is not None and len(path) > 1:
        for i in range(len(path)-1):
            rc.draw_indicator_line(path[i], path[i+1], 0, 0, 50)
    if path is None or len(path) < path_idx+2:
        if destroy_barriers:
            return False
        else:
            return move_to(target, True)
    dir = path[path_idx].direction_to(path[path_idx+1])
    if move(dir):
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
        for i in range(len(path)-1):
            rc.draw_indicator_line(path[i], path[i+1], 0, 50, 0)
    elif path is not None and len(path) > 1:
        for i in range(len(path)-1):
            rc.draw_indicator_line(path[i], path[i+1], 0, 0, 50)
    if path is None or len(path) < path_idx+2:
        return None
    return path

def execute_path(sample_path=None, path_idx=0):
    if (sample_path == None):
        sample_path = path
    if path_idx > len(sample_path)-2:
        return False
    dir = path[path_idx].direction_to(sample_path[path_idx+1])
    if move(dir):
        path_idx += 1
        return True
    return False

def calculate_conveyor_path(ore: Position, update:bool = False):
    core = map_info.my_core
    target = {core.add(i) for i in Direction}
    for p, b in map_info.building.items():
        if b and map_info.is_conveyor(b) and b.load and b.load < 4:
            target.add(p)
    avoid = map_info.get_avoid(True, False, False)
    for dir in CARD_DIR:
        pos = ore.add(dir)
        if pos in map_info.building and map_info.building[pos] and map_info.building[pos].team == rc.get_team() and map_info.building[pos].type == EntityType.BARRIER:
            avoid.discard(pos)
    if len(heap) == 0:
        init_a_star(ore, target, CONV, not update)
    next_path = a_star(ore, avoid)
    if next_path is not None and moves_through_impassible(next_path, avoid):
        init_a_star(ore, target, CONV, not update)
        next_path = a_star(ore, avoid)
    if next_path is not None:
        path = next_path
        path_idx = 0
        for i in range(len(path)-1):
            rc.draw_indicator_line(path[i], path[i+1], 0, 50, 0)
    elif path is not None and len(path) > 1:
        for i in range(len(path)-1):
            rc.draw_indicator_line(path[i], path[i+1], 0, 0, 50)
    if len(path) == 0:
        heap.clear()
    if path is None or len(path) < path_idx+2:
        return None
    return path


def calculate_launcher_positions(path: list[Position], ore: Position) -> list[Position]:
    avoid = map_info.get_avoid(True, False)
    for p in path:
        avoid.add(p)
    result: list[Position] = []
    pos = rc.get_position()

    i = 0
    while i < len(path)-1:
        possible: set[Position] | None = None
        last_possible: set[Position] | None = None
        j = i

        while j < len(path)-1:
            here = {
                path[j].add(dir)
                for dir in Direction
                if 0 <= path[j].add(dir).x < map_info.width
                and 0 <= path[j].add(dir).y < map_info.height
            }
            done = False
            for pos in here:
                if pos in map_info.building and map_info.building[pos] and map_info.building[pos].team == rc.get_team() and map_info.building[pos].type == EntityType.LAUNCHER:
                    done = True
            if done:
                j += 1
                continue
            here -= avoid

            if possible is None:
                new_possible = here
            else:
                new_possible = possible & here

            if not new_possible:
                break

            last_possible = new_possible
            possible = new_possible
            j += 1

        if not last_possible:
            i += 1
            continue

        best = min(last_possible, key=lambda p: p.distance_squared(pos))
        result.append(best)

        pos = best
        i = j

    return result