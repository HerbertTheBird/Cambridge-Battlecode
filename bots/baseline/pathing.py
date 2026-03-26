import heapq
import time
import map_info
from cambc import Controller, Direction, Position, EntityType
import sys
from array import array
import time
weight = 1.5
MAX_ITER = 500
TIME_CUTOFF = 1900
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
avoid_id = 0


heap = []
iter = 0
dirs = None
tx = 0
ty = 0

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
def init_a_star(start_p:Position, target_p: Position| set[Position], input_dirs=DIRS, adjacent: bool = False):
    global heap, iter, dirs, tx, ty
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
    if adjacent:
        for dx, dy, _ in CARD:
            start[hash(start_p.x + dx, start_p.y + dy)] = run_id
    else:
        start[hash(start_p.x, start_p.y)] = run_id
    iter = 0
def a_star(avoid_p: set[Position] = None) -> list[Position] | None:
    global iter, avoid_id
    hash = lambda x, y: y*width + x
    if avoid_p is None:
        avoid_p = set()
    avoid_id += 1
    for a in avoid_p:
        if start[hash(a.x, a.y)] == run_id or target[hash(a.x, a.y)] == run_id:
            continue
        avoid[hash(a.x, a.y)] = avoid_id
    heappush = heapq.heappush
    heappop = heapq.heappop
    abs_local = abs
    max_local = max
    hp = heap
    if dirs == DIRS:
        h = lambda pos: (max_local(abs_local(pos%width - tx), abs_local(pos//width - ty)))
    else:
        h = lambda pos: (abs_local(pos%width - tx) + abs_local(pos//width - ty))
    while hp:
        # time.sleep(0.0001)
        if rc.get_cpu_time_elapsed() > TIME_CUTOFF:
            return None
        iter += 1
        if iter > MAX_ITER:
            break
        f, g, card, pos = heappop(hp)
        if avoid[pos] == avoid_id:
            continue
        rc.draw_indicator_dot(Position(pos%width, pos//width), 255, 0, 0)
        g *= -1
        if start[pos] == run_id:
            path = []
            while pos != -1:
                path.append(Position(pos%width, pos//width))
                pos = parent[pos] if target[pos] != run_id else -1
            hp.clear()
            return path

        for dx, dy, cost in dirs:
            nx = pos%width + dx
            ny = pos//width + dy
            if nx < 0 or nx >= width or ny < 0 or ny >= height:
                continue
            n = hash(nx, ny)
            if avoid[n] == avoid_id:
                continue
            if seen[n] == run_id:
                continue
            ng = g+cost
            seen[n] = run_id
            parent[n] = pos
            h0 = h(n)
            new_h = 0 if h0 == 0 else 1.2+(weight-1.2)*max_local(0, 1-g/h0)
            card = dx == 0 or dy == 0
            heappush(
                hp,
                (ng + h(n)*new_h, -ng, card, n)
            )
    return []
def moves_through_impassible(path: list[Position], avoid: set[Position] = None) -> bool:
    if avoid is None:
        return False
    for i in range(1, len(path)-1):
        if path[i] in avoid:
            return True
    return False
def move_to(target: Position):
    avoid = map_info.get_avoid(False, True)
    if len(heap) == 0:
        init_a_star(rc.get_position(), target)
    path = a_star(avoid)
    if path is not None and moves_through_impassible(path, avoid):
        init_a_star(rc.get_position(), target)
        path = a_star(avoid)
    if path is None or len(path) < 2:
        return
    dir = path[0].direction_to(path[1])
    move(dir)