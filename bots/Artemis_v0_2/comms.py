from cambc import Controller, Position, EntityType, GameError

LAUNCHER_BIT = 31
CENTRALIZED_LAUNCHER_BIT = 30
ID_BITS = 12
_ID_MASK = (1 << ID_BITS) - 1

rc = None


def init(c: Controller):
    global rc
    rc = c


def get_messages():
    # get_nearby_buildings() returns all building IDs in vision in one C-level call,
    # eliminating ~65K Position constructions, ~61K get_tile_building_id calls,
    # ~30K is_in_vision calls, and all GameError try/excepts from the tile loop.
    get_team = rc.get_team
    get_entity_type = rc.get_entity_type
    get_position = rc.get_position
    get_marker_value = rc.get_marker_value
    my_team = get_team()
    marker_type = EntityType.MARKER

    messages = []
    append = messages.append

    for id in rc.get_nearby_buildings():
        # Check entity type first: markers are rarer than same-team buildings,
        # so this order minimises total API calls in most game states.
        # Swap the two conditions if markers are common in your game.
        if get_entity_type(id) == marker_type and get_team(id) == my_team:
            append((get_position(id), get_marker_value(id)))

    return messages


def decode_location(v):
    return Position((v >> ID_BITS) & 63, (v >> (6 + ID_BITS)) & 63)


def decode_id(v):
    return v & _ID_MASK


def decode_launch():
    is_in_vision = rc.is_in_vision
    id_mask = _ID_MASK
    out = []
    append = out.append

    for p, v in get_messages():
        if (v >> LAUNCHER_BIT) & 1:
            target = Position((v >> ID_BITS) & 63, (v >> (6 + ID_BITS)) & 63)
            if is_in_vision(target):
                append((target, v & id_mask, p))

    return out

def decode_centralized_launch():
    id_mask = _ID_MASK
    out = []
    append = out.append

    for p, v in get_messages():
        if (v >> CENTRALIZED_LAUNCHER_BIT) & 1:
                append((v & id_mask, p))

    return out


def encode_launch(target):
    return rc.get_id() + (target.x << ID_BITS) + (target.y << (ID_BITS + 6)) + (1 << LAUNCHER_BIT)

def encode_centralized_launch():
    return rc.get_id() + (1 << CENTRALIZED_LAUNCHER_BIT)
