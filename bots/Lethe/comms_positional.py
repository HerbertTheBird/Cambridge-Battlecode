from __future__ import annotations

from cambc import Position

import comms_stats
import map_info

COMMS_SAMPLE_DISTANCE = 8

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

def _sample_env_idx(marker_pos: Position) -> int:
    if communicates_walls(marker_pos):
        return map_info._IDX_ENV_WALL
    return map_info._IDX_ENV_ORE_TI

def encode_sample_bits(marker_pos: Position, sym_bits: int) -> int:
    corresponding = get_corresponding_pos_by_symmetry(marker_pos, sym_bits)
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

def decode_sample_positions(marker_pos: Position, sample_bits: int, sym_bits: int):
    corresponding = get_corresponding_pos_by_symmetry(marker_pos, sym_bits)
    for i, (dx, dy) in enumerate(OFFSETS):
        if not ((sample_bits >> i) & 1):
            continue
        x = corresponding.x + dx
        y = corresponding.y + dy
        if map_info.in_bounds_coords(x, y):
            pos = Position(x, y)
            yield pos

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

def _record_sample_stats(stats, marker_pos: Position, sample_pos: Position, status: str) -> None:
    if status not in ("known", "learned", "conflict"):
        return

    manhattan_dist = abs(sample_pos.x - marker_pos.x) + abs(sample_pos.y - marker_pos.y)
    chebyshev_dist = max(abs(sample_pos.x - marker_pos.x), abs(sample_pos.y - marker_pos.y))

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

def note_comm_env(pos: Position, env_idx: int) -> str:
    """Record a communicated environment tile if it was previously unknown."""
    if not map_info.in_bounds(pos):
        return "oob"

    n = pos.x + pos.y * map_info._width
    bit = 1 << n
    if map_info._bm_seen & bit:
        if map_info._bm_env[env_idx] & bit:
            return "known"
        return "conflict"

    map_info._bm_seen |= bit
    for i in range(map_info._NUM_ENV):
        map_info._bm_env[i] &= ~bit
    map_info._bm_env[env_idx] |= bit
    return "learned"

def apply_message(marker_pos: Position, sym_bits: int, sample_bits: int, stats=None) -> None:
    if stats is None:
        stats = _active_stats
    env_idx = _sample_env_idx(marker_pos)
    for sample_pos in decode_sample_positions(marker_pos, sample_bits, sym_bits):
        status = note_comm_env(sample_pos, env_idx)
        if stats is not None:
            _record_sample_stats(stats, marker_pos, sample_pos, status)

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
