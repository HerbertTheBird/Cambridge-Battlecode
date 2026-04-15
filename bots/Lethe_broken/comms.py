from cambc import Controller, Position, EntityType, GameError
import map_info
#type = 0:launch, 1:explore, 2:harvest, 3:route
SYM_BITS = 3
POS_BITS = 12
_SYM_MASK = (1 << SYM_BITS) - 1
_POS_MASK = (1 << POS_BITS) - 1
rc: Controller
ENCRYPT = True
key = 0
prev_messages = dict()
_marker_at = {}  # physical tile index -> (marker entity id, decrypted val)
_my_markers = set()  # entity ids of markers this bot placed
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

    seen_positions = set()

    for id in rc.get_nearby_buildings():
        if get_entity_type(id) == marker_type and get_team(id) == my_team:
            pos = rc_get_position(id)
            pos_n = pos.x + pos.y * width
            seen_positions.add(pos_n)

            # Skip markers this bot placed itself.
            if id in _my_markers:
                old_entry = _marker_at.pop(pos_n, None)
                if old_entry is not None:
                    prev_messages.pop(old_entry[1], None)
                continue

            val = get_marker_value(id) ^ key

            # Freshness is tracked by marker entity id: a new marker id at
            # this position means the content was replaced since last turn.
            old_entry = _marker_at.get(pos_n)
            new = old_entry is None or old_entry[0] != id
            if old_entry is not None and old_entry[0] != id:
                prev_messages.pop(old_entry[1], None)
            _marker_at[pos_n] = (id, val)

            if not new:
                continue
            prev_messages[val] = rc.get_current_round()
            append(val)

    # Cleanup: tracked markers that are now gone from visible tiles
    to_remove = []
    for pos_n, (_mid, old_val) in _marker_at.items():
        if pos_n in seen_positions:
            continue
        p = Position(pos_n % width, pos_n // width)
        if rc.is_in_vision(p):
            to_remove.append(pos_n)
            prev_messages.pop(old_val, None)
    for pos_n in to_remove:
        del _marker_at[pos_n]
    return messages
def get_messages():
    get_new_messages()
    return list(prev_messages.keys())
def decode_location(v):
    return v & _POS_MASK
def decode_sym(v):
    return (v >> POS_BITS) & _SYM_MASK
def decode_type(v):
    return v >> (POS_BITS + SYM_BITS)
def encode(target, type, sym=0):
    return ((target & _POS_MASK) + (sym << POS_BITS) + (type << (POS_BITS + SYM_BITS))) ^ key
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

def mark(target_idx, type):
    w = rc.get_map_width()
    # target_pos = Position(target_idx % w, target_idx // w)
    # rc.draw_indicator_line(rc.get_position(), target_pos, 0, 255, 0)
    print("mark", target_idx, type)
    sym = int(map_info._hor_sym) | (int(map_info._ver_sym) << 1) | (int(map_info._rot_sym) << 2)
    val = encode(target_idx, type, sym)
    adjacent_tiles = rc.get_nearby_tiles(2)
    # Pass 1: empty tiles, not bad spots
    for i in adjacent_tiles:
        if not rc.get_tile_building_id(i) and rc.can_place_marker(i) and not _is_bad_marker_spot(i):
            rc.place_marker(i, val)
            _my_markers.add(rc.get_tile_building_id(i))
            return
    # Pass 2: overwrite my marker, not bad spots.
    # Destroy first so the replacement has a fresh entity id, which is how
    # receivers detect that the marker is new.
    for i in adjacent_tiles:
        id = rc.get_tile_building_id(i)
        if id and rc.get_entity_type(id) == EntityType.MARKER and rc.get_team(id) == rc.get_team() and rc.can_place_marker(i) and not _is_bad_marker_spot(i):
            _my_markers.discard(id)
            if rc.can_destroy(i):
                rc.destroy(i)
            rc.place_marker(i, val)
            _my_markers.add(rc.get_tile_building_id(i))
            return
    # Pass 3: destroy my road, not bad spots
    for i in adjacent_tiles:
        id = rc.get_tile_building_id(i)
        if id and rc.get_entity_type(id) == EntityType.ROAD and rc.get_team(id) == rc.get_team() and not _is_bad_marker_spot(i) and not rc.get_tile_builder_bot_id(i):
            if rc.can_destroy(i):
                rc.destroy(i)
                map_info.update_at(i)
            if rc.can_place_marker(i):
                rc.place_marker(i, val)
                _my_markers.add(rc.get_tile_building_id(i))
                return