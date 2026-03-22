from heapq import heappush, heappop
import time

from cambc import Controller, Direction, Position
import sys
# file = open("time.txt", "a")

# 4-direction movement
CARD = [
    (Direction.NORTH, 0, -1),
    (Direction.SOUTH, 0, 1),
    (Direction.WEST, -1, 0),
    (Direction.EAST, 1, 0),
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
        end_time = time.perf_counter()
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
    dirs = CARD
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
            # if rc.get_current_round() < 10:
                # print(rc.get_id(), (end_time - start_time)*1000, start, target, file=sys.stderr)
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
