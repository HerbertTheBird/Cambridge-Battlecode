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

    # For each candidate destination, Chebyshev flood-fill the region the
    # launched bot could navigate using enemy-POV pathing, with unseen tiles
    # treated as impassable. Smaller region = the bot is more contained
    # post-launch, which is what we want.
    forbidden = (
        map_info.get_avoid(False, False, False, enemy_pov=True)
        | (~map_info._bm_seen & map_info._board_mask)
    )
    passable = ~forbidden & map_info._board_mask

    component_size: dict[int, int] = {}
    handled = 0
    m = launchable_mask
    while m:
        lsb = m & -m
        m ^= lsb
        n = lsb.bit_length() - 1
        if handled & lsb:
            continue
        if not (lsb & passable):
            # Destination is enemy-impassable itself (e.g. our core) — best.
            component_size[n] = 0
            handled |= lsb
            continue
        region = lsb
        while True:
            nxt = map_info.expand_chebyshev(region) & passable
            if nxt == region:
                break
            region = nxt
        size = bin(region).count("1")
        rm = launchable_mask & region
        while rm:
            rl = rm & -rm
            rm ^= rl
            component_size[rl.bit_length() - 1] = size
        handled |= region | lsb

    # Tiebreak with the prior heuristic: prefer destinations farthest from
    # (every conveyor) ∪ (launcher) in a wall-only Chebyshev BFS.
    seed = map_info._bm_conveyors | my_bit
    walls = map_info._bm_env[map_info._IDX_ENV_WALL]
    traversable = ~walls & map_info._board_mask
    UNREACHED = 1 << 30
    layer_of: dict[int, int] = {}
    visited = seed
    frontier = seed
    layer_idx = 0
    rm = seed & launchable_mask
    while rm:
        rl = rm & -rm
        rm ^= rl
        layer_of[rl.bit_length() - 1] = layer_idx
    while frontier:
        layer_idx += 1
        next_frontier = map_info.expand_chebyshev(frontier) & traversable & ~visited
        if not next_frontier:
            break
        rm = next_frontier & launchable_mask
        while rm:
            rl = rm & -rm
            rm ^= rl
            layer_of[rl.bit_length() - 1] = layer_idx
        visited |= next_frontier
        frontier = next_frontier

    best_n = None
    best_key = None
    rm = launchable_mask
    while rm:
        rl = rm & -rm
        rm ^= rl
        rn = rl.bit_length() - 1
        key = (component_size[rn], -layer_of.get(rn, UNREACHED))
        if best_key is None or key < best_key:
            best_key = key
            best_n = rn

    bot_pos, tile = launchable_map[best_n]
    rc.launch(bot_pos, tile)
    log(f"launcher threw enemy from {bot_pos} to {tile} (size={best_key[0]}, layer={-best_key[1]})")
    return True


def run():
    map_info.update()
    _try_throw_enemy_away()
