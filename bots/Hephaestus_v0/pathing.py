import heapq
import map_info
from cambc import Controller, Direction, Position, EntityType
import comms
import math
from array import array
import time
import units.builder as builder
import sys
WEIGHT = 2
MIN_WEIGHT = 1.5
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

bridge_cost = 10
barrier_cost = 5
adj_launch_cost = 10
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
    (-2, 0, bridge_cost),
    (2, 0, bridge_cost),
    (0, 2, bridge_cost),
    (0, -2, bridge_cost),
    (-1, -1, bridge_cost),
    (-1, 1, bridge_cost),
    (1, -1, bridge_cost),
    (1, 1, bridge_cost),
]

class Pathing:
        
    seen = None #a stamp array checking if we have seen this tile in this run
    best_g = None #regular array storing best g (best distance)
    target = None #a stamp array containing targets
    start = None #a stamp array containing the start locations (target of a* since its backwards)
    avoid = None #a stamp array (updated every call instead of every run) that stores which tiles are impassible
    barriers = None #stamp array for barriers
    adj_launch = None #stamp array for tiles adjacent to launcher
    #inputs to a*, so we can check if it changed
    start_p = None
    target_p = None
    adjacent = None
    dirs = None

    changed = False
    
    parent = None #for rebuilding (NOT A STAMP ARRAY)
    
    MAX_ITER = None

    destroyed_barriers = set()
    run_id = 1 #start at 1 in case something weird happens (all stamp arrays init to 0)
    avoid_id = 1
    heap = []
    iter = 0

    path = [] #i want to store the current path found so i can follow it while recomputing
    path_idx = 0
    
    forget_launcher = set()
    width = height = 0
    rc = None
    
    def __init__(self, c: Controller):
        self.width = c.get_map_width()
        self.height = c.get_map_height()
        self.rc = c
        self.seen    = array('I', [0]) * (self.width * self.height)
        self.parent  = array('I', [0]) * (self.width * self.height)
        self.target  = array('I', [0]) * (self.width * self.height)
        self.start  = array('I', [0]) * (self.width * self.height)
        self.avoid   = array('I', [0]) * (self.width * self.height)
        self.barriers   = array('I', [0]) * (self.width * self.height)
        self.adj_launch   = array('I', [0]) * (self.width * self.height)
        self.best_g  = array('I', [0]) * (self.width * self.height)
        self.MAX_ITER = int(math.sqrt(self.width * self.height))*10
        self.heap = []
        self.path = []
        self.path_idx = 0


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
            self.destroyed_barriers.add(new_pos)
        if rc.can_build_road(new_pos):
            rc.build_road(new_pos)
        for p in self.destroyed_barriers:
            if rc.can_build_barrier(p):
                rc.build_barrier(p)
        if rc.can_move(dir):
            rc.move(dir)
            return True
        return False


    def init_a_star(self, start_p: Position, target_p: Position | set[Position], input_dirs:list[Direction]=DIRS, adjacent_in: bool = False):
        builder.log("a* init")
        if self == builder.nav:
            print(start_p, target_p)
        if isinstance(target_p, Position):
            target_p = {target_p}
        self.changed = False
        self.moved = self.start_p != start_p
        if self.adjacent is not adjacent_in:
            self.changed = True
        if self.dirs is not input_dirs:
            self.changed = True
        if self.start_p and self.start_p.distance_squared(start_p) > 2:
            self.changed = True
        if input_dirs == CONV and self.moved:
            self.changed = True
        if self.target_p != target_p:
            self.changed = True
        
        self.start_p = start_p
        self.target_p = target_p
        self.adjacent = adjacent_in
        self.dirs = input_dirs

        heappush  = heapq.heappush
        abs_local = abs
        max_local = max

        width_l  = self.width
        if self.changed:
            self.heap.clear()
        if self == builder.ore_nav:
            print("changed? " + str(self.changed) + " " + str(len(self.heap)))
        if len(self.heap) == 0:
            is_dirs  = (input_dirs is DIRS)

            self.run_id += 1
            self.iter = 0
            for p in target_p:
                t  = p.y * width_l + p.x
                self.target[t] = self.run_id
                if is_dirs:
                    h0 = max_local(abs_local(p.x - start_p.x), abs_local(p.y - start_p.y))
                else:
                    h0 = abs_local(p.x - start_p.x) + abs_local(p.y - start_p.y)
                
                heappush(self.heap, (h0*WEIGHT, 0, True, False, 0, t, 0))
                self.seen[t] = self.run_id

    def reconstruct_path(self, pos: int):
        path_out = []
        while pos != -1:
            path_out.append(Position(pos % self.width, pos // self.width))
            pos = self.parent[pos] if self.target[pos] != self.run_id else -1
        return path_out

    def a_star(self, start_p: Position, avoid_p: set[Position] = None) -> list[Position] | None:
        builder.log("a* start")
        if self == builder.ore_nav:
            builder.log("CONV A STAR")
        if avoid_p is None:
            avoid_p = set()
        self.avoid_id += 1

        
        run_id = self.run_id
        seen = self.seen
        changed = self.changed
        width = self.width
        height = self.height
        heap = self.heap
        dirs = self.dirs
        adjacent = self.adjacent
        target = self.target
        run_id = self.run_id
        avoid = self.avoid
        barriers = self.barriers
        adj_launch = self.adj_launch
        avoid_id = self.avoid_id
        rc = self.rc
        parent = self.parent
        best_g = self.best_g
        start = self.start
        sx = start_p.x
        sy = start_p.y
        start_time = time.perf_counter_ns()
        heappush  = heapq.heappush
        heappop   = heapq.heappop
        cx = map_info._my_core.x
        cy = map_info._my_core.y

        
        
        is_dirs = (dirs is DIRS)

        if adjacent:
            for dx, dy, _ in CARD:
                p = Position(sx+dx, sy+dy)
                if not map_info.in_bounds(p):
                    continue
                start[p.x+p.y*width] = run_id
        else:
            start[sx+sy*width] = run_id
        for a in avoid_p:
            h = a.y * width + a.x
            if (h == sx+sy*width and not adjacent) or target[h] == run_id:
                continue
            avoid[h] = avoid_id
        for b in map_info._my_barriers:
            barriers[b.y * width + b.x] = avoid_id
        for p in map_info._enemy_launch_adj:
            adj_launch[p.y * width + p.x] = avoid_id
        
        if not adjacent:
            has_initial_move = False
            for dx, dy, _ in dirs:
                if not map_info.in_bounds(Position(sx+dx, sy+dy)):
                    continue
                if avoid[(start_p.x+dx+(start_p.y+dy)*width)] != avoid_id:
                    has_initial_move = True
            if not has_initial_move:
                return []
            
        WEIGHT_L = WEIGHT
        new_hp = []
        while heap:
            f, g, card, zig_flag, zig_time, pos, iter = heappop(heap)
            g *= -1
            nx = pos%width
            ny = pos//width
            MIN_WEIGHT_L = MIN_WEIGHT+min(iter/100, 1)*(WEIGHT_L-MIN_WEIGHT)
            if avoid[pos] == avoid_id:
                continue
            if g > best_g[pos]:
                continue
            if is_dirs:
                h0 = max(abs(nx - sx), abs(ny - sy))
            else:
                h0 = abs(nx - sx) + abs(ny - sy)
            new_h = 0 if h0 == 0 else MIN_WEIGHT_L + (WEIGHT_L - MIN_WEIGHT) * max(0, 1 - g / h0)
            new_f = g + h0 * new_h
            heappush(new_hp, (new_f, -g, card, zig_flag, zig_time, pos, iter))
        self.heap = new_hp
        heap = self.heap
        #heap format = path length, estimated path length, cardinal?, zigzag flag, zigzag time, start id
        if not adjacent and seen[sx+sy*width] == run_id:
            path_out = self.reconstruct_path(sx+sy*width)
            heap.clear()
            return path_out
        # c = 0
        if self == builder.ore_nav:
            builder.log(str(len(heap)))
        while heap:
            # c += 1
            # if c > 20:
            #     return None
            self.iter += 1
            MIN_WEIGHT_L = MIN_WEIGHT+min(self.iter/100, 1)*(WEIGHT_L-MIN_WEIGHT)
            if self.iter > self.MAX_ITER:
                break
            _, g, card, _, zig_time, pos, _ = heappop(heap)
            g *= -1
            if start[pos] == run_id:
                path_out = self.reconstruct_path(pos)
                heap.clear()
                end_time = time.perf_counter_ns()
                builder.log("a star time: " + str(end_time-start_time))
                return path_out

            px = pos % width
            py = pos // width
            if self == builder.nav:
                rc.draw_indicator_dot(Position(px, py), min(255, self.iter*255//625), 0, 0)
            for dx, dy, cost in dirs:
                nx = px + dx
                ny = py + dy
                if (6 <= (nx-cx)*(nx-cx)+(ny-cy)*(ny-cy) <= 49) and dirs == CONV and cost == 1:
                    continue
                if nx < 0 or nx >= width or ny < 0 or ny >= height:
                    continue
                n = ny * width + nx 
                if avoid[n] == avoid_id:
                    continue
                ng = g + cost
                if dirs != CONV and barriers[n] == avoid_id:
                    ng += barrier_cost
                if dirs != CONV and adj_launch[n] == avoid_id:
                    ng += adj_launch_cost
                if ng >= best_g[n] and seen[n] == run_id:
                    continue
                
                if is_dirs:
                    h0 = max(abs(nx - sx), abs(ny - sy))
                else:
                    h0 = abs(nx - sx) + abs(ny - sy)
                best_g[n] = ng
                seen[n]   = run_id
                parent[n] = pos

                new_h = 0 if h0 == 0 else MIN_WEIGHT_L + (WEIGHT_L - MIN_WEIGHT) * max(0, 1 - ng / h0)
                new_f = ng + h0 * new_h
                # print("before ", n, self.dist_to_target.get(n), max_local(0, 1 - (ng) / h0) if h0 != 0 else 0, ng, MIN_WEIGHT_L, h0, new_h, new_f, is_dirs, nx, ny, tx, ty)

                card = dx == 0 or dy == 0
                new_zigged = (zig_time%(ZIG_LENGTH*2) < ZIG_LENGTH)^(dx>0)^(dy>0)
                if new_zigged:
                    new_zig_time = (zig_time+1)%(ZIG_LENGTH*2)
                else:
                    new_zig_time = ZIG_LENGTH if zig_time < ZIG_LENGTH else 0
                heappush(
                    heap,
                    (new_f, -ng, card, not new_zigged, new_zig_time, n, self.iter)
                )
        end_time = time.perf_counter_ns()
        heap.clear()
        builder.log("a star time: " + str(end_time-start_time)) 
        return []


    def moves_through_impassible(self, path: list[Position], avoid: set[Position]) -> bool:
        for i in range(1, len(path) - 1):
            if path[i] in avoid:
                return True
        return False

    def calculate_path(self, target: set[Position] | Position, avoid = None, start=None, dirs = DIRS, adjacent=False):
        rc = self.rc
        if start is None:
            start = rc.get_position()
        if isinstance(target, Position):
            self.rc.draw_indicator_line(Position(0, 0), start, 255, 255, 255)
            self.rc.draw_indicator_line(Position(0, 0), target, 255, 255, 255)
            target = {target}

        if avoid == None:
            avoid = map_info.get_avoid(False, True, False)
        next_path = None
        self.init_a_star(start, target, dirs, adjacent)
        next_path = self.a_star(start, avoid)
        if next_path is not None:
            self.path = next_path
            self.path_idx = 0
            for i in range(len(self.path) - 1):
                rc.draw_indicator_line(self.path[i], self.path[i + 1], 0, 50, 0)
        elif self.path is not None and len(self.path) > 1:
            for i in range(len(self.path) - 1):
                rc.draw_indicator_line(self.path[i], self.path[i + 1], 0, 0, 50)
        
        on_path = self.path and len(self.path) >= self.path_idx+1 and self.path[self.path_idx] == start and self.path[-1] in target
        if on_path:
            return self.path[self.path_idx:]
        return next_path


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
    def move_to(self, target: Position):
        if {target} != self.target_p:
            self.forget_launcher.clear()
        print("move to ", target)
        avoid = map_info.get_avoid(False, True, False)
        # for a in avoid:
            # self.rc.draw_indicator_dot(a, 255, 0, 0)
        my_pos = self.rc.get_position()

        path = self.calculate_path(target, avoid)
        marked = False
        rc = self.rc

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
                        rc.place_marker(p2, comms.encode_launch(target))
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
                        if rc.can_place_marker(p2):
                            rc.place_marker(p2, comms.encode_launch(target))
                            self.forget_launcher.add(pos)
                            marked = True
                            break
            if marked:
                break
        if marked:
            return
        if path is None:
            return None
        if len(path) < 1:
            return False
        self.execute_path()
        return True



    def calculate_conveyor_path(self, ore: Position, avoid_extra: list[Position] = None, update: bool = False):
        print("conveyors from ", ore)
        core = map_info._my_core
        if not avoid_extra:
            avoid_extra = {}
        target = {Position(core.x + dx, core.y + dy) for _, (dx, dy) in ALL_DIRS_DELTAS}

        # FIX: cache frequently-used references for the loop
        width_l        = map_info._width
        height_l       = map_info._height
        my_team        = self.rc.get_team()
        is_conveyor    = map_info.is_conveyor

        for x in range(width_l):
            for y in range(height_l):
                if map_info.id_at(x, y) != 0 and is_conveyor(map_info.type_at(x, y)) and map_info.can_route(x, y) and map_info.load_at(x, y) <= 3 and map_info.team_at(x, y) == my_team and Position(x, y) not in avoid_extra:
                    target.add(Position(x, y))
        adding_foundry = False
        if builder.target_foundry not in target and (not map_info.id_at(builder.target_foundry.x, builder.target_foundry.y) != 0 or not map_info.is_conveyor(map_info.type_at(builder.target_foundry.x, builder.target_foundry.y))):
            adding_foundry = True
            target.add(builder.target_foundry)
        avoid = map_info.get_avoid(True, False, True)
        for p in avoid_extra:
            avoid.add(p)
        self.calculate_path(target, avoid, ore, CONV, not update)
        if self.path is None or len(self.path) < 1:
            return self.path
        if self.path[-1] == builder.target_foundry and adding_foundry:
            for d in ALL_DIRS:
                if builder.target_foundry.distance_squared(core.add(d)) == 1:
                    self.path.append(core.add(d))
                    break
        return self.path


    def calculate_launcher_position(self, path: list[Position], ore: Position) -> Position | None:
        if self.rc.get_unit_count() == 50: #maybe remove later, but if we hit cap, i literally cant place more launchers
            return None
        avoid = map_info.get_avoid(True, False, True)
        avoid.update(path)

        current_pos  = self.rc.get_position()
        width_local  = map_info._width
        height_local = map_info._height
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

                    if map_info.id_at(x, y) != 0 and map_info.team_at(x, y) == team and map_info.type_at(x, y) == EntityType.LAUNCHER:
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
