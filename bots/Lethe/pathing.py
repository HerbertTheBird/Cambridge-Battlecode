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
adj_launch_cost = 20
conveyor_cost = 10


def _is_builder_nav(pathing: "Pathing") -> bool:
    return getattr(builder, "nav", None) is pathing


def _is_builder_ore_nav(pathing: "Pathing") -> bool:
    return getattr(builder, "ore_nav", None) is pathing
destroyed_barriers = dict()
def rebuild_broken_barriers(rc):
    # print("broken", self.destroyed_barriers)
    built = []
    barrier_cost = rc.get_barrier_cost()[0]
    my_pos = rc.get_position()
    for p in destroyed_barriers:
        if not rc.is_in_vision(p):
            continue
        if destroyed_barriers[p]+2 > rc.get_current_round():
            continue
        if p == my_pos:
            continue
        if rc.get_global_resources()[0] < barrier_cost:
            continue
        id = rc.get_tile_building_id(p)
        if id and rc.get_entity_type(id) == EntityType.ROAD and rc.get_team(id) == rc.get_team() and rc.can_destroy(p):
            # print("barrier place break", p)
            rc.destroy(p)
            map_info.note_destroy(p)
        if rc.can_build_barrier(p):
            # print("barrier place", p)
            rc.build_barrier(p)
            built.append(p)
    # print("put back", built)

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

    def _bridge_zone(self, center: Position) -> int:
        cx, cy = center.x, center.y
        w, h = self.width, self.height
        mask = 0
        for dx, dy in self._bridge_offsets:
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < w and 0 <= ny < h:
                mask |= 1 << (nx + ny * w)
        return mask

    def move(self, dir: Direction):
        rc = self.rc
        px, py = rc.get_position().x, rc.get_position().y
        dx, dy = dir.delta()
        new_pos = Position(px + dx, py + dy)
        # print("move pre", rc.get_position())
        if not map_info.in_bounds(new_pos):
            return False
        id = rc.get_tile_building_id(new_pos)
        if rc.get_tile_builder_bot_id(new_pos) != None:
            return False
        if id and rc.get_entity_type(id) == EntityType.BARRIER and rc.can_destroy(new_pos):
            rc.destroy(new_pos)
            map_info.note_destroy(new_pos)
            destroyed_barriers[new_pos] = rc.get_current_round()
        if rc.can_build_road(new_pos):
            rc.build_road(new_pos)
        if rc.can_move(dir):
            rc.move(dir)
            self.last_last_dir = self.last_dir
            self.last_dir = dir.delta()
            # print("move post", rc.get_position())
            return True
        return False



    def reconstruct_path(
        self,
        can_visit: list[int],
        start: int,
        target: int,
        barriers: int,
        adj_launch: int,
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
        _adj_launch_cost = adj_launch_cost

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
                if current & adj_launch:
                    extra_cost += _adj_launch_cost

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

    def bfs(self, start_p: Position | set[Position], target_p: Position | set[Position], avoid_p: int | None = None, routing = False, bridge_zone: int | None = None) -> list[Position] | None:
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

        # Barriers and enemy launch adjacency directly from bitmasks
        my_team_idx = map_info._TM_INT[self.rc.get_team()]
        barriers = map_info._bm_et[map_info._IDX_BARRIER] & map_info._bm_team[my_team_idx]
        adj_launch = map_info._bm_enemy_launch_adj | map_info._bm_enemy_turret_threat
        barriers &= ~start
        adj_launch &= ~start
        # builder.draw_mask(adj_launch, 255, 0, 0)

        convs = map_info._bm_conveyors & ~map_info._bm_my_core_area
        if not routing:
            max_start = barrier_cost + adj_launch_cost
            can_visit = [0] * (max_start + 1)
            m = target
            while m:
                lsb = m & -m
                cost = 0
                if barriers & lsb:
                    cost += barrier_cost
                if adj_launch & lsb:
                    cost += adj_launch_cost
                can_visit[cost] |= lsb
                m ^= lsb
        else:
            t_core = target & ~convs
            t_conv = target & convs
            can_visit = [t_core] + [0] * conveyor_cost
            can_visit[conveyor_cost] |= t_conv

        stuck = 0
        i = 0
        while True:
            frontier = can_visit[i] & ~visited
            # builder.draw_mask(frontier, 0, 0, min(255, i*10))
            visited |= frontier
            if frontier & start:
                end_time = time.perf_counter_ns()
                self.path = self.reconstruct_path(can_visit, start, target, barriers, adj_launch, routing)
                self.path_idx = 0
                print("bfs time " + str((end_time-start_time)/1000) + "us")

                return self.path
            if frontier == 0:
                stuck += 1
                i += 1
                if i >= len(can_visit):
                    break
                continue
            stuck = 0
            if routing:
                can_visit.extend([0]*(i+bridge_cost+1-len(can_visit)))
                for step in CONV:
                    offset = step[0]+step[1]*width
                    new = ((frontier&step[3])<<offset if offset > 0 else (frontier&step[3]) >> (-offset)) & ~avoid
                    if bridge_zone is not None and step[2] <= 1:
                        new &= ~bridge_zone
                    can_visit[(i+step[2])] |= new
            else:
                can_visit.extend([0]*(i+1+barrier_cost+adj_launch_cost+1-len(can_visit)))
                for step in DIRS:
                    offset = step[0]+step[1]*width
                    new = ((frontier&step[3])<<offset if offset > 0 else (frontier&step[3]) >> (-offset)) & ~avoid
                    can_visit[i+step[2]] |= (new & ~barriers & ~adj_launch)
                    can_visit[i+step[2]+barrier_cost] |= (new & barriers & ~adj_launch)
                    can_visit[i+step[2]+adj_launch_cost] |= (new & ~barriers & adj_launch)
                    can_visit[i+step[2]+barrier_cost+adj_launch_cost] |= (new & barriers & adj_launch)
            i+=1
        self.path = None
        return None

    def moves_through_impassible(self, path: list[Position], avoid: int) -> bool:
        width = self.width
        for i in range(1, len(path) - 1):
            p = path[i]
            if avoid & (1 << (p.x + p.y * width)):
                return True
        return False

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

    def move_to(self, target: Position | set[Position]):
        print("move to", target)
        if isinstance(target, Position):
            target = {target}
        if target != self.target_p:
            self.forget_launcher.clear()
        # print("move to ", target)
        avoid = map_info.get_avoid(False, True, False)
        # builder.draw_mask(avoid, 255, 0, 0)
        my_pos = self.rc.get_position()
        if target == self.target_p and self.rc.get_position() == self.prev_pos and self.rc.get_position() not in target:
            self.stuck_turns += 1
        else:
            self.prev_pos = self.rc.get_position()
            self.stuck_turns = 0
            self.target_p = target
        if self.stuck_turns > 2 + self.rc.get_id()%8:
            for i in ALL_DIRS:
                if self.rc.can_move(i):
                    self.rc.move(i)
                    return True

        path = self.bfs(my_pos, target, avoid, False)
        if path:
            for i in range(len(path)-1):
                self.rc.draw_indicator_line(path[i], path[i+1], 0, 255, 255)
        marked = False
        rc = self.rc
        if len(destroyed_barriers) == 0:
            for dr, (dx, dy) in ALL_DIRS_DELTAS:
                pos = Position(my_pos.x + dx, my_pos.y + dy)
                if not map_info.in_bounds(pos):
                    continue
                id = rc.get_tile_building_id(pos)
                if id and rc.get_entity_type(id) == EntityType.LAUNCHER and rc.get_team(id) == rc.get_team() and pos not in self.forget_launcher:
                    for dr2, (dx2, dy2) in ALL_DIRS_DELTAS:
                        p2 = Position(my_pos.x + dx2, my_pos.y + dy2)
                        if not map_info.in_bounds(p2):
                            continue
                        if rc.can_place_marker(p2):
                            closest = None
                            for t in target:
                                if closest is None or t.distance_squared(pos) < closest.distance_squared(pos):
                                    closest = t
                            rc.place_marker(p2, comms.encode_launch(closest))
                            self.forget_launcher.add(pos)
                            marked = True
                            break
                    if not marked:
                        for dr2, (dx2, dy2) in ALL_DIRS_DELTAS:
                            p2 = Position(my_pos.x + dx2, my_pos.y + dy2)
                            if not map_info.in_bounds(p2):
                                continue
                            id2 = rc.get_tile_building_id(p2)
                            if id2 and rc.get_team(id2) == rc.get_team() and rc.get_entity_type(
                                    id2) == EntityType.ROAD and rc.can_destroy(p2) and dr != Direction.CENTRE:
                                rc.destroy(p2)
                                map_info.note_destroy(p2)
                            if rc.can_place_marker(p2):
                                closest = None
                                for t in target:
                                    if closest is None or t.distance_squared(pos) < closest.distance_squared(pos):
                                        closest = t
                                rc.place_marker(p2, comms.encode_launch(closest))
                                self.forget_launcher.add(pos)
                                marked = True
                                break
                if marked:
                    break
            if marked:
                return True
        if not path:
            return False
        self.execute_path(path)
        return True



    def calculate_conveyor_path(self, start: Position, update: bool = False):
        print("conveyors from ", start)
        target, avoid = self._get_conveyor_targets_and_avoid()
        # builder.draw_mask(target, 0, 255, 0)
        if not target:
            return None
        bz = self._bridge_zone(map_info._my_core)
        if not update:
            new_start = set()
            for dir in CARD_DIR:
                if map_info.in_bounds(start.add(dir)) and (avoid >> ((start.x + dir.delta()[0]) + (start.y + dir.delta()[1]) * self.width) & 1) == 0:
                    new_start.add(start.add(dir))
            start = new_start
        path = self.bfs(start, target, avoid, True, bz)
        if not path:
            return None
        for i in range(len(path)-1):
            self.rc.draw_indicator_line(path[i], path[i+1], 255, 0, 255)
            self.rc.draw_indicator_dot(path[i], 255, 0, 255)
        return path


    def _get_conveyor_targets_and_avoid(
        self,
    ):
        target = map_info._bm_route_targets
        if not target:
            return 0, 0
        avoid = map_info.get_avoid(True, False, True)
        return target, avoid