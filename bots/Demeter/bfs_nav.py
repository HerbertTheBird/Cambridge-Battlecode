import heapq

from cambc import Controller, EntityType, Environment, Position

from globals import (
    BFS_CPU_CHECK_INTERVAL,
    BFS_MIN_COMPUTE_BUDGET_US,
    CONVEYOR_TYPES,
)

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


class BFSNavigator:
    def __init__(self):
        self.width = 0
        self.height = 0
        self.tile_count = 0
        self.board_mask = 0
        self.not_left_col = 0
        self.not_right_col = 0
        self.my_id = 0
        self.my_team = None
        self.path_color: tuple[int, int, int] = (0, 100, 255)

        self.destination: Position | None = None
        self.destination_type: str | None = None
        self.goal_mask = 0

        self.run_id = 1
        self.search_complete = False
        self.changed = True
        self.field_revision = -1
        self.last_draw_round = -1

        self.global_layers: list[int] = []
        self.global_frontier = 0
        self.global_visited = 0

        self.path: list[Position] = []

    def set_statics(self, width: int, height: int, my_id: int, my_team):
        self.width = width
        self.height = height
        self.tile_count = width * height
        self.board_mask = (1 << self.tile_count) - 1
        self.not_left_col = 0
        self.not_right_col = 0
        for y in range(height):
            row_start = y * width
            self.not_left_col |= ((1 << (width - 1)) - 1) << (row_start + 1)
            self.not_right_col |= ((1 << (width - 1)) - 1) << row_start
        self.my_id = my_id
        self.my_team = my_team

    def clear_destination(self):
        self.destination = None
        self.destination_type = None
        self.goal_mask = 0
        self.global_layers = []
        self.global_frontier = 0
        self.global_visited = 0
        self.path = []
        self.search_complete = False
        self.changed = True
        self.field_revision = -1

    def set_destination(self, target: Position, destination_type: str):
        if target != self.destination or destination_type != self.destination_type:
            self.destination = target
            self.destination_type = destination_type
            self.goal_mask = 0
            self.global_layers = []
            self.global_frontier = 0
            self.global_visited = 0
            self.path = []
            self.search_complete = False
            self.changed = True
            self.field_revision = -1

    def advance_compute(self, ct: Controller, map_obj, budget_us: int, draw: bool = False):
        if self.destination is None or budget_us < BFS_MIN_COMPUTE_BUDGET_US:
            return

        map_revision = map_obj.movement_revision
        if self.changed or self.field_revision != map_revision:
            self._start_global_search(map_obj)
            self.field_revision = map_revision

        if self.goal_mask == 0:
            self.search_complete = True
            self.path = []
            return

        self._advance_global_bfs(ct, map_obj.get_walkable_mask(), budget_us)

        if draw and self.path and self.last_draw_round != ct.get_current_round():
            self._draw_path(ct, map_obj)
            self.last_draw_round = ct.get_current_round()

    def step_if_ready(self, player, ct: Controller, map_obj, vc) -> bool:
        if self.destination is None or self.goal_mask == 0:
            return False

        my_pos = ct.get_position()
        start_idx = my_pos.y * self.width + my_pos.x
        start_bit = 1 << start_idx
        if self.goal_mask & start_bit:
            self.path = [my_pos]
            return False

        local_costs, parents, local_visited = self._compute_local_costs(map_obj, start_idx, vc)
        if not local_costs:
            self.path = [my_pos]
            return False

        global_distances = self._get_global_distances(local_visited)
        target_idx, path_bits = self._select_best_path(start_idx, local_costs, parents, global_distances, map_obj)
        if target_idx < 0 or len(path_bits) < 2:
            self.path = [my_pos]
            return False

        self.path = [self._bit_to_pos(bit) for bit in path_bits]
        next_bit = path_bits[1]
        next_idx = next_bit.bit_length() - 1
        next_pos = Position(next_idx % self.width, next_idx // self.width)
        direction = my_pos.direction_to(next_pos)
        if self._execute_step(player, ct, map_obj, vc, direction, next_pos):
            return True

        return False

    def _start_global_search(self, map_obj):
        self.goal_mask = self._get_goal_mask(map_obj)
        self.global_layers = []
        self.global_frontier = self.goal_mask
        self.global_visited = self.goal_mask
        self.path = []
        self.search_complete = self.goal_mask == 0
        if self.goal_mask != 0:
            self.global_layers.append(self.goal_mask)
        self.changed = False

    def _advance_global_bfs(self, ct: Controller, walkable_mask: int, budget_us: int):
        deadline_us = ct.get_cpu_time_elapsed() + budget_us
        iterations = 0

        while self.global_frontier:
            if iterations % BFS_CPU_CHECK_INTERVAL == 0 and ct.get_cpu_time_elapsed() >= deadline_us:
                return
            iterations += 1
            next_frontier = self._expand_mask(self.global_frontier) & walkable_mask & ~self.global_visited
            if next_frontier == 0:
                self.global_frontier = 0
                self.search_complete = True
                return
            self.global_frontier = next_frontier
            self.global_visited |= next_frontier
            self.global_layers.append(next_frontier)

        self.search_complete = True

    def _compute_local_costs(self, map_obj, start_idx: int, vc) -> tuple[dict[int, int], dict[int, int], int]:
        start_bit = 1 << start_idx
        visible_passable = map_obj.get_visible_mask() & map_obj.get_walkable_mask()
        visible_passable |= map_obj.get_entity_mask(EntityType.BARRIER) & map_obj.get_team_mask(self.my_team)
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
            next_mask = self._expand_mask(bit) & visible_passable
            next_mask &= ~bit
            while next_mask:
                next_bit = next_mask & -next_mask
                n_idx = next_bit.bit_length() - 1
                step_cost = cost + 1 + self._tile_penalty(map_obj, n_idx)
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

    def _get_global_distances(self, local_visited: int) -> dict[int, int]:
        distances: dict[int, int] = {}
        remaining = local_visited
        for dist, layer in enumerate(self.global_layers):
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

    def _select_best_bit(self, mask: int, map_obj) -> int:
        best_bit = 0
        best_risk = 255
        while mask:
            bit = mask & -mask
            idx = bit.bit_length() - 1
            risk = map_obj.get_enemy_launcher_adj_count_idx(idx)
            if best_bit == 0 or risk < best_risk:
                best_bit = bit
                best_risk = risk
            mask ^= bit
        return best_bit

    def _reconstruct_path_bits(self, start_idx: int, target_idx: int, parents: dict[int, int]) -> list[int]:
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

    def _select_best_path(self, start_idx: int, local_costs: dict[int, int], parents: dict[int, int], global_distances: dict[int, int], map_obj) -> tuple[int, list[int]]:
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
                self._tile_penalty(map_obj, idx),
                idx,
            )
            if best_score is None or score < best_score:
                path_bits = self._reconstruct_path_bits(start_idx, idx, parents)
                if len(path_bits) < 2:
                    continue
                best_score = score
                best_idx = idx
                best_path = path_bits

        return best_idx, best_path

    def _expand_mask(self, mask: int) -> int:
        horizontal = mask | ((mask & self.not_right_col) << 1) | ((mask & self.not_left_col) >> 1)
        return (horizontal | (horizontal << self.width) | (horizontal >> self.width)) & self.board_mask

    def _bit_to_pos(self, bit: int) -> Position:
        idx = bit.bit_length() - 1
        return Position(idx % self.width, idx // self.width)

    def _tile_penalty(self, map_obj, idx: int) -> int:
        penalty = map_obj.get_enemy_launcher_adj_count_idx(idx) * LAUNCHER_ADJ_PENALTY
        if map_obj.is_ally_barrier_idx(idx):
            penalty += BARRIER_PENALTY
        if map_obj.is_ally_launcher_idx(idx):
            penalty += ALLY_LAUNCHER_PENALTY
        return penalty

    def _execute_step(self, player, ct: Controller, map_obj, vc, direction, next_pos: Position) -> bool:
        if ct.can_move(direction):
            ct.move(direction)
            return True

        bid = ct.get_tile_building_id(next_pos)
        if bid is not None:
            team = ct.get_team(bid)
            etype = ct.get_entity_type(bid)
            if team == self.my_team and etype in (EntityType.BARRIER, EntityType.LAUNCHER):
                if ct.can_destroy(next_pos):
                    ct.destroy(next_pos)
                    vc.remove_entity(player, bid, etype, team, next_pos)
                    map_obj.on_local_destroy(next_pos)
                    if ct.can_move(direction):
                        ct.move(direction)
                        return True

        if ct.get_tile_builder_bot_id(next_pos) not in (None, self.my_id):
            return False

        if ct.can_build_road(next_pos):
            bid = ct.build_road(next_pos)
            vc.add_entity(player, bid, EntityType.ROAD, self.my_team, next_pos)
            map_obj.on_local_build(next_pos, bid, EntityType.ROAD, self.my_team)
            if ct.can_move(direction):
                ct.move(direction)
                return True

        return False

    def _is_standable_target_idx(self, map_obj, idx: int) -> bool:
        bit = 1 << idx
        if map_obj.get_env_mask(Environment.WALL) & bit:
            return False
        entity_mask = map_obj.get_builder_standable_building_mask(self.my_team)
        occupied_mask = map_obj.get_occupied_mask()
        if not (occupied_mask & bit):
            return True
        return bool(entity_mask & bit)

    def _get_goal_mask(self, map_obj) -> int:
        if self.destination is None or self.destination_type not in ("exact", "adjacent"):
            return 0

        if self.destination_type == "adjacent":
            mask = 0
            for dx, dy in DIRS:
                nx = self.destination.x + dx
                ny = self.destination.y + dy
                if nx < 0 or nx >= self.width or ny < 0 or ny >= self.height:
                    continue
                if self._is_standable_target_idx(map_obj, ny * self.width + nx):
                    mask |= 1 << (ny * self.width + nx)
            return mask

        if self._is_standable_target_idx(map_obj, self.destination.y * self.width + self.destination.x):
            return 1 << (self.destination.y * self.width + self.destination.x)

        mask = 0
        for dx, dy in DIRS:
            nx = self.destination.x + dx
            ny = self.destination.y + dy
            if nx < 0 or nx >= self.width or ny < 0 or ny >= self.height:
                continue
            if self._is_standable_target_idx(map_obj, ny * self.width + nx):
                mask |= 1 << (ny * self.width + nx)
        return mask

    def _draw_path(self, ct: Controller, map_obj):
        r, g, b = self.path_color
        debug_path = self._build_debug_draw_path(map_obj)
        for i in range(len(debug_path) - 1):
            ct.draw_indicator_line(debug_path[i], debug_path[i + 1], r, g, b)

    def _build_debug_draw_path(self, map_obj) -> list[Position]:
        if not self.path:
            return []

        debug_path = list(self.path)
        if not self.global_layers:
            return debug_path

        dist_by_idx: dict[int, int] = {}
        for dist, layer in enumerate(self.global_layers):
            remaining = layer
            while remaining:
                bit = remaining & -remaining
                idx = bit.bit_length() - 1
                dist_by_idx[idx] = dist
                remaining ^= bit

        current_idx = debug_path[-1].y * self.width + debug_path[-1].x
        current_dist = dist_by_idx.get(current_idx)
        if current_dist is None:
            return debug_path

        visited = {current_idx}
        while current_dist > 0:
            current_x = current_idx % self.width
            current_y = current_idx // self.width
            best_next_idx = -1
            best_score: tuple[int, int] | None = None

            for dx, dy in DIRS:
                nx = current_x + dx
                ny = current_y + dy
                if nx < 0 or nx >= self.width or ny < 0 or ny >= self.height:
                    continue
                next_idx = ny * self.width + nx
                if next_idx in visited:
                    continue
                if dist_by_idx.get(next_idx) != current_dist - 1:
                    continue
                if not self._is_standable_target_idx(map_obj, next_idx):
                    continue

                score = (
                    self._tile_penalty(map_obj, next_idx),
                    next_idx,
                )
                if best_score is None or score < best_score:
                    best_score = score
                    best_next_idx = next_idx

            if best_next_idx < 0:
                break

            visited.add(best_next_idx)
            debug_path.append(Position(best_next_idx % self.width, best_next_idx // self.width))
            current_idx = best_next_idx
            current_dist -= 1

        return debug_path
