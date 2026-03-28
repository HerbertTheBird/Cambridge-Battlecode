
from cambc import Controller, EntityType, Position, GameError, Direction
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

def run():
    map_info.update()
    
    messages = comms.decode_launch()
    rush_messages = comms.decode_centralized_launch()
    
    nearby_units = rc.get_nearby_units(dist_sq=2)
    for unit in nearby_units:
        if rc.get_team(unit) == rc.get_team() and rc.get_entity_type(unit) == EntityType.BUILDER_BOT and unit <= 4:
            rush_messages.append((unit, rc.get_position(unit)))
    pos = rc.get_position()
    for target, id, p in messages:
        r = int(math.sqrt(rc.get_vision_radius_sq()))
        try:
            bot_pos = rc.get_position(id)
        except GameError:
            bot_pos = None
        if target and bot_pos and bot_pos.distance_squared(pos) <= 2 and rc.can_launch(bot_pos, target):
            rc.launch(bot_pos, target)
            if rc.can_place_marker(p):
                rc.place_marker(p, 0)
    for id, p in rush_messages:
        try:
            bot_pos = rc.get_position(id)
        except GameError:
            bot_pos = None
        if bot_pos and bot_pos.distance_squared(pos) <= 2:
            print(f"Attempting launch bot {id} at {bot_pos}")
            # candidate positions
            candidates = []

            # scan vision for high-priority targets
            for target_tile in rc.get_nearby_tiles(rc.get_vision_radius_sq()):
                # Empty tile next to enemy harvester on titanium
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        adj = Position(target_tile.x + dx, target_tile.y + dy)
                        if not map_info.is_on_map(adj):
                            continue
                        if adj.distance_squared(pos) > rc.get_vision_radius_sq():
                            continue
                        building_id = rc.get_tile_building_id(adj)
                        if building_id is None:
                            continue
                        if rc.get_team(building_id) != rc.get_team() and rc.get_entity_type(building_id) == EntityType.HARVESTER:
                            if map_info.ground[adj.x][adj.y] == map_info._ENV_ORE_TI:
                                if map_info.is_tile_empty(target_tile):
                                    candidates.append((0, target_tile))  # highest priority

                # Empty tile that an enemy conveyor/bridge leads into
                if rc.is_tile_empty(target_tile):
                    for dx in (-1,0,1):
                        for dy2 in (-1,0,1):
                            if dx == 0 and dy2 == 0:
                                continue
                            adj = Position(target_tile.x + dx, target_tile.y + dy2)
                            if adj.distance_squared(pos) > rc.get_vision_radius_sq():
                                continue
                            building_id = rc.get_tile_building_id(adj)
                            if building_id is None:
                                continue
                            if rc.get_team(building_id) != rc.get_team() and rc.get_entity_type(building_id) in (
                                EntityType.CONVEYOR,
                                EntityType.ARMOURED_CONVEYOR,
                                EntityType.BRIDGE,
                            ):
                                if rc.is_tile_passable(target_tile):
                                    candidates.append((1, target_tile))

                # tile 2 chebyshev units from above candidates
                # this will be added below after we sort priority

                # Enemy bridge/conveyor that doesn't eventually lead to a friendly turret
                building_id = rc.get_tile_building_id(target_tile)
                if building_id is not None:
                    if rc.get_team(building_id) != rc.get_team() and rc.get_entity_type(building_id) in (
                        EntityType.CONVEYOR,
                        EntityType.ARMOURED_CONVEYOR,
                        EntityType.BRIDGE,
                    ):
                        if not map_info.leads_to_friendly_turret(building_id):  # custom helper
                            if rc.is_tile_passable(target_tile):
                                candidates.append((3, target_tile))

            # === Sort by priority (lowest number = highest priority) ===
            candidates.sort(key=lambda x: x[0])

            # === Attempt launch at best candidate ===
            for _, target_pos in candidates:
                print(f"Checking candidate {target_pos}")
                if rc.can_launch(bot_pos, target_pos):
                    rc.launch(bot_pos, target_pos)
                    break
        
    if rc.get_action_cooldown() > 0:
        return

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
