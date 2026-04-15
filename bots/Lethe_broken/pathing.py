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
conveyor_end_cost = 10



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
        if id and rc.get_entity_type(id) == EntityType.BARRIER and rc.can_destroy(new_pos) and rc.get_action_cooldown() == 0 and rc.get_global_resources()[0] > rc.get_road_cost()[0]:
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
    def bfs(self, start_mask: int, target_mask: int, avoid: int | None = None, routing = False, avoid_turret = True, end_cost_mask: int = 0):
        if start_mask & target_mask:
            s_idx = (start_mask & target_mask).bit_length() - 1
            return Position(s_idx % self.width, s_idx // self.width), Position(s_idx % self.width, s_idx // self.width), 0
        width = self.width
        height = self.height
        if avoid is None:
            avoid = map_info.get_avoid(False, True, False)
        avoid &= ~start_mask
        my_team_idx = map_info._TM_INT[self.rc.get_team()]
        barriers = map_info._bm_et[map_info._IDX_BARRIER] & map_info._bm_team[my_team_idx]
        barriers &= ~start_mask

        threat = map_info._bm_enemy_launch_adj
        if avoid_turret:
            threat |= map_info._bm_enemy_turret_threat
        if threat & start_mask:
            threat &= ~start_mask

        start_time = time.perf_counter_ns()

        if routing:
            if end_cost_mask:
                t_end = target_mask & end_cost_mask
                t_core = target_mask & ~t_end
            else:
                convs = map_info._bm_conveyors & ~map_info._bm_my_core_area
                t_end = target_mask & convs
                t_core = target_mask & ~convs
            max_c = bridge_cost
            max_seed = conveyor_end_cost
            cycle_len = max(max_c, max_seed) + 1
            frontier = [0] * cycle_len
            frontier[0] = t_core
            frontier[conveyor_end_cost % cycle_len] |= t_end
            steps = self.CONV
            not_barriers = 0
            not_threat = 0
        else:
            max_c = 1 + barrier_cost + threat_cost
            max_seed = barrier_cost + threat_cost
            cycle_len = max(max_c, max_seed) + 1
            frontier = [0] * cycle_len
            frontier[0] = target_mask & ~barriers & ~threat
            frontier[barrier_cost] = target_mask & barriers & ~threat
            frontier[threat_cost] = target_mask & ~barriers & threat
            frontier[barrier_cost + threat_cost] = target_mask & barriers & threat
            steps = self.DIRS
            not_barriers = ~barriers
            not_threat = ~threat

        effective_len = max_seed + 1
        visited = 0
        visited_layers: list[int] = []
        i = 0
        while True:
            slot = i % cycle_len
            cur_frontier = frontier[slot] & ~visited
            frontier[slot] = 0
            visited_layers.append(cur_frontier)
            visited |= cur_frontier

            hit = cur_frontier & start_mask
            if hit:
                end_time = time.perf_counter_ns()
                print("bfs time " + str((end_time - start_time) / 1000) + "us")
                start_bit = hit & -hit
                s_idx = start_bit.bit_length() - 1
                cx = s_idx % width
                cy = s_idx // width
                start_pos = Position(cx, cy)
                vl_len = len(visited_layers)

                if routing:
                    chosen_prev = None
                    for dx, dy, step_cost, _m in steps:
                        px = cx - dx
                        py = cy - dy
                        if not (0 <= px < width and 0 <= py < height):
                            continue
                        prev_layer = i - step_cost
                        if prev_layer < 0 or prev_layer >= vl_len:
                            continue
                        prev_bit = 1 << (py * width + px)
                        if visited_layers[prev_layer] & prev_bit:
                            chosen_prev = Position(px, py)
                            break
                    if chosen_prev is None:
                        return None
                    return (start_pos, chosen_prev, i)

                extra_cost = 0
                if start_bit & barriers:
                    extra_cost += barrier_cost
                if start_bit & threat:
                    extra_cost += threat_cost

                preferred_family = 0
                last_dir = self.last_dir
                last_last_dir = self.last_last_dir
                if last_dir is not None and last_dir[0] != 0 and last_dir[1] != 0:
                    last_family = 1 if last_dir[0] * last_dir[1] > 0 else -1
                    preferred_family = -last_family if last_last_dir == last_dir else last_family

                w_minus_1 = width - 1
                h_minus_1 = height - 1
                cur_edge_dist = min(cx, cy, w_minus_1 - cx, h_minus_1 - cy)
                in_edge_band = cur_edge_dist < 4

                best_key = (2, 2, 2, 3)
                chosen_prev = None
                for dx, dy, step_cost, _m in steps:
                    px = cx - dx
                    py = cy - dy
                    if not (0 <= px < width and 0 <= py < height):
                        continue
                    prev_layer = i - step_cost - extra_cost
                    if prev_layer < 0 or prev_layer >= vl_len:
                        continue
                    prev_bit = 1 << (py * width + px)
                    if not (visited_layers[prev_layer] & prev_bit):
                        continue

                    diag = dx != 0 and dy != 0
                    k0 = 0 if diag else 1

                    next_edge_dist = min(px, py, w_minus_1 - px, h_minus_1 - py)

                    k1 = 1 if (in_edge_band and next_edge_dist <= cur_edge_dist) else 0
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
                        chosen_prev = Position(px, py)

                if chosen_prev is None:
                    return None
                return (start_pos, chosen_prev, i)

            if cur_frontier == 0:
                i += 1
                if i >= effective_len:
                    return None
                continue

            if i + max_c + 1 > effective_len:
                effective_len = i + max_c + 1

            if routing:
                for dx, dy, step_cost, mask in steps:
                    offset = dx + dy * width
                    masked = cur_frontier & mask
                    if offset > 0:
                        new = (masked << offset) & ~avoid
                    else:
                        new = (masked >> (-offset)) & ~avoid
                    frontier[(i + step_cost) % cycle_len] |= new
            else:
                for dx, dy, step_cost, mask in steps:
                    offset = dx + dy * width
                    masked = cur_frontier & mask
                    if offset > 0:
                        new = (masked << offset) & ~avoid
                    else:
                        new = (masked >> (-offset)) & ~avoid
                    new_nt = new & not_threat
                    new_t = new & threat
                    frontier[(i + step_cost) % cycle_len] |= new_nt & not_barriers
                    frontier[(i + step_cost + barrier_cost) % cycle_len] |= new_nt & barriers
                    frontier[(i + step_cost + threat_cost) % cycle_len] |= new_t & not_barriers
                    frontier[(i + step_cost + barrier_cost + threat_cost) % cycle_len] |= new_t & barriers
            i += 1
        return None
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
            target_set = {target}
        else:
            target_set = target
        if target_set != self.target_p:
            self.forget_launcher.clear()
        avoid = map_info.get_avoid(False, True, False)
        if avoid_empty:
            has_building = 0
            for i in range(map_info._NUM_ET):
                has_building |= map_info._bm_et[i]
            avoid |= map_info._bm_seen & ~has_building & ~map_info._bm_env[map_info._IDX_ENV_WALL]
        my_pos = self.rc.get_position()
        if target_set == self.target_p and my_pos == self.prev_pos and my_pos not in target_set and all(max(abs(my_pos.x - t.x), abs(my_pos.y - t.y)) > 1 for t in target_set):
            self.stuck_turns += 1
        else:
            self.prev_pos = my_pos
            self.stuck_turns = 0
            self.target_p = target_set
        if self.stuck_turns > 2 + self.rc.get_id() % 8:
            for d in ALL_DIRS:
                if self.rc.can_move(d):
                    self.rc.move(d)
                    map_info.update_move()
                    return True

        w = self.width
        start_mask = 1 << (my_pos.x + my_pos.y * w)
        target_mask = 0
        for t in target_set:
            target_mask |= 1 << (t.x + t.y * w)
        result = self.bfs(start_mask, target_mask, avoid, False, avoid_turret=avoid_turret)
        if result is None:
            return False
        s_pos, p_pos, _ = result
        if s_pos == p_pos:
            return False
        self.rc.draw_indicator_line(s_pos, p_pos, 0, 255, 255)
        return self.move(s_pos.direction_to(p_pos))



    def calculate_conveyor_path(self, start: Position, raw_axionite: bool, update: bool = False):
        print("conveyors from ", start, raw_axionite)
        w = self.width
        if update:
            target, avoid = self._get_conveyor_targets_and_avoid(raw_axionite, start.x + start.y * map_info._width)
        else:
            target, avoid = self._get_conveyor_targets_and_avoid(raw_axionite)
        if not target:
            return None
        if not update:
            start_mask = 0
            for d in CARD_DIR:
                sp = start.add(d)
                if map_info.in_bounds(sp) and ((avoid >> (sp.x + sp.y * w)) & 1) == 0:
                    start_mask |= 1 << (sp.x + sp.y * w)
            if start_mask == 0:
                return None
        else:
            start_mask = 1 << (start.x + start.y * w)
        end_cost_mask = self.raw_ax_foundry_sites() if raw_axionite else 0
        result = self.bfs(start_mask, target, avoid, True, end_cost_mask=end_cost_mask)
        if result is None:
            return None
        s_pos, p_pos, dist = result
        self.rc.draw_indicator_line(s_pos, p_pos, 255, 0, 255)
        self.rc.draw_indicator_dot(s_pos, 255, 0, 255)
        return (s_pos, p_pos, dist)

    def conveyor_cost(self, dist, scaling=None):
        if scaling is None:
            scaling = self.rc.get_scale_percent() / 100
        if dist is None or dist < 0:
            return None
        cost = 0
        for _ in range(dist):
            cost += 3 * scaling
            scaling += 0.01
        return cost
    def raw_ax_foundry_sites(self):
        w = map_info._width
        my_idx = map_info._TM_INT[self.rc.get_team()]
        enemy_idx = 1 - my_idx
        harv_on_ore = map_info._bm_et[map_info._IDX_HARVESTER] & map_info._bm_team[my_idx] & map_info._bm_env[map_info._IDX_ENV_ORE_TI]
        my_foundries = map_info._bm_et[map_info._IDX_FOUNDRY] & map_info._bm_team[my_idx]
        foundry_adj = ((my_foundries & map_info._not_right_col) << 1) | ((my_foundries & map_info._not_left_col) >> 1) | (my_foundries << w) | (my_foundries >> w)
        harv_on_ore &= ~foundry_adj
        adj = ((harv_on_ore & map_info._not_right_col) << 1) | ((harv_on_ore & map_info._not_left_col) >> 1) | (harv_on_ore << w) | (harv_on_ore >> w)
        enemy_block = (
            map_info._bm_team[enemy_idx]
            & ~map_info._bm_et[map_info._IDX_ROAD]
            & ~map_info._bm_et[map_info._IDX_MARKER]
        )
        friendly_block = (
            (map_info._bm_et[map_info._IDX_HARVESTER]
             | map_info._bm_et[map_info._IDX_FOUNDRY]
             | map_info._bm_et[map_info._IDX_CORE])
            & map_info._bm_team[my_idx]
        )
        blocked = enemy_block | friendly_block | map_info._bm_env[map_info._IDX_ENV_WALL]
        open_mask = ~blocked
        n1 = (open_mask & map_info._not_right_col) << 1
        n2 = (open_mask & map_info._not_left_col) >> 1
        n3 = open_mask << w
        n4 = open_mask >> w
        at_least_two = ((n1 & n2) | (n1 & n3) | (n1 & n4)
                        | (n2 & n3) | (n2 & n4) | (n3 & n4))
        return adj & ~blocked & at_least_two

    def _get_conveyor_targets_and_avoid(
        self, raw_axionite: bool, conveyor = None
    ):
        avoid = map_info.get_avoid(True, False, True)
        if raw_axionite:
            target = self.raw_ax_foundry_sites()
            target |= map_info._bm_route_targets & map_info._bm_conv_raw_ax
            if conveyor:
                avoid &= ~(1<<map_info._building_conv_target[conveyor])
            return target, avoid
        else:
            target = (map_info._bm_route_targets & (map_info._bm_conv_ti | map_info._bm_conv_refined)) | map_info._bm_my_core_area
            if not target:
                return 0, 0
            if conveyor:
                avoid &= ~(1<<map_info._building_conv_target[conveyor])
            return target, avoid