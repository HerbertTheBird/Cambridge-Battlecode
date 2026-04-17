import heapq

from cambc import Controller, EntityType, Environment, Position, Team

from globals import (
    BFS_CPU_CHECK_INTERVAL,
    BFS_MIN_COMPUTE_BUDGET_US,
    CONVEYOR_TYPES,
)
import map as map_mod
import vision as vc

DIRS = (
    (0, -1),
    (0, 1),
    (-1, 0),
    (1, 0),
    (-1, -1),
    (1, -1),
    (-1, 1),
    (1, 1),
)

BARRIER_PENALTY = 15
ALLY_LAUNCHER_PENALTY = 30
LAUNCHER_ADJ_PENALTY = 2

width = 0
height = 0
tile_count = 0
board_mask = 0
not_left_col = 0
not_right_col = 0
my_id = 0
my_team: Team
path_color: tuple[int, int, int] = (0, 100, 255)

destination: Position | None = None
destination_type: str | None = None
goal_mask = 0

run_id = 1
search_complete = False
changed = True
field_revision = -1
last_draw_round = -1

global_layers: list[int] = []
global_frontier = 0
global_visited = 0

path: list[Position] = []

def init():
    global destination, destination_type, goal_mask
    global run_id, search_complete, changed, field_revision, last_draw_round
    global global_layers, global_frontier, global_visited, path
    destination = None
    destination_type = None
    goal_mask = 0
    run_id = 1
    search_complete = False
    changed = True
    field_revision = -1
    last_draw_round = -1
    global_layers = []
    global_frontier = 0
    global_visited = 0
    path = []

def set_statics(w: int, h: int, mid: int, team):
    global width, height, tile_count, board_mask, not_left_col, not_right_col, my_id, my_team
    width = w
    height = h
    tile_count = w * h
    board_mask = (1 << tile_count) - 1
    not_left_col = 0
    not_right_col = 0
    for y in range(h):
        row_start = y * w
        not_left_col |= ((1 << (w - 1)) - 1) << (row_start + 1)
        not_right_col |= ((1 << (w - 1)) - 1) << row_start
    my_id = mid
    my_team = team

def clear_destination():
    global destination, destination_type, goal_mask
    global global_layers, global_frontier, global_visited, path
    global search_complete, changed, field_revision
    destination = None
    destination_type = None
    goal_mask = 0
    global_layers = []
    global_frontier = 0
    global_visited = 0
    path = []
    search_complete = False
    changed = True
    field_revision = -1

def set_destination(target: Position, dest_type: str):
    global destination, destination_type, goal_mask
    global global_layers, global_frontier, global_visited, path
    global search_complete, changed, field_revision
    if target != destination or dest_type != destination_type:
        destination = target
        destination_type = dest_type
        goal_mask = 0
        global_layers = []
        global_frontier = 0
        global_visited = 0
        path = []
        search_complete = False
        changed = True
        field_revision = -1

def advance_compute(ct: Controller, budget_us: int, draw: bool = False):
    global search_complete, field_revision, path
    if destination is None or budget_us < BFS_MIN_COMPUTE_BUDGET_US:
        return

    map_revision = map_mod.movement_revision
    if changed or field_revision != map_revision:
        _start_global_search()
        field_revision = map_revision

    if goal_mask == 0:
        search_complete = True
        path = []
        return

    _advance_global_bfs(ct, map_mod.get_walkable_mask(), budget_us)

    if draw and path and last_draw_round != ct.get_current_round():
        _draw_path(ct)
        globals()['last_draw_round'] = ct.get_current_round()

def step_if_ready(player, ct: Controller) -> bool:
    global path
    if destination is None or goal_mask == 0:
        return False

    my_pos = ct.get_position()
    start_idx = my_pos.y * width + my_pos.x
    start_bit = 1 << start_idx
    if goal_mask & start_bit:
        path = [my_pos]
        return False

    local_costs, parents, local_visited = _compute_local_costs(start_idx)
    if not local_costs:
        path = [my_pos]
        return False

    global_distances = _get_global_distances(local_visited)
    target_idx, path_bits = _select_best_path(start_idx, local_costs, parents, global_distances)
    if target_idx < 0 or len(path_bits) < 2:
        path = [my_pos]
        return False

    path = [_bit_to_pos(bit) for bit in path_bits]
    next_bit = path_bits[1]
    next_idx = next_bit.bit_length() - 1
    next_pos = Position(next_idx % width, next_idx // width)
    direction = my_pos.direction_to(next_pos)
    if _execute_step(player, ct, direction, next_pos):
        return True

    return False

def _start_global_search():
    global goal_mask, global_layers, global_frontier, global_visited, path, search_complete, changed
    goal_mask = _get_goal_mask()
    global_layers = []
    global_frontier = goal_mask
    global_visited = goal_mask
    path = []
    search_complete = goal_mask == 0
    if goal_mask != 0:
        global_layers.append(goal_mask)
    changed = False

def _advance_global_bfs(ct: Controller, walkable_mask: int, budget_us: int):
    global global_frontier, global_visited, search_complete
    deadline_us = ct.get_cpu_time_elapsed() + budget_us
    iterations = 0

    while global_frontier:
        if iterations % BFS_CPU_CHECK_INTERVAL == 0 and ct.get_cpu_time_elapsed() >= deadline_us:
            return
        iterations += 1
        next_frontier = _expand_mask(global_frontier) & walkable_mask & ~global_visited
        if next_frontier == 0:
            global_frontier = 0
            search_complete = True
            return
        global_frontier = next_frontier
        global_visited |= next_frontier
        global_layers.append(next_frontier)

    search_complete = True

def _compute_local_costs(start_idx: int) -> tuple[dict[int, int], dict[int, int], int]:
    start_bit = 1 << start_idx
    visible_passable = map_mod.get_visible_mask() & map_mod.get_walkable_mask()
    visible_passable |= map_mod.get_entity_mask(EntityType.BARRIER) & map_mod.get_team_mask(my_team)
    visible_passable |= start_bit
    visible_passable &= ~(vc.ally_builder_mask | vc.enemy_builder_mask)
    visible_passable |= start_bit

    best_costs = {start_idx: 0}
    parents = {start_idx: -1}
    visited_mask = start_bit
    heap: list[tuple[int, int]] = [(0, start_idx)]

    while heap:
        cost, idx = heapq.heappop(heap)
        if cost != best_costs.get(idx):
            continue
        bit = 1 << idx
        next_mask = _expand_mask(bit) & visible_passable
        next_mask &= ~bit
        while next_mask:
            next_bit = next_mask & -next_mask
            n_idx = next_bit.bit_length() - 1
            step_cost = cost + 1 + _tile_penalty(n_idx)
            prev_best = best_costs.get(n_idx)
            if (
                prev_best is None
                or step_cost < prev_best
                or (step_cost == prev_best and idx < parents[n_idx])
            ):
                best_costs[n_idx] = step_cost
                parents[n_idx] = idx
                heapq.heappush(heap, (step_cost, n_idx))
                visited_mask |= next_bit
            next_mask ^= next_bit

    return best_costs, parents, visited_mask

def _get_global_distances(local_visited: int) -> dict[int, int]:
    distances: dict[int, int] = {}
    remaining = local_visited
    for dist, layer in enumerate(global_layers):
        hit = layer & remaining
        while hit:
            bit = hit & -hit
            idx = bit.bit_length() - 1
            distances[idx] = dist
            remaining ^= bit
            hit ^= bit
        if remaining == 0:
            break
    return distances

def _reconstruct_path_bits(start_idx: int, target_idx: int, parents: dict[int, int]) -> list[int]:
    path_bits: list[int] = []
    cur = target_idx
    while cur != -1:
        path_bits.append(1 << cur)
        if cur == start_idx:
            break
        cur = parents.get(cur, -1)
    path_bits.reverse()
    if not path_bits or path_bits[0] != (1 << start_idx):
        return []
    return path_bits

def _select_best_path(start_idx: int, local_costs: dict[int, int], parents: dict[int, int], global_distances: dict[int, int]) -> tuple[int, list[int]]:
    best_idx = -1
    best_score: tuple[int, int, int, int] | None = None
    best_path: list[int] = []

    for idx, local_cost in local_costs.items():
        if idx == start_idx:
            continue
        global_dist = global_distances.get(idx)
        if global_dist is None:
            continue
        score = (
            local_cost + global_dist,
            local_cost,
            _tile_penalty(idx),
            idx,
        )
        if best_score is None or score < best_score:
            path_bits = _reconstruct_path_bits(start_idx, idx, parents)
            if len(path_bits) < 2:
                continue
            best_score = score
            best_idx = idx
            best_path = path_bits

    return best_idx, best_path

def _expand_mask(mask: int) -> int:
    horizontal = mask | ((mask & not_right_col) << 1) | ((mask & not_left_col) >> 1)
    return (horizontal | (horizontal << width) | (horizontal >> width)) & board_mask

def _bit_to_pos(bit: int) -> Position:
    idx = bit.bit_length() - 1
    return Position(idx % width, idx // width)

def _tile_penalty(idx: int) -> int:
    penalty = map_mod.get_enemy_launcher_adj_count_idx(idx) * LAUNCHER_ADJ_PENALTY
    if map_mod.is_ally_barrier_idx(idx):
        penalty += BARRIER_PENALTY
    if map_mod.is_ally_launcher_idx(idx):
        penalty += ALLY_LAUNCHER_PENALTY
    return penalty

def _execute_step(player, ct: Controller, direction, next_pos: Position) -> bool:
    if ct.can_move(direction):
        ct.move(direction)
        return True

    bid = ct.get_tile_building_id(next_pos)
    if bid is not None:
        team = ct.get_team(bid)
        etype = ct.get_entity_type(bid)
        if team == my_team and etype in (EntityType.BARRIER, EntityType.LAUNCHER):
            if ct.can_destroy(next_pos):
                ct.destroy(next_pos)
                vc.remove_entity(player, bid, etype, team, next_pos)
                map_mod.on_local_destroy(next_pos)
                if ct.can_move(direction):
                    ct.move(direction)
                    return True

    if ct.get_tile_builder_bot_id(next_pos) not in (None, my_id):
        return False

    if ct.can_build_road(next_pos):
        bid = ct.build_road(next_pos)
        vc.add_entity(player, bid, EntityType.ROAD, my_team, next_pos)
        map_mod.on_local_build(next_pos, bid, EntityType.ROAD, my_team)
        if ct.can_move(direction):
            ct.move(direction)
            return True

    return False

def _is_standable_target_idx(idx: int) -> bool:
    bit = 1 << idx
    if map_mod.get_env_mask(Environment.WALL) & bit:
        return False
    entity_mask = map_mod.get_builder_standable_building_mask(my_team)
    occupied_mask = map_mod.get_occupied_mask()
    if not (occupied_mask & bit):
        return True
    return bool(entity_mask & bit)

def _get_goal_mask() -> int:
    if destination is None or destination_type not in ("exact", "adjacent"):
        return 0

    if destination_type == "adjacent":
        mask = 0
        for dx, dy in DIRS:
            nx = destination.x + dx
            ny = destination.y + dy
            if nx < 0 or nx >= width or ny < 0 or ny >= height:
                continue
            if _is_standable_target_idx(ny * width + nx):
                mask |= 1 << (ny * width + nx)
        return mask

    if _is_standable_target_idx(destination.y * width + destination.x):
        return 1 << (destination.y * width + destination.x)

    mask = 0
    for dx, dy in DIRS:
        nx = destination.x + dx
        ny = destination.y + dy
        if nx < 0 or nx >= width or ny < 0 or ny >= height:
            continue
        if _is_standable_target_idx(ny * width + nx):
            mask |= 1 << (ny * width + nx)
    return mask

def _draw_path(ct: Controller):
    r, g, b = path_color
    debug_path = _build_debug_draw_path()
    # for i in range(len(debug_path) - 1):
    #     ct.draw_indicator_line(debug_path[i], debug_path[i + 1], r, g, b)

def _build_debug_draw_path() -> list[Position]:
    if not path:
        return []

    debug_path = list(path)
    if not global_layers:
        return debug_path

    dist_by_idx: dict[int, int] = {}
    for dist, layer in enumerate(global_layers):
        remaining = layer
        while remaining:
            bit = remaining & -remaining
            idx = bit.bit_length() - 1
            dist_by_idx[idx] = dist
            remaining ^= bit

    current_idx = debug_path[-1].y * width + debug_path[-1].x
    current_dist = dist_by_idx.get(current_idx)
    if current_dist is None:
        return debug_path

    visited = {current_idx}
    while current_dist > 0:
        current_x = current_idx % width
        current_y = current_idx // width
        best_next_idx = -1
        best_score: tuple[int, int] | None = None

        for dx, dy in DIRS:
            nx = current_x + dx
            ny = current_y + dy
            if nx < 0 or nx >= width or ny < 0 or ny >= height:
                continue
            next_idx = ny * width + nx
            if next_idx in visited:
                continue
            if dist_by_idx.get(next_idx) != current_dist - 1:
                continue
            if not _is_standable_target_idx(next_idx):
                continue

            score = (
                _tile_penalty(next_idx),
                next_idx,
            )
            if best_score is None or score < best_score:
                best_score = score
                best_next_idx = next_idx

        if best_next_idx < 0:
            break

        visited.add(best_next_idx)
        debug_path.append(Position(best_next_idx % width, best_next_idx // width))
        current_idx = best_next_idx
        current_dist -= 1

    return debug_path
