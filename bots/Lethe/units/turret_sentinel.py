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
    my_team = map_info._my_team
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


def _get_feeder_positions():
    """Return set of positions that feed this sentinel (don't shoot these)."""
    my_pos = rc.get_position()
    feeders = set()
    for dx, dy in CARDINAL_OFFSETS:
        p = Position(my_pos.x + dx, my_pos.y + dy)
        if not map_info.in_bounds(p):
            continue
        bid = rc.get_tile_building_id(p)
        if not bid:
            continue
        etype = rc.get_entity_type(bid)
        if etype == EntityType.HARVESTER:
            feeders.add(p)
        elif etype in (EntityType.CONVEYOR, EntityType.ARMOURED_CONVEYOR):
            d = rc.get_direction(bid)
            ddx, ddy = d.delta()
            if p.x + ddx == my_pos.x and p.y + ddy == my_pos.y:
                feeders.add(p)
    return feeders


def _tile_score(tile, feeders):
    my_team = map_info._my_team
    if tile in feeders:
        return 0
    # Turrets hit builder bot first if present
    builder_id = rc.get_tile_builder_bot_id(tile)
    if builder_id and rc.get_team(builder_id) != my_team:
        return _WEIGHTS.get(EntityType.BUILDER_BOT, 0)
    building_id = rc.get_tile_building_id(tile)
    if building_id and rc.get_team(building_id) != my_team:
        return _WEIGHTS.get(rc.get_entity_type(building_id), 0)
    return 0


def _prune_conveyor_targets(target_positions):
    # Convert list of Position objects to a bitmask
    targets = map_info.positions_to_mask(target_positions)

    # expensive calculations - nonbitmasked, leave at end. calculates conveyors that go into a turret.
    pruned_targets = 0
    invalid_sabotage_locations = set()
    my_pos = rc.get_position()
    for p in map_info.iter_mask((map_info._bm_et[map_info._IDX_GUNNER] | map_info._bm_et[map_info._IDX_SENTINEL]) & map_info._bm_team[map_info._my_team_idx]):
        front_positions = []

        if p.distance_squared(my_pos) <= 100:
            # Iterating over conveyors that feed into p
            for conv_pos in map_info.iter_mask(map_info._conv_reverse[p.x + p.y * map_info._width]):
                # Allow skipping blacklisting if it's in vision and has a builder bot
                if rc.is_in_vision(conv_pos) and rc.get_tile_builder_bot_id(conv_pos) is not None:
                    continue
                if conv_pos not in invalid_sabotage_locations:
                    front_positions.append(conv_pos)
                    invalid_sabotage_locations.add(conv_pos)
                    # rc.draw_indicator_dot(conv, 0, 0, 255)

            # Propagate up conveyor chain
            for _ in range(4):
                new_front = []
                for front_p in front_positions:
                    for conv_pos in map_info.iter_mask(map_info._conv_reverse[front_p.x + front_p.y * map_info._width]):
                        # Allow skipping blacklisting if it's in vision and has a builder bot
                        if rc.is_in_vision(conv_pos) and rc.get_tile_builder_bot_id(conv_pos) is not None:
                            continue
                        if conv_pos not in invalid_sabotage_locations:
                            new_front.append(conv_pos)
                            invalid_sabotage_locations.add(conv_pos)
                            # rc.draw_indicator_dot(conv, 0, 0, 255)
                front_positions = new_front

    # Prune targets that are in invalid_sabotage_locations
    for target in map_info.iter_mask(targets):
        if target not in invalid_sabotage_locations:
            pruned_targets |= (1 << (target.x + target.y * map_info._width))

    # Convert pruned_targets bitmask back to a list of Position objects
    return list(map_info.iter_mask(pruned_targets))


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

    feeders = _get_feeder_positions()
    best_target = None
    best_score = 0

    # Get attackable tiles and then prune them
    attackable_tiles = rc.get_attackable_tiles()
    pruned_attackable_tiles = _prune_conveyor_targets(attackable_tiles)

    for tile in pruned_attackable_tiles:
        if not rc.can_fire(tile):
            continue
        s = _tile_score(tile, feeders)
        if s > best_score:
            best_score = s
            best_target = tile

    if best_target is None:
        if not _should_stay():
            rc.self_destruct()
        return

    rc.fire(best_target)
