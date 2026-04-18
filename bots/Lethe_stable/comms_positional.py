from cambc import Position

import comms_stats
import map_info
from log import log

COMMS_SAMPLE_DISTANCE = 7

OFFSETS = (
    (0, 0),
    (0, 1),
    (1, 1),
    (1, 0),
    (1, -1),
    (0, -1),
    (-1, -1),
    (-1, 0),
    (-1, 1),
)
LEARN_MAP_OFFSETS = tuple(
    (dx, dy)
    for dy in range(-2, 3)
    for dx in range(-2, 3)
    if dx * dx + dy * dy <= 5
)
_active_stats = None

def get_corresponding_pos(pos: Position) -> Position:
    """Pick a comms sample position relative to our core and marker parity."""
    core = map_info._my_core
    if core is None:
        return pos

    dist = COMMS_SAMPLE_DISTANCE
    farther_horizontal = Position(pos.x - dist if pos.x <= core.x else pos.x + dist, pos.y)
    farther_vertical = Position(pos.x, pos.y - dist if pos.y <= core.y else pos.y + dist)
    closer_horizontal = Position(pos.x + dist if pos.x <= core.x else pos.x - dist, pos.y)
    closer_vertical = Position(pos.x, pos.y + dist if pos.y <= core.y else pos.y - dist)

    if pos.x % 2 == 0:
        candidates = (
            farther_horizontal,
            farther_vertical,
            closer_horizontal,
            closer_vertical,
        )
    else:
        candidates = (
            farther_vertical,
            farther_horizontal,
            closer_vertical,
            closer_horizontal,
        )

    for candidate in candidates:
        if map_info.in_bounds(candidate):
            return candidate
    return pos

def get_corresponding_pos_by_symmetry(pos: Position, sym_bits: int) -> Position:
    """Pick a comms sample position using the symmetry bits stored in a marker."""
    eliminated = []
    if not (sym_bits & 1):
        eliminated.append(map_info.hor_flip)
    if not (sym_bits & 2):
        eliminated.append(map_info.ver_flip)
    if not (sym_bits & 4):
        eliminated.append(map_info.rot_flip)

    if len(eliminated) == 0:
        return map_info.rot_flip(pos)
    if len(eliminated) == 1:
        return eliminated[0](pos)
    if pos.x % 2 == 0:
        return eliminated[0](pos)
    return eliminated[1](pos)

def communicates_walls(marker_pos: Position) -> bool:
    return marker_pos.y % 2 == 0

def get_learn_map_bucket_pos(x_bucket: int, y_bucket: int) -> Position:
    return Position(
        min(x_bucket * 5 + 2, map_info._width - 1),
        min(y_bucket * 5 + 2, map_info._height - 1),
    )

def get_learn_map_corresponding_pos(pos: Position) -> Position:
    return get_learn_map_bucket_pos(pos.x // 5, pos.y // 5)

def _learn_map_env_indices(marker_pos: Position) -> tuple[int, int]:
    if marker_pos.y % 2 == 0:
        return map_info._IDX_ENV_ORE_TI, map_info._IDX_ENV_WALL
    return map_info._IDX_ENV_ORE_AX, map_info._IDX_ENV_EMPTY

def _learn_map_env_idx(marker_pos: Position, env_bit: int) -> int:
    low_idx, high_idx = _learn_map_env_indices(marker_pos)
    return high_idx if env_bit else low_idx

def _sample_env_idx(marker_pos: Position) -> int:
    if communicates_walls(marker_pos):
        return map_info._IDX_ENV_WALL
    return map_info._IDX_ENV_ORE_TI

def encode_sample_bits(marker_pos: Position, sym_bits: int) -> int:
    corresponding = get_corresponding_pos(marker_pos)
    env_mask = map_info._bm_env[_sample_env_idx(marker_pos)]
    seen = map_info._bm_seen
    width = map_info._width
    result = 0

    for i, (dx, dy) in enumerate(OFFSETS):
        x = corresponding.x + dx
        y = corresponding.y + dy
        if not map_info.in_bounds_coords(x, y):
            continue
        bit = 1 << (x + y * width)
        if (seen & bit) and (env_mask & bit):
            result |= 1 << i
    return result

def encode_learn_map_sample_bits(corresponding_pos: Position, marker_pos: Position, env_bit: int) -> int:
    corresponding = get_learn_map_corresponding_pos(corresponding_pos)
    env_idx = _learn_map_env_idx(marker_pos, env_bit)
    env_mask = map_info._bm_env[env_idx]
    seen = map_info._bm_seen
    width = map_info._width
    result = 0

    for i, (dx, dy) in enumerate(LEARN_MAP_OFFSETS):
        x = corresponding.x + dx
        y = corresponding.y + dy
        if not map_info.in_bounds_coords(x, y):
            continue
        bit = 1 << (x + y * width)
        if (seen & bit) and (env_mask & bit):
            result |= 1 << i
    return result

def _new_stats():
    return {
        "markers_read": 0,
        "tiles_learned": 0,
        "tiles_known": 0,
        "tiles_conflict": 0,
        "learned_manhattan_distance_sum": 0,
        "known_manhattan_distance_sum": 0,
        "learned_chebyshev_distance_sum": 0,
        "known_chebyshev_distance_sum": 0,
    }

def start_round_stats():
    global _active_stats
    if comms_stats.is_enabled():
        _active_stats = _new_stats()
    else:
        _active_stats = None

def record_marker_read() -> None:
    if _active_stats is not None:
        _active_stats["markers_read"] += 1

def _record_sample_stats(stats, marker_x: int, marker_y: int, sample_x: int, sample_y: int, status: str) -> None:
    if status not in ("known", "learned", "conflict"):
        return

    manhattan_dist = abs(sample_x - marker_x) + abs(sample_y - marker_y)
    chebyshev_dist = max(abs(sample_x - marker_x), abs(sample_y - marker_y))

    if status == "known":
        stats["tiles_known"] += 1
        stats["known_manhattan_distance_sum"] += manhattan_dist
        stats["known_chebyshev_distance_sum"] += chebyshev_dist
    elif status == "learned":
        stats["tiles_learned"] += 1
        stats["learned_manhattan_distance_sum"] += manhattan_dist
        stats["learned_chebyshev_distance_sum"] += chebyshev_dist
    else:
        stats["tiles_conflict"] += 1

def note_comm_env(x: int, y: int, env_idx: int) -> str:
    """Record a communicated environment tile if it was previously unknown."""
    if not map_info.in_bounds_coords(x, y):
        return "oob"

    width = map_info._width
    height = map_info._height
    n = x + y * width
    bit = 1 << n
    if map_info._bm_seen & bit:
        if map_info._bm_env[env_idx] & bit:
            return "known"
        return "conflict"

    map_info._bm_seen |= bit
    for i in range(map_info._NUM_ENV):
        map_info._bm_env[i] &= ~bit
    map_info._bm_env[env_idx] |= bit
    map_info._env_idx_by_tile[n] = env_idx

    if map_info._solved_sym:
        if map_info._hor_sym:
            fn = (width - 1 - x) + y * width
        elif map_info._ver_sym:
            fn = x + (height - 1 - y) * width
        else:
            fn = (width - 1 - x) + (height - 1 - y) * width
        fbit = 1 << fn
        map_info._bm_env[env_idx] |= fbit
        map_info._env_idx_by_tile[fn] = env_idx
        map_info._bm_seen |= fbit
    else:
        bm_seen = map_info._bm_seen
        bm_env_match = map_info._bm_env[env_idx]
        rx = width - 1 - x
        ry = height - 1 - y
        if map_info._hor_sym:
            fbit = 1 << (rx + y * width)
            if (bm_seen & fbit) and not (bm_env_match & fbit):
                map_info._hor_sym = False
        if map_info._ver_sym:
            fbit = 1 << (x + ry * width)
            if (bm_seen & fbit) and not (bm_env_match & fbit):
                map_info._ver_sym = False
        if map_info._rot_sym:
            fbit = 1 << (rx + ry * width)
            if (bm_seen & fbit) and not (bm_env_match & fbit):
                map_info._rot_sym = False
    return "learned"

def apply_message(marker_pos: Position, sym_bits: int, sample_bits: int, stats=None) -> None:
    if stats is None:
        stats = _active_stats
    env_idx = _sample_env_idx(marker_pos)
    corresponding = get_corresponding_pos(marker_pos)
    marker_x = marker_pos.x
    marker_y = marker_pos.y
    base_x = corresponding.x
    base_y = corresponding.y

    for i, (dx, dy) in enumerate(OFFSETS):
        if not ((sample_bits >> i) & 1):
            continue
        sample_x = base_x + dx
        sample_y = base_y + dy
        if not map_info.in_bounds_coords(sample_x, sample_y):
            continue
        status = note_comm_env(sample_x, sample_y, env_idx)
        if stats is not None:
            _record_sample_stats(stats, marker_x, marker_y, sample_x, sample_y, status)

def apply_learn_map_message(marker_pos: Position, corresponding_pos: Position, env_bit: int, sample_bits: int, stats=None) -> None:
    if stats is None:
        stats = _active_stats
    corresponding = get_learn_map_corresponding_pos(corresponding_pos)
    marker_x = corresponding.x
    marker_y = corresponding.y
    base_x = corresponding.x
    base_y = corresponding.y
    env_idx = _learn_map_env_idx(marker_pos, env_bit)
    for i, (dx, dy) in enumerate(LEARN_MAP_OFFSETS):
        if not ((sample_bits >> i) & 1):
            continue
        sample_x = base_x + dx
        sample_y = base_y + dy
        if not map_info.in_bounds_coords(sample_x, sample_y):
            continue
        status = note_comm_env(sample_x, sample_y, env_idx)
        if stats is not None:
            _record_sample_stats(stats, marker_x, marker_y, sample_x, sample_y, status)

def flush_round_stats(current_round: int) -> None:
    global _active_stats
    if _active_stats is None:
        return
    flush_stats(current_round, _active_stats)
    _active_stats = None

def flush_stats(current_round: int, stats) -> None:
    comms_stats.record_round(
        current_round=current_round,
        markers_read=stats["markers_read"],
        tiles_learned=stats["tiles_learned"],
        tiles_known=stats["tiles_known"],
        tiles_conflict=stats["tiles_conflict"],
        learned_manhattan_distance_sum=stats["learned_manhattan_distance_sum"],
        known_manhattan_distance_sum=stats["known_manhattan_distance_sum"],
        learned_chebyshev_distance_sum=stats["learned_chebyshev_distance_sum"],
        known_chebyshev_distance_sum=stats["known_chebyshev_distance_sum"],
    )
