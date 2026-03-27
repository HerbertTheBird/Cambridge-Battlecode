from cambc import Controller, Position, EntityType, GameError
import math

rc = None
LAUNCHER_BIT = 31
ID_BITS = 12

# Caches to avoid redundant coordinate generation
_vision_offsets = []
_cached_vision_radius_sq = -1


def init(c: Controller):
    global rc
    rc = c


def get_messages():
    global _vision_offsets, _cached_vision_radius_sq

    pos = rc.get_position()
    rad_sq = rc.get_vision_radius_sq()

    # 1. Circle Offset Caching: Calculate circular offsets once per vision radius
    if rad_sq != _cached_vision_radius_sq:
        _vision_offsets.clear()
        r = int(math.sqrt(rad_sq))
        for dx in range(-r, r + 1):
            dx_sq = dx * dx
            for dy in range(-r, r + 1):
                if dx_sq + dy * dy <= rad_sq:
                    _vision_offsets.append((dx, dy))
        _cached_vision_radius_sq = rad_sq

    messages = []

    # 2. Localize method references (Massive speedup in Python tight loops)
    pos_x = pos.x
    pos_y = pos.y
    is_in_vision = rc.is_in_vision
    get_tile_building_id = rc.get_tile_building_id
    get_team = rc.get_team
    get_entity_type = rc.get_entity_type
    get_marker_value = rc.get_marker_value

    # 3. Pre-calculate static values
    my_team = get_team()
    marker_type = EntityType.MARKER

    for dx, dy in _vision_offsets:
        p = Position(pos_x + dx, pos_y + dy)
        if not is_in_vision(p):
            continue

        try:
            id = get_tile_building_id(p)
        except GameError:
            continue

        # Evaluate cheapest boolean conditions first
        if id and get_team(id) == my_team and get_entity_type(id) == marker_type:
            messages.append((p, get_marker_value(id)))

    return messages


# Keep original helpers for external compatibility, but inline them internally
def decode_location(v):
    return Position((v >> ID_BITS) & 63, (v >> (6 + ID_BITS)) & 63)


def decode_id(v):
    return v & ((1 << ID_BITS) - 1)


def decode_launch():
    out = []
    messages = get_messages()

    # Localize methods and constants
    is_in_vision = rc.is_in_vision
    id_mask = (1 << ID_BITS) - 1

    for p, v in messages:
        if (v >> LAUNCHER_BIT) & 1:
            # 4. Inline function logic to avoid CALL_FUNCTION overhead
            target = Position((v >> ID_BITS) & 63, (v >> (6 + ID_BITS)) & 63)
            if is_in_vision(target):
                out.append((target, v & id_mask, p))

    return out


def encode_launch(target):
    return rc.get_id() + (target.x << ID_BITS) + (target.y << (ID_BITS + 6)) + (1 << LAUNCHER_BIT)