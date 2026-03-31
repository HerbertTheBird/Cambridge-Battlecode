
from cambc import Controller, EntityType, Position, GameError, Direction, Environment
import map_info
import sys
import comms
import math
rc: Controller | None = None
all_dirs = list(Direction)
_DIRS_8 = (
    (1, 0), (-1, 0), (0, 1), (0, -1),
    (1, 1), (1, -1), (-1, 1), (-1, -1),
)
_width = 0
_height = 0
_visible_passable = []
_search_seen = []
_search_dist = []
_search_start_edge = []
_heap = []
_visible_run_id = 0
_search_run_id = 0
_BEST_TILE_MAX_US = 1800

def init(c: Controller):
    global rc, _width, _height, _visible_passable, _search_seen, _search_dist, _search_start_edge
    rc = c
    _width = c.get_map_width()
    _height = c.get_map_height()
    grid_size = _width * _height
    _visible_passable = [0] * grid_size
    _search_seen = [0] * grid_size
    _search_dist = [0] * grid_size
    _search_start_edge = [0] * grid_size
    comms.init(c)
    map_info.init(c)

import heapq

def prepare_visible_passability(nearby_tiles):
    global _visible_run_id
    _visible_run_id += 1
    run_id = _visible_run_id

    passable = _visible_passable
    get_tile_env = rc.get_tile_env
    get_tile_building_id = rc.get_tile_building_id
    get_entity_type = rc.get_entity_type

    for pos in nearby_tiles:
        x = pos.x
        y = pos.y
        if get_tile_env(pos) == Environment.WALL:
            continue

        building_id = get_tile_building_id(pos)
        if building_id is None:
            passable[y * _width + x] = run_id
            continue

        b_type = get_entity_type(building_id)
        if b_type == EntityType.ROAD or map_info.is_conveyor(b_type):
            passable[y * _width + x] = run_id

    return run_id


def best_launch_tile(target: Position, builder_pos: Position, nearby_tiles, visible_run_id: int):
    global _search_run_id
    _search_run_id += 1
    run_id = _search_run_id
    start_us = rc.get_cpu_time_elapsed()

    def over_budget() -> bool:
        return rc.get_cpu_time_elapsed() - start_us >= _BEST_TILE_MAX_US

    width = _width
    height = _height
    passable = _visible_passable
    seen = _search_seen
    dist = _search_dist
    start_edge = _search_start_edge
    heap = _heap
    heap.clear()

    candidates = {}

    for i, tile in enumerate(nearby_tiles):
        if (i & 15) == 0 and over_budget():
            return None

        idx = tile.y * width + tile.x
        if passable[idx] != visible_run_id:
            continue
        if rc.can_launch(builder_pos, tile):
            candidates[idx] = tile

    if not candidates:
        return None
    if over_budget():
        return None

    target_x = target.x
    target_y = target.y
    target_in_vision = rc.is_in_vision(target)

    if target_in_vision:
        target_idx = target_y * width + target_x
        if passable[target_idx] != visible_run_id:
            return None
        seen[target_idx] = run_id
        dist[target_idx] = 0
        heapq.heappush(heap, (0, target_idx))
    else:
        is_in_vision = rc.is_in_vision
        for i, pos in enumerate(nearby_tiles):
            if (i & 15) == 0 and over_budget():
                return None

            x = pos.x
            y = pos.y
            idx = y * width + x
            if passable[idx] != visible_run_id:
                continue

            step_x = x + (target_x > x) - (target_x < x)
            step_y = y + (target_y > y) - (target_y < y)
            if is_in_vision(Position(step_x, step_y)):
                continue
            start_edge[idx] = run_id

            dx0 = abs(target_x - x)
            dy0 = abs(target_y - y)
            d0 = max(dx0, dy0) + dx0 + dy0
            if seen[idx] != run_id or d0 < dist[idx]:
                seen[idx] = run_id
                dist[idx] = d0
                heapq.heappush(heap, (d0, idx))

    while heap:
        if over_budget():
            return None

        cur_d, idx = heapq.heappop(heap)
        if seen[idx] != run_id or cur_d != dist[idx]:
            continue

        tile = candidates.get(idx)
        if tile is not None:
            if (not target_in_vision) and start_edge[idx] == run_id:
                tile = None
            else:
                return tile

        x = idx % width
        y = idx // width
        for dx, dy in _DIRS_8:
            nx = x + dx
            ny = y + dy
            if nx < 0 or nx >= width or ny < 0 or ny >= height:
                continue

            nidx = ny * width + nx
            if passable[nidx] != visible_run_id:
                continue

            nd = cur_d + 1
            if seen[nidx] != run_id or nd < dist[nidx]:
                seen[nidx] = run_id
                dist[nidx] = nd
                heapq.heappush(heap, (nd, nidx))

    return None
def run():
    messages = comms.decode_launch()
    pos = rc.get_position()
    nearby_tiles = rc.get_nearby_tiles()
    visible_run_id = prepare_visible_passability(nearby_tiles)

    for target, launch_id, turn, p in messages:
        if rc.get_action_cooldown() != 0:
            break

        if not (
            (turn == rc.get_current_round() and rc.get_id() > launch_id)
            or (turn == rc.get_current_round() - 1 and rc.get_id() < launch_id)
        ):
            continue

        builder_pos = None
        for dir in all_dirs:
            adj = pos.add(dir)
            if not map_info.in_bounds(adj):
                continue
            builder_id = rc.get_tile_builder_bot_id(adj)
            if builder_id and ((builder_id & comms._ID_MASK) == launch_id):
                builder_pos = adj
                break

        if not builder_pos:
            continue

        best = best_launch_tile(target, builder_pos, nearby_tiles, visible_run_id)
        if best:
            rc.launch(builder_pos, best)
    
    nearby_units = rc.get_nearby_units(dist_sq=2)
    my_team = rc.get_team()
    action_radius_sq = rc.get_vision_radius_sq()

    # --- Find Targets ---
    primary_targets = []
    secondary_targets = []

    for unit_id in nearby_units:
        try:
            if rc.get_team(unit_id) != my_team and rc.get_entity_type(unit_id) == EntityType.BUILDER_BOT:
                bot_pos = rc.get_position(unit_id)
                building_id = rc.get_tile_building_id(bot_pos)
                
                # Primary Target: opponent bot on our conveyor/bridge
                if (
                    building_id is not None
                    and rc.get_team(building_id) == my_team
                    and map_info.is_conveyor(rc.get_entity_type(building_id))
                ):
                    primary_targets.append(unit_id)
                else:
                    secondary_targets.append(unit_id)
        except GameError:
            # Unit might have died or moved since get_nearby_units was called
            continue

    target_bot_id = None
    if primary_targets:
        target_bot_id = primary_targets[0]
    elif secondary_targets:
        target_bot_id = secondary_targets[0]

    if not target_bot_id:
        return

    # --- Find Best Launch Destination ---
    all_roads = []
    all_conveyances = []
    allied_launchers = []
    enemy_launchers = []

    for building_id in rc.get_nearby_buildings():
        try:
            b_type = rc.get_entity_type(building_id)
            b_pos = rc.get_position(building_id)
            if b_type == EntityType.ROAD:
                all_roads.append(b_pos)
            elif map_info.is_conveyor(b_type):
                all_conveyances.append(b_pos)
            elif b_type == EntityType.LAUNCHER:
                b_team = rc.get_team(building_id)
                if b_team == my_team:
                    allied_launchers.append((building_id, b_pos))
                else:
                    enemy_launchers.append((building_id, b_pos))
                    for dir in all_dirs:
                        around = b_pos.add(dir)
                        if map_info.in_bounds(around):
                            all_conveyances.append(around)
        except GameError:
            continue

    # --- Priority launch destinations (launcher-based) ---
    priority_destinations = []

    best_score = -float('inf')
    best_tile = None

    # --- Case 1: Around higher-ID allied launchers with no nearby bots ---
    for lid, lpos in allied_launchers:
        if lid <= rc.get_id():
            continue

        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                tile = Position(lpos.x + dx, lpos.y + dy)
                if tile.distance_squared(rc.get_position()) > action_radius_sq:
                    continue
                if not map_info.is_on_map(tile):
                    continue

                # Must be empty
                if rc.get_tile_builder_bot_id(tile) is not None:
                    continue
                if not rc.is_tile_passable(tile):
                    continue
                
                # No nearby bots in vision
                has_bot = False
                for unit_id in nearby_units:
                    if rc.get_entity_type(unit_id) == EntityType.BUILDER_BOT and rc.get_position(unit_id).distance_squared(lpos) <= 8:
                        has_bot = True
                        break
                if has_bot:
                    continue

                # Compute score for tiebreak
                score = tile.distance_squared(rc.get_position()) - lid
                if score > best_score:
                    best_score = score
                    best_tile = tile

    if best_tile is not None:
        priority_destinations.append(best_tile)

    # # --- Case 2: Around lower-ID enemy launchers with spacing constraint ---
    # for lid, lpos in enemy_launchers:
    #     if lid >= rc.get_id():
    #         continue

    #     for dx in (-1, 0, 1):
    #         for dy in (-1, 0, 1):
    #             if dx == 0 and dy == 0:
    #                 continue
    #             tile = Position(lpos.x + dx, lpos.y + dy)
                
    #             if tile.distance_squared(rc.get_position()) > action_radius_sq:
    #                 continue
    #             if not map_info.is_on_map(tile):
    #                 continue

    #             # Must be empty
    #             if not rc.is_tile_passable(tile):
    #                 continue

    #             # Check distance from nearest allied conveyance or higher-ID allied launcher
    #             too_close = False

    #             for conveyance_pos in all_conveyances:
    #                 if tile.distance_squared(conveyance_pos) <= 8:
    #                     too_close = True
    #                     break

    #             if not too_close:
    #                 for aid, apos in allied_launchers:
    #                     if aid > rc.get_id() and tile.distance_squared(apos) < 8:
    #                         too_close = True
    #                         break

    #             if not too_close:
    #                 priority_destinations.append(tile)

    valid_destinations = []
    for road_pos in all_roads:
        try:
            # Must be empty of builder bots
            if rc.get_tile_builder_bot_id(road_pos) is None:
                 # Verify it's still a road, as map_info could be stale
                building_id = rc.get_tile_building_id(road_pos)
                if building_id and rc.get_entity_type(building_id) == EntityType.ROAD:
                    valid_destinations.append(road_pos)
        except GameError:
            continue

    if not valid_destinations or not all_conveyances:
        return
    max_min_dist_sq = 1000
    # --- Use priority destinations if available ---
    if priority_destinations:
        best_destination = priority_destinations[0]
    else:
        best_destination = None
        max_min_dist_sq = -1

    for dest_pos in valid_destinations:
        min_dist_sq_to_conveyance = sys.maxsize
        for conveyance_pos in all_conveyances:
            dist_sq = abs(dest_pos.x - conveyance_pos.x) + abs(dest_pos.y - conveyance_pos.y)
            if dist_sq < min_dist_sq_to_conveyance:
                min_dist_sq_to_conveyance = dist_sq
        
        if min_dist_sq_to_conveyance > max_min_dist_sq:
            max_min_dist_sq = min_dist_sq_to_conveyance
            best_destination = dest_pos
    
    # --- Launch ---
    if best_destination:
        target_bot_pos = rc.get_position(target_bot_id)
        if rc.can_launch(target_bot_pos, best_destination):
            rc.launch(target_bot_pos, best_destination)
