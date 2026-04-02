import heapq
import map_info
from cambc import Controller, Direction, Position, EntityType
import comms
import math
from array import array
import time
import units.builder as builder
import sys
WEIGHT = 3
MIN_WEIGHT = 1.2
TIME_CUTOFF = 1600
MAX_TIME = 400
ZIG_LENGTH = 2

ALL_DIRS = list(Direction)
ALL_DIRS_DELTAS = [(d, d.delta()) for d in ALL_DIRS]

CARD_DIR = [
    Direction.NORTH,
    Direction.SOUTH,
    Direction.EAST,
    Direction.WEST,
]
CARD = [
    (0, -1, 1),
    (0, 1, 1),
    (-1, 0, 1),
    (1, 0, 1),
]
DIRS = [
    (0, -1, 1),
    (0, 1, 1),
    (-1, 0, 1),
    (1, 0, 1),
    (-1, -1, 1),
    (1, -1, 1),
    (-1, 1, 1),
    (1, 1, 1),
]

bridge_cost = 2
CONV = [
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
    (2, 1, bridge_cost),
    (2, -1, bridge_cost),
    (-2, 1, bridge_cost),
    (-2, -1, bridge_cost),
    (1, 2, bridge_cost),
    (1, -2, bridge_cost),
    (-1, 2, bridge_cost),
    (-1, -2, bridge_cost),
]

class Pathing:
        
    seen = None
    best_g = None
    adjacent = None
    parent = None
    target = None
    avoid = None
    MAX_ITER = None

    dist_to_target = {}
    
    run_id = 1
    avoid_id = 1

    heap = []
    iter = 0
    dirs = None

    path = []
    path_idx = 0
    
    start_p = None
    target_p = None
    changed = False
    moved = False

    reference_path = None
    width = height = 0
    rc = None
    
    child_pathing = None
    def __init__(self, c: Controller, child=False):
        self.width = c.get_map_width()
        self.height = c.get_map_height()
        self.rc = c
        self.seen    = array('I', [0]) * (self.width * self.height)
        self.parent  = array('I', [0]) * (self.width * self.height)
        self.target  = array('I', [0]) * (self.width * self.height)
        self.avoid   = array('I', [0]) * (self.width * self.height)
        self.best_g  = array('I', [0]) * (self.width * self.height)
        self.MAX_ITER = int(math.sqrt(self.width * self.height))*10
        self.heap = []
        self.path = []
        self.dist_to_target = {}
        self.path_idx = 0
        if not child:
            self.child_pathing = Pathing(c, True)

    def move(self, dir: Direction):
        rc = self.rc
        px, py = rc.get_position().x, rc.get_position().y
        dx, dy = dir.delta()
        new_pos = Position(px + dx, py + dy)

        if not map_info.in_bounds(new_pos):
            return False
        id = rc.get_tile_building_id(new_pos)

        if id and rc.get_entity_type(id) == EntityType.BARRIER and rc.can_destroy(new_pos):
            rc.destroy(new_pos)
        if rc.can_build_road(new_pos):
            rc.build_road(new_pos)
        if rc.can_move(dir):
            rc.move(dir)
            return True
        return False


    def init_a_star(self, start_p: Position, target_p: Position | set[Position], input_dirs:list[Direction]=DIRS, adjacent_in: bool = False):
        if isinstance(target_p, Position):
            target_p = {target_p}
        self.changed = False
        self.moved = self.start_p != start_p
        if self.adjacent is not adjacent_in:
            self.changed = True
        if self.dirs is not input_dirs:
            self.changed = True
        if not self.start_p or not self.target_p:
            self.changed = True
        else:
            if self.start_p.distance_squared(start_p) > 2:
                self.changed = True
            if self.target_p != target_p:
                self.changed = True
        if self.changed:
            self.dist_to_target = {}
        self.start_p = start_p
        self.target_p = target_p
        self.adjacent = adjacent_in
        heappush  = heapq.heappush
        abs_local = abs
        max_local = max
        self.dirs = input_dirs

        is_dirs  = (input_dirs is DIRS)
        width_l  = self.width
        if self.changed:
            self.heap.clear()
        insert_heap = False
        if len(self.heap) == 0:
            insert_heap = True
            self.run_id += 1
            self.iter = 0
            for p in target_p:
                t  = p.y * width_l + p.x
                self.target[t] = self.run_id
                if insert_heap:
                    if is_dirs:
                        h0 = max_local(abs_local(p.x - start_p.x), abs_local(p.y - start_p.y))
                    else:
                        h0 = abs_local(p.x - start_p.x) + abs_local(p.y - start_p.y)
                    
                    heappush(self.heap, (h0*WEIGHT, 0, True, False, 0, t, 0))
                    self.seen[t] = self.run_id
                    # print("pushing", p, start_p)


    def a_star(self, start_p: Position, avoid_p: set[Position] = None) -> list[Position] | None:
        width_l = self.width
        my_core = map_info.my_core
        start_time = time.perf_counter_ns()
        heappush  = heapq.heappush
        heappop   = heapq.heappop
        abs_local = abs
        max_local = max
        hp        = self.heap
        width_l   = self.width
        height_l  = self.height

        start = start_p.y * width_l + start_p.x
        tx = start_p.x
        ty = start_p.y
        
        if avoid_p is None:
            avoid_p = set()
        self.avoid_id += 1
        max_length = 1_000_000

        dirs = self.dirs
        adjacent = self.adjacent
        target = self.target
        run_id = self.run_id
        avoid = self.avoid
        avoid_id = self.avoid_id
        seen = self.seen
        rc = self.rc
        parent = self.parent
        best_g = self.best_g
        
        is_dirs = (dirs is DIRS)

        if adjacent:
            left  = -1 if start % width_l == 0          else start - 1
            right = -1 if start % width_l == width_l - 1 else start + 1
            up    = -1 if start // width_l == 0          else start - width_l
            down  = -1 if start // width_l == height_l - 1 else start + width_l

        avoid_changed = False
        for a in avoid_p:
            h = a.y * width_l + a.x
            if (h == start and not adjacent) or target[h] == run_id:
                continue
            if avoid[h] != avoid_id-1:
                avoid_changed = True
            avoid[h] = avoid_id
        has_initial_move = False
        for dx, dy, _ in self.dirs:
            if not map_info.in_bounds(Position(start_p.x+dx, start_p.y+dy)):
                continue
            if avoid[(start_p.x+dx+(start_p.y+dy)*width_l)] != avoid_id:
                has_initial_move = True
        if not has_initial_move:
            return []
        if not self.changed and not avoid_changed and self.reference_path:
            max_length = len(self.reference_path) - (1 if self.moved else 0)
        else:
            self.reference_path = []
        if self.changed:
            self.dist_to_target = {}
        if self.changed or avoid_changed:
            self.dist_to_target = {}
        WEIGHT_L = WEIGHT
        new_hp = []
        while hp:
            f, g, card, zig_flag, zig_time, pos, iter = heappop(hp)
            g *= -1
            nx = pos%width_l
            ny = pos//width_l
            MIN_WEIGHT_L = MIN_WEIGHT+min(iter/100, 1)*(WEIGHT-MIN_WEIGHT)
            if avoid[pos] == avoid_id:
                continue
            if g > best_g[pos]:
                continue
            if pos in self.dist_to_target:
                h0 = self.dist_to_target[pos]
                new_h = 1
            else:
                if is_dirs:
                    h0 = max_local(abs_local(nx - tx), abs_local(ny - ty))
                else:
                    h0 = abs_local(nx - tx) + abs_local(ny - ty)
                new_h = 0 if h0 == 0 else MIN_WEIGHT_L + (WEIGHT_L - MIN_WEIGHT) * max_local(0, 1 - (g) / h0)
            new_f = g + h0 * new_h
            # rc.draw_indicator_dot(Position(nx, ny), 0, 255, 255)
            # print("after ", pos, self.dist_to_target.get(pos), max_local(0, 1 - (g) / h0) if h0 != 0 else 0, g, MIN_WEIGHT_L, h0, new_h, new_f, is_dirs, nx, ny, tx, ty)

            heappush(new_hp, (new_f, -g, card, zig_flag, zig_time, pos, iter))
        self.heap = new_hp
        hp = self.heap
        #heap format = path length, estimated path length, cardinal?, zigzag flag, zigzag time, start id
        if seen[start] == run_id:
            heappush(hp, (-1, 0, False, False, 0, start, 0))
        seen[start] = 0
        ZIG_LENGTH_L = ZIG_LENGTH
        start_cpu_time = rc.get_cpu_time_elapsed()
        c = 0
        while hp:
            # c += 1
            # if c > 20:
            #     return None
            if rc.get_cpu_time_elapsed() > TIME_CUTOFF or rc.get_cpu_time_elapsed()-start_cpu_time > MAX_TIME:
                return None
            self.iter += 1
            MIN_WEIGHT_L = MIN_WEIGHT+min(self.iter/100, 1)*(WEIGHT-MIN_WEIGHT)
            if self.iter > self.MAX_ITER:
                break
            _, g, card, _, zig_time, pos, _ = heappop(hp)
            g *= -1
            if (not adjacent and pos == start) or (adjacent and (pos == left or pos == right or pos == up or pos == down)):
                path_out = []
                path_length = best_g[pos]
                while pos != -1:
                    path_out.append(Position(pos % width_l, pos // width_l))
                    self.dist_to_target[pos] = path_length-best_g[pos]
                    pos = parent[pos] if target[pos] != run_id else -1
                hp.clear()
                end_time = time.perf_counter_ns()
                self.reference_path = path_out[:]
                return path_out

            px_cache = pos % width_l
            py_cache = pos // width_l
            for dx, dy, cost in dirs:
                nx = px_cache + dx
                ny = py_cache + dy
                if nx < 0 or nx >= width_l or ny < 0 or ny >= height_l:
                    continue
                n = ny * width_l + nx 
                if avoid[n] == avoid_id:
                    continue
                ng = g + cost
                if ng >= best_g[n] and seen[n] == run_id:
                    continue
                # if abs_local(dx) > 1 or abs_local(dy) > 1 and abs_local(nx-my_core.x) <= 1 and abs_local(ny-my_core.y) <= 1:  #this is so i can place a splitter at the end
                #     continue
                if is_dirs:
                    h0 = max_local(abs_local(nx - tx), abs_local(ny - ty))
                else:
                    h0 = abs_local(nx - tx) + abs_local(ny - ty)
                if ng + h0 > max_length:
                    continue
                best_g[n] = ng
                seen[n]   = run_id
                parent[n] = pos

                if n in self.dist_to_target:
                    h0 = self.dist_to_target[n]
                    new_h = 1
                else:
                    new_h = 0 if h0 == 0 else MIN_WEIGHT_L + (WEIGHT_L - MIN_WEIGHT) * max_local(0, 1 - (ng) / h0)
                    new_f = ng + h0 * new_h
                # print("before ", n, self.dist_to_target.get(n), max_local(0, 1 - (ng) / h0) if h0 != 0 else 0, ng, MIN_WEIGHT_L, h0, new_h, new_f, is_dirs, nx, ny, tx, ty)

                card = dx == 0 or dy == 0
                new_zigged = (zig_time%(ZIG_LENGTH_L*2) < ZIG_LENGTH_L)^(dx>0)^(dy>0)
                if new_zigged:
                    new_zig_time = (zig_time+1)%(ZIG_LENGTH_L*2)
                else:
                    new_zig_time = ZIG_LENGTH_L if zig_time < ZIG_LENGTH_L else 0
                heappush(
                    hp,
                    (new_f, -ng, card, not new_zigged, new_zig_time, n, self.iter)
                )
        end_time = time.perf_counter_ns()
        hp.clear()
        return []


    def moves_through_impassible(self, path: list[Position], avoid: set[Position] = None) -> bool:
        if avoid is None:
            return False
        for i in range(1, len(path) - 1):
            if path[i] in avoid:
                return True
        return False

    def calculate_path(self, target: set[Position] | Position, avoid = None, start=None, dirs = DIRS, adjacent=False):
        rc = self.rc
        if target is None:
            return []
        if start is None:
            start = rc.get_position()

        if avoid == None:
            avoid = map_info.get_avoid(False, True)
        next_path = None
        on_path = self.path and len(self.path) >= 2 and (self.path[0] == start or self.path[1] == start) and self.path[-1] in target
        if not self.path or self.moves_through_impassible(self.path, avoid) or not on_path:
            self.init_a_star(start, target, dirs, adjacent)
            next_path = self.a_star(start, avoid)
        elif on_path:
            for i in range(len(self.path)):
                if self.path[i] == start:
                    self.path = self.path[i:]
                    self.path_idx = 0
                    return self.path
            self.init_a_star(start, target, dirs, adjacent)
            next_path = self.a_star(start, avoid)
        if next_path is not None:
            self.path = next_path
            self.path_idx = 0
        return self.path


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
            if sample_path == self.path:
                self.path_idx += 1
            return True
        return False
    def move_to(self, target: Position, destroy_barrier: bool = False):
        if target is None:
            return False
        avoid = map_info.get_avoid(False, True, not destroy_barrier, False)
        my_pos = self.rc.get_position()

        path = self.calculate_path(target, avoid)

        if path is None or len(path) < 1:
            if destroy_barrier:
                return False
            else:
                return self.child_pathing.move_to(target, True)
        move_dir = None
        if self.path_idx+1 < len(self.path):
            move_dir = path[self.path_idx].direction_to(path[self.path_idx + 1])
        marked = False
        rc = self.rc
        # FIX: cache frequently-used method references
        rc_get_tile_building_id = rc.get_tile_building_id
        rc_get_entity_type      = rc.get_entity_type
        rc_get_team             = rc.get_team
        rc_get_vision_radius_sq = rc.get_vision_radius_sq
        rc_is_in_vision         = rc.is_in_vision
        rc_is_tile_passable     = rc.is_tile_passable
        rc_can_place_marker     = rc.can_place_marker
        rc_place_marker         = rc.place_marker
        rc_can_destroy          = rc.can_destroy
        rc_destroy              = rc.destroy
        my_team                 = rc.get_team()   # FIX: cache own team

        for dr, (dx, dy) in ALL_DIRS_DELTAS:
            pos = Position(my_pos.x + dx, my_pos.y + dy)
            if not map_info.in_bounds(pos):
                continue
            id = rc_get_tile_building_id(pos)

            if id and rc_get_entity_type(id) == EntityType.LAUNCHER and rc_get_team(id) == my_team:
                r = int(math.sqrt(rc_get_vision_radius_sq()))
                best = None
                best_dist = 0
                for x in range(pos.x - r, pos.x + r + 1):
                    for y in range(pos.y - r, pos.y + r + 1):
                        p = Position(x, y)
                        if rc_is_in_vision(p) and rc_is_tile_passable(p) and target.distance_squared(p) <= 2:
                            dist = max(abs(p.x - target.x), abs(p.y - target.y))
                            if not best or best_dist > dist * WEIGHT:
                                best_dist = dist * WEIGHT
                                best = p
                if best and best_dist < len(path) - self.path_idx:
                    for dr2, (dx2, dy2) in ALL_DIRS_DELTAS:
                        p2 = Position(pos.x + dx2, pos.y + dy2)
                        if not map_info.in_bounds(p2):
                            continue
                        id2 = rc_get_tile_building_id(p2)
                        if id2 and rc_get_team(id2) == my_team and rc_get_entity_type(
                                id2) == EntityType.ROAD and rc_can_destroy(p2) and dr != Direction.CENTRE:
                            rc_destroy(p2)
                        if rc_can_place_marker(p2):
                            rc_place_marker(p2, comms.encode_launch(best))
                            marked = True
                            break
            if marked:
                break

        if marked:
            return
        if move_dir and self.move(move_dir):
            self.path_idx += 1
            return True
        return False



    def calculate_conveyor_path(self, ore: Position, avoid_extra: list[Position] = None, update: bool = False):
        core = map_info.my_core
        if not avoid_extra:
            avoid_extra = {}
        target = {Position(core.x + dx, core.y + dy) for _, (dx, dy) in ALL_DIRS_DELTAS}

        # FIX: cache frequently-used references for the loop
        building_cache = map_info.building
        width_l        = map_info.width
        height_l       = map_info.height
        my_team        = self.rc.get_team()
        is_conveyor    = map_info.is_conveyor

        for x in range(width_l):
            for y in range(height_l):
                b = building_cache[x][y]
                if b and is_conveyor(b.type) and b.team == my_team and Position(x, y) not in avoid_extra:
                    # Accept conveyors with low load OR unknown load (out of vision)
                    if b.load is None or b.load < 4:
                        target.add(Position(x, y))

        avoid = map_info.get_avoid(True, False, False, True)
        for p in avoid_extra:
            avoid.add(p)
        for dir in CARD_DIR:
            dx, dy = dir.delta()
            pos = Position(ore.x + dx, ore.y + dy)
        next_path = self.calculate_path(target, avoid, ore, CONV, not update)
        if next_path:
            self.path = next_path
            self.path_idx = 0


        if self.path is None or len(self.path) < 1:
            return None
        return self.path


    def calculate_launcher_position(self, path: list[Position], ore: Position) -> Position | None:
        avoid = map_info.get_avoid(True, False)
        avoid.update(path)

        current_pos  = self.rc.get_position()
        width_local  = map_info.width
        height_local = map_info.height
        building     = map_info.building
        team         = self.rc.get_team()
        path_len     = len(path)

        for i in range(path_len - 1):
            possible: set[Position] | None = None
            last_possible: set[Position] | None = None

            for j in range(i, path_len - 1):
                base = path[j]
                bx   = base.x
                by   = base.y

                here = set()
                has_launcher = False

                for _, (dx, dy) in ALL_DIRS_DELTAS:
                    x = bx + dx
                    y = by + dy

                    if x < 0 or x >= width_local or y < 0 or y >= height_local:
                        continue

                    candidate = Position(x, y)
                    here.add(candidate)

                    b = building[x][y]
                    if b and b.team == team and b.type == EntityType.LAUNCHER:
                        has_launcher = True

                if has_launcher:
                    continue

                if possible is None:
                    new_possible = here - avoid
                else:
                    new_possible = possible.intersection(here)
                    if new_possible:
                        new_possible.difference_update(avoid)

                if not new_possible:
                    break

                last_possible = new_possible
                possible = new_possible

            if last_possible:
                best = min(last_possible, key=lambda p: p.distance_squared(current_pos))
                return best

        return None