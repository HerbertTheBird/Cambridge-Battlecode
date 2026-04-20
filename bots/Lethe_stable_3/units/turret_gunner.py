from cambc import Controller, Direction, EntityType, Position, Team, Environment, GameConstants
import map_info
from log import log

rc: Controller = None
my_pos: Position = None
my_team: Team = None
last_fired_round: int = 0
skipped_firing_turns: int = 0

# --- Ported from dragonfruit/globals.py ---
TURRET_TYPES = {EntityType.GUNNER, EntityType.SENTINEL, EntityType.BREACH}

INF = 999999

def init(c: Controller):
    global rc, my_pos, my_team, last_fired_round, skipped_firing_turns
    rc = c
    my_pos = rc.get_position()
    last_fired_round = rc.get_current_round()
    skipped_firing_turns = 0
    my_team = map_info._my_team

# --- Ported and adapted from dragonfruit/units/gunner/combat.py ---

def _get_invalid_sabotage_locations() -> set[Position]:
    invalid_sabotage_locations = set()
    my_pos_local = rc.get_position()
    for p in map_info.iter_mask((map_info._bm_et[map_info._IDX_GUNNER] | map_info._bm_et[map_info._IDX_SENTINEL]) & map_info._bm_team[map_info._my_team_idx]):
        front_positions = []

        if p.distance_squared(my_pos_local) <= 100:
            for conv_pos in map_info.iter_mask(map_info._conv_reverse[p.x + p.y * map_info._width]):
                if rc.is_in_vision(conv_pos) and rc.get_tile_builder_bot_id(conv_pos) is not None:
                    continue
                if conv_pos not in invalid_sabotage_locations:
                    front_positions.append(conv_pos)
                    invalid_sabotage_locations.add(conv_pos)

            for _ in range(4):
                new_front = []
                for front_p in front_positions:
                    for conv_pos in map_info.iter_mask(map_info._conv_reverse[front_p.x + front_p.y * map_info._width]):
                        if rc.is_in_vision(conv_pos) and rc.get_tile_builder_bot_id(conv_pos) is not None:
                            continue
                        if conv_pos not in invalid_sabotage_locations:
                            new_front.append(conv_pos)
                            invalid_sabotage_locations.add(conv_pos)
                front_positions = new_front
    return invalid_sabotage_locations

def choose_gunner_target() -> Position | None:
    """Pick the gunner's shot by scanning its short forward ray."""
    direction = rc.get_direction()
    attackable_tiles = set(rc.get_attackable_tiles()) # Re-added this line
    ray_tiles = []
    tile = map_info.pos_add(my_pos, direction)

    invalid_sabotage_locations = _get_invalid_sabotage_locations()

    for _ in range(3):
        if not map_info.in_bounds(tile):
            break
        if tile not in attackable_tiles: # Re-added this check
            break
        ray_tiles.append(tile)
        tile = map_info.pos_add(tile, direction)

    first_enemy_idx = None
    for i, current_ray_tile in enumerate(ray_tiles):
        if current_ray_tile in invalid_sabotage_locations:
            continue

        bot_id = rc.get_tile_builder_bot_id(current_ray_tile)
        if bot_id is not None:
            if rc.get_team(bot_id) != my_team:
                first_enemy_idx = i
                break
            return None

        building_id = rc.get_tile_building_id(current_ray_tile)
        if building_id is not None:
            etype = rc.get_entity_type(building_id)
            if etype == EntityType.MARKER:
                continue
            if rc.get_team(building_id) != my_team:
                first_enemy_idx = i
                break
            if etype == EntityType.ROAD:
                continue
            return None

        if not rc.is_tile_empty(current_ray_tile):
            return None

    if first_enemy_idx is None:
        return None

    for i in range(first_enemy_idx):
        tile_to_check = ray_tiles[i]
        if tile_to_check in invalid_sabotage_locations:
            continue

        bot_id = rc.get_tile_builder_bot_id(tile_to_check)
        if bot_id is not None:
            return None

        building_id = rc.get_tile_building_id(tile_to_check)
        if building_id is None:
            continue

        etype = rc.get_entity_type(building_id)
        if etype == EntityType.MARKER:
            continue
        if rc.get_team(building_id) != my_team or etype == EntityType.ROAD:
            return tile_to_check
        return None

    return ray_tiles[first_enemy_idx]

def get_gunner_threat_tiles(tpos: Position) -> set[Position]:
    threat_tiles = set()
    width = map_info._width
    height = map_info._height

    for d in map_info._DIRECTIONS:
        dx, dy = map_info._DIRECTION_DELTAS[d]
        max_range = 3 if d in map_info._CARDINAL else 2

        x, y = tpos.x, tpos.y
        for _ in range(max_range):
            x += dx
            y += dy
            cur = Position(x, y)

            if not map_info.in_bounds(cur):
                break

            if map_info.ground_at(x, y) == Environment.WALL:
                break

            threat_tiles.add(cur)
            
            if not rc.is_in_vision(cur):
                break

            bbid = rc.get_tile_builder_bot_id(cur)
            if bbid is not None:
                if rc.get_team(bbid) == my_team:
                    break
                continue

            bid = rc.get_tile_building_id(cur)
            if bid is not None:
                etype = rc.get_entity_type(bid)
                team = rc.get_team(bid)

                if etype == EntityType.MARKER or etype == EntityType.ROAD:
                    continue

                if team == my_team:
                    break
                continue

    return threat_tiles

def get_enemy_units():
    global my_team
    enemy_units = []

    for p in map_info._nearby_tiles:
        # --- builder bots ---
        bbid = rc.get_tile_builder_bot_id(p)
        if bbid is not None:
            if rc.get_team(bbid) != my_team:
                etype = rc.get_entity_type(bbid)
                enemy_units.append((bbid, etype, p, rc.get_team(bbid)))
            continue

        # --- buildings ---
        bid = rc.get_tile_building_id(p)
        if bid is None:
            continue

        if rc.get_team(bid) == my_team:
            continue

        etype = rc.get_entity_type(bid)

        if etype in {
            EntityType.LAUNCHER,
            EntityType.SENTINEL,
            EntityType.BREACH,
            EntityType.GUNNER,
            EntityType.CONVEYOR,
            EntityType.BRIDGE,
            EntityType.ARMOURED_CONVEYOR,
            EntityType.CORE,
            EntityType.BUILDER_BOT,
            EntityType.BARRIER,
        }:
            enemy_units.append((bid, etype, p, rc.get_team(bid)))

    return enemy_units

def _get_loaders(pos):
    """Return list of direction indices (0-7) from pos toward buildings that feed it."""
    w = map_info._width
    h = map_info._height
    px, py = pos.x, pos.y
    pos_n = px + py * w
    loaders = []

    harvesters = map_info._bm_et[map_info._IDX_HARVESTER]
    conveyors = (map_info._bm_et[map_info._IDX_CONVEYOR]
                 | map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR])

    # Cardinal-adjacent harvesters
    for di, (dx, dy) in zip([0, 2, 4, 6], [(0, -1), (1, 0), (0, 1), (-1, 0)]):
        nx, ny = px + dx, py + dy
        if 0 <= nx < w and 0 <= ny < h:
            if harvesters & (1 << (nx + ny * w)):
                loaders.append(di)

    # Any neighbor conveyor whose output targets this tile
    for di in range(8):
        dx, dy = map_info._DIR_VECS[di]
        nx, ny = px + dx, py + dy
        if 0 <= nx < w and 0 <= ny < h:
            nn = nx + ny * w
            if (conveyors & (1 << nn)) and map_info._building_conv_target[nn] == pos_n:
                if di not in loaders:
                    loaders.append(di)

    return loaders

def choose_rotate_dir(enemies) -> Direction | None:
    current_dir = rc.get_direction()
    rotate_dir = None
    rotate_dist = INF
    blocked_dirs = _get_loaders(my_pos)
    can_face_any_dir = len(blocked_dirs) >= 2

    for (eid, etype, tpos, team) in enemies:
        if etype not in TURRET_TYPES:
            continue

        threat_tiles = get_gunner_threat_tiles(tpos)

        if my_pos not in threat_tiles:
            continue

        dist = my_pos.distance_squared(tpos)
        desired_dir = my_pos.direction_to(tpos)

        if desired_dir == current_dir:
            continue
        if not can_face_any_dir and desired_dir in blocked_dirs:
            continue

        if dist < rotate_dist:
            rotate_dist = dist
            rotate_dir = desired_dir

    return rotate_dir

# --- Ported and adapted from dragonfruit/units/gunner/run.py ---
def run():
    global last_fired_round, skipped_firing_turns
    map_info.update()
    enemies = get_enemy_units()
    target = choose_gunner_target()
    log(f"gunner target: {target}")

    if target is not None and rc.can_fire(target):
        rc.fire(target)
        log(f"gunner fired at {target}")
        last_fired_round = rc.get_current_round()
        skipped_firing_turns = 0

    elif rc.get_global_resources()[0] >= 60:
        rotate_dir = choose_rotate_dir(enemies)

        if rotate_dir is not None and rc.can_rotate(rotate_dir):
            rc.rotate(rotate_dir)
            skipped_firing_turns = 0
            log(f"gunner rotated toward adjacent enemy turret: {rotate_dir}")

    if rc.get_action_cooldown() == 0:
        skipped_firing_turns += 1

    if skipped_firing_turns >= 8:
        if len(enemies) > 0:
            last_fired_round = rc.get_current_round()
            skipped_firing_turns -= 1
        if (rc.get_scale_percent() > 500 or skipped_firing_turns >= 32):
            rc.self_destruct()
