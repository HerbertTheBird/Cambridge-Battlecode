from cambc import Controller, Position, Team

import comms
import map_info
from log import log

rc: Controller = None
my_pos: Position = None
my_team: Team = None
ORDER_MAX_AGE = 5
_launch_orders: list[tuple[int, Position, Position, int, int]] = []
_seen_order_marker_ids: set[int] = set()

def init(c: Controller):
    global rc, my_pos, my_team, _launch_orders, _seen_order_marker_ids
    rc = c
    my_pos = rc.get_position()
    my_team = map_info._my_team
    _launch_orders = []
    _seen_order_marker_ids = set()

def _prune_orders(current_round: int) -> None:
    global _launch_orders
    _launch_orders = [
        order
        for order in _launch_orders
        if current_round - order[4] < ORDER_MAX_AGE
    ]

def _enqueue_new_orders(current_round: int) -> None:
    for v, sender_pos, marker_pos, marker_id, estimated_turn in comms.get_new_messages():
        if comms.decode_type(v) != 0:
            continue
        if marker_id in _seen_order_marker_ids:
            continue
        _seen_order_marker_ids.add(marker_id)
        _launch_orders.append((v, sender_pos, marker_pos, marker_id, estimated_turn))

def _drop_order(marker_id: int) -> None:
    global _launch_orders
    _launch_orders = [order for order in _launch_orders if order[3] != marker_id]

def _adjacent_builders(team: Team | None = None) -> list[tuple[int, Position]]:
    result = []
    my_pos = rc.get_position()
    for pos in rc.get_nearby_tiles(2):
        if max(abs(pos.x - my_pos.x), abs(pos.y - my_pos.y)) > 1 or pos == my_pos:
            continue
        bot_id = rc.get_tile_builder_bot_id(pos)
        if bot_id is None:
            continue
        if team is not None and rc.get_team(bot_id) != team:
            continue
        result.append((bot_id, pos))
    return result

def _protected_tiles() -> list[Position]:
    my_team_mask = map_info._bm_team[map_info._my_team_idx]
    protected = map_info._bm_my_core_area
    protected |= (
        map_info._bm_et[map_info._IDX_CONVEYOR]
        | map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR]
        | map_info._bm_et[map_info._IDX_SPLITTER]
        | map_info._bm_et[map_info._IDX_BRIDGE]
    ) & my_team_mask
    return list(map_info.iter_mask(protected))

def _score_landing(tile: Position, protected_tiles: list[Position], my_pos: Position) -> tuple[int, int, int]:
    if not protected_tiles:
        return (tile.distance_squared(my_pos), tile.distance_squared(my_pos), tile.distance_squared(my_pos))

    min_dist = min(tile.distance_squared(anchor) for anchor in protected_tiles)
    total_dist = sum(tile.distance_squared(anchor) for anchor in protected_tiles)
    return (min_dist, total_dist, tile.distance_squared(my_pos))

def _try_execute_launch_order() -> bool:
    current_round = rc.get_current_round()
    launcher_pos = rc.get_position()
    _prune_orders(current_round)
    _enqueue_new_orders(current_round)
    adjacent_friendlies = _adjacent_builders(my_team)
    if not adjacent_friendlies:
        return False

    for v, sender_pos, marker_pos, marker_id, estimated_turn in tuple(_launch_orders):
        if estimated_turn + ORDER_MAX_AGE < current_round:
            continue
        if max(abs(sender_pos.x - launcher_pos.x), abs(sender_pos.y - launcher_pos.y)) > 1:
            continue

        target_idx = comms.decode_location(v)
        target = Position(target_idx % map_info._width, target_idx // map_info._width)

        matched_pos = None
        for _builder_id, bot_pos in adjacent_friendlies:
            if bot_pos == sender_pos:
                matched_pos = bot_pos
                break
        if matched_pos is None:
            continue

        if not rc.can_launch(matched_pos, target):
            continue

        rc.launch(matched_pos, target)
        log(f"launcher executed order from {sender_pos} to {target}")
        _drop_order(marker_id)
        if rc.can_destroy(marker_pos):
            rc.destroy(marker_pos)
            map_info.update_at(marker_pos)
        return True

    return False

def _try_throw_enemy_away() -> bool:
    enemy_team = Team.A if my_team == Team.B else Team.B
    adjacent_enemies = _adjacent_builders(enemy_team)
    if not adjacent_enemies:
        return False

    my_pos = rc.get_position()
    protected_tiles = _protected_tiles()
    best = None

    for _enemy_id, bot_pos in adjacent_enemies:
        for tile in rc.get_attackable_tiles():
            if not rc.is_tile_passable(tile):
                continue
            if not rc.can_launch(bot_pos, tile):
                continue

            score = _score_landing(tile, protected_tiles, my_pos)
            if best is None or score > best[0]:
                best = (score, bot_pos, tile)

    if best is None:
        return False

    _score, bot_pos, tile = best
    rc.launch(bot_pos, tile)
    log(f"launcher threw enemy from {bot_pos} to {tile}")
    return True

def run():
    map_info.update()
    if _try_execute_launch_order():
        return
    _try_throw_enemy_away()
