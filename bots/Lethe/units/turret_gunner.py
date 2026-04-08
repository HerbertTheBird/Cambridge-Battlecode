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
DIRECTIONS = [
    Direction.NORTH, Direction.NORTHEAST, Direction.EAST, Direction.SOUTHEAST,
    Direction.SOUTH, Direction.SOUTHWEST, Direction.WEST, Direction.NORTHWEST,
]
CARDINAL_DIRECTIONS = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]
INF = 999999

def init(c: Controller):
    global rc, my_pos, my_team, last_fired_round, skipped_firing_turns
    rc = c
    my_pos = rc.get_position()
    my_team = rc.get_team()
    last_fired_round = rc.get_current_round()
    skipped_firing_turns = 0
    map_info.init(c)

# --- Ported and adapted from dragonfruit/units/gunner/combat.py ---

def choose_gunner_target() -> Position | None:
    """Pick the gunner's shot by scanning its short forward ray."""
    direction = rc.get_direction()
    attackable_tiles = set(rc.get_attackable_tiles())
    ray_tiles = []
    tile = my_pos.add(direction)

    for _ in range(3):
        if tile not in attackable_tiles:
            break
        ray_tiles.append(tile)
        tile = tile.add(direction)

    first_enemy_idx = None
    for i, tile in enumerate(ray_tiles):
        bot_id = rc.get_tile_builder_bot_id(tile)
        if bot_id is not None:
            if rc.get_team(bot_id) != my_team:
                first_enemy_idx = i
                break
            return None

        building_id = rc.get_tile_building_id(tile)
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

        if not rc.is_tile_empty(tile):
            return None

    if first_enemy_idx is None:
        return None

    for i in range(first_enemy_idx):
        tile = ray_tiles[i]
        bot_id = rc.get_tile_builder_bot_id(tile)
        if bot_id is not None:
            return None

        building_id = rc.get_tile_building_id(tile)
        if building_id is None:
            continue

        etype = rc.get_entity_type(building_id)
        if etype == EntityType.MARKER:
            continue
        if rc.get_team(building_id) != my_team or etype == EntityType.ROAD:
            return tile
        return None

    return ray_tiles[first_enemy_idx]

def get_gunner_threat_tiles(tpos: Position) -> set[Position]:
    threat_tiles = set()
    width = map_info._width
    height = map_info._height

    for d in DIRECTIONS:
        dx, dy = d.delta()
        max_range = 3 if d in CARDINAL_DIRECTIONS else 2

        x, y = tpos.x, tpos.y
        for _ in range(max_range):
            x += dx
            y += dy
            cur = Position(x, y)

            if not map_info.in_bounds(cur):
                break

            if map_info.ground_at(x, y):
                break

            threat_tiles.add(cur)

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
    width = map_info._width
    height = map_info._height

    for p in rc.get_nearby_tiles():
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

def choose_rotate_dir(enemies) -> Direction | None:
    current_dir = rc.get_direction()
    rotate_dir = None
    rotate_dist = INF

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

    elif rc.get_global_resources()[0] <= 60:
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
