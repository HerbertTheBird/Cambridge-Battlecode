from cambc import Controller, Position, EntityType, GameError
import map_info
import comms_positional
#type = 0:launch, 1:explore, 2:harvest, 3:route
SYM_BITS = 3
POS_BITS = 12
SAMPLE_BITS = 9
TYPE_BITS = 32 - POS_BITS - SYM_BITS - SAMPLE_BITS
_SYM_MASK = (1 << SYM_BITS) - 1
_POS_MASK = (1 << POS_BITS) - 1
_SAMPLE_MASK = (1 << SAMPLE_BITS) - 1
_TYPE_MASK = (1 << TYPE_BITS) - 1
_SAMPLE_SHIFT = POS_BITS + SYM_BITS
_TYPE_SHIFT = _SAMPLE_SHIFT + SAMPLE_BITS
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
    my_team = map_info._my_team
    marker_type = EntityType.MARKER
    width = rc.get_map_width()
    current_round = rc.get_current_round()
    marker_at = _marker_at
    my_markers = _my_markers
    messages_seen = prev_messages

    messages = []
    append = messages.append

    seen_positions = set()

    for id in rc.get_nearby_buildings():
        if get_entity_type(id) == marker_type and get_team(id) == my_team:
            pos = rc_get_position(id)
            pos_n = pos.x + pos.y * width
            seen_positions.add(pos_n)

            # Skip markers this bot placed itself.
            if id in my_markers:
                old_entry = marker_at.pop(pos_n, None)
                if old_entry is not None:
                    messages_seen.pop(old_entry[1], None)
                continue

            # Freshness is tracked by marker entity id: a new marker id at
            # this position means the content was replaced since last turn.
            old_entry = marker_at.get(pos_n)
            if old_entry is not None and old_entry[0] == id:
                continue

            val = get_marker_value(id) ^ key

            if old_entry is not None:
                messages_seen.pop(old_entry[1], None)
            marker_at[pos_n] = (id, val)

            messages_seen[val] = current_round
            # Off for now while testing feature
            # comms_positional.record_marker_read()
            # comms_positional.apply_message(pos, decode_sym(val), decode_sample_bits(val))
            append(val)

    # Cleanup: tracked markers that are now gone from visible tiles
    to_remove = []
    bm_visible = map_info._bm_visible
    for pos_n, (_mid, old_val) in marker_at.items():
        if pos_n in seen_positions:
            continue
        if bm_visible & (1 << pos_n):
            to_remove.append(pos_n)
            messages_seen.pop(old_val, None)
    for pos_n in to_remove:
        del marker_at[pos_n]
    return messages

def get_messages():
    get_new_messages()
    return list(prev_messages.keys())

def decode_location(v):
    return v & _POS_MASK

def decode_sym(v):
    return (v >> POS_BITS) & _SYM_MASK

def decode_sample_bits(v):
    return (v >> _SAMPLE_SHIFT) & _SAMPLE_MASK

def decode_type(v):
    return (v >> _TYPE_SHIFT) & _TYPE_MASK

def encode(target, type, sym=0, sample_bits=0):
    return (
        (target & _POS_MASK)
        | ((sym & _SYM_MASK) << POS_BITS)
        | ((sample_bits & _SAMPLE_MASK) << _SAMPLE_SHIFT)
        | ((type & _TYPE_MASK) << _TYPE_SHIFT)
    ) ^ key

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

def get_sym_bits() -> int:
    return int(map_info._hor_sym) | (int(map_info._ver_sym) << 1) | (int(map_info._rot_sym) << 2)

def mark(target_idx, type):
    print("mark", target_idx, type)

    adjacent_tiles = rc.get_nearby_tiles(2)

    best = None # (priority, pos, tile_id)

    for pos in adjacent_tiles:
        if _is_bad_marker_spot(pos):
            continue

        tile_id = rc.get_tile_building_id(pos)
        can_place = rc.can_place_marker(pos)

        # Priority 0: empty tile
        if not tile_id:
            if can_place:
                best = (0, pos, None)
                break
            else:
                continue

        entity_type = rc.get_entity_type(tile_id)
        same_team = rc.get_team(tile_id) == map_info._my_team

        if not same_team:
            continue

        # Priority 1: overwrite own marker
        if (entity_type == EntityType.MARKER and can_place):
            if best is None or best[0] > 1:
                best = (1, pos, tile_id)

        # Priority 2: replace own road
        elif (entity_type == EntityType.ROAD and not rc.get_tile_builder_bot_id(pos)):
            if best is None or best[0] > 2:
                best = (2, pos, tile_id)

    # Execute best fallback
    if best:
        priority, pos, tile_id = best
        sym = get_sym_bits()
        sample_bits = 0
        # sample_bits = comms_positional.encode_sample_bits(pos, sym)
        val = encode(target_idx, type, sym, sample_bits)

        _my_markers.discard(tile_id)
        if tile_id is not None and rc.can_destroy(pos):
            rc.destroy(pos)
            
            # Don't bother updating map if we replaced marker with marker
            if priority == 2:
                map_info.update_at(pos)

        if rc.can_place_marker(pos):
            rc.place_marker(pos, val)
            _my_markers.add(rc.get_tile_building_id(pos))
