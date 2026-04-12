from cambc import Controller, Direction, Position, GameConstants

from globals import (
    Symmetry,
    CARDINAL_DIRECTIONS, 
    TURN_CPU_BUDGET_US, 
    CPU_SAFETY_MARGIN_US
)

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
    px = pos.x
    py = pos.y
    cx = core_pos.x
    cy = core_pos.y
    for d in CARDINAL_DIRECTIONS:
        dx, dy = d.delta()
        nx = px + dx
        ny = py + dy
        if abs(cx - nx) <= 1 and abs(cy - ny) <= 1:
            return d
    return None

def is_foundry_position(core_pos: Position | None, pos: Position) -> bool:
    """True if pos is cardinally adjacent to the core's 3x3 area (valid foundry location)."""
    if core_pos is None:
        return False
    dist = core_pos.distance_squared(pos)
    return 2 < dist <= 5

def get_foundry_position_idxs(core_pos: Position, width: int, height: int) -> set[int]:
    """Return idx set of valid foundry positions (cardinally adjacent to core's 3x3)."""
    positions: set[int] = set()
    cx, cy = core_pos.x, core_pos.y
    for x in range(cx - 1, cx + 2):
        for dy in (-2, 2):
            y = cy + dy
            if 0 <= x < width and 0 <= y < height:
                positions.add(y * width + x)
    for y in range(cy - 1, cy + 2):
        for dx in (-2, 2):
            x = cx + dx
            if 0 <= x < width and 0 <= y < height:
                positions.add(y * width + x)
    return positions

def check_for_resource_increase(player, ct: Controller):
    # We gain passive titanium income every 4 rounds, so ignore for inferring harvest success
    if ct.get_current_round() % 4 == 0:
        return
    if player.global_titanium > player.prev_global_titanium:
        player.last_global_titanium_increase = ct.get_current_round()
    if player.global_axionite > player.prev_global_axionite:
        player.last_global_axionite_increase = ct.get_current_round()

def get_opposite_ore_mask(is_axionite: bool) -> int:
    """Return the ore mask of the opposite type."""
    import map as map_mod
    return map_mod.get_titanium_ore_mask() if is_axionite else map_mod.get_axionite_ore_mask()

def get_predicted_enemy_core_pos(player) -> Position | None:
    import map as map_mod
    if player.enemy_core_pos is not None:
        return player.enemy_core_pos
    if player.core_pos is None:
        return None
    if map_mod.symmetry is not Symmetry.UNKNOWN:
        return map_mod.get_symmetric_pos(player.core_pos, map_mod.symmetry)
    if map_mod.can_rotate:
        return map_mod.get_symmetric_pos(player.core_pos, Symmetry.ROTATE)
    if map_mod.can_flip_x:
        return map_mod.get_symmetric_pos(player.core_pos, Symmetry.FLIP_X)
    if map_mod.can_flip_y:
        return map_mod.get_symmetric_pos(player.core_pos, Symmetry.FLIP_Y)
    return None

_BUILDER_BOT_VISION_RADIUS_SQ = GameConstants.BUILDER_BOT_VISION_RADIUS_SQ

def is_in_vision(my_pos, pos):
    return my_pos.distance_squared(pos) <= _BUILDER_BOT_VISION_RADIUS_SQ

def get_remaining_turn_budget_us(elapsed_us: int, reserve_us: int = 0) -> int:
    return max(0, TURN_CPU_BUDGET_US - elapsed_us - reserve_us - CPU_SAFETY_MARGIN_US)
