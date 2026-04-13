import heapq
import map_info
from cambc import Controller, Direction, Position, EntityType, ResourceType, Environment
import comms
import math
from collections.abc import Collection
import time
import units.builder as builder
import sys
from functools import lru_cache

ALL_DIRS = list(Direction)
ALL_DIRS_DELTAS = [(d, d.delta()) for d in ALL_DIRS]

CARD_DIR = [
    Direction.NORTH,
    Direction.SOUTH,
    Direction.EAST,
    Direction.WEST,
]
from typing import TypeAlias

Step: TypeAlias = tuple[int, int, int, int]
# (dx, dy, cost, valid_from_mask)

bridge_cost = 10
barrier_cost = 10
threat_cost = 20




destroyed_barriers = dict()
def rebuild_broken_barriers(rc: Controller):
    built = []
    barrier_cost = rc.get_barrier_cost()[0]
    my_pos = rc.get_position()
    for p in destroyed_barriers:
        if not rc.is_in_vision(p):
            continue
        if destroyed_barriers[p]+1 > rc.get_current_round():
            continue
        if p == my_pos:
            continue
        if rc.get_global_resources()[0] < barrier_cost:
            continue
        id = rc.get_tile_building_id(p)
        if id and rc.get_entity_type(id) == EntityType.ROAD and rc.get_team(id) == rc.get_team() and rc.can_destroy(p) and not rc.get_tile_builder_bot_id(p) and rc.get_action_cooldown() == 0:
            rc.destroy(p)
            map_info.update_at(p)
        if rc.can_build_barrier(p):
            rc.build_barrier(p)
            map_info.update_at(p)
            built.append(p)
    for p in built:
        destroyed_barriers.pop(p)
class Pathing:


    forget_launcher = set()
    width = height = 0
    rc: Controller

    stuck_turns = 0
    prev_pos = None

    target_p = None

    last_dir = None
    last_last_dir = None

    path = None
    path_idx = 0



    def closest(self, targets: int, pos: Position = None) -> tuple[Position | None, int]:
        """Find closest bit in *targets* from *pos*, avoiding get_avoid(F,F,F).

        Uses Chebyshev flood-fill on bitmasks.  Returns (position, distance) or
        (None, -1) if unreachable.
        """
        if targets == 0:
            return None, -1
        if pos is None:
            pos = self.rc.get_position()
        w = map_info._width
        board = (1 << (w * map_info._height)) - 1
        avoid = map_info.get_avoid(False, False, False)
        passable = (~avoid & board) |  targets
        start = 1 << (pos.x + pos.y * w)
        if start & targets:
            return pos, 0
        visited = start
        frontier = start
        dist = 0
        nlc = map_info._not_left_col
        nrc = map_info._not_right_col
        while frontier:
            dist += 1
            h = frontier | ((frontier & nrc) << 1) | ((frontier & nlc) >> 1)
            expanded = h | (h << w) | (h >> w)
            frontier = expanded & passable & ~visited
            hit = frontier & targets
            if hit:
                lsb = hit & -hit
                n = lsb.bit_length() - 1

                return Position(n % w, n // w), dist
            visited |= frontier
        return None, -1

    def __init__(self, c: Controller):
        self.width = c.get_map_width()
        self.height = c.get_map_height()
        self.rc = c

        w = self.width
        h = self.height

        # --- movement definitions (make these class-level if truly constant) ---
        raw_card: list[tuple[int, int, int]] = [
            (0, -1, 1),
            (0, 1, 1),
            (-1, 0, 1),
            (1, 0, 1),
        ]

        raw_dirs: list[tuple[int, int, int]] = [
            (0, -1, 1),
            (0, 1, 1),
            (-1, 0, 1),
            (1, 0, 1),
            (-1, -1, 1),
            (1, -1, 1),
            (-1, 1, 1),
            (1, 1, 1),
        ]

        raw_conv: list[tuple[int, int, int]] = [
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

            # (2, 1, bridge_cost),
            # (2, -1, bridge_cost),
            # (-2, 1, bridge_cost),
            # (-2, -1, bridge_cost),

            # (1, 2, bridge_cost),
            # (1, -2, bridge_cost),
            # (-1, 2, bridge_cost),
            # (-1, -2, bridge_cost),

            # (-2, 0, bridge_cost),
            # (2, 0, bridge_cost),
            # (0, 2, bridge_cost),
            # (0, -2, bridge_cost),

            # (-1, -1, bridge_cost),
            # (-1, 1, bridge_cost),
            # (1, -1, bridge_cost),
            # (1, 1, bridge_cost),
        ]

        # --- mask cache (important: many dx/dy repeat) ---
        mask_cache: dict[tuple[int, int], int] = {}

        def build_mask(dx: int, dy: int) -> int:
            key = (dx, dy)
            cached = mask_cache.get(key)
            if cached is not None:
                return cached

            # out of bounds entirely
            if abs(dx) >= w or abs(dy) >= h:
                mask_cache[key] = 0
                return 0

            # valid rectangle of source cells
            x0 = max(0, -dx)
            x1 = min(w, w - dx)
            y0 = max(0, -dy)
            y1 = min(h, h - dy)

            if x0 >= x1 or y0 >= y1:
                mask_cache[key] = 0
                return 0

            # build one row
            row_bits = ((1 << (x1 - x0)) - 1) << x0
            nrows = y1 - y0

            # repeat row_bits every w bits (no loops)
            block = row_bits * ((1 << (nrows * w)) - 1) // ((1 << w) - 1)

            mask = block << (y0 * w)
            mask_cache[key] = mask
            return mask

        def make_steps(raw: list[tuple[int, int, int]]) -> list[Step]:
            return [(dx, dy, cost, build_mask(dx, dy)) for dx, dy, cost in raw]

        # --- final tables ---
        self.CARD = make_steps(raw_card)
        self.DIRS = make_steps(raw_dirs)
        self.CONV = make_steps(raw_conv)

        # Precompute offsets for bridge zone (5 < dist² <= 18)
        self._bridge_offsets = [
            (dx, dy)
            for dy in range(-4, 5)
            for dx in range(-4, 5)
            if 5 < dx * dx + dy * dy <= 18
        ]

    def move(self, dir: Direction):
        rc = self.rc
        px, py = rc.get_position().x, rc.get_position().y
        dx, dy = dir.delta()
        new_pos = Position(px + dx, py + dy)
        if not map_info.in_bounds(new_pos):
            return False
        id = rc.get_tile_building_id(new_pos)
        if rc.get_tile_builder_bot_id(new_pos) != None:
            return False
        if id and rc.get_entity_type(id) == EntityType.BARRIER and rc.can_destroy(new_pos):
            rc.destroy(new_pos)
            map_info.update_at(new_pos)
            destroyed_barriers[new_pos] = rc.get_current_round()
        if rc.can_build_road(new_pos):
            rc.build_road(new_pos)
            map_info.update_at(new_pos)
        if rc.can_move(dir):
            rc.move(dir)
            map_info.update_move()
            self.last_last_dir = self.last_dir
            self.last_dir = dir.delta()
            return True
        return False



    def reconstruct_path(
        self,
        can_visit: list[int],
        start: int,
        target: int,
        barriers: int,
        threat: int = 0,
        routing: bool = False,
    ) -> list[Position] | None:
        width = self.width
        height = self.height

        steps = tuple(
            (dx, dy, step_cost)
            for dx, dy, step_cost, _ in (self.CONV if routing else self.DIRS)
        )

        best = -1
        for layer, bits in enumerate(can_visit):
            if bits & start:
                best = layer
                break
        if best == -1:
            return None

        current = can_visit[best] & start
        current &= -current  # isolate lowest set bit

        path_bits = [current]
        dist = best
        can_visit_len = len(can_visit)

        last_dir = self.last_dir
        last_last_dir = self.last_last_dir

        w_minus_1 = width - 1
        h_minus_1 = height - 1

        _barrier_cost = barrier_cost
        _threat_cost = threat_cost

        while not (current & target):
            cur_idx = current.bit_length() - 1
            cx = cur_idx % width
            cy = cur_idx // width

            if routing:
                # For routing, just pick the first valid candidate
                chosen = None
                for dx, dy, step_cost in steps:
                    px = cx - dx
                    py = cy - dy
                    if not (0 <= px < width and 0 <= py < height):
                        continue
                    prev_dist = dist - step_cost
                    if prev_dist < 0 or prev_dist >= can_visit_len:
                        continue
                    prev_bit = 1 << (py * width + px)
                    if can_visit[prev_dist] & prev_bit:
                        chosen = (prev_bit, prev_dist, dx, dy)
                        break
            else:
                # Hoist enter_cost: current doesn't change in inner loop
                extra_cost = 0
                if current & barriers:
                    extra_cost += _barrier_cost
                if current & threat:
                    extra_cost += _threat_cost

                # Compute preferred_family once per outer iteration
                preferred_family = 0  # 0 = no preference
                if last_dir is not None and last_dir[0] != 0 and last_dir[1] != 0:
                    last_family = 1 if last_dir[0] * last_dir[1] > 0 else -1
                    preferred_family = -last_family if last_last_dir == last_dir else last_family

                cur_edge_dist = min(cx, cy, w_minus_1 - cx, h_minus_1 - cy)
                in_edge_band = cur_edge_dist < 4

                best_key = (2, 2, 2, 3)  # worse than any real key
                chosen = None

                for dx, dy, step_cost in steps:
                    px = cx - dx
                    py = cy - dy
                    if not (0 <= px < width and 0 <= py < height):
                        continue

                    prev_dist = dist - step_cost - extra_cost
                    if prev_dist < 0 or prev_dist >= can_visit_len:
                        continue

                    prev_bit = 1 << (py * width + px)
                    if not (can_visit[prev_dist] & prev_bit):
                        continue

                    # Inline sort_key: compute key tuple and track best
                    diag = dx != 0 and dy != 0
                    k0 = 0 if diag else 1

                    # px/py == nx/ny (the predecessor tile)
                    next_edge_dist = min(px, py, w_minus_1 - px, h_minus_1 - py)

                    k1 = 0
                    if in_edge_band and next_edge_dist <= cur_edge_dist:
                        k1 = 1

                    k2 = 0 if next_edge_dist >= 4 else 1

                    k3 = 0
                    if preferred_family:
                        if diag:
                            fam = 1 if dx * dy > 0 else -1
                            if fam != preferred_family:
                                k3 = 1
                        else:
                            k3 = 2

                    key = (k0, k1, k2, k3)
                    if key < best_key:
                        best_key = key
                        chosen = (prev_bit, prev_dist, dx, dy)

            if chosen is None:
                return None

            prev_bit, prev_dist, chosen_dx, chosen_dy = chosen

            last_last_dir = last_dir
            last_dir = (-chosen_dx, -chosen_dy)

            current = prev_bit
            dist = prev_dist
            path_bits.append(current)

        return [Position((b.bit_length() - 1) % width, (b.bit_length() - 1) // width) for b in path_bits]

    def bfs(self, start_p: Position | set[Position], target_p: Position | set[Position], avoid_p: int | None = None, routing = False, avoid_turret = True) -> list[Position] | None:
        width = self.width
        if avoid_p is None:
            avoid_p = map_info.get_avoid(False, True, False)

        if isinstance(start_p, int):
            start = start_p
        elif isinstance(start_p, Position):
            start = 1 << (start_p.x + start_p.y * width)
        else:
            start = 0
            for p in start_p:
                start |= 1 << (p.x + p.y * width)

        if isinstance(target_p, int):
            target = target_p
        elif isinstance(target_p, Position):
            target = 1 << (target_p.x + target_p.y * width)
        else:
            target = 0
            for p in target_p:
                target |= 1 << (p.x + p.y * width)

        # avoid_p is already a bitmask; just clear start/target from it
        avoid = avoid_p & ~start & ~target

        CONV = self.CONV
        DIRS = self.DIRS
        can_visit = [target]
        visited = 0

        start_time = time.perf_counter_ns()

        # Barriers directly from bitmasks
        my_team_idx = map_info._TM_INT[self.rc.get_team()]
        barriers = map_info._bm_et[map_info._IDX_BARRIER] & map_info._bm_team[my_team_idx]

        # Threat as soft cost — only active when not hard-avoided (bot is inside threat zone)
        threat = map_info._bm_enemy_launch_adj
        if avoid_turret:
            threat |= map_info._bm_enemy_turret_threat
        if threat & start:
            threat &= ~start
        convs = map_info._bm_conveyors & ~map_info._bm_my_core_area
        if not routing:
            max_start = barrier_cost + threat_cost
            can_visit = [0] * (max_start + 1)
            m = target
            while m:
                lsb = m & -m
                cost = 0
                if barriers & lsb:
                    cost += barrier_cost
                if threat & lsb:
                    cost += threat_cost
                can_visit[cost] |= lsb
                m ^= lsb
        else:
            t_core = target & ~convs
            t_conv = target & convs
            can_visit = [t_core] + [0] * bridge_cost
            can_visit[bridge_cost] |= t_conv

        i = 0
        stuck = 0
        visited = 0

        ESTIMATED_MAX_DIST = 50

        # Preallocate some space up front, but allow growth if search goes longer.
        if len(can_visit) < ESTIMATED_MAX_DIST:
            can_visit.extend([0] * (ESTIMATED_MAX_DIST - len(can_visit)))

        not_barriers = ~barriers
        not_threat = ~threat

        if routing:
            steps = CONV
            max_extra_cost = bridge_cost
        else:
            steps = DIRS
            max_extra_cost = barrier_cost + threat_cost

        def ensure_capacity(idx_needed: int) -> None:
            """Grow can_visit only when needed."""
            if idx_needed >= len(can_visit):
                # Grow by at least enough for the current need, but with slack so
                # repeated growth is less frequent.
                new_len = max(idx_needed + 1, len(can_visit) * 2, ESTIMATED_MAX_DIST)
                can_visit.extend([0] * (new_len - len(can_visit)))

        if routing:
            while True:
                frontier = can_visit[i] & ~visited
                visited |= frontier

                if frontier & start:
                    end_time = time.perf_counter_ns()
                    self.path = self.reconstruct_path(
                        can_visit, start, target, barriers, threat, routing
                    )
                    self.path_idx = 0
                    print("bfs time " + str((end_time - start_time) / 1000) + "us")
                    return self.path

                if frontier == 0:
                    stuck += 1
                    i += 1
                    if i >= len(can_visit):
                        break
                    continue

                stuck = 0

                # Ensure capacity for the worst destination layer this iteration.
                ensure_capacity(i + max_extra_cost)

                for dx, dy, step_cost, mask in steps:
                    offset = dx + dy * width
                    masked = frontier & mask

                    if offset > 0:
                        new = (masked << offset) & ~avoid
                    else:
                        new = (masked >> (-offset)) & ~avoid

                    can_visit[i + step_cost] |= new

                i += 1

        else:
            while True:
                frontier = can_visit[i] & ~visited
                visited |= frontier

                if frontier & start:
                    end_time = time.perf_counter_ns()
                    self.path = self.reconstruct_path(
                        can_visit, start, target, barriers, threat, routing
                    )
                    self.path_idx = 0
                    print("bfs time " + str((end_time - start_time) / 1000) + "us")
                    return self.path

                if frontier == 0:
                    stuck += 1
                    i += 1
                    if i >= len(can_visit):
                        break
                    continue

                stuck = 0

                # Worst case target index for non-routing branch.
                ensure_capacity(i + max_extra_cost + 1)

                for dx, dy, step_cost, mask in steps:
                    offset = dx + dy * width
                    masked = frontier & mask

                    if offset > 0:
                        new = (masked << offset) & ~avoid
                    else:
                        new = (masked >> (-offset)) & ~avoid

                    new_not_threat = new & not_threat
                    new_threat = new & threat

                    can_visit[i + step_cost] |= (new_not_threat & not_barriers)
                    can_visit[i + step_cost + barrier_cost] |= (new_not_threat & barriers)
                    can_visit[i + step_cost + threat_cost] |= (new_threat & not_barriers)
                    can_visit[i + step_cost + barrier_cost + threat_cost] |= (new_threat & barriers)

                i += 1

        self.path = None
        return None


    def execute_path(self, sample_path=None, path_idx_in=None):
        if sample_path is None:
            sample_path = self.path
            idx = self.path_idx
        else:
            idx = path_idx_in or 0

        if idx > len(sample_path) - 2:
            return False

        dir = sample_path[idx].direction_to(sample_path[idx + 1])
        if self.move(dir):
            if sample_path is self.path:
                self.path_idx += 1
            return True
        return False

    def move_adjacent(self, pos: Position, fallback: Position | None = None, **kwargs):
        """Move to an adjacent tile of pos. Filters by in_bounds, passable, no builder bot, and in vision."""
        rc = self.rc
        adj = set()
        for d in ALL_DIRS:
            if d == Direction.CENTRE:
                continue
            p = pos.add(d)
            if not map_info.in_bounds(p):
                continue
            if p == rc.get_position():
                adj.add(p)
                continue
            if not map_info.is_passable(p):
                continue
            if rc.is_in_vision(p) and rc.get_tile_builder_bot_id(p):
                continue
            adj.add(p)
        if not adj:
            if fallback is not None:
                adj.add(fallback)
            else:
                adj.add(pos)
        return self.move_to(adj, **kwargs)

    def move_to(self, target: Position | set[Position], avoid_empty: bool = False, avoid_turret: bool = True):
        print("move to", target)
        if isinstance(target, Position):
            target = {target}
        if target != self.target_p:
            self.forget_launcher.clear()
        avoid = map_info.get_avoid(False, True, False)
        if avoid_empty:
            has_building = 0
            for i in range(map_info._NUM_ET):
                has_building |= map_info._bm_et[i]
            avoid |= map_info._bm_seen & ~has_building & ~map_info._bm_env[map_info._IDX_ENV_WALL]
        my_pos = self.rc.get_position()
        if target == self.target_p and self.rc.get_position() == self.prev_pos and self.rc.get_position() not in target and all(max(abs(my_pos.x - t.x), abs(my_pos.y - t.y)) > 1 for t in target):
            self.stuck_turns += 1
        else:
            self.prev_pos = self.rc.get_position()
            self.stuck_turns = 0
            self.target_p = target
        if self.stuck_turns > 2 + self.rc.get_id()%8:
            for i in ALL_DIRS:
                if self.rc.can_move(i):
                    self.rc.move(i)
                    map_info.update_move()
                    return True

        path = self.bfs(my_pos, target, avoid, False, avoid_turret=avoid_turret)
        if path:
            for i in range(len(path)-1):
                self.rc.draw_indicator_line(path[i], path[i+1], 0, 255, 255)
        if not path:
            return False
        self.execute_path(path)
        return True



    def calculate_conveyor_path(self, start: Position, update: bool = False):
        print("conveyors from ", start)
        if update:
            target, avoid = self._get_conveyor_targets_and_avoid(start.x+start.y*map_info._width)
        else:
            target, avoid = self._get_conveyor_targets_and_avoid()
        if not target:
            return None
        if not update:
            new_start = set()
            for dir in CARD_DIR:
                if map_info.in_bounds(start.add(dir)) and (avoid >> ((start.x + dir.delta()[0]) + (start.y + dir.delta()[1]) * self.width) & 1) == 0:
                    new_start.add(start.add(dir))
            start = new_start
        path = self.bfs(start, target, avoid, True)
        if not path:
            return None
        for i in range(len(path)-1):
            self.rc.draw_indicator_line(path[i], path[i+1], 255, 0, 255)
            self.rc.draw_indicator_dot(path[i], 255, 0, 255)
        return path

    def conveyor_cost(self, path, scaling = None):
        if scaling is None:
            scaling = self.rc.get_scale_percent()/100
        if not path:
            return None
        cost = 0
        rc = self.rc
        scaling = rc.get_scale_percent()/100
        for i in range(len(path) - 1):
            is_bridge = path[i].distance_squared(path[i + 1]) > 1
            if is_bridge:
                cost += 20*scaling
                scaling += 0.1
            else:
                cost += 3*scaling
                scaling += 0.01
        return cost
    def _get_conveyor_targets_and_avoid(
        self, conveyor = None
    ):
        target = map_info._bm_route_targets
        if not target:
            return 0, 0
        avoid = map_info.get_avoid(True, False, True)
        if conveyor:
            avoid &= ~(1<<map_info._building_conv_target[conveyor])
        return target, avoid