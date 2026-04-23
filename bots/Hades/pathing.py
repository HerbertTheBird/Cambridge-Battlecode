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
from log import DRAW_DEBUG, log

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
barrier_cost = 15
threat_cost = 20
conveyor_end_cost = 10



destroyed_barriers = dict()

# Column masks for shifting by 2/3 — precomputed once from map dimensions.
_col_masks_initialized = False
_nlc2 = 0
_nlc3 = 0
_nrc3 = 0

def _init_col_masks(width: int, height: int) -> None:
    global _col_masks_initialized, _nlc2, _nlc3, _nrc3
    if _col_masks_initialized:
        return
    left_col = 0
    right_col = 0
    for y in range(height):
        left_col |= 1 << (y * width)
        right_col |= 1 << ((width - 1) + y * width)
    left_col_2 = left_col | (left_col << 1)
    left_col_3 = left_col_2 | (left_col << 2)
    right_col_2 = right_col | (right_col >> 1)
    right_col_3 = right_col_2 | (right_col >> 2)
    _nlc2 = ~left_col_2
    _nlc3 = ~left_col_3
    _nrc3 = ~right_col_3
    _col_masks_initialized = True
def rebuild_broken_barriers(rc: Controller):
    if  rc.get_global_resources()[0] < rc.get_barrier_cost()[0]:
        return
    if rc.get_action_cooldown() > 0:
        return

    my_pos = map_info._my_pos
    my_team = map_info._my_team
    current_round = rc.get_current_round()
    
    rebuilt_pos = None
    
    for p in destroyed_barriers:
        if p == my_pos:
            continue
        if my_pos.distance_squared(p) > 2:
            continue
        if destroyed_barriers[p]+1 > current_round:
            continue
        id = rc.get_tile_building_id(p)
        if id and rc.get_entity_type(id) == EntityType.ROAD and rc.get_team(id) == my_team and rc.can_destroy(p) and not rc.get_tile_builder_bot_id(p):
            rc.destroy(p)
            map_info.update_at(p)
        if rc.can_build_barrier(p):
            rc.build_barrier(p)
            map_info.update_at(p)
            rebuilt_pos = p
            break
    if rebuilt_pos is not None:
        destroyed_barriers.pop(rebuilt_pos, None)
def voronoi_claim(my_mask, others_mask, claims):
    if not claims:
        return 0
    if not others_mask:
        return claims
    w = map_info._width
    board = (1 << (w * map_info._height)) - 1
    avoid = map_info.get_avoid(False, False, False)
    passable = (~avoid & board) | claims

    my_front = my_mask & passable
    other_front = others_mask & passable
    my_claimed = my_front
    other_claimed = other_front
    all_claimed = my_claimed | other_claimed

    while (claims & ~all_claimed) and (my_front or other_front):
        if my_front:
            my_expand = map_info.expand_chebyshev(my_front) & passable & ~all_claimed
            my_claimed |= my_expand
            all_claimed |= my_expand
            my_front = my_expand
        if not (claims & ~all_claimed):
            break
        if other_front:
            other_expand = map_info.expand_chebyshev(other_front) & passable & ~all_claimed
            other_claimed |= other_expand
            all_claimed |= other_expand
            other_front = other_expand

    return my_claimed & claims

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
            pos = map_info._my_pos
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
        # builder.draw_mask(targets, 255, 0, 0)
        while frontier:
            hit = frontier & targets
            if hit:
                lsb = hit & -hit
                n = lsb.bit_length() - 1

                return Position(n % w, n // w), dist
            visited |= frontier
            dist += 1
            h = frontier | ((frontier & nrc) << 1) | ((frontier & nlc) >> 1)
            expanded = h | (h << w) | (h >> w)
            frontier = expanded & passable & ~visited
        return None, -1

    def __init__(self, c: Controller):
        self.width = c.get_map_width()
        self.height = c.get_map_height()
        self.rc = c

        w = self.width
        h = self.height
        _init_col_masks(w, h)

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
        px, py = map_info._my_pos.x, map_info._my_pos.y
        dx, dy = map_info._DIRECTION_DELTAS[dir]
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
            self.last_dir = map_info._DIRECTION_DELTAS[dir]
            return True
        return False
    # Move reconstruction offsets: (dx, dy, step_cost) for all 8 dirs
    _MOVE_OFFSETS = [
        (0, -1, 1), (0, 1, 1), (-1, 0, 1), (1, 0, 1),
        (-1, -1, 1), (1, -1, 1), (-1, 1, 1), (1, 1, 1),
    ]
    # Route reconstruction offsets: 4 cardinals (cost 1) + 24 bridge offsets
    # (all (dx,dy) with max(|dx|,|dy|) <= 2 and not (0,0)) at bridge_cost
    _ROUTE_OFFSETS = (
        [(0, -1, 1), (0, 1, 1), (-1, 0, 1), (1, 0, 1)]
        + [(dx, dy, bridge_cost)
           for dy in (-2, -1, 0, 1, 2)
           for dx in (-2, -1, 0, 1, 2)
           if (dx, dy) != (0, 0) and (abs(dx) == 2 or abs(dy) == 2)]
        + [(3, 0, bridge_cost), (-3, 0, bridge_cost),
           (0, 3, bridge_cost), (0, -3, bridge_cost)]
    )

    def bfs_move(self, start_n: int, target_mask: int, avoid: int, avoid_turret: bool = True):
        start_mask = 1 << start_n
        if start_mask & target_mask:
            s_idx = (start_mask & target_mask).bit_length() - 1
            return Position(s_idx % self.width, s_idx // self.width), Position(s_idx % self.width, s_idx // self.width), 0
        width = self.width
        height = self.height
        avoid &= ~start_mask
        # builders_mask = (map_info._bm_friendly_bots | map_info._bm_enemy_bots) & ~start_mask
        can_move_to = map_info.expand_chebyshev(start_mask) & ~avoid

        my_team_idx = map_info._my_team_idx
        barriers = map_info._bm_et[map_info._IDX_BARRIER] & map_info._bm_team[my_team_idx]
        barriers &= ~start_mask
        threat = map_info._bm_enemy_launch_adj
        if avoid_turret:
            threat |= map_info._bm_enemy_turret_threat
        threat &= ~start_mask
        # builder.draw_mask(target_mask, 0, 255, 255)
        # builder.draw_mask(avoid, 255, 0, 255)

        # builder.draw_mask(barriers, 0, 0, 255)

        walkable = (map_info._bm_et[map_info._IDX_ROAD]
                    | map_info._bm_conveyors
                    | map_info._bm_my_core_area
                    | start_mask)

        start_time = time.perf_counter_ns()

        nlc = map_info._not_left_col
        nrc = map_info._not_right_col
        w = width
        board = (1 << (w * height)) - 1
        not_avoid = board & ~avoid


        nb_nt  = board & ~barriers & ~threat
        b_nt   = board & barriers  & ~threat
        nb_t   = board & ~barriers & threat
        b_t    = board & barriers  & threat

        max_c = 1 + barrier_cost + threat_cost
        max_seed = barrier_cost + threat_cost
        cycle_len = max(max_c, max_seed) + 1
        frontier = [0] * cycle_len
        frontier[0]                                      = target_mask & nb_nt
        frontier[barrier_cost]                          |= target_mask & b_nt
        frontier[threat_cost]                           |= target_mask & nb_t
        frontier[barrier_cost + threat_cost]            |= target_mask & b_t
        visited = 0
        i = 0
        stuck_turns = 0
        while True:
            slot = i % cycle_len
            cur_frontier = frontier[slot] & ~visited
            # builder.draw_mask(cur_frontier, (i*64)%256, 0, 0)
            visited |= cur_frontier
            if cur_frontier == 0:
                stuck_turns += 1
                i += 1
                if stuck_turns >= cycle_len:
                    log("bfs move miss")
                    return None
                continue
            else:
                stuck_turns = 0
            hit = cur_frontier & can_move_to
            if hit:
                end_time = time.perf_counter_ns()
                log("bfs time " + str((end_time - start_time) / 1000) + "us")
                
                
                cx = start_n % width
                cy = start_n // width
                start_pos = Position(cx, cy)
                from_mask = hit
                if from_mask & walkable:
                    from_mask &= walkable
                border = (~nlc | ~nrc | ((1 << width) - 1) | (((1 << width) - 1)<<(w*(height-1)))) & board
                border |= map_info._bm_friendly_bots
                last_working_mask = from_mask
                c = 0
                while from_mask and c <= 4:
                    c += 1
                    last_working_mask = from_mask
                    from_mask &= ~border
                    border = map_info.expand_manhattan(border)
                from_mask = last_working_mask
                def family(dir):
                    dx, dy = dir
                    if dx == 0 or dy == 0:
                        return 0
                    return 1 if dx * dy > 0 else -1
                last_fam = family(self.last_dir) if self.last_dir is not None else 0
                last_last_fam = family(self.last_last_dir) if self.last_last_dir is not None else 0
                preferred_family = 0 if last_fam == 0 else -last_fam if last_fam == last_last_fam else last_fam
                if preferred_family == 0:
                    preferred_family = 2 #dont want to prefer straight over diag
                # builder.draw_mask(from_mask, 255, 255, 0)
                log("preferred family", preferred_family, self.last_dir, self.last_last_dir)
                best_dir = None
                while from_mask:
                    check_bit = from_mask & -from_mask
                    from_mask ^= check_bit
                    n = check_bit.bit_length() - 1
                    dir = (n % width - cx, n // width - cy)
                    if best_dir is None or family(dir) == preferred_family or abs(family(dir)) > abs(family(best_dir)):
                        best_dir = dir
                
                return start_pos, Position(cx+best_dir[0], cy+best_dir[1]), i
            # 3x3 Chebyshev expansion via 4 shifts
            f = cur_frontier
            h = f | ((f & nrc) << 1) | ((f & nlc) >> 1)
            expanded = h | (h << w) | (h >> w)
            new = expanded & ~visited & not_avoid

            frontier[(i + 1) % cycle_len]                                        |= new & nb_nt
            frontier[(i + 1 + barrier_cost) % cycle_len]                         |= new & b_nt
            frontier[(i + 1 + threat_cost) % cycle_len]                          |= new & nb_t
            frontier[(i + 1 + barrier_cost + threat_cost) % cycle_len]           |= new & b_t
            i += 1
            frontier[slot] = 0

    def bfs_route(self, start_mask: int, target_mask: int, avoid: int | None = None, end_cost_mask: int = 0):
        if start_mask & target_mask:
            s_idx = (start_mask & target_mask).bit_length() - 1
            return Position(s_idx % self.width, s_idx // self.width), Position(s_idx % self.width, s_idx // self.width), 0
        width = self.width
        height = self.height
        if avoid is None:
            avoid = map_info.get_avoid(False, True, False)
        # builder.draw_mask(avoid, 255, 0, 0)

        # builder.draw_mask(target_mask, 0, 255, 255)
        avoid &= ~start_mask

        start_time = time.perf_counter_ns()

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

        nlc = map_info._not_left_col
        nrc = map_info._not_right_col
        nlc2 = _nlc2
        nlc3 = _nlc3
        nrc3 = _nrc3
        w = width
        board = (1 << (w * height)) - 1
        not_avoid = board & ~avoid

        effective_len = max_seed + 1
        visited = 0
        visited_layers: list[int] = []
        i = 0
        while True:
            # log("route",i,file=sys.stderr)
            slot = i % cycle_len
            cur_frontier = frontier[slot] & ~visited
            frontier[slot] = 0
            visited_layers.append(cur_frontier)
            visited |= cur_frontier

            hit = cur_frontier & start_mask
            if hit:
                end_time = time.perf_counter_ns()
                log("bfs time " + str((end_time - start_time) / 1000) + "us")
                start_bit = hit & -hit
                s_idx = start_bit.bit_length() - 1
                cx = s_idx % width
                cy = s_idx // width
                start_pos = Position(cx, cy)
                vl_len = len(visited_layers)

                chosen_prev = None
                for dx, dy, step_cost in self._ROUTE_OFFSETS:
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

            if cur_frontier == 0:
                i += 1
                if i >= effective_len:
                    return None
                continue

            if i + max_c + 1 > effective_len:
                effective_len = i + max_c + 1

            f = cur_frontier
            # Cardinals (cost 1) — unrolled, avoid filter at end
            new_card = (
                ((f & nrc) << 1)
                | ((f & nlc) >> 1)
                | (f << w)
                | (f >> w)
            ) & not_avoid
            frontier[(i + 1) % cycle_len] |= new_card

            # Bridges — full 5x5 Chebyshev-2 zone via 6 OR-shifts (no mid-cell filtering)
            a = f | ((f & nrc) << 1)
            b = a | ((a & nrc) << 1)
            row = b | ((b & nlc2) >> 2)
            va = row | (row << w)
            vb = va | (va << w)
            zone = vb | (vb >> (2 * w))
            new_bridge = (zone & ~f) & not_avoid
            # 3-step cardinals (bridge jumps of distance 3)
            new_bridge |= (
                ((f & nrc3) << 3)
                | ((f & nlc3) >> 3)
                | (f << (3 * w))
                | (f >> (3 * w))
            ) & not_avoid
            frontier[(i + bridge_cost) % cycle_len] |= new_bridge
            i += 1
    def move_adjacent(self, pos: Position, fallback: Position | None = None, **kwargs):
        """Move to an adjacent tile of pos. Filters by in_bounds, passable, no builder bot, and in vision."""
        rc = self.rc
        adj = set()
        for d in ALL_DIRS:
            if d == Direction.CENTRE:
                continue
            p = map_info.pos_add(pos, d)
            if not map_info.in_bounds(p):
                continue
            if p == map_info._my_pos:
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
        log("move to", target)
        if isinstance(target, Position):
            target_set = {target}
        else:
            target_set = target
        if target_set != self.target_p:
            self.forget_launcher.clear()
        avoid = map_info.get_avoid(False, True, False)
        # if avoid_empty:
        #     avoid |= map_info._bm_seen & ~map_info._bm_any_building & ~map_info._bm_env[map_info._IDX_ENV_WALL]
        my_pos = map_info._my_pos
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
        target_mask = 0
        for t in target_set:
            target_mask |= 1 << (t.x + t.y * w)
        result = self.bfs_move(my_pos.x + my_pos.y * w, target_mask, avoid, avoid_turret=avoid_turret)
        if result is None:
            return False
        s_pos, p_pos, _ = result
        if s_pos == p_pos:
            return False
        if DRAW_DEBUG:
            self.rc.draw_indicator_line(s_pos, p_pos, 0, 255, 255)
        return self.move(s_pos.direction_to(p_pos))



    def calculate_conveyor_path(self, start: Position, raw_axionite: bool, update: bool = False):
        log("conveyors from ", start, raw_axionite)
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
                sp = map_info.pos_add(start, d)
                if map_info.in_bounds(sp) and ((avoid >> (sp.x + sp.y * w)) & 1) == 0:
                    start_mask |= 1 << (sp.x + sp.y * w)
            if start_mask == 0:
                return None
        else:
            start_mask = 1 << (start.x + start.y * w)
        end_cost_mask = self.raw_ax_foundry_sites() if raw_axionite else 0
        result = self.bfs_route(start_mask, target, avoid, end_cost_mask=end_cost_mask)
        if result is None:
            return None
        s_pos, p_pos, dist = result
        if DRAW_DEBUG:
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
        my_idx = map_info._my_team_idx
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
        core_adj = map_info.expand_manhattan(map_info._bm_my_core_area)
        return (adj & ~blocked & at_least_two) | (core_adj & map_info._bm_conveyors & map_info._bm_team[my_idx] & map_info._bm_conv_ti)

    def _get_conveyor_targets_and_avoid(
        self, raw_axionite: bool, conveyor = None
    ):
        avoid = map_info.get_avoid(True, False, True)
        if raw_axionite:
            ti_harvesters = map_info.expand_manhattan(map_info._bm_et[map_info._IDX_HARVESTER] & map_info._bm_env[map_info._IDX_ENV_ORE_TI])
            target = self.raw_ax_foundry_sites()
            avoid |= ti_harvesters
            target |= map_info._bm_route_targets & map_info._bm_conv_raw_ax
            if conveyor:
                target &= ~(1<<conveyor)
            return target, avoid
        else:
            ax_harvesters = map_info.expand_manhattan(map_info._bm_et[map_info._IDX_HARVESTER] & map_info._bm_env[map_info._IDX_ENV_ORE_AX])
            target = (map_info._bm_route_targets & ~map_info._bm_conv_raw_ax) | map_info._bm_my_core_area
            target &= ~ax_harvesters
            avoid |= ax_harvesters
            if not target:
                return 0, 0
            if conveyor:
                target &= ~(1<<conveyor)
            return target, avoid
