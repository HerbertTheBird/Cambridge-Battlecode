from cambc import Controller, Position, Team

import map_info
from log import log

rc: Controller = None
my_team: Team = None
_is_chokepoint_launcher: bool | None = None


def init(c: Controller):
    global rc, my_team, _is_chokepoint_launcher
    rc = c
    my_team = map_info._my_team
    _is_chokepoint_launcher = None


def _classify_launcher() -> None:
    global _is_chokepoint_launcher
    if _is_chokepoint_launcher is not None:
        return
    w = map_info._width
    my_pos = rc.get_position()
    my_bit = 1 << (my_pos.x + my_pos.y * w)
    enemy_conveyors = map_info._bm_conveyors & map_info._bm_team[1 - map_info._my_team_idx]
    adjacent = map_info.expand_chebyshev(my_bit) & ~my_bit
    _is_chokepoint_launcher = not bool(adjacent & enemy_conveyors)


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

    w = map_info._width
    my_pos = rc.get_position()
    my_bit = 1 << (my_pos.x + my_pos.y * w)

    # Collect legal (bot_pos, tile) launches; keep one bot per destination tile.
    launchable_map = {}
    launchable_mask = 0
    for _enemy_id, bot_pos in adjacent_enemies:
        for tile in rc.get_attackable_tiles():
            if not rc.is_tile_passable(tile):
                continue
            if not rc.can_launch(bot_pos, tile):
                continue
            tn = tile.x + tile.y * w
            if tn in launchable_map:
                continue
            launchable_map[tn] = (bot_pos, tile)
            launchable_mask |= 1 << tn

    if not launchable_mask:
        return False

    # Multi-source BFS from (every conveyor) ∪ (launcher position). The throw
    # target is the launchable tile last reached by the wavefront. Tiles never
    # reached (disconnected from the seed) are best of all — pick those first.
    seed = map_info._bm_conveyors | my_bit
    walls = map_info._bm_env[map_info._IDX_ENV_WALL]
    traversable = ~walls & map_info._board_mask

    visited = seed
    frontier = seed
    last_wave_hit = seed & launchable_mask
    while frontier:
        next_frontier = map_info.expand_chebyshev(frontier) & traversable & ~visited
        if not next_frontier:
            break
        hit = next_frontier & launchable_mask
        if hit:
            last_wave_hit = hit
        visited |= next_frontier
        frontier = next_frontier

    unvisited = launchable_mask & ~visited
    if unvisited:
        chosen_mask = unvisited
    elif last_wave_hit:
        chosen_mask = last_wave_hit
    else:
        return False

    tn = (chosen_mask & -chosen_mask).bit_length() - 1
    bot_pos, tile = launchable_map[tn]
    rc.launch(bot_pos, tile)
    log(f"launcher threw enemy from {bot_pos} to {tile}")
    return True


def _try_throw_enemy_to_history() -> bool:
    adjacent_enemies = _adjacent_enemy_builders()
    if not adjacent_enemies:
        return False

    w = map_info._width
    histories = [
        (enemy_id, bot_pos, map_info.bot_position_history(enemy_id))
        for enemy_id, bot_pos in adjacent_enemies
    ]
    max_history_len = max((len(history) for _enemy_id, _bot_pos, history in histories), default=0)

    for depth in range(1, max_history_len):
        for enemy_id, bot_pos, history in histories:
            if depth >= len(history):
                continue
            tn = history[depth]
            tile = Position(tn % w, tn // w)
            if tile == bot_pos:
                continue
            if not rc.is_tile_passable(tile):
                continue
            if not rc.can_launch(bot_pos, tile):
                continue
            rc.launch(bot_pos, tile)
            log(f"chokepoint launcher threw enemy {enemy_id} from {bot_pos} back to {tile}")
            return True
    return False


def run():
    map_info.update()
    _classify_launcher()
    if _is_chokepoint_launcher and _try_throw_enemy_to_history():
        return
    _try_throw_enemy_away()
