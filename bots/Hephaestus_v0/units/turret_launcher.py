
from cambc import Controller, EntityType, Position, GameError, Direction
import map_info
import sys
import comms
import units.builder as builder
from pathing import Pathing
rc: Controller
nav: Pathing | None = None
all_dirs = list(Direction)

def init(c: Controller):
    global rc, nav
    rc = c
    nav = Pathing(c)
    comms.init(c)
    map_info.init(c)


def best_launch_tile(target: Position, builder_pos: Position, nearby_tiles):
    if nav is None:
        return None

    starts = set()
    can_launch = rc.can_launch
    for tile in nearby_tiles:
        if can_launch(builder_pos, tile):
            starts.add(tile)

    if len(starts) == 0:
        return None

    path = nav.bfs(starts, target)
    if not path:
        return None

    # bfs returns [best_start, ..., target], so path[0] is the closest launch tile.
    best = path[0]
    if can_launch(builder_pos, best):
        return best

    for pos in path[1:]:
        if can_launch(builder_pos, pos):
            return pos
    return None
def try_launch_enemy_builder(nearby_units, pos: Position) -> bool:
    my_team = rc.get_team()
    action_radius_sq = rc.get_vision_radius_sq()

    primary_targets = []
    secondary_targets = []
    for unit_id in nearby_units:
        try:
            if rc.get_team(unit_id) != my_team and rc.get_entity_type(unit_id) == EntityType.BUILDER_BOT:
                bot_pos = rc.get_position(unit_id)
                if map_info.id_at(bot_pos.x, bot_pos.y) != 0 and map_info.team_at(bot_pos.x, bot_pos.y) == my_team and map_info.is_conveyor(map_info.type_at(bot_pos.x, bot_pos.y)):
                    primary_targets.append(unit_id)
                else:
                    secondary_targets.append(unit_id)
        except GameError:
            continue

    builder.log(f"turret_launcher: enemy builders near primary={str(primary_targets)} secondary={str(secondary_targets)}")
    target_bot_id = primary_targets[0] if primary_targets else (secondary_targets[0] if secondary_targets else None)
    if not target_bot_id:
        builder.log("turret_launcher: no enemy builder target_bot_id")
        return False

    all_roads = []
    all_conveyances = []
    for x in range(map_info._width):
        for y in range(map_info._height):
            if map_info.id_at(x, y) == 0:
                continue
            p = Position(x, y)
            t = map_info.type_at(x, y)
            if t == EntityType.ROAD:
                all_roads.append(p)
            elif map_info.is_conveyor(t):
                all_conveyances.append(p)
            elif t == EntityType.LAUNCHER and map_info.team_at(x, y) != my_team:
                for d in all_dirs:
                    all_conveyances.append(p.add(d))
    conveyance_set = set(all_conveyances)
    print(all_conveyances)
    priority_destinations = []
    allied_launchers = []
    for x in range(map_info._width):
        for y in range(map_info._height):
            if map_info.id_at(x, y) == 0:
                continue
            if map_info.type_at(x, y) == EntityType.LAUNCHER and map_info.team_at(x, y) == my_team:
                allied_launchers.append((map_info.id_at(x, y), Position(x, y)))

    best_score = -float('inf')
    best_tile = None
    for lid, lpos in allied_launchers:
        if lid <= rc.get_id():
            continue
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                tile = Position(lpos.x + dx, lpos.y + dy)
                if tile.distance_squared(pos) > action_radius_sq or not map_info.in_bounds(tile):
                    continue
                if tile in conveyance_set:
                    continue
                if rc.get_tile_builder_bot_id(tile) is not None or not rc.is_tile_passable(tile):
                    continue
                has_bot = False
                for unit_id in nearby_units:
                    if rc.get_entity_type(unit_id) == EntityType.BUILDER_BOT and rc.get_position(unit_id).distance_squared(lpos) <= 8:
                        has_bot = True
                        break
                if has_bot:
                    continue
                score = -lid
                if score > best_score:
                    best_score = score
                    best_tile = tile
    if best_tile is not None:
        priority_destinations.append(best_tile)

    valid_destinations = []
    for road_pos in all_roads:
        if road_pos in conveyance_set:
            continue
        try:
            if rc.get_tile_builder_bot_id(road_pos) is None:
                building_id = rc.get_tile_building_id(road_pos)
                if building_id and rc.get_entity_type(building_id) == EntityType.ROAD:
                    valid_destinations.append(road_pos)
        except GameError:
            continue

    if not valid_destinations or not all_conveyances:
        builder.log(f"turret_launcher: abort enemy launch valid_destinations={len(valid_destinations)} conveyances={len(all_conveyances)}")
        return False

    max_min_dist_sq = 1000
    if priority_destinations:
        best_destination = priority_destinations[0]
    else:
        best_destination = None
        max_min_dist_sq = -1
    for dest_pos in valid_destinations:
        min_dist_sq_to_conveyance = sys.maxsize
        if min_dist_sq_to_conveyance > max_min_dist_sq:
            max_min_dist_sq = min_dist_sq_to_conveyance
            best_destination = dest_pos

    if best_destination:
        target_bot_pos = rc.get_position(target_bot_id)
        if rc.can_launch(target_bot_pos, best_destination):
            builder.log(f"turret_launcher: enemy launch {target_bot_pos} -> {best_destination}")
            rc.launch(target_bot_pos, best_destination)
            builder.log("turret_launcher: enemy launch issued")
            return True
        builder.log(f"turret_launcher: enemy launch blocked can_launch=False {target_bot_pos} -> {best_destination}")
    else:
        builder.log("turret_launcher: no best_destination for enemy launch")
    return False
def run():
    builder.log("turret_launcher: run start")
    map_info.update(False)
    pos = rc.get_position()
    
    rush_messages = comms.decode_centralized_launch()
    
    nearby_units = rc.get_nearby_units(dist_sq=2)
    if try_launch_enemy_builder(nearby_units, pos):
        return
    for unit in nearby_units:
        if rc.get_team(unit) == rc.get_team() and rc.get_entity_type(unit) == EntityType.BUILDER_BOT and (unit <= 4 or rc.get_current_round() > 1050 and unit % 2 == 0):
            rush_messages.append((unit, rc.get_position(unit)))
    
    messages = comms.decode_launch()
    nearby_tiles = rc.get_nearby_tiles()

    for target, launch_id, turn, p in messages:
        if rc.get_action_cooldown() != 0:
            builder.log("turret_launcher: skipping message launches due to cooldown")
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

        best = best_launch_tile(target, builder_pos, nearby_tiles)
        if best:
            builder.log(f"turret_launcher: message launch {builder_pos} -> {best}")
            rc.launch(builder_pos, best)
        else:
            builder.log(f"turret_launcher: no best tile for message launch target={target} builder_pos={builder_pos}")
    for id, p in rush_messages:
        try:
            bot_pos = rc.get_position(id)
        except GameError:
            bot_pos = None
        if bot_pos and bot_pos.distance_squared(pos) <= 2:
            builder.log(f"turret_launcher: rush bot in range id={id} pos={bot_pos}")
            print(f"Attempting launch bot {id} at {bot_pos}")
            if map_info.id_at(bot_pos.x, bot_pos.y) and map_info.is_conveyor(map_info.type_at(bot_pos.x, bot_pos.y)) and map_info.team_at(bot_pos.x, bot_pos.y) != rc.get_team():
                builder.log("turret_launcher: skipping rush launch, bot standing on enemy conveyor")
                continue

            # scan vision for high-priority targets (harvester-first)
            for tile in rc.get_nearby_tiles(rc.get_vision_radius_sq()):
                building_id = rc.get_tile_building_id(tile)
                if building_id is None:
                    continue

                # only care about enemy harvesters
                if rc.get_team(building_id) == rc.get_team():
                    continue
                if rc.get_entity_type(building_id) != EntityType.HARVESTER:
                    continue

                # must be on titanium
                if map_info.ground_at(tile.x, tile.y) != map_info._ENV_ORE_TI:
                    continue

                # now check adjacent tiles for launch positions
                
                print(building_id)
                for direction in map_info._CARDINAL:
                    target_tile = tile.add(direction)
                    if not map_info.in_bounds(target_tile):
                        continue
                    if target_tile.distance_squared(pos) > rc.get_vision_radius_sq():
                        continue
                    if not map_info.is_tile_empty(target_tile):
                        continue
                    
                    
                    for ddx in (-1, 0, 1):
                        for ddy in (-1, 0, 1):
                            adj = Position(target_tile.x + ddx, target_tile.y + ddy)
                            if adj.distance_squared(pos) > rc.get_vision_radius_sq():
                                continue

                            if rc.can_launch(bot_pos, adj):
                                builder.log(f"turret_launcher: rush harvester launch {bot_pos} -> {adj}")
                                rc.launch(bot_pos, adj)
                                print("Harvester launch")
                                return

            for target_tile in rc.get_nearby_tiles(rc.get_vision_radius_sq()):
                
                
                # Empty tile that an enemy conveyor/bridge leads into
                if rc.is_tile_empty(target_tile):
                    for dx in (-1,0,1):
                        for dy2 in (-1,0,1):
                            if dx == 0 and dy2 == 0:
                                continue
                            adj = Position(target_tile.x + dx, target_tile.y + dy2)
                            if not map_info.in_bounds(adj) or adj.distance_squared(pos) > rc.get_vision_radius_sq():
                                continue
                            building_id = rc.get_tile_building_id(adj)
                            if building_id is None:
                                continue
                            if rc.get_team(building_id) != rc.get_team() and rc.get_entity_type(building_id) in (
                                EntityType.CONVEYOR,
                                EntityType.ARMOURED_CONVEYOR,
                                EntityType.BRIDGE,
                            ):
                                if rc.can_launch(bot_pos, target_tile):
                                    builder.log(f"turret_launcher: rush empty-output launch {bot_pos} -> {target_tile}")
                                    rc.launch(bot_pos, target_tile)
                                    return
                
                # --- New: Enemy conveyor next to enemy harvester ---
                building_id = rc.get_tile_building_id(target_tile)
                if building_id is not None and rc.get_team(building_id) != rc.get_team() and rc.get_entity_type(building_id) in (
                    EntityType.CONVEYOR,
                    EntityType.ARMOURED_CONVEYOR,
                ):
                    for dx in (-1, 0, 1):
                        for dy in (-1, 0, 1):
                            if dx == 0 and dy == 0:
                                continue
                            adj = Position(target_tile.x + dx, target_tile.y + dy)
                            if adj.distance_squared(pos) > rc.get_vision_radius_sq():
                                continue
                            if not map_info.in_bounds(adj):
                                continue
                            adj_id = rc.get_tile_building_id(adj)
                            if adj_id is None:
                                continue
                            if rc.get_team(adj_id) != rc.get_team() and rc.get_entity_type(adj_id) == EntityType.HARVESTER:
                                if rc.is_tile_passable(target_tile):
                                    if rc.can_launch(bot_pos, target_tile):
                                        builder.log(f"turret_launcher: rush convoy-near-harvester launch {bot_pos} -> {target_tile}")
                                        rc.launch(bot_pos, target_tile)
                                        return

                # Enemy bridge/conveyor that doesn't eventually lead to a friendly turret
                building_id = rc.get_tile_building_id(target_tile)
                # print("reached conveyer logic")
                if building_id is not None:
                    if rc.get_team(building_id) != rc.get_team() and rc.get_entity_type(building_id) in (
                        EntityType.CONVEYOR,
                        EntityType.ARMOURED_CONVEYOR,
                        EntityType.BRIDGE,
                    ):
                        print("Conveyer target")
                        # if not map_info.leads_to_friendly_turret(building_id):  # custom helper
                        if rc.is_tile_passable(target_tile):
                            if rc.can_launch(bot_pos, target_tile):
                                builder.log(f"turret_launcher: rush conveyor launch {bot_pos} -> {target_tile}")
                                rc.launch(bot_pos, target_tile)
                                return

    if rc.get_action_cooldown() > 0:
        builder.log("turret_launcher: cooldown after message/rush phase, exiting before enemy launch")
        return
