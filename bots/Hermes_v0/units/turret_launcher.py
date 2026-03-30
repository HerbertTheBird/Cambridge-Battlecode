
from cambc import Controller, EntityType, Position, GameError, Direction, Environment
import map_info
import sys
import comms
import math
rc: Controller | None = None
all_dirs = list(Direction)

def init(c: Controller):
    global rc
    rc = c
    comms.init(c)
    map_info.init(c)

from collections import deque
import heapq

def launch_distance_map(target):
    my_team = rc.get_team()

    def key(pos):
        return (pos.x, pos.y)

    def chebyshev(a, b):
        return max(abs(a.x - b.x), abs(a.y - b.y))

    def is_passable(pos):
        if not map_info.in_bounds(pos):
            return False
        if not rc.is_in_vision(pos):
            return False
        if rc.get_tile_env(pos) == Environment.WALL:
            return False

        building_id = rc.get_tile_building_id(pos)
        if building_id is not None:
            team = rc.get_team(building_id)
            entity_type = rc.get_entity_type(building_id)

            # Enemy building
            if team != my_team:
                if entity_type != EntityType.ROAD and not map_info.is_conveyor(entity_type):
                    return False

            # Friendly building
            else:
                if (
                    entity_type != EntityType.ROAD
                    and not map_info.is_conveyor(entity_type)
                ):
                    return False

        return True

    directions = [
        (1, 0), (-1, 0), (0, 1), (0, -1),
        (1, 1), (1, -1), (-1, 1), (-1, -1),
    ]

    dist = {}
    pq = []

    if rc.is_in_vision(target):
        if not is_passable(target):
            return dist
        k = key(target)
        dist[k] = 0
        heapq.heappush(pq, (0, target.x, target.y, target))
    else:
        # Seed with visible frontier tiles that step toward the target leaves vision
        for pos in rc.get_nearby_tiles():
            if not map_info.in_bounds(pos):
                continue
            if not rc.is_in_vision(pos):
                continue
            if not is_passable(pos):
                continue

            step = pos.add(pos.direction_to(target))
            if rc.is_in_vision(step):
                continue

            d0 = chebyshev(pos, target)
            k = key(pos)

            if k not in dist or d0 < dist[k]:
                dist[k] = d0
                heapq.heappush(pq, (d0, pos.x, pos.y, pos))

    while pq:
        cur_d, _, _, cur = heapq.heappop(pq)
        cur_k = key(cur)

        if cur_d != dist[cur_k]:
            continue

        for dx, dy in directions:
            nxt = Position(cur.x + dx, cur.y + dy)
            nxt_k = key(nxt)

            if not is_passable(nxt):
                continue

            nd = cur_d + 1
            if nxt_k not in dist or nd < dist[nxt_k]:
                dist[nxt_k] = nd
                heapq.heappush(pq, (nd, nxt.x, nxt.y, nxt))

    return dist
def run():
    map_info.update()

    messages = comms.decode_launch()
    pos = rc.get_position()

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
            if builder_id and (builder_id & comms._ID_MASK):
                builder_pos = adj
                break

        if not builder_pos:
            continue

        dist = launch_distance_map(target)

        best = None
        best_d = None

        for tile in rc.get_nearby_tiles():
            if not rc.is_tile_passable(tile):
                continue
            if not rc.can_launch(builder_pos, tile):
                continue

            k = (tile.x, tile.y)
            if k not in dist:
                continue

            d = dist[k]
            if best is None or d < best_d:
                best = tile
                best_d = d

        if (
            best
            and rc.can_launch(builder_pos, best)
            and best in dist
            and dist[best] < dist.get(builder_pos, 10000)
        ):
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
                building_on_tile = map_info.building[bot_pos.x][bot_pos.y]
                
                # Primary Target: opponent bot on our conveyor/bridge
                if building_on_tile and building_on_tile.team == my_team and map_info.is_conveyor(building_on_tile.type):
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

    for x in range(map_info.width):
        for y in range(map_info.height):
            b = map_info.building[x][y]
            if b:
                pos = Position(x, y)
                if b.type == EntityType.ROAD:
                    all_roads.append(pos)
                elif map_info.is_conveyor(b.type):
                    all_conveyances.append(pos)
                elif b.type == EntityType.LAUNCHER and b.team != rc.get_team():
                    for dir in all_dirs:
                        all_conveyances.append(pos.add(dir))

    # --- Priority launch destinations (launcher-based) ---
    priority_destinations = []

    allied_launchers = []
    enemy_launchers = []

    for x in range(map_info.width):
        for y in range(map_info.height):
            b = map_info.building[x][y]
            if not b:
                continue
            temppos = Position(x, y)
            if b.type == EntityType.LAUNCHER:
                if b.team == my_team:
                    allied_launchers.append((b.id, temppos))
                else:
                    enemy_launchers.append((b.id, temppos))

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
