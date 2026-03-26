from cambc import Controller, Position, EntityType, GameError
import math
rc = None
LAUNCHER_BIT = 31
ID_BITS = 7
def init(c: Controller):
    global rc
    rc = c
def get_messages():
    messages = []
    pos = rc.get_position()
    r = int(math.sqrt(rc.get_vision_radius_sq()))
    for x in range(pos.x-r, pos.x+r+1):
        for y in range(pos.y-r, pos.y+r+1):
            p = Position(x, y)
            if not rc.is_in_vision(p):
                continue
            try:
                id = rc.get_tile_building_id(p)
            except GameError:
                id = None
            if id and rc.get_team(id) == rc.get_team() and rc.get_entity_type(id) == EntityType.MARKER:
                messages.append((p, rc.get_marker_value(id)))
    return messages
def decode_location(v):
    return Position((v>>ID_BITS)&63, (v>>(6+ID_BITS))&63)
def decode_id(v):
    return v&((1<<ID_BITS)-1)
def decode_launch():
    out = []
    messages = get_messages()
    for p, v in messages:
        if (v >> LAUNCHER_BIT)&1:
            target = decode_location(v)
            if rc.is_in_vision(target):
                out.append((target, decode_id(v), p))
    return out
def encode_launch(target):
    pos = rc.get_position()
    return rc.get_id() + (target.x<<ID_BITS) + (target.y<<(ID_BITS+6)) + (1<<LAUNCHER_BIT)