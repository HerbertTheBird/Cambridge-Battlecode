from cambc import Controller, Position, EntityType, Direction
import map_info

rc = None
_no_ammo_turns = 0

DIRS = [
    Direction.NORTH, Direction.NORTHEAST, Direction.EAST, Direction.SOUTHEAST,
    Direction.SOUTH, Direction.SOUTHWEST, Direction.WEST, Direction.NORTHWEST,
]

_WEIGHTS = {
    EntityType.CORE: 100,
    EntityType.BREACH: 65,
    EntityType.SENTINEL: 30,
    EntityType.LAUNCHER: 20,
    EntityType.HARVESTER: 20,
    EntityType.BUILDER_BOT: 15,
    EntityType.GUNNER: 10,
    EntityType.FOUNDRY: 50,
    EntityType.BRIDGE: 5,
    EntityType.ARMOURED_CONVEYOR: 5,
    EntityType.BARRIER: 5,
    EntityType.SPLITTER: 2,
    EntityType.CONVEYOR: 1,
    EntityType.ROAD: 0,
    EntityType.MARKER: 0,
}


def init(c: Controller):
    global rc
    rc = c
    map_info.init(c)


def _adj_harvester():
    my_pos = rc.get_position()
    for dx, dy in [(0, -1), (1, 0), (0, 1), (-1, 0)]:
        p = Position(my_pos.x + dx, my_pos.y + dy)
        if map_info.in_bounds(p):
            bid = rc.get_tile_building_id(p)
            if bid and rc.get_entity_type(bid) == EntityType.HARVESTER:
                return True
    return False


def _tile_score(tile):
    my_team = rc.get_team()
    # Turrets hit builder bot first if present
    builder_id = rc.get_tile_builder_bot_id(tile)
    if builder_id and rc.get_team(builder_id) != my_team:
        return _WEIGHTS.get(EntityType.BUILDER_BOT, 0)
    building_id = rc.get_tile_building_id(tile)
    if building_id and rc.get_team(building_id) != my_team:
        return _WEIGHTS.get(rc.get_entity_type(building_id), 0)
    return 0


def _dir_score(direction):
    """Score a direction by the best single target we could hit."""
    my_pos = rc.get_position()
    best = 0
    for tile in rc.get_attackable_tiles_from(my_pos, direction, EntityType.GUNNER):
        if rc.can_fire_from(my_pos, direction, EntityType.GUNNER, tile):
            s = _tile_score(tile)
            if s > best:
                best = s
    return best


def run():
    global _no_ammo_turns
    map_info.update()

    if rc.get_ammo_amount() <= 0:
        _no_ammo_turns += 1
        if _no_ammo_turns >= 10 and not _adj_harvester():
            rc.self_destruct()
            return
    else:
        _no_ammo_turns = 0

    if rc.get_action_cooldown() > 0:
        return

    my_pos = rc.get_position()

    # Try to fire in current direction
    if rc.get_ammo_amount() > 0:
        best_target = None
        best_score = 0
        for tile in rc.get_attackable_tiles():
            if not rc.can_fire(tile):
                continue
            s = _tile_score(tile)
            if s > best_score:
                best_score = s
                best_target = tile
        if best_target:
            rc.fire(best_target)
            return

    # Evaluate all 8 directions for rotation
    if rc.get_global_resources()[0] > rc.get_harvester_cost()[0]:
        current_dir = rc.get_direction()
        best_dir = None
        best_dir_score = 0
        for d in DIRS:
            if d == current_dir:
                continue
            s = _dir_score(d)
            if s > best_dir_score:
                best_dir_score = s
                best_dir = d
        if best_dir and rc.can_rotate(best_dir):
            rc.rotate(best_dir)
            return

    # No targets in any direction
    if not _adj_harvester():
        rc.self_destruct()
