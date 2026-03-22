from heapq import heappush, heappop
import time

from cambc import Controller, Direction, Position

# file = open("time.txt", "a")

# 4-direction movement
CARD = [
    (Direction.NORTH, 0, -1),
    (Direction.SOUTH, 0, 1),
    (Direction.WEST, -1, 0),
    (Direction.EAST, 1, 0),
]
width = height = 0
def init(c: Controller):
    global width, height
    width = c.get_map_width()
    height = c.get_map_height()
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

    if avoid is None:
        avoid = set()

    sx, sy = start.x, start.y
    tx, ty = target.x, target.y

    if sx == tx and sy == ty:
        return None, 0

    avoid_keys = {(p.x, p.y) for p in avoid}

    if (tx, ty) in avoid_keys:
        return None, 0

    def h(x: int, y: int) -> int:
        return abs(x - tx) + abs(y - ty)
    
    open_heap = []
    heappush(open_heap, (h(sx, sy), 0, sx, sy, None))

    # best known g for each tile
    best_g = {(sx, sy): 0}

    while open_heap:
        f, g, x, y, first_dir = heappop(open_heap)

        # stale entry check
        if best_g.get((x, y)) != g:
            continue

        if x == tx and y == ty:
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
            heappush(
                open_heap,
                (ng + h(nx, ny), ng, nx, ny, next_first_dir)
            )
    return None, 0