import heapq
from array import array

from cambc import Controller, EntityType, Environment, Position

from globals import CONVEYOR_TYPES

WEIGHT = 2.0
MIN_WEIGHT = 1.5
MAX_CPU_US = 1900
CPU_CHECK_INTERVAL = 16
BARRIER_PENALTY = 15
ALLY_LAUNCHER_PENALTY = 30
LAUNCHER_ADJ_PENALTY = 2
MIN_COMPUTE_BUDGET_US = 120

DIRS = [
    (0, -1),
    (0, 1),
    (-1, 0),
    (1, 0),
    (-1, -1),
    (1, -1),
    (-1, 1),
    (1, 1),
]

class AStarNavigator:
    def __init__(self):
        self.width = 0
        self.height = 0
        self.my_id = 0
        self.my_team = None
        self.path_color: tuple[int, int, int] = (0, 100, 255)

        self.seen: array | None = None
        self.best_g: array | None = None
        self.parent: array | None = None
        self.target_stamp: array | None = None
        self.start_stamp: array | None = None
        self.avoid_stamp: array | None = None

        self.run_id = 1
        self.avoid_id = 1
        self.heap = []
        self.path = []
        self.path_idx = 0
        self.iter = 0
        self.max_iter = 0

        self.destination = None
        self.destination_type = None
        self.start_pos = None
        self.changed = True
        self.ready = False
        self.last_draw_round = -1

    def set_statics(self, width, height, my_id, my_team):
        self.width = width
        self.height = height
        self.my_id = my_id
        self.my_team = my_team
        tiles = width * height
        self.seen = array("I", [0]) * tiles
        self.best_g = array("I", [0]) * tiles
        self.parent = array("i", [-1]) * tiles
        self.target_stamp = array("I", [0]) * tiles
        self.start_stamp = array("I", [0]) * tiles
        self.avoid_stamp = array("I", [0]) * tiles
        self.max_iter = int((width * height) ** 0.5) * 12

    def clear_destination(self):
        self.destination = None
        self.destination_type = None
        self.start_pos = None
        self.heap.clear()
        self.path = []
        self.path_idx = 0
        self.iter = 0
        self.ready = False
        self.changed = True

    def set_destination(self, target: Position | None, destination_type: str | None = None):
        if target != self.destination or destination_type != self.destination_type:
            self.destination = target
            self.destination_type = destination_type
            self.heap.clear()
            self.path = []
            self.path_idx = 0
            self.iter = 0
            self.ready = False
            self.changed = True

    def has_ready_path(self, ct: Controller) -> bool:
        if not self.ready or not self.path:
            return False
        pos = ct.get_position()
        return self.path_idx < len(self.path) - 1 and self.path[self.path_idx] == pos

    def step_if_ready(self, ct: Controller) -> bool:
        if not self.has_ready_path(ct):
            return False

        current = self.path[self.path_idx]
        nxt = self.path[self.path_idx + 1]
        direction = current.direction_to(nxt)

        if ct.can_move(direction):
            ct.move(direction)
            self.path_idx += 1
            return True

        bid = ct.get_tile_building_id(nxt)
        if bid is not None and ct.get_team(bid) == self.my_team and ct.get_entity_type(bid) in (EntityType.BARRIER, EntityType.LAUNCHER):
            if ct.can_destroy(nxt):
                ct.destroy(nxt)
                if ct.can_move(direction):
                    ct.move(direction)
                    self.path_idx += 1
                    return True

        if ct.get_tile_builder_bot_id(nxt) not in (None, self.my_id):
            self.ready = False
            return False

        if ct.can_build_road(nxt):
            ct.build_road(nxt)
            if ct.can_move(direction):
                ct.move(direction)
                self.path_idx += 1
                return True

        self.ready = False
        return False

    def advance_compute(self, ct: Controller, map_obj, budget_us: int | None = None, draw: bool = False):
        if self.destination is None:
            return
        if ct.get_cpu_time_elapsed() >= MAX_CPU_US:
            return
        if budget_us is not None and budget_us < MIN_COMPUTE_BUDGET_US:
            return
        targets = self._get_astar_targets(map_obj)
        if not targets:
            self.ready = False
            self.path = []
            self.path_idx = 0
            self.heap.clear()
            return

        start = ct.get_position()
        if self.start_pos is None or self.start_pos.distance_squared(start) > 2:
            self.changed = True
        self.start_pos = start

        avoid = self._build_avoid(ct, map_obj, start)
        self._init_search(start, targets)
        path = self._run_search(start, avoid, map_obj, ct, budget_us)
        if path is not None:
            self.path = path
            self.path_idx = 0
            self.ready = len(path) > 1
            if self.ready and draw and self.last_draw_round != ct.get_current_round():
                self._draw_path(ct)
                self.last_draw_round = ct.get_current_round()

    def _build_avoid(self, ct: Controller, map_obj, start: Position) -> set[Position]:
        avoid = set()
        for uid in ct.get_nearby_units():
            if ct.get_entity_type(uid) != EntityType.BUILDER_BOT:
                continue
            pos = ct.get_position(uid)
            if pos != start and pos != self.destination:
                avoid.add(pos)
        return avoid

    def _init_search(self, start: Position, targets: list[Position]):
        assert self.target_stamp is not None
        assert self.seen is not None
        assert self.best_g is not None
        assert self.parent is not None
        if self.changed:
            self.heap.clear()
        self.changed = False

        if len(self.heap) == 0:
            self.run_id += 1
            self.iter = 0
            self.ready = False
            self.path = []
            self.path_idx = 0

            for target in targets:
                t = target.y * self.width + target.x
                self.target_stamp[t] = self.run_id
                self.seen[t] = self.run_id
                self.best_g[t] = 0
                self.parent[t] = -1
                h0 = max(abs(target.x - start.x), abs(target.y - start.y))
                heapq.heappush(self.heap, (h0 * WEIGHT, 0, t, 0))

    def _is_standable_target(self, map_obj, pos: Position) -> bool:
        env = map_obj.get_tile_env(pos)
        if env == Environment.WALL:
            return False

        entity = map_obj.get_tile_entity(pos)
        if entity is None:
            return True

        _, etype, team = entity
        if etype in CONVEYOR_TYPES or etype == EntityType.ROAD:
            return True
        if etype == EntityType.CORE and team == self.my_team:
            return True
        return False

    def _get_astar_targets(self, map_obj) -> list[Position]:
        if self.destination is None:
            return []
        if self.destination_type not in ("exact", "adjacent"):
            return []

        if self.destination_type == "adjacent":
            out = []
            seen = set()
            for dx, dy in DIRS:
                nx = self.destination.x + dx
                ny = self.destination.y + dy
                if nx < 0 or nx >= self.width or ny < 0 or ny >= self.height:
                    continue
                pos = Position(nx, ny)
                if pos in seen:
                    continue
                if not self._is_standable_target(map_obj, pos):
                    continue
                seen.add(pos)
                out.append(pos)
            return out

        if self._is_standable_target(map_obj, self.destination):
            return [self.destination]

        out = []
        seen = set()
        for dx, dy in DIRS:
            nx = self.destination.x + dx
            ny = self.destination.y + dy
            if nx < 0 or nx >= self.width or ny < 0 or ny >= self.height:
                continue
            pos = Position(nx, ny)
            if pos in seen:
                continue
            if not self._is_standable_target(map_obj, pos):
                continue
            seen.add(pos)
            out.append(pos)
        return out

    def _run_search(self, start: Position, avoid: set[Position], map_obj, ct: Controller, budget_us: int | None = None):
        assert self.start_stamp is not None
        assert self.avoid_stamp is not None
        assert self.seen is not None
        assert self.best_g is not None
        assert self.parent is not None
        self.avoid_id += 1
        start_idx = start.y * self.width + start.x
        dest_idx = self.destination.y * self.width + self.destination.x if self.destination is not None else -1
        self.start_stamp[start_idx] = self.run_id
        for pos in avoid:
            self.avoid_stamp[pos.y * self.width + pos.x] = self.avoid_id

        deadline_us = MAX_CPU_US
        if budget_us is not None:
            deadline_us = min(MAX_CPU_US, ct.get_cpu_time_elapsed() + budget_us)

        while self.heap:
            if self.iter % CPU_CHECK_INTERVAL == 0 and ct.get_cpu_time_elapsed() >= deadline_us:
                return None
            self.iter += 1
            if self.iter > self.max_iter:
                self.heap.clear()
                return []

            _, g_neg, pos_idx, age = heapq.heappop(self.heap)
            g = -g_neg

            if pos_idx == start_idx:
                self.heap.clear()
                return self._reconstruct_path(start_idx)

            px = pos_idx % self.width
            py = pos_idx // self.width

            min_weight = MIN_WEIGHT + min(age / 100, 1) * (WEIGHT - MIN_WEIGHT)

            for dx, dy in DIRS:
                nx = px + dx
                ny = py + dy
                if nx < 0 or nx >= self.width or ny < 0 or ny >= self.height:
                    continue
                n_idx = ny * self.width + nx
                if self.avoid_stamp[n_idx] == self.avoid_id:
                    continue
                if n_idx != dest_idx and map_obj.is_blocked_idx(n_idx):
                    continue

                ng = g + 1
                if n_idx != dest_idx and map_obj.is_ally_barrier_idx(n_idx):
                    ng += BARRIER_PENALTY
                if n_idx != dest_idx and map_obj.is_ally_launcher_idx(n_idx):
                    ng += ALLY_LAUNCHER_PENALTY
                if n_idx != dest_idx:
                    ng += map_obj.get_enemy_launcher_adj_count_idx(n_idx) * LAUNCHER_ADJ_PENALTY
                if self.seen[n_idx] == self.run_id and ng >= self.best_g[n_idx]:
                    continue

                h0 = max(abs(nx - start.x), abs(ny - start.y))
                new_h = 0 if h0 == 0 else min_weight + (WEIGHT - MIN_WEIGHT) * max(0, 1 - ng / h0)
                new_f = ng + h0 * new_h

                self.seen[n_idx] = self.run_id
                self.best_g[n_idx] = ng
                self.parent[n_idx] = pos_idx
                heapq.heappush(self.heap, (new_f, -ng, n_idx, self.iter))

        return []

    def _reconstruct_path(self, pos_idx: int) -> list[Position]:
        assert self.parent is not None
        assert self.target_stamp is not None
        out = []
        cur = pos_idx
        while cur != -1:
            out.append(Position(cur % self.width, cur // self.width))
            cur = self.parent[cur] if self.target_stamp[cur] != self.run_id else -1
        return out

    def _draw_path(self, ct: Controller):
        r, g, b = self.path_color
        for i in range(len(self.path) - 1):
            ct.draw_indicator_line(self.path[i], self.path[i + 1], r, g, b)
