from cambc import Controller, Direction, Position, EntityType

from globals import CARDINAL_DIRECTIONS

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

def is_marker_building(ct: Controller, bid: int | None) -> bool:
    return bid is not None and ct.get_entity_type(bid) == EntityType.MARKER

def on_map_coords(x: int, y: int, width: int, height: int) -> bool:
    return 0 <= x < width and 0 <= y < height

def on_map(pos: Position, width: int, height: int) -> bool:
    return 0 <= pos.x < width and 0 <= pos.y < height

def is_core_tile(core_pos: Position | None, pos: Position) -> bool:
    """True if pos is one of the 9 tiles occupied by the allied core."""
    if core_pos is None:
        return False
    return core_pos.distance_squared(pos) <= 2

def get_core_tiles(core_pos: Position | None) -> list[Position]:
    """Return the 9 occupied tiles for a core anchor position."""
    if core_pos is None:
        return []
    return [
        Position(core_pos.x + dx, core_pos.y + dy)
        for dx in range(-1, 2)
        for dy in range(-1, 2)
    ]

def get_nearest_core_tile(core_pos: Position | None, reference_pos: Position) -> Position | None:
    """Return the core tile closest to reference_pos."""
    tiles = get_core_tiles(core_pos)
    if not tiles:
        return None
    return min(tiles, key=lambda pos: pos.distance_squared(reference_pos))

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

def get_foundry_positions(core_pos: Position | None, width: int, height: int) -> set:
    """Return set of valid foundry positions (cardinally adjacent to core's 3x3)."""
    positions = set()
    if core_pos is None:
        return positions
    cx, cy = core_pos.x, core_pos.y
    for x in range(cx - 1, cx + 2):
        for dy in (-2, 2):
            y = cy + dy
            if on_map_coords(x, y, width, height):
                positions.add(Position(x, y))
    for y in range(cy - 1, cy + 2):
        for dx in (-2, 2):
            x = cx + dx
            if on_map_coords(x, y, width, height):
                positions.add(Position(x, y))
    return positions
