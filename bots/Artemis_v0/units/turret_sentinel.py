from cambc import Controller, Position, EntityType
import math
import map_info
rc = None
def init(c: Controller):
    global rc
    rc = c
 
def priority(tile: Position):
    id = rc.get_tile_builder_bot_id(tile)
    enemy_builder = False
    if rc.get_team(id) != rc.get_team():
        enemy_builder = True
    id = rc.get_tile_building_id(tile)
    building_type = None
    my_building = id is not None and rc.get_team(id) == rc.get_team()
    if id is not None and rc.get_team(id) != rc.get_team():
        building_type = rc.get_entity_type(id)
    
    if enemy_builder and building_type is not None:
        return 0
    if map_info.is_conveyor(building_type):
        return 1
    if map_info.is_turret(building_type):
        return 2
    if enemy_builder and not my_building:
        return 3
    if building_type == EntityType.CORE:
        return 4
    if building_type == EntityType.HARVESTER and rc.get_position().distance_squared(tile) > 1:
        return 5
    if building_type == EntityType.LAUNCHER:
        return 6
    if building_type == EntityType.BARRIER:
        return 7
    if building_type == EntityType.ROAD:
        return 8
    return 9
        
        
    
    
def run():
    if rc.get_action_cooldown() > 0:
        return
    if rc.get_ammo_amount() < 5:
        return
    vision_r = int(math.sqrt(rc.get_vision_radius_sq()))
    pos = rc.get_position()
    best = None
    best_priority = float('inf')
    for x in range(pos.x-vision_r, pos.x+vision_r+1):
        for y in range(pos.y-vision_r, pos.y+vision_r+1):
            if rc.can_fire(Position(x, y)):
                if priority(Position(x, y)) < priority(best):
                    best = Position(x, y)
    if best is not None:
        rc.fire(best)