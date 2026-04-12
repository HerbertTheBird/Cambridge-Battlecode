from cambc import Controller, Position, Team

def _get_farthest_core_tile(my_pos, enemy_core_pos):
    """Return the enemy core tile farthest from this breach within the core's 3x3 footprint."""
    if enemy_core_pos is None:
        return None

    best_tile = None
    best_dist = -1

    for dx in range(-1, 2):
        for dy in range(-1, 2):
            tile_x = enemy_core_pos.x + dx
            tile_y = enemy_core_pos.y + dy

            dx_me = my_pos.x - tile_x
            dy_me = my_pos.y - tile_y
            dist = dx_me * dx_me + dy_me * dy_me  # squared distance

            if dist > best_dist:
                best_dist = dist
                best_tile = Position(tile_x, tile_y)

    return best_tile

# Breach defaults to attacking farthest enemy core tile to avoid hitting foundry feeding it
def choose_target(ct: Controller, my_pos: Position, enemy_core_pos: Position | None) -> Position | None:
    """Return the farthest enemy core tile that this breach can fire at."""
    target = _get_farthest_core_tile(my_pos, enemy_core_pos)
    if target is None or not ct.can_fire(target):
        return None
    return target

