
from cambc import Controller, EntityType, Position, GameError
import map_info
import sys
import comms
import math
rc: Controller | None = None

def init(c: Controller):
    global rc
    rc = c
    comms.init(c)
    map_info.init(c)

def run():
    messages = comms.decode_launch()
    for target, id, p in messages:
        r = int(math.sqrt(rc.get_vision_radius_sq()))
        pos = rc.get_position()
        try:
            bot_pos = rc.get_position(id)
        except GameError:
            bot_pos = None
        if target and bot_pos and bot_pos.distance_squared(pos) <= 2 and rc.can_launch(bot_pos, target):
            rc.launch(bot_pos, target)
            if rc.can_place_marker(p):
                rc.place_marker(p, 0)
                
        
    map_info.update()
    if rc.get_action_cooldown() > 0:
        return

    my_team = rc.get_team()
    action_radius_sq = rc.get_vision_radius_sq()

    # --- Find Targets ---
    primary_targets = []
    secondary_targets = []

    try:
        nearby_units = rc.get_nearby_units(dist_sq=2)
    except GameError:
        nearby_units = []

    for unit_id in nearby_units:
        try:
            if rc.get_team(unit_id) != my_team and rc.get_entity_type(unit_id) == EntityType.BUILDER_BOT:
                bot_pos = rc.get_position(unit_id)
                building_on_tile = map_info.building.get(bot_pos)
                
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

    for pos, building in map_info.building.items():
        if building:
            if building.type == EntityType.ROAD:
                all_roads.append(pos)
            elif map_info.is_conveyor(building.type) or building.type == EntityType.BRIDGE:
                all_conveyances.append(pos)

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
            dist_sq = dest_pos.distance_squared(conveyance_pos)
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
