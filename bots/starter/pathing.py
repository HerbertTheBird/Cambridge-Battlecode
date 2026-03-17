from heapq import heappush, heappop
import time

from cambc import Controller, Direction, Position

file = open("time.txt", "a")

# 4-direction movement
DIRS = [
    (Direction.NORTH, 0, -1),
    (Direction.SOUTH, 0, 1),
    (Direction.WEST, -1, 0),
    (Direction.EAST, 1, 0),
]


def _key(pos: Position) -> tuple[int, int]:
    return (pos.x, pos.y)


def bfs_best_move(c, target: Position, avoid: set[Position] | None = None):

    if avoid is None:
        avoid = set()

    start = c.get_position()
    sx, sy = start.x, start.y
    tx, ty = target.x, target.y

    if sx == tx and sy == ty:
        return None

    width = c.get_map_width()
    height = c.get_map_height()

    avoid_keys = {(p.x, p.y) for p in avoid}

    # Same kind of blocking logic your BFS uses
    if (tx, ty) in avoid_keys:
        return None

    # local bindings for speed
    dirs = DIRS
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

        # stale entry check
        if best_g.get((x, y)) != g:
            continue

        if x == tx and y == ty:
            return first_dir

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
                (ng + h(nx, ny), ng, nx, ny, next_first_dir)
            )
    return None