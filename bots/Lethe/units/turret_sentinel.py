from cambc import Controller, Position, EntityType, Direction
import map_info

rc = None
_no_ammo_turns = 0

CARDINAL_OFFSETS = [(0, 1), (0, -1), (-1, 0), (1, 0)]

_WEIGHTS = {
    EntityType.CORE: 35,
    EntityType.BREACH: 60,
    EntityType.SENTINEL: 50,
    EntityType.LAUNCHER: 10,
    EntityType.HARVESTER: 35,
    EntityType.BUILDER_BOT: 15,
    EntityType.GUNNER: 40,
    EntityType.FOUNDRY: 55,
    EntityType.BRIDGE: 4,
    EntityType.ARMOURED_CONVEYOR: 4,
    EntityType.BARRIER: 4,
    EntityType.SPLITTER: 2,
    EntityType.CONVEYOR: 1,
    EntityType.ROAD: 0,
    EntityType.MARKER: 0,
}


def init(c: Controller):
    global rc
    rc = c
    map_info.init(c)


def _should_stay():
    my_pos = rc.get_position()
    my_team = rc.get_team()
    for dx, dy in CARDINAL_OFFSETS:
        p = Position(my_pos.x + dx, my_pos.y + dy)
        if map_info.in_bounds(p):
            bid = rc.get_tile_building_id(p)
            if bid and rc.get_entity_type(bid) == EntityType.HARVESTER:
                return True
            bot_id = rc.get_tile_builder_bot_id(p)
            if bot_id and rc.get_team(bot_id) != my_team:
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


def run():
    global _no_ammo_turns

    if rc.get_ammo_amount() < 10:
        _no_ammo_turns += 1
        if _no_ammo_turns >= 10 and not _should_stay():
            rc.self_destruct()
            return
    else:
        _no_ammo_turns = 0

    if rc.get_action_cooldown() > 0:
        return
    if rc.get_ammo_amount() < 5:
        return

    best_target = None
    best_score = 0

    for tile in rc.get_attackable_tiles():
        if not rc.can_fire(tile):
            continue
        s = _tile_score(tile)
        if s > best_score:
            best_score = s
            best_target = tile

    if best_target is None:
        if not _should_stay():
            rc.self_destruct()
        return

    rc.fire(best_target)
