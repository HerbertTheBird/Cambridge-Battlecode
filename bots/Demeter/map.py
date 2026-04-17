import random
from array import array
from collections import defaultdict

from cambc import Controller, Direction, EntityType, Environment, Position, ResourceType, Team

from log import log, log_time
from globals import (
    Symmetry,
    DIRECTIONS, 
    CARDINAL_DIRECTIONS, 
    CONVEYOR_TYPES, 
    TURRET_TYPES, 
    OPTIMISTIC_REACHABILITY,
    INF, 
    DELTAS, 
    DELTA_TO_DIRECTION, 
    TURN_CPU_BUDGET_US, 
    END_TURN_RESERVE_US, 
    CPU_SAFETY_MARGIN_US
)
from helpers import (
    get_foundry_position_idxs, 
    is_core_tile,
    is_in_vision,
)

_ET_INT = {t: i for i, t in enumerate(EntityType)}
_INT_ET = {i: t for i, t in enumerate(EntityType)}
_TM_INT = {t: i for i, t in enumerate(Team)}
_INT_TM = {i: t for i, t in enumerate(Team)}
_ENV_INT = {t: i for i, t in enumerate(Environment)}
_INT_ENV = {i: t for i, t in enumerate(Environment)}

_IDX_CONVEYOR = _ET_INT[EntityType.CONVEYOR]
_IDX_ARMOURED_CONVEYOR = _ET_INT[EntityType.ARMOURED_CONVEYOR]
_IDX_BRIDGE = _ET_INT[EntityType.BRIDGE]
_IDX_SPLITTER = _ET_INT[EntityType.SPLITTER]
_IDX_HARVESTER = _ET_INT[EntityType.HARVESTER]
_IDX_FOUNDRY = _ET_INT[EntityType.FOUNDRY]
_IDX_ROAD = _ET_INT[EntityType.ROAD]
_IDX_BARRIER = _ET_INT[EntityType.BARRIER]
_IDX_MARKER = _ET_INT[EntityType.MARKER]
_IDX_CORE = _ET_INT[EntityType.CORE]
_IDX_GUNNER = _ET_INT[EntityType.GUNNER]
_IDX_SENTINEL = _ET_INT[EntityType.SENTINEL]
_IDX_BREACH = _ET_INT[EntityType.BREACH]
_IDX_LAUNCHER = _ET_INT[EntityType.LAUNCHER]

_IDX_ENV_EMPTY = _ENV_INT[Environment.EMPTY]
_IDX_ENV_WALL = _ENV_INT[Environment.WALL]
_IDX_ENV_ORE_TI = _ENV_INT[Environment.ORE_TITANIUM]
_IDX_ENV_ORE_AX = _ENV_INT[Environment.ORE_AXIONITE]

_CONVEYOR_TYPE_IDXS = frozenset(_ET_INT[t] for t in CONVEYOR_TYPES)
_TURRET_TYPE_IDXS = frozenset(_ET_INT[t] for t in TURRET_TYPES)

_CACHE_MISS = object()

def on_map_coords(x: int, y: int, w: int, h: int) -> bool:
    return 0 <= x < w and 0 <= y < h

def on_map(pos: Position, w: int, h: int) -> bool:
    return 0 <= pos.x < w and 0 <= pos.y < h

SYMMETRY_MAPPING = {
    (True,  False, False): Symmetry.FLIP_X,
    (False, True,  False): Symmetry.FLIP_Y,
    (False, False, True):  Symmetry.ROTATE,
}

_BRIDGE_OFFSETS = tuple((dx, dy) for dx in range(-3, 4) for dy in range(-3, 4) if dx*dx + dy*dy <= 9)
_CARDINAL_OFFSETS = ((0, -1), (1, 0), (0, 1), (-1, 0))

RESOURCE_MASK_TITANIUM = 1
RESOURCE_MASK_AXIONITE = 2
OBSERVED_RESOURCE_MAX_AGE = 5

FLAG_SEEN = 1 << 0
FLAG_WALL = 1 << 1
FLAG_BLOCKED = 1 << 2
FLAG_ALLY_BARRIER = 1 << 3
FLAG_ALLY_LAUNCHER = 1 << 4
FLAG_ORE_TITANIUM = 1 << 5
FLAG_ORE_AXIONITE = 1 << 6
_CLEAR_ENTITY_FLAGS = ~(FLAG_BLOCKED | FLAG_ALLY_BARRIER | FLAG_ALLY_LAUNCHER)

# Module-level globals (initialized by init())
width = 0
height = 0
tile_count = 0
_board_mask = 0
_not_left_col = 0
_not_right_col = 0
_tile_flags: array
_env_idx: array
_entity_id: array
_entity_type_idx: array
_entity_team_idx: array
_bm_et: list[int] = []
_bm_team: list[int] = []
_bm_env: list[int] = []
_bm_occupied = 0
_bm_walkable = 0
_bm_seen = 0
_bm_wall = 0
_bm_blocked = 0
_bm_visible = 0
_bm_ore_ti = 0
_bm_ore_ax = 0
_bm_unreachable_harvesters = 0
_bm_unreachable_ores = 0
_bm_reachable = 0
_bm_might_reach = 0
_bm_floodfill_open_prev = 0
_bm_enemy_core = 0
_enemy_launcher_adj: bytearray
_output_idx: array
_input_masks: list[int] = []
conveyor_resources: defaultdict
conveyor_resources_last_seen: defaultdict
_input_chain_valid: bytearray
_input_resource_masks: array
current_round = 0
_feeds_turret_cache: dict = {}
_feeds_building_cache: dict = {}
_feeds_building_in_vision_cache: dict = {}
_sabotage_downstream_cache: dict = {}
_chain_terminal_cache: dict = {}
_chain_last_visible_cache: dict = {}
movement_revision = 0
symmetry = Symmetry.UNKNOWN
can_flip_x = True
can_flip_y = True
can_rotate = True
should_update_all_symmetric = False
symmetric_update_x = 0
symmetric_update_y = 0


def init(w: int, h: int):
    global width, height, tile_count, _board_mask, _not_left_col, _not_right_col
    global _tile_flags, _env_idx, _entity_id, _entity_type_idx, _entity_team_idx
    global _bm_et, _bm_team, _bm_env, _bm_occupied, _bm_walkable, _bm_seen, _bm_wall, _bm_blocked, _bm_visible
    global _bm_ore_ti, _bm_ore_ax, _bm_unreachable_harvesters, _bm_unreachable_ores
    global _bm_reachable, _bm_might_reach, _bm_floodfill_open_prev, _bm_enemy_core
    global _enemy_launcher_adj, _output_idx, _input_masks
    global conveyor_resources, conveyor_resources_last_seen
    global _input_chain_valid, _input_resource_masks, current_round
    global _feeds_turret_cache, _feeds_building_cache, _feeds_building_in_vision_cache
    global _sabotage_downstream_cache, _chain_terminal_cache, _chain_last_visible_cache
    global movement_revision, symmetry, can_flip_x, can_flip_y, can_rotate
    global should_update_all_symmetric, symmetric_update_x, symmetric_update_y

    width = w
    height = h
    tile_count = w * h
    _board_mask = (1 << tile_count) - 1
    left_col = 0
    right_col = 0
    for y in range(h):
        row_start = y * w
        left_col |= 1 << row_start
        right_col |= 1 << (row_start + w - 1)
    _not_left_col = _board_mask & ~left_col
    _not_right_col = _board_mask & ~right_col
    _tile_flags = array("I", [0]) * tile_count
    _env_idx = array("b", [-1]) * tile_count
    _entity_id = array("i", [0]) * tile_count
    _entity_type_idx = array("b", [-1]) * tile_count
    _entity_team_idx = array("b", [-1]) * tile_count
    _bm_et = [0] * len(EntityType)
    _bm_team = [0] * len(Team)
    _bm_env = [0] * len(Environment)
    _bm_occupied = 0
    _bm_walkable = _board_mask
    _bm_seen = 0
    _bm_wall = 0
    _bm_blocked = 0
    _bm_visible = 0
    _bm_ore_ti = 0
    _bm_ore_ax = 0
    _bm_unreachable_harvesters = 0
    _bm_unreachable_ores = 0
    _bm_reachable = 0
    _bm_might_reach = 0
    _bm_floodfill_open_prev = 0
    _bm_enemy_core = 0
    _enemy_launcher_adj = bytearray(tile_count)
    _output_idx = array("i", [-1]) * tile_count
    _input_masks = [0] * tile_count
    conveyor_resources = defaultdict(set)
    conveyor_resources_last_seen = defaultdict(dict)
    _input_chain_valid = bytearray(tile_count)
    _input_resource_masks = array("I", [0]) * tile_count
    current_round = 0
    _feeds_turret_cache = {}
    _feeds_building_cache = {}
    _feeds_building_in_vision_cache = {}
    _sabotage_downstream_cache = {}
    _chain_terminal_cache = {}
    _chain_last_visible_cache = {}
    movement_revision = 0
    symmetry = Symmetry.UNKNOWN
    can_flip_x = True
    can_flip_y = True
    can_rotate = True
    should_update_all_symmetric = False
    symmetric_update_x = 0
    symmetric_update_y = 0


def _idx(pos: Position) -> int:
    return pos.y * width + pos.x

def _idx_if_on_map(pos: Position) -> int | None:
    if not on_map(pos, width, height):
        return None
    return pos.y * width + pos.x

def _pos(idx: int) -> Position:
    return Position(idx % width, idx // width)

def pos_to_idx(pos: Position) -> int:
    return _idx(pos)

def idx_to_pos(idx: int) -> Position:
    return _pos(idx)

def _get_flag_idx(idx: int, flag: int) -> bool:
    return bool(_tile_flags[idx] & flag)

def _set_flag_idx(idx: int, flag: int):
    _tile_flags[idx] |= flag

def _clear_flag_idx(idx: int, flag: int):
    _tile_flags[idx] &= ~flag

def _set_tile_env_idx(idx: int, env: Environment):
    global _bm_seen, _bm_wall, _bm_walkable
    bit = 1 << idx
    prev_env_idx = _env_idx[idx]
    if prev_env_idx >= 0:
        _bm_env[prev_env_idx] &= ~bit
    env_i = _ENV_INT[env]
    _env_idx[idx] = env_i
    _bm_env[env_i] |= bit
    _set_flag_idx(idx, FLAG_SEEN)
    _bm_seen |= bit
    _clear_flag_idx(idx, FLAG_WALL | FLAG_ORE_TITANIUM | FLAG_ORE_AXIONITE)
    _bm_wall &= ~bit
    if env == Environment.WALL:
        _set_flag_idx(idx, FLAG_WALL)
        _bm_wall |= bit
    elif env == Environment.ORE_TITANIUM:
        _set_flag_idx(idx, FLAG_ORE_TITANIUM)
    elif env == Environment.ORE_AXIONITE:
        _set_flag_idx(idx, FLAG_ORE_AXIONITE)
    _bm_walkable = _board_mask & ~_bm_wall & ~_bm_blocked

def _sync_blocked_mask_idx(idx: int, prev_flags: int):
    global _bm_blocked, _bm_walkable
    bit = 1 << idx
    if prev_flags & FLAG_BLOCKED:
        _bm_blocked &= ~bit
    if _tile_flags[idx] & FLAG_BLOCKED:
        _bm_blocked |= bit
    _bm_walkable = _board_mask & ~_bm_wall & ~_bm_blocked

def _set_tile_entity_idx(idx: int, bid: int, etype: EntityType, team: Team):
    global _bm_occupied
    bit = 1 << idx
    prev_etype_idx = _entity_type_idx[idx]
    if prev_etype_idx >= 0:
        _bm_et[prev_etype_idx] &= ~bit
    prev_team_idx = _entity_team_idx[idx]
    if prev_team_idx >= 0:
        _bm_team[prev_team_idx] &= ~bit
    _entity_id[idx] = bid
    etype_i = _ET_INT[etype]
    team_i = _TM_INT[team]
    _entity_type_idx[idx] = etype_i
    _entity_team_idx[idx] = team_i
    _bm_et[etype_i] |= bit
    _bm_team[team_i] |= bit
    _bm_occupied |= bit

def _clear_tile_entity_idx(idx: int):
    global _bm_occupied
    bit = 1 << idx
    _entity_id[idx] = 0
    prev_etype_idx = _entity_type_idx[idx]
    if prev_etype_idx >= 0:
        _bm_et[prev_etype_idx] &= ~bit
    prev_team_idx = _entity_team_idx[idx]
    if prev_team_idx >= 0:
        _bm_team[prev_team_idx] &= ~bit
    _entity_type_idx[idx] = -1
    _entity_team_idx[idx] = -1
    _bm_occupied &= ~bit


def _is_visited_idx(idx: int) -> bool:
    return _get_flag_idx(idx, FLAG_SEEN)

def _get_tile_env_idx(idx: int) -> Environment | None:
    ei = _env_idx[idx]
    return None if ei < 0 else _INT_ENV[ei]

def _is_ore_idx(idx: int) -> bool:
    flags = _tile_flags[idx]
    return bool(flags & (FLAG_ORE_TITANIUM | FLAG_ORE_AXIONITE))

def _has_ore_harvester_idx(idx: int) -> bool:
    if _entity_type_idx[idx] != _IDX_HARVESTER:
        return False
    ei = _env_idx[idx]
    return ei == _IDX_ENV_ORE_TI or ei == _IDX_ENV_ORE_AX

def _has_adjacent_ally_conveyor_idx(idx: int, my_team: Team) -> bool:
    my_team_idx = _TM_INT[my_team]
    x = idx % width
    y = idx // width
    for dx, dy in _CARDINAL_OFFSETS:
        nx = x + dx
        ny = y + dy
        if not on_map_coords(nx, ny, width, height):
            continue
        nidx = ny * width + nx
        if _entity_type_idx[nidx] in _CONVEYOR_TYPE_IDXS and _entity_team_idx[nidx] == my_team_idx:
            if _output_idx[nidx] != idx:
                return True
    return False

def has_entity(pos: Position) -> bool:
    idx = _idx_if_on_map(pos)
    if idx is None:
        return False
    return _entity_id[idx] != 0

def get_tile_entity_id(pos: Position) -> int | None:
    idx = _idx_if_on_map(pos)
    if idx is None:
        return None
    bid = _entity_id[idx]
    return bid if bid != 0 else None

def get_tile_entity_type(pos: Position) -> EntityType | None:
    idx = _idx_if_on_map(pos)
    if idx is None:
        return None
    etype_i = _entity_type_idx[idx]
    return None if etype_i < 0 else _INT_ET[etype_i]

def get_tile_entity_team(pos: Position) -> Team | None:
    idx = _idx_if_on_map(pos)
    if idx is None:
        return None
    team_i = _entity_team_idx[idx]
    return None if team_i < 0 else _INT_TM[team_i]

def get_tile_env_code(pos: Position) -> int:
    idx = _idx_if_on_map(pos)
    if idx is None:
        return -1
    return _env_idx[idx]

def get_tile_entity_type_code(pos: Position) -> int:
    idx = _idx_if_on_map(pos)
    if idx is None:
        return -1
    return _entity_type_idx[idx]

def get_tile_entity_team_code(pos: Position) -> int:
    idx = _idx_if_on_map(pos)
    if idx is None:
        return -1
    return _entity_team_idx[idx]

def is_blocked_idx(idx: int) -> bool:
    return _get_flag_idx(idx, FLAG_BLOCKED)

def get_walkable_mask() -> int:
    return _bm_walkable

def get_visible_mask() -> int:
    return _bm_visible

def get_seen_mask() -> int:
    return _bm_seen

def get_titanium_ore_mask() -> int:
    return _bm_ore_ti

def get_axionite_ore_mask() -> int:
    return _bm_ore_ax

def get_entity_mask(etype: EntityType) -> int:
    return _bm_et[_ET_INT[etype]]

def get_team_mask(team: Team) -> int:
    return _bm_team[_TM_INT[team]]

def get_env_mask(env: Environment) -> int:
    return _bm_env[_ENV_INT[env]]

def get_occupied_mask() -> int:
    return _bm_occupied

def get_builder_standable_building_mask(team: Team) -> int:
    return (
        _bm_et[_IDX_CONVEYOR]
        | _bm_et[_IDX_ARMOURED_CONVEYOR]
        | _bm_et[_IDX_BRIDGE]
        | _bm_et[_IDX_SPLITTER]
        | _bm_et[_IDX_ROAD]
        | (_bm_et[_IDX_CORE] & _bm_team[_TM_INT[team]])
    )

def get_not_left_col_mask() -> int:
    return _not_left_col

def get_not_right_col_mask() -> int:
    return _not_right_col

def get_reachable_mask() -> int:
    return _bm_reachable

def get_might_reach_mask() -> int:
    return _bm_might_reach

def get_enemy_core_mask() -> int:
    return _bm_enemy_core

def is_reachable_idx(idx: int) -> bool:
    return bool(_bm_reachable & (1 << idx))

def is_reachable(pos: Position) -> bool:
    idx = _idx_if_on_map(pos)
    return idx is not None and bool(_bm_reachable & (1 << idx))

def is_confirmed_unreachable_idx(idx: int) -> bool:
    bit = 1 << idx
    return bool(_bm_seen & bit) and not bool(_bm_might_reach & bit)

def is_confirmed_unreachable(pos: Position) -> bool:
    idx = _idx_if_on_map(pos)
    return idx is not None and is_confirmed_unreachable_idx(idx)

def _expand_reach_mask(mask: int) -> int:
    horizontal = mask | ((mask & _not_right_col) << 1) | ((mask & _not_left_col) >> 1)
    return (horizontal | (horizontal << width) | (horizontal >> width)) & _board_mask

def _mark_enemy_core_3x3(center_idx: int):
    global _bm_enemy_core
    cx = center_idx % width
    cy = center_idx // width
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            ex = cx + dx
            ey = cy + dy
            if on_map_coords(ex, ey, width, height):
                _bm_enemy_core |= 1 << (ey * width + ex)

def _maybe_mark_enemy_core_from_symmetry(core_pos: Position | None):
    if symmetry == Symmetry.UNKNOWN:
        return
    if core_pos is None:
        return
    enemy_center = get_symmetric_pos(core_pos, symmetry)
    enemy_center_idx = _idx(enemy_center)
    _mark_enemy_core_3x3(enemy_center_idx)

def _update_reachability(my_pos_idx: int, my_team_idx: int):
    global _bm_reachable, _bm_floodfill_open_prev
    floodfill_open = _bm_seen & ~_bm_wall & ~_bm_enemy_core
    newly_open = floodfill_open & ~_bm_floodfill_open_prev

    my_bit = 1 << my_pos_idx
    our_buildings = _bm_team[my_team_idx] & ~_bm_enemy_core
    new_seeds = (my_bit | our_buildings) & ~_bm_reachable

    if newly_open or new_seeds:
        frontier = (new_seeds | (_expand_reach_mask(_bm_reachable) & newly_open)) & floodfill_open & ~_bm_reachable
        _bm_reachable |= frontier
        while frontier:
            expanded = _expand_reach_mask(frontier) & floodfill_open & ~_bm_reachable
            if not expanded:
                break
            _bm_reachable |= expanded
            frontier = expanded

    _bm_floodfill_open_prev = floodfill_open
    _compute_might_reach()

def _compute_might_reach():
    global _bm_might_reach
    may_open = _board_mask & ~_bm_wall & ~_bm_enemy_core
    might = _bm_reachable
    frontier = might
    while frontier:
        expanded = _expand_reach_mask(frontier) & may_open & ~might
        if not expanded:
            break
        might |= expanded
        frontier = expanded
    _bm_might_reach = might

def is_ally_barrier_idx(idx: int) -> bool:
    return _get_flag_idx(idx, FLAG_ALLY_BARRIER)

def is_ally_launcher_idx(idx: int) -> bool:
    return _get_flag_idx(idx, FLAG_ALLY_LAUNCHER)

def get_enemy_launcher_adj_count_idx(idx: int) -> int:
    return _enemy_launcher_adj[idx]

def get_enemy_launcher_adj_count(pos: Position) -> int:
    idx = _idx_if_on_map(pos)
    if idx is None:
        return 0
    return _enemy_launcher_adj[idx]

def _adjust_enemy_launcher_adj_idx(launcher_idx: int, delta: int):
    px = launcher_idx % width
    py = launcher_idx // width
    for d in DIRECTIONS:
        dx, dy = DELTAS[d]
        x = px + dx
        y = py + dy
        if not on_map_coords(x, y, width, height):
            continue
        idx = y * width + x
        if delta > 0:
            if _enemy_launcher_adj[idx] < 255:
                _enemy_launcher_adj[idx] += 1
        elif _enemy_launcher_adj[idx] > 0:
            _enemy_launcher_adj[idx] -= 1

def get_conveyor_output(pos: Position) -> Position | None:
    idx = _idx_if_on_map(pos)
    if idx is None:
        return None
    out_idx = _output_idx[idx]
    if out_idx < 0:
        return None
    return _pos(out_idx)

def has_conveyor_output(pos: Position) -> bool:
    idx = _idx_if_on_map(pos)
    if idx is None:
        return False
    return _output_idx[idx] >= 0

def _iter_mask_indices(mask: int):
    while mask:
        bit = mask & -mask
        yield bit.bit_length() - 1
        mask ^= bit

def _iter_mask_positions(mask: int):
    for idx in _iter_mask_indices(mask):
        yield _pos(idx)

def iter_conveyor_input_indices(pos: Position):
    return _iter_mask_indices(_input_masks[_idx(pos)])

def has_conveyor_inputs(pos: Position) -> bool:
    idx = _idx_if_on_map(pos)
    if idx is None:
        return False
    return bool(_input_masks[idx])

def get_conveyor_input_count(pos: Position) -> int:
    idx = _idx_if_on_map(pos)
    if idx is None:
        return 0
    return _input_masks[idx].bit_count()

def get_conveyor_input_positions(pos: Position) -> list[Position]:
    idx = _idx_if_on_map(pos)
    if idx is None:
        return []
    return list(_iter_mask_positions(_input_masks[idx]))

def iter_titanium_ores():
    return _iter_mask_positions(_bm_ore_ti)

def iter_axionite_ores():
    return _iter_mask_positions(_bm_ore_ax)

def is_titanium_ore(pos: Position) -> bool:
    idx = _idx_if_on_map(pos)
    if idx is None:
        return False
    return bool(_bm_ore_ti & (1 << idx))

def is_axionite_ore(pos: Position) -> bool:
    idx = _idx_if_on_map(pos)
    if idx is None:
        return False
    return bool(_bm_ore_ax & (1 << idx))

def is_wall(pos: Position) -> bool:
    idx = _idx_if_on_map(pos)
    if idx is None:
        return False
    return bool(_bm_wall & (1 << idx))

def add_unreachable_harvester(pos: Position):
    global _bm_unreachable_harvesters
    idx = _idx_if_on_map(pos)
    if idx is not None:
        _bm_unreachable_harvesters |= 1 << idx

def add_unreachable_ore(pos: Position):
    global _bm_unreachable_ores
    idx = _idx_if_on_map(pos)
    if idx is not None:
        _bm_unreachable_ores |= 1 << idx

def discard_unreachable_ore(pos: Position):
    global _bm_unreachable_ores
    idx = _idx_if_on_map(pos)
    if idx is not None:
        _bm_unreachable_ores &= ~(1 << idx)

def is_unreachable_harvester(pos: Position) -> bool:
    idx = _idx_if_on_map(pos)
    if idx is None:
        return False
    return bool(_bm_unreachable_harvesters & (1 << idx))

def is_unreachable_ore(pos: Position) -> bool:
    idx = _idx_if_on_map(pos)
    if idx is None:
        return False
    return bool(_bm_unreachable_ores & (1 << idx))

def _clear_runtime_caches():
    _feeds_turret_cache.clear()
    _feeds_building_cache.clear()
    _feeds_building_in_vision_cache.clear()
    _sabotage_downstream_cache.clear()
    _chain_terminal_cache.clear()
    _chain_last_visible_cache.clear()

def _finalize_local_entity_update(idx: int, prev_output_idx: int, prev_was_ore_harvester: bool):
    dirty_cache_positions: set[int] = set()
    new_output_idx = _output_idx[idx]
    if prev_output_idx != new_output_idx:
        if prev_output_idx >= 0:
            dirty_cache_positions.add(prev_output_idx)
        if new_output_idx >= 0:
            dirty_cache_positions.add(new_output_idx)

    new_is_ore_harvester = _has_ore_harvester_idx(idx)
    if prev_was_ore_harvester != new_is_ore_harvester:
        x = idx % width
        y = idx // width
        for dx, dy in _CARDINAL_OFFSETS:
            nx = x + dx
            ny = y + dy
            if on_map_coords(nx, ny, width, height):
                dirty_cache_positions.add(ny * width + nx)

    _clear_runtime_caches()
    if dirty_cache_positions:
        _recompute_input_chain_cache(dirty_cache_positions)

def on_local_destroy(pos: Position):
    global movement_revision
    idx = _idx_if_on_map(pos)
    if idx is None:
        return
    prev_flags = _tile_flags[idx]
    prev_output_idx_val = _output_idx[idx]
    prev_was_ore_harvester = _has_ore_harvester_idx(idx)
    if prev_output_idx_val >= 0:
        _remove_conveyor_tracking(pos)
    _clear_tile_entity_idx(idx)
    _tile_flags[idx] &= _CLEAR_ENTITY_FLAGS
    if _is_ore_idx(idx):
        discard_unreachable_ore(pos)
    _sync_blocked_mask_idx(idx, prev_flags)
    if ((_tile_flags[idx] ^ prev_flags) & FLAG_BLOCKED) != 0:
        movement_revision += 1
    _finalize_local_entity_update(idx, prev_output_idx_val, prev_was_ore_harvester)

def on_local_build(pos: Position, bid: int, etype: EntityType, team: Team, direction: Direction | None = None, output_target: Position | None = None):
    global movement_revision
    idx = _idx_if_on_map(pos)
    if idx is None:
        return
    prev_flags = _tile_flags[idx]
    prev_output_idx_val = _output_idx[idx]
    prev_was_ore_harvester = _has_ore_harvester_idx(idx)
    if prev_output_idx_val >= 0:
        _remove_conveyor_tracking(pos)

    _set_tile_entity_idx(idx, bid, etype, team)
    _tile_flags[idx] &= _CLEAR_ENTITY_FLAGS
    if _is_ore_idx(idx):
        discard_unreachable_ore(pos)
    if etype == EntityType.BARRIER:
        _tile_flags[idx] |= FLAG_ALLY_BARRIER
    elif etype == EntityType.LAUNCHER:
        _tile_flags[idx] |= FLAG_ALLY_LAUNCHER
    elif etype not in CONVEYOR_TYPES and etype not in (EntityType.ROAD, EntityType.CORE, EntityType.BARRIER):
        _tile_flags[idx] |= FLAG_BLOCKED

    if etype in CONVEYOR_TYPES:
        if etype == EntityType.BRIDGE:
            target = output_target
        elif direction is not None:
            target = pos.add(direction)
        else:
            target = None
        if target is not None and on_map(target, width, height):
            new_out = _idx(target)
            _output_idx[idx] = new_out
            _input_masks[new_out] |= 1 << idx

    _sync_blocked_mask_idx(idx, prev_flags)
    if ((_tile_flags[idx] ^ prev_flags) & FLAG_BLOCKED) != 0:
        movement_revision += 1
    _finalize_local_entity_update(idx, prev_output_idx_val, prev_was_ore_harvester)

def get_symmetric_idx(idx: int, sym: Symmetry) -> int:
    x = idx % width
    y = idx // width
    if sym == Symmetry.FLIP_X:
        return y * width + (width - 1 - x)
    elif sym == Symmetry.FLIP_Y:
        return (height - 1 - y) * width + x
    elif sym == Symmetry.ROTATE:
        return (height - 1 - y) * width + (width - 1 - x)
    return idx

def get_symmetric_pos(pos: Position, sym: Symmetry):
    return _pos(get_symmetric_idx(_idx(pos), sym))

def _set_symmetry(sym: Symmetry):
    global symmetry, should_update_all_symmetric, symmetric_update_x, symmetric_update_y
    if sym == Symmetry.UNKNOWN or symmetry == sym:
        return
    if symmetry == Symmetry.UNKNOWN:
        should_update_all_symmetric = True
        symmetric_update_x = 0
        symmetric_update_y = 0
    symmetry = sym

def _apply_env_idx(idx: int, env: Environment):
    global _bm_ore_ti, _bm_ore_ax
    _set_tile_env_idx(idx, env)
    bit = 1 << idx
    _bm_ore_ti &= ~bit
    _bm_ore_ax &= ~bit
    if env == Environment.ORE_TITANIUM:
        _bm_ore_ti |= bit
    elif env == Environment.ORE_AXIONITE:
        _bm_ore_ax |= bit

def check_symmetry(pos: Position, env: Environment):
    global can_flip_x, can_flip_y, can_rotate
    idx = _idx(pos)
    env_i = _ENV_INT[env]
    if can_flip_x:
        sym_env_i = _env_idx[get_symmetric_idx(idx, Symmetry.FLIP_X)]
        if sym_env_i >= 0 and sym_env_i != env_i:
            can_flip_x = False
    if can_flip_y:
        sym_env_i = _env_idx[get_symmetric_idx(idx, Symmetry.FLIP_Y)]
        if sym_env_i >= 0 and sym_env_i != env_i:
            can_flip_y = False
    if can_rotate:
        sym_env_i = _env_idx[get_symmetric_idx(idx, Symmetry.ROTATE)]
        if sym_env_i >= 0 and sym_env_i != env_i:
            can_rotate = False

def check_core_symmetry(pos: Position):
    global can_flip_x, can_flip_y, can_rotate
    if symmetry != Symmetry.UNKNOWN:
        return
    idx = _idx(pos)
    if can_flip_x:
        sym_etype_i = _entity_type_idx[get_symmetric_idx(idx, Symmetry.FLIP_X)]
        if sym_etype_i >= 0 and sym_etype_i != _IDX_CORE:
            can_flip_x = False
    if can_flip_y:
        sym_etype_i = _entity_type_idx[get_symmetric_idx(idx, Symmetry.FLIP_Y)]
        if sym_etype_i >= 0 and sym_etype_i != _IDX_CORE:
            can_flip_y = False
    if can_rotate:
        sym_etype_i = _entity_type_idx[get_symmetric_idx(idx, Symmetry.ROTATE)]
        if sym_etype_i >= 0 and sym_etype_i != _IDX_CORE:
            can_rotate = False

def update_symmetry():
    if symmetry != Symmetry.UNKNOWN:
        return
    key = (can_flip_x, can_flip_y, can_rotate)
    if key in SYMMETRY_MAPPING:
        _set_symmetry(SYMMETRY_MAPPING[key])

def update_all_symmetric_tiles(ct: Controller):
    global should_update_all_symmetric, symmetric_update_x, symmetric_update_y
    if not should_update_all_symmetric or symmetry == Symmetry.UNKNOWN:
        return

    w = width
    h = height

    log_time(ct, "Start of symmetric update")

    while symmetric_update_y < h:
        idx = symmetric_update_y * w + symmetric_update_x
        if idx % 50 == 0:
            budget = TURN_CPU_BUDGET_US - ct.get_cpu_time_elapsed() - END_TURN_RESERVE_US - CPU_SAFETY_MARGIN_US
            if budget <= 0:
                return

        if _env_idx[idx] < 0:
            sym_idx = get_symmetric_idx(idx, symmetry)
            sym_env_i = _env_idx[sym_idx]
            if sym_env_i >= 0:
                _apply_env_idx(idx, _INT_ENV[sym_env_i])

        symmetric_update_x += 1
        if symmetric_update_x == w:
            symmetric_update_x = 0
            symmetric_update_y += 1

    symmetric_update_x = 0
    symmetric_update_y = 0
    should_update_all_symmetric = False

    log_time(ct, "End of symmetric update")

def _resource_to_mask(resource: ResourceType | None) -> int:
    if resource == ResourceType.TITANIUM:
        return RESOURCE_MASK_TITANIUM
    if resource == ResourceType.RAW_AXIONITE:
        return RESOURCE_MASK_AXIONITE
    return 0


def _get_cached_resource_mask(pos: Position) -> int:
    return _input_resource_masks[_idx(pos)]

def _get_cached_resource_mask_idx(idx: int) -> int:
    return _input_resource_masks[idx]

def _set_cached_resource_mask(pos: Position, mask: int):
    _input_resource_masks[_idx(pos)] = mask

def _get_cached_chain_valid(pos: Position) -> bool:
    return bool(_input_chain_valid[_idx(pos)])

def _get_cached_chain_valid_idx(idx: int) -> bool:
    return bool(_input_chain_valid[idx])

def _set_cached_chain_valid(pos: Position, valid: bool):
    _input_chain_valid[_idx(pos)] = 1 if valid else 0

def input_chain_reaches_resource(pos: Position, resource: ResourceType) -> bool:
    return bool(_get_cached_resource_mask(pos) & _resource_to_mask(resource))

def input_chain_reaches_resource_idx(idx: int, resource: ResourceType) -> bool:
    return bool(_get_cached_resource_mask_idx(idx) & _resource_to_mask(resource))

def _record_conveyor_resource(pos: Position, resource: ResourceType):
    idx = _idx(pos)
    conveyor_resources[idx].add(resource)
    conveyor_resources_last_seen[idx][resource] = current_round

def get_recent_conveyor_resources(pos: Position, max_age: int = OBSERVED_RESOURCE_MAX_AGE) -> set[ResourceType]:
    recent = set()
    idx = _idx(pos)
    last_seen = conveyor_resources_last_seen.get(idx)
    if last_seen is None:
        return recent

    stale_resources = []
    for resource, seen_round in last_seen.items():
        if current_round - seen_round <= max_age:
            recent.add(resource)
        else:
            stale_resources.append(resource)

    if stale_resources:
        tracked = conveyor_resources.get(idx)
        for resource in stale_resources:
            del last_seen[resource]
            if tracked is not None:
                tracked.discard(resource)
        if tracked is not None and not tracked:
            conveyor_resources.pop(idx, None)
        if not last_seen:
            conveyor_resources_last_seen.pop(idx, None)

    return recent

def has_recent_conveyor_resource(pos: Position, resource: ResourceType, max_age: int = OBSERVED_RESOURCE_MAX_AGE) -> bool:
    last_seen = conveyor_resources_last_seen.get(_idx(pos))
    if last_seen is None:
        return False
    seen_round = last_seen.get(resource)
    return seen_round is not None and current_round - seen_round <= max_age

def has_recent_conveyor_resource_idx(idx: int, resource: ResourceType, max_age: int = OBSERVED_RESOURCE_MAX_AGE) -> bool:
    last_seen = conveyor_resources_last_seen.get(idx)
    if last_seen is None:
        return False
    seen_round = last_seen.get(resource)
    return seen_round is not None and current_round - seen_round <= max_age

def get_cached_conveyor_resources(pos: Position) -> set[ResourceType]:
    resources = set(get_recent_conveyor_resources(pos))
    mask = _get_cached_resource_mask(pos)
    if mask & RESOURCE_MASK_TITANIUM:
        resources.add(ResourceType.TITANIUM)
    if mask & RESOURCE_MASK_AXIONITE:
        resources.add(ResourceType.RAW_AXIONITE)
    return resources

def get_conveyor_resource_evidence(pos: Position, ct: Controller, my_pos: Position) -> set[ResourceType]:
    if is_in_vision(my_pos, pos):
        bid = get_tile_entity_id(pos)
        if bid is not None and get_tile_entity_type(pos) in CONVEYOR_TYPES:
            stored = ct.get_stored_resource(bid)
            if stored is not None:
                return {stored}
    return get_cached_conveyor_resources(pos)

def _get_conveyor_resource_state(pos: Position, ct: Controller, my_pos: Position, resource: ResourceType) -> int:
    if is_in_vision(my_pos, pos):
        bid = get_tile_entity_id(pos)
        if bid is not None and get_tile_entity_type(pos) in CONVEYOR_TYPES:
            stored = ct.get_stored_resource(bid)
            if stored is not None:
                return 1 if stored == resource else 2

    recent = get_recent_conveyor_resources(pos)
    mask = _get_cached_resource_mask(pos)
    has_match = resource in recent or bool(mask & _resource_to_mask(resource))

    if resource == ResourceType.TITANIUM:
        has_other = (
            ResourceType.RAW_AXIONITE in recent
            or ResourceType.REFINED_AXIONITE in recent
            or bool(mask & RESOURCE_MASK_AXIONITE)
        )
    elif resource == ResourceType.RAW_AXIONITE:
        has_other = (
            ResourceType.TITANIUM in recent
            or ResourceType.REFINED_AXIONITE in recent
            or bool(mask & RESOURCE_MASK_TITANIUM)
        )
    else:
        has_other = any(r != resource for r in recent)

    if has_other:
        return 2
    if has_match:
        return 1
    return 0

def _get_conveyor_resource_state_idx(idx: int, ct: Controller, resource: ResourceType) -> int:
    if (_bm_visible >> idx) & 1:
        bid = _entity_id[idx]
        if bid != 0 and _entity_type_idx[idx] in _CONVEYOR_TYPE_IDXS:
            stored = ct.get_stored_resource(bid)
            if stored is not None:
                return 1 if stored == resource else 2

    recent = conveyor_resources_last_seen.get(idx)
    mask = _input_resource_masks[idx]
    if resource == ResourceType.TITANIUM:
        has_match = (
            (recent is not None and ResourceType.TITANIUM in recent and current_round - recent[ResourceType.TITANIUM] <= OBSERVED_RESOURCE_MAX_AGE)
            or bool(mask & RESOURCE_MASK_TITANIUM)
        )
        has_other = (
            (recent is not None and (
                (ResourceType.RAW_AXIONITE in recent and current_round - recent[ResourceType.RAW_AXIONITE] <= OBSERVED_RESOURCE_MAX_AGE)
                or (ResourceType.REFINED_AXIONITE in recent and current_round - recent[ResourceType.REFINED_AXIONITE] <= OBSERVED_RESOURCE_MAX_AGE)
            ))
            or bool(mask & RESOURCE_MASK_AXIONITE)
        )
    elif resource == ResourceType.RAW_AXIONITE:
        has_match = (
            (recent is not None and ResourceType.RAW_AXIONITE in recent and current_round - recent[ResourceType.RAW_AXIONITE] <= OBSERVED_RESOURCE_MAX_AGE)
            or bool(mask & RESOURCE_MASK_AXIONITE)
        )
        has_other = (
            (recent is not None and (
                (ResourceType.TITANIUM in recent and current_round - recent[ResourceType.TITANIUM] <= OBSERVED_RESOURCE_MAX_AGE)
                or (ResourceType.REFINED_AXIONITE in recent and current_round - recent[ResourceType.REFINED_AXIONITE] <= OBSERVED_RESOURCE_MAX_AGE)
            ))
            or bool(mask & RESOURCE_MASK_TITANIUM)
        )
    else:
        has_match = False
        has_other = recent is not None and any(
            r != resource and current_round - seen_round <= OBSERVED_RESOURCE_MAX_AGE
            for r, seen_round in recent.items()
        )

    if has_other:
        return 2
    if has_match:
        return 1
    return 0

def infer_chain_resource_at_output(output_pos: Position, ct: Controller, my_pos: Position) -> ResourceType | None:
    live_resources = set()
    for input_idx in iter_conveyor_input_indices(output_pos):
        input_pos = _pos(input_idx)
        if not is_in_vision(my_pos, input_pos):
            continue
        bid = _entity_id[input_idx]
        if bid == 0 or _entity_type_idx[input_idx] not in _CONVEYOR_TYPE_IDXS:
            continue
        stored = ct.get_stored_resource(bid)
        if stored is not None:
            live_resources.add(stored)
    if len(live_resources) == 1:
        return next(iter(live_resources))
    if len(live_resources) > 1:
        return None

    cached_resources = set(get_cached_conveyor_resources(output_pos))
    for input_idx in iter_conveyor_input_indices(output_pos):
        input_pos = _pos(input_idx)
        cached_resources.update(get_cached_conveyor_resources(input_pos))
    if len(cached_resources) == 1:
        return next(iter(cached_resources))
    return None

def is_unserviced_harvester(pos: Position, my_team: Team) -> bool:
    idx = _idx_if_on_map(pos)
    if idx is None:
        return False
    return _has_ore_harvester_idx(idx) and not _has_adjacent_ally_conveyor_idx(idx, my_team)

def _collect_downstream_indices(dirty_roots: set[int]) -> list[int]:
    positions = []
    seen = set()
    stack = list(dirty_roots)
    while stack:
        idx = stack.pop()
        if idx in seen:
            continue
        seen.add(idx)
        positions.append(idx)
        next_idx = _output_idx[idx]
        if next_idx >= 0:
            stack.append(next_idx)
    return positions

def _compute_cached_resource_mask_idx(idx: int) -> int:
    mask = 0
    w = width
    h = height
    x = idx % w
    y = idx // w

    for dx, dy in _CARDINAL_OFFSETS:
        nx = x + dx
        ny = y + dy
        if not on_map_coords(nx, ny, w, h):
            continue
        nidx = ny * w + nx
        if _entity_type_idx[nidx] != _IDX_HARVESTER:
            continue
        ei = _env_idx[nidx]
        if ei == _IDX_ENV_ORE_TI:
            mask |= RESOURCE_MASK_TITANIUM
        elif ei == _IDX_ENV_ORE_AX:
            mask |= RESOURCE_MASK_AXIONITE

    for input_idx in _iter_mask_indices(_input_masks[idx]):
        if not _get_flag_idx(input_idx, FLAG_SEEN):
            continue
        if _entity_type_idx[input_idx] not in _CONVEYOR_TYPE_IDXS:
            continue
        if not _input_chain_valid[input_idx]:
            continue

        mask |= _input_resource_masks[input_idx]
        if mask == (RESOURCE_MASK_TITANIUM | RESOURCE_MASK_AXIONITE):
            break

    return mask

def _compute_cached_chain_valid_idx(idx: int) -> bool:
    has_valid_feeder = False
    w = width
    h = height
    x = idx % w
    y = idx // w

    for dx, dy in _CARDINAL_OFFSETS:
        nx = x + dx
        ny = y + dy
        if not on_map_coords(nx, ny, w, h):
            continue
        nidx = ny * w + nx
        if _entity_type_idx[nidx] != _IDX_HARVESTER:
            continue
        ei = _env_idx[nidx]
        if ei == _IDX_ENV_ORE_TI or ei == _IDX_ENV_ORE_AX:
            has_valid_feeder = True

    for input_idx in _iter_mask_indices(_input_masks[idx]):
        if not _get_flag_idx(input_idx, FLAG_SEEN):
            # Unvisited input — optimistically assume valid
            has_valid_feeder = True
            continue
        if _entity_type_idx[input_idx] not in _CONVEYOR_TYPE_IDXS:
            continue  # broken input, but other inputs may still be valid
        if not _input_chain_valid[input_idx]:
            continue  # invalid upstream, but other inputs may still be valid
        has_valid_feeder = True

    return has_valid_feeder

def _recompute_input_chain_cache(dirty_roots: set[int]):
    positions = _collect_downstream_indices(dirty_roots)
    if not positions:
        return

    pos_set = set(positions)

    # Build in-degree map (only counting edges within pos_set)
    in_degree: dict[int, int] = {idx: 0 for idx in positions}
    for idx in positions:
        for input_idx in _iter_mask_indices(_input_masks[idx]):
            if input_idx in pos_set:
                in_degree[idx] += 1

    # Topological sort via Kahn's algorithm
    queue = [idx for idx in positions if in_degree[idx] == 0]
    topo_order: list[int] = []
    qi = 0
    while qi < len(queue):
        idx = queue[qi]
        qi += 1
        topo_order.append(idx)
        out_idx = _output_idx[idx]
        if out_idx >= 0 and out_idx in pos_set:
            in_degree[out_idx] -= 1
            if in_degree[out_idx] == 0:
                queue.append(out_idx)

    # Positions not in topo_order are in cycles — mark invalid
    for idx in positions:
        if idx not in in_degree or in_degree[idx] != 0:
            pos = _pos(idx)
            _set_cached_chain_valid(pos, False)
            _set_cached_resource_mask(pos, 0)

    # Single pass in topological order (upstream first)
    for idx in topo_order:
        pos = _pos(idx)
        new_valid = _compute_cached_chain_valid_idx(idx)
        new_mask = _compute_cached_resource_mask_idx(idx) if new_valid else 0
        _set_cached_chain_valid(pos, new_valid)
        _set_cached_resource_mask(pos, new_mask)

def _remove_conveyor_tracking(pos: Position):
    idx = _idx(pos)
    old_output = _output_idx[idx]
    if old_output >= 0:
        _input_masks[old_output] &= ~(1 << idx)
    _output_idx[idx] = -1
    conveyor_resources.pop(idx, None)
    conveyor_resources_last_seen.pop(idx, None)

def update_vision(ct: Controller, core_pos: Position | None = None, enemy_core_pos: Position | None = None):
    import comms
    global _bm_visible, movement_revision, current_round, _bm_enemy_core
    log_time(ct, "Start of update vision")
    current_round = ct.get_current_round()
    _feeds_turret_cache.clear()
    _feeds_building_cache.clear()
    _feeds_building_in_vision_cache.clear()
    _sabotage_downstream_cache.clear()
    _chain_terminal_cache.clear()
    _chain_last_visible_cache.clear()
    my_team = ct.get_team()
    my_pos = ct.get_position()
    nearby = ct.get_nearby_tiles()
    dirty_cache_positions: set[int] = set()

    env_idx_grid = _env_idx
    entity_ids = _entity_id
    entity_type_idxs = _entity_type_idx
    entity_team_idxs = _entity_team_idx
    output_idx_arr = _output_idx
    input_masks_arr = _input_masks
    tile_flags_arr = _tile_flags
    w = width
    h = height
    nav_changed = False

    ct_get_tile_env = ct.get_tile_env
    ct_get_tile_building_id = ct.get_tile_building_id
    ct_get_entity_type = ct.get_entity_type
    ct_get_team = ct.get_team
    ct_get_marker_value = ct.get_marker_value
    ct_get_bridge_target = ct.get_bridge_target
    ct_get_direction = ct.get_direction
    ct_get_stored_resource = ct.get_stored_resource
    should_fill_symmetry = symmetry != Symmetry.UNKNOWN
    known_symmetry = symmetry
    visible_mask = 0
    my_team_idx = _TM_INT[my_team]

    log_time(ct, "After local variable assignment")

    for pos in nearby:
        x = pos.x
        y = pos.y
        idx = y * w + x
        visible_mask |= 1 << idx
        prev_output = output_idx_arr[idx]
        prev_was_enemy_launcher = entity_type_idxs[idx] == _IDX_LAUNCHER and entity_team_idxs[idx] != my_team_idx
        prev_was_ore_harvester = False
        if tile_flags_arr[idx] & FLAG_SEEN and entity_type_idxs[idx] == _IDX_HARVESTER:
            prev_env = env_idx_grid[idx]
            prev_was_ore_harvester = prev_env == _IDX_ENV_ORE_TI or prev_env == _IDX_ENV_ORE_AX

        ei = env_idx_grid[idx]
        prev_flags = tile_flags_arr[idx]
        if ei < 0:
            env = ct_get_tile_env(pos)
            _apply_env_idx(idx, env)
            ei = _ENV_INT[env]
            if should_fill_symmetry:
                sym_idx = get_symmetric_idx(idx, known_symmetry)
                if env_idx_grid[sym_idx] < 0:
                    _apply_env_idx(sym_idx, env)
            else:
                check_symmetry(pos, env)

        if ei == _IDX_ENV_WALL:
            _clear_tile_entity_idx(idx)
            tile_flags_arr[idx] = (tile_flags_arr[idx] & _CLEAR_ENTITY_FLAGS) | FLAG_BLOCKED
            _sync_blocked_mask_idx(idx, prev_flags)
            if ((tile_flags_arr[idx] ^ prev_flags) & (FLAG_BLOCKED | FLAG_WALL)) != 0:
                nav_changed = True
            continue

        bid = ct_get_tile_building_id(pos)
        if bid is not None:
            cached_bid = entity_ids[idx]
            if cached_bid != 0 and cached_bid == bid:
                etype_idx = entity_type_idxs[idx]
                team_idx = entity_team_idxs[idx]
                etype = _INT_ET[etype_idx]
                team = _INT_TM[team_idx]
            else:
                etype = ct_get_entity_type(bid)
                team = ct_get_team(bid)
                etype_idx = _ET_INT[etype]
                team_idx = _TM_INT[team]
            if etype == EntityType.MARKER:
                if team == my_team:
                    comms.read_marker(ct_get_marker_value(bid), pos, bid, current_round)
                bid = None
        if bid is not None:
            if etype == EntityType.CORE:
                if cached_bid != bid:
                    check_core_symmetry(pos)
                if team_idx != my_team_idx:
                    _bm_enemy_core |= 1 << idx
            assert etype is not None
            assert team is not None
            is_enemy_launcher = etype_idx == _IDX_LAUNCHER and team_idx != my_team_idx
            if prev_was_enemy_launcher != is_enemy_launcher:
                _adjust_enemy_launcher_adj_idx(idx, 1 if is_enemy_launcher else -1)
            _set_tile_entity_idx(idx, bid, etype, team)
            tile_flags_arr[idx] &= _CLEAR_ENTITY_FLAGS
            if _is_ore_idx(idx):
                if team_idx != my_team_idx and (etype_idx == _IDX_BARRIER or etype_idx == _IDX_ARMOURED_CONVEYOR):
                    add_unreachable_ore(pos)
                else:
                    discard_unreachable_ore(pos)
            if etype_idx == _IDX_BARRIER and team_idx == my_team_idx:
                tile_flags_arr[idx] |= FLAG_ALLY_BARRIER
            elif etype_idx == _IDX_LAUNCHER and team_idx == my_team_idx:
                tile_flags_arr[idx] |= FLAG_ALLY_LAUNCHER
            elif (
                (etype_idx == _IDX_CORE and team_idx != my_team_idx)
                or (etype_idx not in _CONVEYOR_TYPE_IDXS and etype_idx != _IDX_ROAD and etype_idx != _IDX_CORE and not (etype_idx == _IDX_BARRIER and team_idx == my_team_idx) and not (etype_idx == _IDX_LAUNCHER and team_idx == my_team_idx))
            ):
                tile_flags_arr[idx] |= FLAG_BLOCKED

            # Track conveyor outputs and resources
            if etype_idx in _CONVEYOR_TYPE_IDXS:
                if cached_bid != 0 and cached_bid == bid:
                    new_output = prev_output
                else:
                    new_out_pos = ct_get_bridge_target(bid) if etype == EntityType.BRIDGE else pos.add(ct_get_direction(bid))
                    if 0 <= new_out_pos.x < w and 0 <= new_out_pos.y < h:
                        new_output = new_out_pos.y * w + new_out_pos.x
                    else:
                        new_output = -1
                old_output = output_idx_arr[idx]
                if old_output != new_output:
                    if old_output >= 0:
                        input_masks_arr[old_output] &= ~(1 << idx)
                    output_idx_arr[idx] = new_output
                    if new_output >= 0:
                        input_masks_arr[new_output] |= 1 << idx
                resource = ct_get_stored_resource(bid)
                if resource is not None:
                    _record_conveyor_resource(pos, resource)
            elif output_idx_arr[idx] >= 0:
                _remove_conveyor_tracking(pos)
        else:
            if prev_was_enemy_launcher:
                _adjust_enemy_launcher_adj_idx(idx, -1)
            _clear_tile_entity_idx(idx)
            tile_flags_arr[idx] &= _CLEAR_ENTITY_FLAGS
            if _is_ore_idx(idx):
                discard_unreachable_ore(pos)
            if output_idx_arr[idx] >= 0:
                _remove_conveyor_tracking(pos)

        _sync_blocked_mask_idx(idx, prev_flags)
        if ((tile_flags_arr[idx] ^ prev_flags) & (FLAG_BLOCKED | FLAG_WALL)) != 0:
            nav_changed = True

        new_output = output_idx_arr[idx]
        if prev_output != new_output:
            if prev_output >= 0:
                dirty_cache_positions.add(prev_output)
            if new_output >= 0:
                dirty_cache_positions.add(new_output)

        new_is_ore_harvester = (
            entity_type_idxs[idx] == _IDX_HARVESTER
            and (env_idx_grid[idx] == _IDX_ENV_ORE_TI or env_idx_grid[idx] == _IDX_ENV_ORE_AX)
        )
        if prev_was_ore_harvester != new_is_ore_harvester:
            for dx, dy in _CARDINAL_OFFSETS:
                nx = x + dx
                ny = y + dy
                if not on_map_coords(nx, ny, w, h):
                    continue
                dirty_cache_positions.add(ny * w + nx)

    log_time(ct, "After processing nearby tiles")
    _bm_visible = visible_mask
    if comms.symmetry is not None and symmetry == Symmetry.UNKNOWN:
        _set_symmetry(comms.symmetry)
        log(f"symmetry from marker: {symmetry.name}")

    update_symmetry()

    log_time(ct, "After updating symmetry")

    _recompute_input_chain_cache(dirty_cache_positions)
    if nav_changed:
        movement_revision += 1

    log_time(ct, "After recomputing conveyor cache")

    if enemy_core_pos is not None:
        _mark_enemy_core_3x3(_idx(enemy_core_pos))
    else:
        _maybe_mark_enemy_core_from_symmetry(core_pos)
    _update_reachability(my_pos.y * w + my_pos.x, my_team_idx)
    # indicate_reachability(ct)

    log_time(ct, "After reachability update")

def _would_create_loop_idx(build_idx: int, out_idx: int) -> bool:
    cur_idx = _output_idx[out_idx]
    if cur_idx < 0:
        return False

    seen = {build_idx}
    while cur_idx >= 0:
        if cur_idx in seen:
            return cur_idx == build_idx
        seen.add(cur_idx)
        cur_idx = _output_idx[cur_idx]
    return False

def _follow_chain_terminal_idx(start_idx: int) -> int:
    cache = _chain_terminal_cache
    cached = cache.get(start_idx)
    if cached is not None:
        return cached

    cur_idx = start_idx
    path: list[int] = []
    seen: set[int] = set()
    cache_result = True
    while True:
        cached_cur = cache.get(cur_idx)
        if cached_cur is not None:
            result = cached_cur
            break
        if cur_idx in seen:
            result = start_idx  # cycle — don't cache
            cache_result = False
            break
        next_idx = _output_idx[cur_idx]
        if next_idx < 0:
            result = cur_idx
            break
        path.append(cur_idx)
        seen.add(cur_idx)
        cur_idx = next_idx

    if cache_result:
        for idx in path:
            cache[idx] = result
    return result

def _follow_chain_last_visible_idx(start_idx: int) -> int | None:
    cache = _chain_last_visible_cache
    cached = cache.get(start_idx, _CACHE_MISS)
    if cached is not _CACHE_MISS:
        return cached  # type: ignore[return-value]

    if not _is_visited_idx(start_idx):
        cache[start_idx] = None
        return None

    cur_idx = start_idx
    path: list[int] = []
    seen: set[int] = set()
    cache_result = True
    result: int | None
    while True:
        cached_cur = cache.get(cur_idx, _CACHE_MISS)
        if cached_cur is not _CACHE_MISS:
            result = cached_cur  # type: ignore[assignment]
            break
        if cur_idx in seen:
            result = start_idx  # cycle — don't cache
            cache_result = False
            break
        next_idx = _output_idx[cur_idx]
        if next_idx < 0:
            result = cur_idx
            break
        path.append(cur_idx)
        seen.add(cur_idx)
        if not _is_visited_idx(next_idx):
            result = cur_idx
            break
        cur_idx = next_idx

    if cache_result:
        for idx in path:
            cache[idx] = result
    return result

def get_feeder_idxs(output_idx_val: int) -> list[tuple[int, EntityType]]:
    feeders: list[tuple[int, EntityType]] = []

    x = output_idx_val % width
    y = output_idx_val // width
    for dx, dy in _CARDINAL_OFFSETS:
        nx = x + dx
        ny = y + dy
        if not on_map_coords(nx, ny, width, height):
            continue
        adj_idx = ny * width + nx
        if _entity_type_idx[adj_idx] != _IDX_HARVESTER:
            continue
        ei = _env_idx[adj_idx]
        if ei == _IDX_ENV_ORE_TI or ei == _IDX_ENV_ORE_AX:
            feeders.append((adj_idx, EntityType.HARVESTER))

    input_mask = _input_masks[output_idx_val]
    for input_idx in _iter_mask_indices(input_mask):
        if _is_visited_idx(input_idx):
            etype_i = _entity_type_idx[input_idx]
            if etype_i < 0 or etype_i not in _CONVEYOR_TYPE_IDXS:
                continue
            etype = _INT_ET[etype_i]
        else:
            etype = EntityType.CONVEYOR
        feeders.append((input_idx, etype))

    return feeders

def has_adjacent_harvester(pos: Position) -> bool:
    x = pos.x
    y = pos.y
    for dx, dy in _CARDINAL_OFFSETS:
        nx = x + dx
        ny = y + dy
        if not on_map_coords(nx, ny, width, height):
            continue
        if _entity_type_idx[ny * width + nx] == _IDX_HARVESTER:
            return True
    return False

def has_valid_input_chain(pos: Position) -> bool:
    return _get_cached_chain_valid(pos)

def has_valid_input_chain_idx(idx: int) -> bool:
    return _get_cached_chain_valid_idx(idx)

def get_chain_terminal_idx(idx: int) -> int:
    return _follow_chain_terminal_idx(idx)

def feeds_ally_building(pos: Position, my_team: Team) -> bool:
    return _feeds_ally_chain_idx(
        _idx(pos),
        my_team,
        _feeds_building_cache,
        lambda etype, team, cur: team == my_team,
    )

def feeds_ally_building_idx(idx: int, my_team: Team) -> bool:
    return _feeds_ally_chain_idx(
        idx,
        my_team,
        _feeds_building_cache,
        lambda etype, team, cur: team == my_team,
    )

def _feeds_ally_chain_idx(
    idx: int,
    my_team: Team,
    cache: dict[tuple[int, Team], bool],
    success_predicate,
    ct: Controller | None = None,
    my_pos: Position | None = None,
    core_pos: Position | None = None,
    require_visible: bool = False,
) -> bool:
    key = (idx, my_team)
    cached = cache.get(key)
    if cached is not None:
        return cached

    cur_idx = idx
    visited_idxs: set[int] = set()
    while _output_idx[cur_idx] >= 0:
        if cur_idx in visited_idxs:
            cache[key] = False
            return False
        visited_idxs.add(cur_idx)

        next_idx = _output_idx[cur_idx]
        if next_idx < 0:
            cache[key] = False
            return False

        cur_idx = next_idx
        if require_visible:
            assert ct is not None
            assert my_pos is not None
            cur = _pos(cur_idx)
            if not is_in_vision(my_pos, cur):
                cache[key] = False
                return False
            if is_core_tile(core_pos, cur):
                cache[key] = True
                return True

        if not _is_visited_idx(cur_idx):
            cache[key] = False
            return False

        etype_i = _entity_type_idx[cur_idx]
        if etype_i < 0:
            cache[key] = False
            return False

        etype = _INT_ET[etype_i]
        team = _INT_TM[_entity_team_idx[cur_idx]]
        cur = _pos(cur_idx)
        if success_predicate(etype, team, cur):
            cache[key] = True
            return True
        if etype_i not in _CONVEYOR_TYPE_IDXS:
            cache[key] = False
            return False

    cache[key] = False
    return False

def feeds_ally_building_in_vision(pos: Position, my_team: Team, ct: Controller, my_pos: Position, core_pos: Position | None = None) -> bool:
    return _feeds_ally_chain_idx(
        _idx(pos),
        my_team,
        _feeds_building_in_vision_cache,
        lambda etype, team, cur: team == my_team,
        ct=ct,
        my_pos=my_pos,
        core_pos=core_pos,
        require_visible=True,
    )

def feeds_ally_building_in_vision_idx(idx: int, my_team: Team, ct: Controller, my_pos: Position, core_pos: Position | None = None) -> bool:
    return _feeds_ally_chain_idx(
        idx,
        my_team,
        _feeds_building_in_vision_cache,
        lambda etype, team, cur: team == my_team,
        ct=ct,
        my_pos=my_pos,
        core_pos=core_pos,
        require_visible=True,
    )

def feeds_ally_turret(pos: Position, my_team: Team) -> bool:
    return _feeds_ally_chain_idx(
        _idx(pos),
        my_team,
        _feeds_turret_cache,
        lambda etype, team, cur: team == my_team and etype in TURRET_TYPES,
    )

def feeds_ally_turret_idx(idx: int, my_team: Team) -> bool:
    return _feeds_ally_chain_idx(
        idx,
        my_team,
        _feeds_turret_cache,
        lambda etype, team, cur: team == my_team and etype in TURRET_TYPES,
    )

def get_sabotage_downstream_priority(pos: Position, my_team: Team) -> int:
    key = (_idx(pos), my_team)
    cached = _sabotage_downstream_cache.get(key)
    if cached is not None:
        return cached

    cur = pos
    path_idxs: list[int] = []
    visited_idxs: set[int] = set()
    cache_result = True
    result = 1

    while has_conveyor_output(cur):
        cur_idx = _idx(cur)
        if cur_idx in visited_idxs:
            break
        visited_idxs.add(cur_idx)
        path_idxs.append(cur_idx)

        next_pos = get_conveyor_output(cur)
        if next_pos is None:
            break
        if not is_visited(next_pos):
            break

        next_idx = _idx(next_pos)
        etype_i = _entity_type_idx[next_idx]
        if etype_i < 0:
            result = 0
            break

        team_idx = _entity_team_idx[next_idx]
        if team_idx == _TM_INT[my_team]:
            result = 0
            break
        if etype_i == _IDX_CORE:
            result = 3
            break
        if etype_i in _TURRET_TYPE_IDXS:
            result = 2
            break
        if etype_i == _IDX_FOUNDRY:
            result = 1
            break
        if etype_i in _CONVEYOR_TYPE_IDXS:
            cur = next_pos
            continue

        result = 0
        break

    if cache_result:
        for path_idx in path_idxs:
            _sabotage_downstream_cache[(path_idx, my_team)] = result
    return result

def get_nearest_unserviced_harvester(pos: Position, ct: Controller, core_pos: Position | None = None) -> Position | None:
    my_team = ct.get_team()
    best_ti_dist = INF
    best_ti_core_dist = INF
    best_ti_idx = -1
    best_ax_dist = INF
    best_ax_core_dist = INF
    best_ax_idx = -1
    w = width
    px = pos.x
    py = pos.y
    core_x = core_pos.x if core_pos is not None else 0
    core_y = core_pos.y if core_pos is not None else 0
    for idx in _iter_mask_indices(_bm_ore_ti):
        if is_confirmed_unreachable_idx(idx):
            continue
        if _bm_unreachable_harvesters & (1 << idx):
            continue
        if _entity_type_idx[idx] != _IDX_HARVESTER:
            continue
        ei = _env_idx[idx]
        if ei != _IDX_ENV_ORE_TI and ei != _IDX_ENV_ORE_AX:
            continue
        x = idx % w
        y = idx // w
        dist = (px - x) * (px - x) + (py - y) * (py - y)
        core_dist = (x - core_x) * (x - core_x) + (y - core_y) * (y - core_y) if core_pos is not None else INF
        if dist > best_ti_dist or (dist == best_ti_dist and core_dist >= best_ti_core_dist):
            continue
        if _has_adjacent_ally_conveyor_idx(idx, my_team):
            continue
        best_ti_dist = dist
        best_ti_core_dist = core_dist
        best_ti_idx = idx

    if best_ti_idx >= 0:
        return _pos(best_ti_idx)

    for idx in _iter_mask_indices(_bm_ore_ax):
        if is_confirmed_unreachable_idx(idx):
            continue
        if _bm_unreachable_harvesters & (1 << idx):
            continue
        if _entity_type_idx[idx] != _IDX_HARVESTER:
            continue
        ei = _env_idx[idx]
        if ei != _IDX_ENV_ORE_TI and ei != _IDX_ENV_ORE_AX:
            continue
        x = idx % w
        y = idx // w
        dist = (px - x) * (px - x) + (py - y) * (py - y)
        core_dist = (x - core_x) * (x - core_x) + (y - core_y) * (y - core_y) if core_pos is not None else INF
        if dist > best_ax_dist or (dist == best_ax_dist and core_dist >= best_ax_core_dist):
            continue
        if _has_adjacent_ally_conveyor_idx(idx, my_team):
            continue
        best_ax_dist = dist
        best_ax_core_dist = core_dist
        best_ax_idx = idx
    if ct.get_global_resources()[0] >= 1500 and best_ax_idx >= 0:
        return _pos(best_ax_idx)
    return None

def is_visited(pos: Position) -> bool:
    return _get_flag_idx(_idx(pos), FLAG_SEEN)

def get_random_tile() -> Position:
    return Position(random.randint(0, width - 1), random.randint(0, height - 1))

def get_nearest_ore_without_harvester(pos: Position, ct: Controller, core_pos: Position | None = None) -> Position | None:
    global _bm_unreachable_ores
    best_ti_dist = INF
    best_ti_core_dist = INF
    best_ti_idx = -1
    best_ax_dist = INF
    best_ax_core_dist = INF
    best_ax_idx = -1
    w = width
    h = height
    px = pos.x
    py = pos.y
    core_x = core_pos.x if core_pos is not None else 0
    core_y = core_pos.y if core_pos is not None else 0

    def _has_adjacent_opposite_resource_chain_idx(ore_idx: int, resource: ResourceType | None) -> bool:
        if resource == ResourceType.TITANIUM:
            opposite = ResourceType.RAW_AXIONITE
        elif resource == ResourceType.RAW_AXIONITE:
            opposite = ResourceType.TITANIUM
        else:
            return False

        if _entity_type_idx[ore_idx] in _CONVEYOR_TYPE_IDXS:
            if _get_conveyor_resource_state_idx(ore_idx, ct, opposite) == 1:
                return True

        x = ore_idx % w
        y = ore_idx // w
        for dx, dy in _CARDINAL_OFFSETS:
            nx = x + dx
            ny = y + dy
            if not on_map_coords(nx, ny, w, h):
                continue
            adj_idx = ny * w + nx
            if not _get_flag_idx(adj_idx, FLAG_SEEN):
                continue
            if _entity_type_idx[adj_idx] not in _CONVEYOR_TYPE_IDXS:
                continue
            if _get_conveyor_resource_state_idx(adj_idx, ct, opposite) == 1:
                return True
        return False

    for idx in _iter_mask_indices(_bm_ore_ti):
        bit = 1 << idx
        if is_confirmed_unreachable_idx(idx):
            continue
        if (_bm_unreachable_ores | _bm_unreachable_harvesters) & bit:
            continue
        if _entity_type_idx[idx] == _IDX_HARVESTER:
            continue
        if _has_adjacent_opposite_resource_chain_idx(idx, ResourceType.TITANIUM):
            _bm_unreachable_ores |= bit
            continue
        x = idx % w
        y = idx // w
        dist = (px - x) * (px - x) + (py - y) * (py - y)
        core_dist = (x - core_x) * (x - core_x) + (y - core_y) * (y - core_y) if core_pos is not None else INF
        if dist < best_ti_dist or (dist == best_ti_dist and core_dist < best_ti_core_dist):
            best_ti_dist = dist
            best_ti_core_dist = core_dist
            best_ti_idx = idx

    if best_ti_idx >= 0:
        return _pos(best_ti_idx)

    for idx in _iter_mask_indices(_bm_ore_ax):
        bit = 1 << idx
        if is_confirmed_unreachable_idx(idx):
            continue
        if (_bm_unreachable_ores | _bm_unreachable_harvesters) & bit:
            continue
        if _entity_type_idx[idx] == _IDX_HARVESTER:
            continue
        if _has_adjacent_opposite_resource_chain_idx(idx, ResourceType.RAW_AXIONITE):
            _bm_unreachable_ores |= bit
            continue
        x = idx % w
        y = idx // w
        dist = (px - x) * (px - x) + (py - y) * (py - y)
        core_dist = (x - core_x) * (x - core_x) + (y - core_y) * (y - core_y) if core_pos is not None else INF
        if dist < best_ax_dist or (dist == best_ax_dist and core_dist < best_ax_core_dist):
            best_ax_dist = dist
            best_ax_core_dist = core_dist
            best_ax_idx = idx

    if best_ax_idx >= 0 and ct.get_global_resources()[0] >= 1500:
        return _pos(best_ax_idx)
    return None

def get_nearest_titanium_ore(pos: Position) -> Position | None:
    best = None
    best_dist = INF
    for ti_pos in iter_titanium_ores():
        if is_confirmed_unreachable(ti_pos):
            continue
        dist = pos.distance_squared(ti_pos)
        if dist < best_dist:
            best_dist = dist
            best = ti_pos
    return best

def tag_conveyor_resource(pos: Position, resource: ResourceType):
    _record_conveyor_resource(pos, resource)

def find_nearest_conveyor_with_resource(pos: Position, resource: ResourceType, my_team: Team | None = None, target_foundry: Position | None = None) -> Position | None:
    best = None
    best_dist = INF
    for conv_idx in tuple(conveyor_resources):
        if is_confirmed_unreachable_idx(conv_idx):
            continue
        conv_pos = _pos(conv_idx)
        if not has_recent_conveyor_resource(conv_pos, resource):
            continue
        if my_team is not None and feeds_other_ally_foundry(conv_pos, my_team, target_foundry):
            continue
        dist = pos.distance_squared(conv_pos)
        if dist < best_dist:
            best_dist = dist
            best = conv_pos
    return best

def find_nearest_conveyor_with_resource_idx(pos_idx: int, resource: ResourceType, my_team: Team | None = None, target_foundry_idx: int | None = None) -> int | None:
    best_idx = None
    best_dist = INF
    px = pos_idx % width
    py = pos_idx // width
    for conv_idx in tuple(conveyor_resources):
        if is_confirmed_unreachable_idx(conv_idx):
            continue
        if not has_recent_conveyor_resource_idx(conv_idx, resource):
            continue
        if my_team is not None and feeds_other_ally_foundry_idx(conv_idx, my_team, target_foundry_idx):
            continue
        cx = conv_idx % width
        cy = conv_idx // width
        dx = px - cx
        dy = py - cy
        dist = dx * dx + dy * dy
        if dist < best_dist:
            best_dist = dist
            best_idx = conv_idx
    return best_idx

def feeds_other_ally_foundry(pos: Position, my_team: Team, target_foundry: Position | None) -> bool:
    return feeds_other_ally_foundry_idx(
        _idx(pos),
        my_team,
        None if target_foundry is None else _idx(target_foundry),
    )

def feeds_other_ally_foundry_idx(idx: int, my_team: Team, target_foundry_idx: int | None) -> bool:
    terminal_idx = _follow_chain_terminal_idx(idx)
    if terminal_idx < 0 or not _is_visited_idx(terminal_idx):
        return False
    etype_i = _entity_type_idx[terminal_idx]
    return (
        etype_i == _IDX_FOUNDRY
        and _entity_team_idx[terminal_idx] == _TM_INT[my_team]
        and terminal_idx != target_foundry_idx
    )

def is_single_input_foundry(pos: Position, my_team) -> bool:
    idx = _idx(pos)
    return is_single_input_foundry_idx(idx, my_team)

def is_single_input_foundry_idx(idx: int, my_team) -> bool:
    if _entity_type_idx[idx] != _IDX_FOUNDRY or _entity_team_idx[idx] != _TM_INT[my_team]:
        return False
    input_idxs = tuple(_iter_mask_indices(_input_masks[idx]))
    if len(input_idxs) > 1:
        return False
    return not any(
        has_recent_conveyor_resource_idx(input_idx, ResourceType.TITANIUM)
        for input_idx in input_idxs
    )

def find_single_input_foundry(core_pos: Position, my_team) -> Position | None:
    for idx in get_foundry_position_idxs(core_pos, width, height):
        pos = _pos(idx)
        if is_single_input_foundry(pos, my_team):
            return pos
    return None

def is_ore(pos: Position) -> bool:
    return _is_ore_idx(_idx(pos))

def is_adjacent_to_opposite_ore(pos: Position, resource: ResourceType | None) -> bool:
    return is_adjacent_to_opposite_ore_idx(_idx(pos), resource)

def is_adjacent_to_opposite_ore_idx(idx: int, resource: ResourceType | None) -> bool:
    if resource is None:
        return False
    if resource == ResourceType.RAW_AXIONITE:
        opposite_mask = _bm_ore_ti
    elif resource == ResourceType.TITANIUM:
        opposite_mask = _bm_ore_ax
    else:
        return False
    x = idx % width
    y = idx // width
    for dx, dy in _CARDINAL_OFFSETS:
        nx = x + dx
        ny = y + dy
        if not on_map_coords(nx, ny, width, height):
            continue
        if opposite_mask & (1 << (ny * width + nx)):
            return True
    return False

def has_adjacent_opposite_resource_chain(ore_pos: Position, resource: ResourceType | None, ct: Controller, my_pos: Position) -> bool:
    if resource == ResourceType.TITANIUM:
        opposite = ResourceType.RAW_AXIONITE
    elif resource == ResourceType.RAW_AXIONITE:
        opposite = ResourceType.TITANIUM
    else:
        return False

    ore_idx = _idx(ore_pos)
    if _entity_type_idx[ore_idx] in _CONVEYOR_TYPE_IDXS:
        if _get_conveyor_resource_state(ore_pos, ct, my_pos, opposite) == 1:
            return True

    for d in CARDINAL_DIRECTIONS:
        adj = ore_pos.add(d)
        if not on_map(adj, width, height) or not is_visited(adj):
            continue
        adj_idx = _idx(adj)
        if _entity_type_idx[adj_idx] not in _CONVEYOR_TYPE_IDXS:
            continue
        if _get_conveyor_resource_state(adj, ct, my_pos, opposite) == 1:
            return True
    return False

def has_conflict(resource: ResourceType | None, pos: Position, ct: Controller) -> bool:
    return has_conflict_idx(resource, _idx(pos), ct)

def has_conflict_idx(resource: ResourceType | None, idx: int, ct: Controller) -> bool:
    if resource is None:
        return False
    return _get_conveyor_resource_state_idx(idx, ct, resource) == 2

def has_input_conflict(resource: ResourceType | None, pos: Position, ct: Controller) -> bool:
    return has_input_conflict_idx(resource, _idx(pos), ct)

def has_input_conflict_idx(resource: ResourceType | None, idx: int, ct: Controller) -> bool:
    if resource is None:
        return False
    for input_idx in _iter_mask_indices(_input_masks[idx]):
        if _get_conveyor_resource_state_idx(input_idx, ct, resource) == 2:
            return True
    return False

def _make_dist_fns(terminal_positions_xy, core_pos):
    if terminal_positions_xy:
        def _dist_pos(pos):
            px, py = pos
            best = INF
            for tx, ty in terminal_positions_xy:
                dx = px - tx
                dy = py - ty
                d = dx * dx + dy * dy
                if d < best:
                    best = d
            return best
        def _dist_xy(x, y):
            best = INF
            for tx, ty in terminal_positions_xy:
                dx = x - tx
                dy = y - ty
                d = dx * dx + dy * dy
                if d < best:
                    best = d
            return best
    else:
        cx, cy = core_pos
        def _dist_pos(pos):
            dx = pos.x - cx
            dy = pos.y - cy
            return dx * dx + dy * dy
        def _dist_xy(x, y):
            dx = x - cx
            dy = y - cy
            return dx * dx + dy * dy
    return _dist_pos, _dist_xy

def _terminal_xy_from_idx_set(end_idx_set: set[int] | None, w: int) -> tuple[tuple[int, int], ...] | None:
    if not end_idx_set:
        return None
    return tuple((idx % w, idx // w) for idx in end_idx_set)

def _score_output_candidate_idx(adj_idx, adj_x, adj_y, dist, build_dist,
                                my_team, resource, ct, my_pos, core_pos, end_idx_set,
                                dist_to_terminal, check_splitter_dir=None):
    adj_label = f"({adj_x}, {adj_y})"
    etype_i = _entity_type_idx[adj_idx]
    if etype_i < 0:
        if has_input_conflict_idx(resource, adj_idx, ct):
            log(f"    {adj_label}: skip empty opposite-resource input")
            return None
        log(f"    {adj_label}: empty dist^2={dist}")
        return (dist, False)

    eteam_idx = _entity_team_idx[adj_idx]
    my_team_idx = _TM_INT[my_team] if my_team is not None else -1
    etype = _INT_ET[etype_i]
    eteam = _INT_TM[eteam_idx]

    if etype_i in _CONVEYOR_TYPE_IDXS and my_team is not None and eteam_idx == my_team_idx:
        if check_splitter_dir is not None and etype_i == _IDX_SPLITTER:
            splitter_output = _output_idx[adj_idx]
            if splitter_output >= 0:
                splitter_dir = DELTA_TO_DIRECTION[((splitter_output % width) - adj_x, (splitter_output // width) - adj_y)]
                if check_splitter_dir != splitter_dir:
                    log(f"    {adj_label}: skip ally splitter facing {splitter_dir}")
                    return None

        if has_conflict_idx(resource, adj_idx, ct):
            log(f"    {adj_label}: skip ally {etype} wrong/no resource")
            return None

        ally_output = _output_idx[adj_idx]
        ally_output_pos = _pos(ally_output) if ally_output >= 0 else None
        if ally_output_pos is not None and dist_to_terminal(ally_output_pos) >= build_dist:
            log(f"    {adj_label}: skip ally {etype} output not closer")
            return None

        terminal_idx = _follow_chain_terminal_idx(adj_idx)
        terminal = _pos(terminal_idx)
        if end_idx_set is not None and terminal_idx not in end_idx_set:
            is_core = core_pos is not None and abs(terminal.x - core_pos.x) <= 1 and abs(terminal.y - core_pos.y) <= 1
            if is_core or (on_map(terminal, width, height) and has_entity(terminal)):
                log(f"    {adj_label}: skip ally {etype} wrong terminal {terminal}")
                return None

        effective_idx = _follow_chain_last_visible_idx(adj_idx)
        if effective_idx is None:
            log(f"    {adj_label}: skip ally {etype} chain leaves vision")
            return None

        effective_pos = _pos(effective_idx)
        eff_dist = dist_to_terminal(effective_pos)
        is_fallback = resource == ResourceType.TITANIUM and ResourceType.TITANIUM in get_conveyor_resource_evidence(Position(adj_x, adj_y), ct, my_pos)
        if is_fallback:
            log(f"    {adj_label}: fallback ally {etype} observed titanium")
        else:
            log(f"    {adj_label}: chain ally {etype} eff_dist^2={eff_dist}")
        return (eff_dist, is_fallback)

    if etype_i == _IDX_MARKER or (etype_i == _IDX_ROAD and my_team is not None and eteam_idx == my_team_idx):
        if has_input_conflict_idx(resource, adj_idx, ct):
            log(f"    {adj_label}: skip road/marker opposite-resource input")
            return None
        log(f"    {adj_label}: road dist^2={dist}")
        return (dist, False)

    log(f"    {adj_label}: skip occupied by {eteam} {etype}")
    return None

def _get_best_output_with_fallback(build_pos: Position, core_pos: Position | None, ct: Controller, my_pos: Position,
                                   offsets, my_team: Team | None = None, end_positions: set | None = None,
                                   end_position_idxs: set[int] | None = None,
                                   resource: ResourceType | None = None, check_splitter: bool = False,
                                   allow_far_terminals: bool = False, label: str = "output",
                                   forbidden_output_mask: int = 0,
                                   strict_reachability: bool = False) -> tuple[Position | None, bool]:
    if core_pos is None:
        return (None, False)

    if end_position_idxs is None and end_positions is not None:
        end_position_idxs = {_idx(p) for p in end_positions}
    terminal_positions_xy = _terminal_xy_from_idx_set(end_position_idxs, width)
    dist_to_terminal, dist_to_terminal_xy = _make_dist_fns(terminal_positions_xy, core_pos)
    build_dist = dist_to_terminal_xy(build_pos.x, build_pos.y)
    best_terminal = None
    best_terminal_dist = INF
    best_next = None
    best_next_dist = INF
    best_fallback = None
    best_fallback_dist = INF
    build_idx = _idx(build_pos)
    end_idx_set = end_position_idxs
    w = width
    h = height
    log(f"  {label}: build={build_pos} core={core_pos} term_dist²={build_dist} res={resource}")

    for dx, dy in offsets:
        x = build_pos.x + dx
        y = build_pos.y + dy
        if not on_map_coords(x, y, w, h):
            continue
        adj_idx = y * w + x
        if is_confirmed_unreachable_idx(adj_idx):
            continue
        if strict_reachability and not is_reachable_idx(adj_idx):
            continue
        if forbidden_output_mask & (1 << adj_idx):
            continue
        dist = dist_to_terminal_xy(x, y)
        if not allow_far_terminals and dist >= build_dist:
            continue
        if _would_create_loop_idx(build_idx, adj_idx):
            continue

        if _is_ore_idx(adj_idx):
            continue

        is_terminal = (adj_idx in end_idx_set) if end_idx_set is not None else (core_pos is not None and abs(x - core_pos.x) <= 1 and abs(y - core_pos.y) <= 1)
        if is_terminal:
            if dist < best_terminal_dist:
                best_terminal_dist = dist
                best_terminal = Position(x, y)
            continue

        if allow_far_terminals and dist >= build_dist:
            continue
        if is_adjacent_to_opposite_ore_idx(adj_idx, resource):
            continue
        if not _is_visited_idx(adj_idx):
            continue
        if _env_idx[adj_idx] == _IDX_ENV_WALL:
            continue

        splitter_dir = DELTA_TO_DIRECTION[(dx, dy)] if check_splitter else None
        candidate = _score_output_candidate_idx(
            adj_idx, x, y, dist, build_dist,
            my_team, resource, ct, my_pos, core_pos, end_idx_set,
            dist_to_terminal, check_splitter_dir=splitter_dir,
        )
        if candidate is None:
            continue
        eff_dist, is_fallback = candidate
        if is_fallback:
            if eff_dist < best_fallback_dist:
                best_fallback_dist = eff_dist
                best_fallback = Position(x, y)
        elif eff_dist < best_next_dist:
            best_next_dist = eff_dist
            best_next = Position(x, y)

    result = best_terminal or best_next or best_fallback
    is_fallback = result is not None and best_terminal is None and best_next is None
    log(f"  {label} result: {result}")
    return (result, is_fallback)

def _get_best_output(build_pos: Position, core_pos: Position | None, ct: Controller, my_pos: Position,
                     offsets, my_team: Team | None = None, end_positions: set | None = None,
                     end_position_idxs: set[int] | None = None,
                     resource: ResourceType | None = None, check_splitter: bool = False,
                     allow_far_terminals: bool = False, label: str = "output",
                     forbidden_output_mask: int = 0,
                     strict_reachability: bool = False) -> Position | None:
    result, _ = _get_best_output_with_fallback(
        build_pos, core_pos, ct, my_pos, offsets,
        my_team=my_team, end_positions=end_positions, end_position_idxs=end_position_idxs,
        resource=resource, check_splitter=check_splitter,
        allow_far_terminals=allow_far_terminals, label=label,
        forbidden_output_mask=forbidden_output_mask, strict_reachability=strict_reachability,
    )
    return result

def get_best_conveyor_output(build_pos: Position, core_pos: Position | None, ct: Controller, my_pos: Position, my_team: Team | None = None, end_positions: set | None = None, resource: ResourceType | None = None, forbidden_output_mask: int = 0) -> tuple[Direction, Position] | None:
    result = _get_best_output(build_pos, core_pos, ct, my_pos, _CARDINAL_OFFSETS,
                               my_team=my_team, end_positions=end_positions,
                               resource=resource, check_splitter=True,
                               allow_far_terminals=False, label="conv_output", forbidden_output_mask=forbidden_output_mask)
    if result is None:
        return None
    return (build_pos.direction_to(result), result)

def get_best_conveyor_output_idx(build_pos: Position, core_pos: Position | None, ct: Controller, my_pos: Position, my_team: Team | None = None, end_position_idxs: set[int] | None = None, resource: ResourceType | None = None, forbidden_output_mask: int = 0) -> tuple[Direction, Position] | None:
    result = _get_best_output(build_pos, core_pos, ct, my_pos, _CARDINAL_OFFSETS,
                               my_team=my_team, end_position_idxs=end_position_idxs,
                               resource=resource, check_splitter=True,
                               allow_far_terminals=False, label="conv_output", forbidden_output_mask=forbidden_output_mask)
    if result is None:
        return None
    return (build_pos.direction_to(result), result)

def get_best_bridge_output(bridge_pos: Position, core_pos: Position | None, ct: Controller, my_pos: Position, my_team: Team | None = None, end_positions: set | None = None, resource: ResourceType | None = None, forbidden_output_mask: int = 0) -> Position | None:
    return _get_best_output(bridge_pos, core_pos, ct, my_pos, _BRIDGE_OFFSETS,
                             my_team=my_team, end_positions=end_positions,
                             resource=resource, check_splitter=False,
                             allow_far_terminals=True, label="bridge_output", forbidden_output_mask=forbidden_output_mask,
                             strict_reachability=not OPTIMISTIC_REACHABILITY)

def get_best_bridge_output_idx(bridge_pos: Position, core_pos: Position | None, ct: Controller, my_pos: Position, my_team: Team | None = None, end_position_idxs: set[int] | None = None, resource: ResourceType | None = None, forbidden_output_mask: int = 0) -> Position | None:
    return _get_best_output(bridge_pos, core_pos, ct, my_pos, _BRIDGE_OFFSETS,
                             my_team=my_team, end_position_idxs=end_position_idxs,
                             resource=resource, check_splitter=False,
                             allow_far_terminals=True, label="bridge_output", forbidden_output_mask=forbidden_output_mask,
                             strict_reachability=not OPTIMISTIC_REACHABILITY)

def get_best_conveyor_output_with_fallback(build_pos: Position, core_pos: Position | None, ct: Controller, my_pos: Position, my_team: Team | None = None, end_positions: set | None = None, resource: ResourceType | None = None, forbidden_output_mask: int = 0) -> tuple[tuple[Direction, Position] | None, bool]:
    result, is_fallback = _get_best_output_with_fallback(
        build_pos, core_pos, ct, my_pos, _CARDINAL_OFFSETS,
        my_team=my_team, end_positions=end_positions,
        resource=resource, check_splitter=True,
        allow_far_terminals=False, label="conv_output", forbidden_output_mask=forbidden_output_mask,
    )
    if result is None:
        return (None, False)
    return ((build_pos.direction_to(result), result), is_fallback)

def get_best_conveyor_output_with_fallback_idx(build_pos: Position, core_pos: Position | None, ct: Controller, my_pos: Position, my_team: Team | None = None, end_position_idxs: set[int] | None = None, resource: ResourceType | None = None, forbidden_output_mask: int = 0) -> tuple[tuple[Direction, Position] | None, bool]:
    result, is_fallback = _get_best_output_with_fallback(
        build_pos, core_pos, ct, my_pos, _CARDINAL_OFFSETS,
        my_team=my_team, end_position_idxs=end_position_idxs,
        resource=resource, check_splitter=True,
        allow_far_terminals=False, label="conv_output", forbidden_output_mask=forbidden_output_mask,
    )
    if result is None:
        return (None, False)
    return ((build_pos.direction_to(result), result), is_fallback)

def get_best_bridge_output_with_fallback(bridge_pos: Position, core_pos: Position | None, ct: Controller, my_pos: Position, my_team: Team | None = None, end_positions: set | None = None, resource: ResourceType | None = None, forbidden_output_mask: int = 0) -> tuple[Position | None, bool]:
    return _get_best_output_with_fallback(
        bridge_pos, core_pos, ct, my_pos, _BRIDGE_OFFSETS,
        my_team=my_team, end_positions=end_positions,
        resource=resource, check_splitter=False,
        allow_far_terminals=True, label="bridge_output", forbidden_output_mask=forbidden_output_mask,
        strict_reachability=not OPTIMISTIC_REACHABILITY,
    )

def get_best_bridge_output_with_fallback_idx(bridge_pos: Position, core_pos: Position | None, ct: Controller, my_pos: Position, my_team: Team | None = None, end_position_idxs: set[int] | None = None, resource: ResourceType | None = None, forbidden_output_mask: int = 0) -> tuple[Position | None, bool]:
    return _get_best_output_with_fallback(
        bridge_pos, core_pos, ct, my_pos, _BRIDGE_OFFSETS,
        my_team=my_team, end_position_idxs=end_position_idxs,
        resource=resource, check_splitter=False,
        allow_far_terminals=True, label="bridge_output", forbidden_output_mask=forbidden_output_mask,
        strict_reachability=not OPTIMISTIC_REACHABILITY,
    )

def indicate_entity_map(ct: Controller, my_team: Team):
    return
    _UNIT_TYPES = (EntityType.CORE, EntityType.BUILDER_BOT, *TURRET_TYPES, EntityType.LAUNCHER)
    for idx in range(tile_count):
        entity_id_val = _entity_id[idx]
        if entity_id_val == 0:
            continue
        etype = _INT_ET[_entity_type_idx[idx]]
        team = _INT_TM[_entity_team_idx[idx]]
        x = idx % width
        y = idx // width
        if etype == EntityType.ROAD or etype == EntityType.MARKER:
            continue
        pos = Position(x, y)
        if team != my_team:
            if etype in _UNIT_TYPES:
                pass # ct.draw_indicator_dot(pos, 255, 0, 0)      # red
            elif etype in CONVEYOR_TYPES:
                pass # ct.draw_indicator_dot(pos, 255, 165, 0)    # orange
            else:
                pass # ct.draw_indicator_dot(pos, 255, 255, 0)    # yellow
        else:
            if etype in _UNIT_TYPES:
                pass # ct.draw_indicator_dot(pos, 0, 255, 0)      # green
            elif etype in CONVEYOR_TYPES:
                pass # ct.draw_indicator_dot(pos, 0, 100, 255)    # blue
            else:
                pass # ct.draw_indicator_dot(pos, 180, 0, 255)    # purple

def indicate_reachability(ct: Controller):
    return
    seen = _bm_seen
    reachable = _bm_reachable
    might = _bm_might_reach
    for idx in range(tile_count):
        bit = 1 << idx
        if not (seen & bit):
            continue
        if reachable & bit:
            ct.draw_indicator_dot(Position(idx % width, idx // width), 0, 255, 0)
        elif might & bit:
            ct.draw_indicator_dot(Position(idx % width, idx // width), 255, 165, 0)
        elif not (might & bit):
            ct.draw_indicator_dot(Position(idx % width, idx // width), 255, 0, 0)

def indicate_seen(ct: Controller):
    return
    for idx in range(tile_count):
        ei = _env_idx[idx]
        if ei < 0:
            continue
        env = _INT_ENV[ei]
        x = idx % width
        y = idx // width
        pos = Position(x, y)
        if env == Environment.WALL:
            ct.draw_indicator_dot(pos, 255, 0, 0)      # red
        elif env == Environment.ORE_TITANIUM:
            ct.draw_indicator_dot(pos, 0, 255, 255)    # cyan
        elif env == Environment.ORE_AXIONITE:
            ct.draw_indicator_dot(pos, 255, 0, 255)    # magenta
