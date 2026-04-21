from cambc import Controller, Direction, Position, EntityType, ResourceType

from globals import CARDINAL_DIRECTIONS, Symmetry

# Used to differentiate bot paths by color

_GOLDEN = 0.618033988749895
def bot_path_color(bot_id: int) -> tuple[int, int, int]:
    hue = (bot_id * _GOLDEN) % 1.0
    h6 = hue * 6.0
    sector = int(h6)
    f = h6 - sector
    q = int((1 - f) * 255)
    t = int(f * 255)
    match sector % 6:
        case 0: return 255, t,   0
        case 1: return q,   255, 0
        case 2: return 0,   255, t
        case 3: return 0,   q,   255
        case 4: return t,   0,   255
        case _: return 255, 0,   q

def is_core_tile(core_pos: Position | None, pos: Position) -> bool:
    """True if pos is one of the 9 tiles occupied by the allied core."""
    if core_pos is None:
        return False
    return core_pos.distance_squared(pos) <= 2

def get_core_tiles(core_pos: Position) -> list[Position]:
    """Return the 9 occupied tiles for a core anchor position."""
    return [
        Position(core_pos.x + dx, core_pos.y + dy)
        for dx in range(-1, 2)
        for dy in range(-1, 2)
    ]

def get_nearest_core_tile(core_pos: Position, reference_pos: Position) -> Position:
    """Return the core tile closest to reference_pos."""
    return min(get_core_tiles(core_pos), key=lambda pos: pos.distance_squared(reference_pos))

def get_cardinal_direction_into_core(core_pos: Position | None, pos: Position) -> Direction | None:
    """Return the cardinal direction from pos into one of the core's 3x3 tiles."""
    if core_pos is None:
        return None
    for d in CARDINAL_DIRECTIONS:
        if is_core_tile(core_pos, pos.add(d)):
            return d
    return None

def is_foundry_position(core_pos: Position | None, pos: Position) -> bool:
    """True if pos is cardinally adjacent to the core's 3x3 area (valid foundry location)."""
    if core_pos is None:
        return False
    dist = core_pos.distance_squared(pos)
    return 2 < dist <= 5

def get_foundry_positions(core_pos: Position, width: int, height: int) -> set:
    """Return set of valid foundry positions (cardinally adjacent to core's 3x3)."""
    positions = set()
    cx, cy = core_pos.x, core_pos.y
    for x in range(cx - 1, cx + 2):
        for dy in (-2, 2):
            y = cy + dy
            if 0 <= x < width and 0 <= y < height:
                positions.add(Position(x, y))
    for y in range(cy - 1, cy + 2):
        for dx in (-2, 2):
            x = cx + dx
            if 0 <= x < width and 0 <= y < height:
                positions.add(Position(x, y))
    return positions

def check_for_resource_increase(player, ct: Controller):
    # We gain passive titanium income every 4 rounds, so ignore for inferring harvest success
    if ct.get_current_round() % 4 == 0:
        return
    if player.global_titanium > player.prev_global_titanium:
        player.last_global_titanium_increase = ct.get_current_round()
    if player.global_axionite > player.prev_global_axionite:
        player.last_global_axionite_increase = ct.get_current_round()

def get_opposite_ore(map_obj, is_axionite: bool):
    """Return the ore set of the opposite type."""
    return map_obj.ore_ti if is_axionite else map_obj.ore_ax

def get_predicted_enemy_core_pos(player) -> Position | None:
    if player.enemy_core_pos is not None:
        return player.enemy_core_pos
    if player.core_pos is None or player.map is None:
        return None
    if player.map.symmetry is not Symmetry.UNKNOWN:
        return player.map.get_symmetric_pos(player.core_pos, player.map.symmetry)
    if player.map.can_rotate:
        return player.map.get_symmetric_pos(player.core_pos, Symmetry.ROTATE)
    if player.map.can_flip_x:
        return player.map.get_symmetric_pos(player.core_pos, Symmetry.FLIP_X)
    if player.map.can_flip_y:
        return player.map.get_symmetric_pos(player.core_pos, Symmetry.FLIP_Y)
    return None
