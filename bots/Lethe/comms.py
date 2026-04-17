from cambc import Controller, Position, Direction, EntityType, GameError
import map_info
from log import DRAW_DEBUG, log
import comms_positional
#type = 0:launch, 1:explore, 2:harvest, 3:route
POS_BITS = 12
SYM_BITS = 3
SAMPLE_BITS = 9
SENDER_BITS = 3
TYPE_BITS = 32 - POS_BITS - SYM_BITS - SAMPLE_BITS - SENDER_BITS
_POS_MASK = (1 << POS_BITS) - 1
_SYM_MASK = (1 << SYM_BITS) - 1
_SAMPLE_MASK = (1 << SAMPLE_BITS) - 1
_SENDER_MASK = (1 << SENDER_BITS) - 1
_TYPE_MASK = (1 << TYPE_BITS) - 1
_SYM_SHIFT = POS_BITS
_SAMPLE_SHIFT = _SYM_SHIFT + SYM_BITS
_SENDER_SHIFT = _SAMPLE_SHIFT + SAMPLE_BITS
_TYPE_SHIFT = _SENDER_SHIFT + SENDER_BITS

_DIRS_8 = [
    Direction.NORTH, Direction.NORTHEAST, Direction.EAST, Direction.SOUTHEAST,
    Direction.SOUTH, Direction.SOUTHWEST, Direction.WEST, Direction.NORTHWEST,
]
_DIR_TO_IDX = {d: i for i, d in enumerate(_DIRS_8)}
rc: Controller
ENCRYPT = True
key = 0

_marker_id_at: list = []  # tile idx -> last-seen marker entity id (0 = none)
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
    global rc, _marker_id_at
    rc = c
    if ENCRYPT:
        global key
        key = random_hash()
    _marker_id_at = [0] * (c.get_map_width() * c.get_map_height())


def estimate_turn(entity_id):
    max_ids = map_info._max_id_by_round
    lo, hi = 0, len(max_ids) - 1
    result = hi
    while lo <= hi:
        mid = (lo + hi) >> 1
        if max_ids[mid] < entity_id:
            lo = mid + 1
        else:
            result = mid
            hi = mid - 1
    return result

def decode_visible_marker(id: int, pos: Position):
    width = map_info._width
    marker_id_at = _marker_id_at
    my_markers = _my_markers
    if id in my_markers:
        return None

    if not map_info.in_bounds(pos):
        return None
    pos_n = pos.x + pos.y * width
    if pos_n < 0 or pos_n >= len(marker_id_at):
        return None
    if marker_id_at[pos_n] == id:
        return None
    marker_id_at[pos_n] = id

    val = rc.get_marker_value(id) ^ key
    sender_dir_idx = (val >> _SENDER_SHIFT) & _SENDER_MASK
    sender_dir = _DIRS_8[sender_dir_idx]
    dx, dy = map_info._DIRECTION_DELTAS[sender_dir]
    sender_pos = Position(pos.x + dx, pos.y + dy)
    return (val, sender_pos)


def get_new_messages():
    return map_info._new_marker_messages

def decode_location(v):
    return v & _POS_MASK

def decode_sym(v):
    return (v >> _SYM_SHIFT) & _SYM_MASK

def decode_sample_bits(v):
    return (v >> _SAMPLE_SHIFT) & _SAMPLE_MASK

def decode_sender_location(v):
    return (v >> _SENDER_SHIFT) & _SENDER_MASK

def decode_type(v):
    return (v >> _TYPE_SHIFT) & _TYPE_MASK

def encode(target, type, sym=0, sample_bits=0, sender_loc=0):
    return (
        (target & _POS_MASK)
        | ((sym & _SYM_MASK) << _SYM_SHIFT)
        | ((sample_bits & _SAMPLE_MASK) << _SAMPLE_SHIFT)
        | ((sender_loc & _SENDER_MASK) << _SENDER_SHIFT)
        | ((type & _TYPE_MASK) << _TYPE_SHIFT)
    ) ^ key

def _is_bad_marker_spot(pos):
    """True if pos is cardinally adjacent to a harvester or is a conveyor target."""
    bit = 1 << (pos.x + pos.y * map_info._width)
    return bool((map_info._bm_conveyor_targets | map_info._bm_harv_adj) & bit)

def get_sym_bits() -> int:
    return int(map_info._hor_sym) | (int(map_info._ver_sym) << 1) | (int(map_info._rot_sym) << 2)

def mark(target_idx, type):
    if DRAW_DEBUG and type != 7:
        rc.draw_indicator_line(map_info._my_pos, Position(target_idx % map_info._width, target_idx // map_info._width), 255, 255, 0)
    log("mark", target_idx, type)

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
        sample_bits = comms_positional.encode_sample_bits(pos, sym)
        sender_dir = pos.direction_to(map_info._my_pos)
        sender_loc = _DIR_TO_IDX.get(sender_dir, 0)
        val = encode(target_idx, type, sym, sample_bits, sender_loc)

        _my_markers.discard(tile_id)
        if tile_id is not None and rc.can_destroy(pos):
            rc.destroy(pos)
            
            # Don't bother updating map if we replaced marker with marker
            map_info.update_at(pos)

        if rc.can_place_marker(pos):
            rc.place_marker(pos, val)
            map_info.update_at(pos)
            _my_markers.add(rc.get_tile_building_id(pos))
