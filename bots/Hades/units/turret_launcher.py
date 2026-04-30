from cambc import Controller, Position, Team

import map_info
from log import log

rc: Controller = None
my_team: Team = None


def init(c: Controller):
    global rc, my_team
    rc = c
    my_team = map_info._my_team


def _adjacent_enemy_builders() -> list[tuple[int, Position]]:
    result = []
    my_pos = rc.get_position()
    for pos in rc.get_nearby_tiles(2):
        if pos == my_pos:
            continue
        if max(abs(pos.x - my_pos.x), abs(pos.y - my_pos.y)) > 1:
            continue
        bot_id = rc.get_tile_builder_bot_id(pos)
        if bot_id is None:
            continue
        if rc.get_team(bot_id) == my_team:
            continue
        result.append((bot_id, pos))
    return result


def _try_throw_enemy_away() -> bool:
    adjacent_enemies = _adjacent_enemy_builders()
    if not adjacent_enemies:
        return False

    my_pos = rc.get_position()
    best = None  # (d2, bot_pos, tile)

    for _enemy_id, bot_pos in adjacent_enemies:
        for tile in rc.get_attackable_tiles():
            if not rc.is_tile_passable(tile):
                continue
            if not rc.can_launch(bot_pos, tile):
                continue
            d2 = tile.distance_squared(my_pos)
            if best is None or d2 > best[0]:
                best = (d2, bot_pos, tile)

    if best is None:
        return False

    _d2, bot_pos, tile = best
    rc.launch(bot_pos, tile)
    log(f"launcher threw enemy from {bot_pos} to {tile}")
    return True


def run():
    map_info.update()
    _try_throw_enemy_away()
