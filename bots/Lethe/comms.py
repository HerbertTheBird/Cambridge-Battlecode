from cambc import Controller, Position, EntityType, GameError
import map_info
#type = 0:launch, 1:explore, 2:harvest, 3:route
TURN_BITS = 1
SYM_BITS = 3
POS_BITS = 12
ID_BITS = 12 # mod 4096
_ID_MASK = (1 << ID_BITS) - 1
_TURN_MASK = (1 << TURN_BITS) - 1
_SYM_MASK = (1 << SYM_BITS) - 1

rc: Controller
ENCRYPT = True
key = 0
prev_messages = dict()
_marker_at = {}  # physical tile index -> decrypted val
def random_hash() -> int:
    # Force inputs into 32-bit unsigned space
    a = rc.get_map_width()
    b = rc.get_map_height()
    a &= 0xFFFFFFFF
    b &= 0xFFFFFFFF

    # Combine into 64 bits
    x = (a << 32) | b

    # SplitMix64-style finalizer
    x ^= x >> 30
    x = (x * 0xbf58476d1ce4e5b9) & 0xFFFFFFFFFFFFFFFF
    x ^= x >> 27
    x = (x * 0x94d049bb133111eb) & 0xFFFFFFFFFFFFFFFF
    x ^= x >> 31

    # Return 32-bit unsigned int
    return x & 0xFFFFFFFF
def init(c: Controller):
    global rc
    rc = c
    if ENCRYPT:
        global key
        key = random_hash()


def get_new_messages():
    get_team = rc.get_team
    get_entity_type = rc.get_entity_type
    get_marker_value = rc.get_marker_value
    rc_get_position = rc.get_position
    my_team = get_team()
    marker_type = EntityType.MARKER
    width = rc.get_map_width()

    messages = []
    append = messages.append

    remove = set()
    seen_positions = set()

    for id in rc.get_nearby_buildings():
        if get_entity_type(id) == marker_type and get_team(id) == my_team:
            val = get_marker_value(id) ^ key
            pos = rc_get_position(id)
            pos_n = pos.x + pos.y * width
            seen_positions.add(pos_n)

            # If this position had a different marker before, clean it up
            old_val = _marker_at.get(pos_n)
            if old_val is not None and old_val != val:
                prev_messages.pop(old_val, None)
            _marker_at[pos_n] = val

            if (rc.get_id()&_ID_MASK) == decode_id(val):
                remove.add(val >> ID_BITS)
                remove.add((val^(1<<(ID_BITS+POS_BITS))) >> ID_BITS)
            if val in prev_messages:
                continue
            prev_messages[val] = rc.get_current_round()
            append(val)

    # Cleanup: tracked markers that are now gone from visible tiles
    to_remove = []
    for pos_n, old_val in _marker_at.items():
        if pos_n in seen_positions:
            continue
        p = Position(pos_n % width, pos_n // width)
        if rc.is_in_vision(p):
            to_remove.append(pos_n)
            prev_messages.pop(old_val, None)
    for pos_n in to_remove:
        del _marker_at[pos_n]
    messages[:] = [x for x in messages if ((x >> ID_BITS) not in remove)]
    return messages
def get_messages():
    get_new_messages()
    return list(prev_messages.keys())
def decode_location(v):
    return Position((v >> ID_BITS) & 63, (v >> (6 + ID_BITS)) & 63)
def decode_id(v):
    return v & _ID_MASK
def decode_type(v):
    return (v >> (ID_BITS + POS_BITS + TURN_BITS + SYM_BITS))
def decode_turn(v):
    return (v >> (ID_BITS + POS_BITS)) & _TURN_MASK
def decode_sym(v):
    return (v >> (ID_BITS + POS_BITS + TURN_BITS)) & _SYM_MASK
def encode(target, type, sym=0):
    return ((rc.get_id()&_ID_MASK) + (target.x << (ID_BITS)) + (target.y << (ID_BITS + 6)) + ((rc.get_current_round()&_TURN_MASK)<<(ID_BITS+POS_BITS)) + (sym << (ID_BITS + POS_BITS + TURN_BITS)) + (type << (ID_BITS + POS_BITS + TURN_BITS + SYM_BITS)))^key
def decode(v):
    return (decode_location(v), decode_id(v), decode_turn(v), decode_sym(v), decode_type(v))
def _is_bad_marker_spot(pos):
    """True if pos is cardinally adjacent to a harvester or is a conveyor target."""
    w = map_info._width
    bit = 1 << (pos.x + pos.y * w)
    if map_info._bm_conveyor_targets & bit:
        return True
    harv = map_info._bm_et[map_info._IDX_HARVESTER]
    if harv:
        harv_adj = map_info.expand_manhattan(harv)
        if harv_adj & bit:
            return True
    return False

def mark(target, type):
    rc.draw_indicator_line(rc.get_position(), target, 0, 255, 0)
    print("mark", target, type)
    sym = int(map_info._hor_sym) | (int(map_info._ver_sym) << 1) | (int(map_info._rot_sym) << 2)
    val = encode(target, type, sym)
    # Pass 1: empty tiles, not bad spots
    for i in rc.get_nearby_tiles(2):
        if not rc.get_tile_building_id(i) and rc.can_place_marker(i) and not _is_bad_marker_spot(i):
            rc.place_marker(i, val)
            return
    # Pass 2: overwrite my marker, not bad spots
    for i in rc.get_nearby_tiles(2):
        id = rc.get_tile_building_id(i)
        if id and rc.get_entity_type(id) == EntityType.MARKER and rc.get_team(id) == rc.get_team() and rc.can_place_marker(i) and not _is_bad_marker_spot(i):
            rc.place_marker(i, val)
            return
    # Pass 3: destroy my road, not bad spots
    for i in rc.get_nearby_tiles(2):
        id = rc.get_tile_building_id(i)
        if id and rc.get_entity_type(id) == EntityType.ROAD and rc.get_team(id) == rc.get_team() and not _is_bad_marker_spot(i) and not rc.get_tile_builder_bot_id(i):
            if rc.can_destroy(i):
                rc.destroy(i)
                map_info.update_at(i)
            if rc.can_place_marker(i):
                rc.place_marker(i, val)
                return