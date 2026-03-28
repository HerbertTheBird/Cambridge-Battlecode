from cambc import Controller, Position, Direction, EntityType, Environment, ResourceType

from enum import Enum
import random
import sys

import map_info
from pathing import Pathing
import comms

indicator = []
blocked_ores = {}
defended_ores = set()
cardinal_dirs = [Direction.NORTH, Direction.SOUTH, Direction.EAST, Direction.WEST]
all_dirs = list(Direction)

# Cache deltas directly to bypass `Enum` and `.add()` overhead
ALL_DIRS_DELTAS = [(d, d.delta()) for d in all_dirs]
CARDINAL_DELTAS = [(d, d.delta()) for d in cardinal_dirs]
OUR_BUILDINGS = {EntityType.BARRIER, EntityType.HARVESTER, EntityType.LAUNCHER,
                 EntityType.CONVEYOR, EntityType.BRIDGE, EntityType.SENTINEL}

# explore state
explore_target = None
turns_since_last_explore_target = 0

rc = None
MODE_ACTIONS = None
nav = None
class Mode(Enum):
    RUSH_CORE = (255, 165, 0, "rush opponent core")
    PREPARE_LAUNCHER = (0, 180, 180, "build launcher")

    def __init__(self, r, g, b, desc):
        self.r = r
        self.g = g
        self.b = b
        self.desc = desc

mode = Mode.RUSH_CORE

def init(c: Controller):
    global rc, MODE_ACTIONS
    rc = c
    map_info.init(c)
    comms.init(c)
    nav = Pathing(c)

def run():
    global mode
    run_pre()  # preliminary calculations
    print(f"CHECK STATE: <span style='color: #{mode.r:02x}{mode.g:02x}{mode.b:02x}'>{mode.desc}</span>")
    globals()[f"check_{mode.name.lower()}"]()
    print(f"EXEC. STATE: <span style='color: #{mode.r:02x}{mode.g:02x}{mode.b:02x}'>{mode.desc}</span>")
    globals()[f"run_{mode.name.lower()}"]()
    run_post()  # cleanup

def log(text : str):
    print(f" <span style='color: #{mode.r:02x}{mode.g:02x}{mode.b:02x}'>|</span> {text}")

def check_rush_core():
    global mode

    # find stuff to sabotage
    for pos in rc.get_nearby_tiles():
        building_id = rc.get_tile_building_id(pos)
        if building_id is None:
            continue

        # Check if it's enemy
        if rc.get_team(building_id) == rc.get_team():
            continue

        entity_type = rc.get_entity_type(building_id)

        # Case 1: Enemy harvester on titanium ore
        if entity_type == EntityType.HARVESTER:
            if map_info.ground[pos.x][pos.y] == Environment.ORE_TITANIUM:
                log("Prepare launcher: titanium harvester detected")
                mode = Mode.PREPARE_LAUNCHER
                return

        # Case 2: Enemy conveyor carrying titanium
        elif entity_type in (
            EntityType.CONVEYOR,
            EntityType.ARMOURED_CONVEYOR,
            EntityType.SPLITTER,
            EntityType.BRIDGE,
        ):
            stored = rc.get_stored_resource(building_id)
            if stored and stored == ResourceType.TITANIUM:
                log("Prepare launcher: connected conveyer detected")
                mode = Mode.PREPARE_LAUNCHER
                return

    # route to core
    path = nav.calculate_path(map_info.predicted_enemy_core)
    if path:
        if len(path) > 0:
            log("Path to core found.")
        else:
            log("Opponent core is unreachable")
    else:
        log("A* TLE - assuming safe state, ignoring")
        
def run_rush_core():
    nav.execute_path()

def check_prepare_launcher():
    global mode

    my_pos = rc.get_position()
    # Only need to check very close range (adjacent = dist_sq <= 2)
    for unit_id in rc.get_nearby_units(2):
        # Must be allied
        if rc.get_team(unit_id) != rc.get_team():
            continue

        # Must be a launcher
        if rc.get_entity_type(unit_id) != EntityType.LAUNCHER:
            continue

        if rc.get_position(unit_id).distance_squared(my_pos) <= 2:
            log("Launcher placed, waiting")
            mode = Mode.ATTACK
            return

def run_prepare_launcher():
    my_pos = rc.get_position()

    best_empty = None
    best_empty_dist = float('inf')

    best_restrict = None
    best_restrict_dist = float('inf')

    # check all 8 surrounding tiles
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue

            pos = Position(my_pos.x + dx, my_pos.y + dy)

            if not map_info.is_on_map(pos):
                continue

            dist = pos.distance_squared(map_info.predicted_enemy_core)

            # Priority 1: empty tiles
            if map_info.is_tile_empty(pos):
                if dist < best_empty_dist:
                    best_empty = pos
                    best_empty_dist = dist

            # Priority 2: restrictive, owned tiles
            elif map_info.can_place_at_restrictive(pos):
                if dist < best_restrict_dist:
                    best_restrict = pos
                    best_restrict_dist = dist

    # try placing on best empty tile
    if best_empty is not None:
        if rc.can_build_launcher(best_empty):
            rc.build_launcher(best_empty)
            return

    # otherwise clear restrictive tile and place
    if best_restrict is not None:
        if rc.can_destroy(best_restrict):
            rc.destroy(best_restrict)

        if rc.can_build_launcher(best_restrict):
            rc.build_launcher(best_restrict)
            return

def run_pre():
    map_info.update()

def run_post():
    pass