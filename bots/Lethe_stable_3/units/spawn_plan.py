import random
from itertools import combinations

from cambc import Controller, Direction, Environment, Position

import map_info
from log import DRAW_DEBUG

# Number of initial builder bots that follow the core's spawn plan.
INITIAL_SPAWN_COUNT = 4

# Chebyshev-step cap for a builder bot's initial ray-follow exploration.
# The core draws lines capped to +1 to account for the bot starting one step out from the core.
INITIAL_EXPLORE_MAX_STEPS = 12

DIRECTIONS = [
    Direction.NORTH,
    Direction.NORTHEAST,
    Direction.EAST,
    Direction.SOUTHEAST,
    Direction.SOUTH,
    Direction.SOUTHWEST,
    Direction.WEST,
    Direction.NORTHWEST,
]


def dir_distance(a: Direction, b: Direction) -> int:
    ia = DIRECTIONS.index(a)
    ib = DIRECTIONS.index(b)
    diff = abs(ia - ib)
    return min(diff, 8 - diff)


def get_ray_endpoint(start: Position, direction: Direction, width: int, height: int, max_steps: int | None = None) -> Position:
    dx, dy = direction.delta()
    x, y = start.x, start.y
    steps = 0
    while True:
        if max_steps is not None and steps >= max_steps:
            return Position(x, y)
        nx, ny = x + dx, y + dy
        if nx < 0 or nx >= width or ny < 0 or ny >= height:
            return Position(x, y)
        x, y = nx, ny
        steps += 1


def _all_dir_endpoints(core_pos: Position, width: int, height: int):
    return [(d, get_ray_endpoint(core_pos, d, width, height)) for d in DIRECTIONS]


def _build_ti_near_mask(rc: Controller) -> int:
    """Bitmap of tiles within Chebyshev 1 (dist² ≤ 2) of a visible titanium ore."""
    w = map_info._width
    ti_mask = 0
    for p in rc.get_nearby_tiles():
        if rc.get_tile_env(p) == Environment.ORE_TITANIUM:
            ti_mask |= 1 << (p.x + p.y * w)
    if not ti_mask:
        return 0
    return map_info.expand_chebyshev(ti_mask)


def _ray_hits_mask(core_pos: Position, direction: Direction, width: int, height: int, mask: int) -> bool:
    if not mask:
        return False
    dx, dy = direction.delta()
    w = width
    x, y = core_pos.x + dx, core_pos.y + dy
    while 0 <= x < width and 0 <= y < height:
        if mask & (1 << (x + y * w)):
            return True
        x += dx
        y += dy
    return False


def get_valid_directions(rc: Controller, core_pos: Position, width: int, height: int):
    ti_near = _build_ti_near_mask(rc)
    valid = []
    for d, endpoint in _all_dir_endpoints(core_pos, width, height):
        if not rc.is_in_vision(endpoint):
            valid.append((d, endpoint))
        elif _ray_hits_mask(core_pos, d, width, height, ti_near):
            valid.append((d, endpoint))
    return valid


def pick_n_directions(width: int, height: int, pool, n: int):
    if len(pool) <= n:
        return list(pool)

    center = Position(width // 2, height // 2)
    half_w, half_h = width // 2, height // 2
    max_dist_sq = half_w * half_w + half_h * half_h or 1

    best = tuple(range(n))
    best_score = -1
    for combo in combinations(range(len(pool)), n):
        spread = 1
        for i in range(n):
            for j in range(i + 1, n):
                spread *= dir_distance(pool[combo[i]][0], pool[combo[j]][0])

        best_closeness = max(
            1.0 - pool[k][1].distance_squared(center) / max_dist_sq
            for k in combo
        )

        score = spread * 10 + best_closeness * 30
        if score > best_score:
            best_score = score
            best = combo

    return [pool[k] for k in best]


def draw_spawn_plan(rc: Controller, core_pos: Position, spawn_plan, width: int, height: int) -> None:
    if not DRAW_DEBUG:
        return
    # +1 because the builder starts one step out from the core along the direction
    for d in spawn_plan:
        endpoint = get_ray_endpoint(core_pos, d, width, height, max_steps=INITIAL_EXPLORE_MAX_STEPS + 1)
        rc.draw_indicator_line(core_pos, endpoint, 0, 255, 0)


def choose_spawn_plan(rc: Controller, core_pos: Position, n: int):
    width = rc.get_map_width()
    height = rc.get_map_height()
    valid = get_valid_directions(rc, core_pos, width, height)

    if len(valid) == 0:
        return random.sample(DIRECTIONS, n)

    chosen = pick_n_directions(width, height, valid, n)

    # Spawn directions that point closer to map center first
    center = Position(width // 2, height // 2)
    center_dir = core_pos.direction_to(center)
    chosen.sort(key=lambda de: (dir_distance(de[0], center_dir), de[1].distance_squared(center)))

    return [d for (d, _) in chosen]
