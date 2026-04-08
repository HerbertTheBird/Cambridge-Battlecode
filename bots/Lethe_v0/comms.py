from cambc import Controller, Position, EntityType, GameError
#type = 0:launch, 1:explore, 2:harvest, 3:route
TURN_BITS = 3
POS_BITS = 12
ID_BITS = 12 # mod 4096
_ID_MASK = (1 << ID_BITS) - 1

rc: Controller

prev_messages = dict()
def init(c: Controller):
    global rc
    rc = c


def get_new_messages():
    get_team = rc.get_team
    get_entity_type = rc.get_entity_type
    get_marker_value = rc.get_marker_value
    my_team = get_team()
    marker_type = EntityType.MARKER

    messages = []
    append = messages.append

    remove = set()
    
    for id in rc.get_nearby_buildings():
        if get_entity_type(id) == marker_type and get_team(id) == my_team:
            val = get_marker_value(id)
            if (rc.get_id()&_ID_MASK) == decode_id(val):
                remove.add(val >> ID_BITS)
                continue
            if val in prev_messages:
                continue
            prev_messages[val] = rc.get_current_round()
            append(val)
    messages[:] = [x for x in messages if ((x >> ID_BITS) not in remove or x&_ID_MASK < rc.get_id()&_ID_MASK)]
    return messages
def get_messages():
    get_new_messages()
    return prev_messages.keys()
def decode_location(v):
    return Position((v >> ID_BITS) & 63, (v >> (6 + ID_BITS)) & 63)
def decode_id(v):
    return v & _ID_MASK
def decode_type(v):
    return (v >> (ID_BITS + POS_BITS + TURN_BITS))
def decode_turn(v):
    return (v >> (ID_BITS + POS_BITS)) & 7
def encode(target, type):
    return (rc.get_id()&_ID_MASK) + (target.x << (ID_BITS)) + (target.y << (ID_BITS + 6)) + ((rc.get_current_round()&7)<<(ID_BITS+POS_BITS)) + (type << (ID_BITS + POS_BITS + TURN_BITS))
def decode(v):
    return (decode_location(v), decode_id(v), decode_turn(v), decode_type(v))
def mark(target, type):
    rc.draw_indicator_line(rc.get_position(), target, 0, 255, 0)
    print("mark", target, type)
    for i in rc.get_nearby_tiles(2):
        if not rc.get_tile_building_id(i) and rc.can_place_marker(i):
            rc.place_marker(i, encode(target, type))
            return
    for i in rc.get_nearby_tiles(2):
        id = rc.get_tile_building_id(i)
        if id and rc.get_entity_type(id) == EntityType.MARKER and rc.get_team(id) == rc.get_team() and rc.can_place_marker(i):
            rc.place_marker(i, encode(target, type))
            return
    for i in rc.get_nearby_tiles(2):
        id = rc.get_tile_building_id(i)
        if id and rc.get_entity_type(id) == EntityType.ROAD and rc.get_team(id) == rc.get_team():
            if rc.can_destroy(i):
                rc.destroy(i)
            if rc.can_place_marker(i):
                rc.place_marker(i, encode(target, type))
                return
            