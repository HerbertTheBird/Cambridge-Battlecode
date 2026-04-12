import random
from array import array
from collections import defaultdict

from cambc import Controller, Direction, EntityType, Environment, Position, ResourceType, Team

from globals import DIRECTIONS, CARDINAL_DIRECTIONS, CONVEYOR_TYPES, TURRET_TYPES, Symmetry, INF, DELTAS, DELTA_TO_DIRECTION, TURN_CPU_BUDGET_US, END_TURN_RESERVE_US, CPU_SAFETY_MARGIN_US
from comms import Comms
from helpers import get_foundry_position_idxs, is_core_tile

from log import log, log_time

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

def on_map_coords(x: int, y: int, width: int, height: int) -> bool:
    return 0 <= x < width and 0 <= y < height

def on_map(pos: Position, width: int, height: int) -> bool:
    return 0 <= pos.x < width and 0 <= pos.y < height

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

class Map:
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.tile_count = width * height
        self._board_mask = (1 << self.tile_count) - 1
        left_col = 0
        right_col = 0
        for y in range(height):
            row_start = y * width
            left_col |= 1 << row_start
            right_col |= 1 << (row_start + width - 1)
        self._not_left_col = self._board_mask & ~left_col
        self._not_right_col = self._board_mask & ~right_col
        self._tile_flags = array("I", [0]) * self.tile_count
        self._env_idx = array("b", [-1]) * self.tile_count
        self._entity_id = array("i", [0]) * self.tile_count
        self._entity_type_idx = array("b", [-1]) * self.tile_count
        self._entity_team_idx = array("b", [-1]) * self.tile_count
        self._bm_et = [0] * len(EntityType)
        self._bm_team = [0] * len(Team)
        self._bm_env = [0] * len(Environment)
        self._bm_occupied = 0
        self._bm_walkable = self._board_mask
        self._bm_seen = 0
        self._bm_wall = 0
        self._bm_blocked = 0
        self._bm_visible = 0
        self._bm_ore_ti = 0
        self._bm_ore_ax = 0
        self._bm_unreachable_harvesters = 0
        self._bm_unreachable_ores = 0
        self._enemy_launcher_adj = bytearray(self.tile_count)
        self._output_idx = array("i", [-1]) * self.tile_count
        self._input_masks: list[int] = [0] * self.tile_count
        self.conveyor_resources: defaultdict[int, set[ResourceType]] = defaultdict(set)  # tile idx -> observed resource types
        self.conveyor_resources_last_seen: defaultdict[int, dict[ResourceType, int]] = defaultdict(dict)
        self._input_chain_valid = bytearray(self.tile_count)
        self._input_resource_masks = array("I", [0]) * self.tile_count
        self.current_round = 0
        self._feeds_turret_cache: dict[tuple[int, Team], bool] = {}
        self._feeds_building_cache: dict[tuple[int, Team], bool] = {}
        self._feeds_building_in_vision_cache: dict[tuple[int, Team], bool] = {}
        self._sabotage_downstream_cache: dict[tuple[int, Team], int] = {}
        self._chain_terminal_cache: dict[int, int] = {}
        self._chain_last_visible_cache: dict[int, int | None] = {}
        self.movement_revision = 0
        self.symmetry = Symmetry.UNKNOWN
        self.can_flip_x = True
        self.can_flip_y = True
        self.can_rotate = True
        self.should_update_all_symmetric = False
        self.symmetric_update_x = 0
        self.symmetric_update_y = 0

    def _idx(self, pos: Position) -> int:
        return pos.y * self.width + pos.x

    def _idx_if_on_map(self, pos: Position) -> int | None:
        if not on_map(pos, self.width, self.height):
            return None
        return pos.y * self.width + pos.x

    def _pos(self, idx: int) -> Position:
        return Position(idx % self.width, idx // self.width)

    def pos_to_idx(self, pos: Position) -> int:
        return self._idx(pos)

    def idx_to_pos(self, idx: int) -> Position:
        return self._pos(idx)

    def _get_flag_idx(self, idx: int, flag: int) -> bool:
        return bool(self._tile_flags[idx] & flag)

    def _set_flag_idx(self, idx: int, flag: int):
        self._tile_flags[idx] |= flag

    def _clear_flag_idx(self, idx: int, flag: int):
        self._tile_flags[idx] &= ~flag

    def _set_tile_env_idx(self, idx: int, env: Environment):
        bit = 1 << idx
        prev_env_idx = self._env_idx[idx]
        if prev_env_idx >= 0:
            self._bm_env[prev_env_idx] &= ~bit
        env_idx = _ENV_INT[env]
        self._env_idx[idx] = env_idx
        self._bm_env[env_idx] |= bit
        self._set_flag_idx(idx, FLAG_SEEN)
        self._bm_seen |= bit
        self._clear_flag_idx(idx, FLAG_WALL | FLAG_ORE_TITANIUM | FLAG_ORE_AXIONITE)
        self._bm_wall &= ~bit
        if env == Environment.WALL:
            self._set_flag_idx(idx, FLAG_WALL)
            self._bm_wall |= bit
        elif env == Environment.ORE_TITANIUM:
            self._set_flag_idx(idx, FLAG_ORE_TITANIUM)
        elif env == Environment.ORE_AXIONITE:
            self._set_flag_idx(idx, FLAG_ORE_AXIONITE)
        self._bm_walkable = self._board_mask & ~self._bm_wall & ~self._bm_blocked

    def _sync_blocked_mask_idx(self, idx: int, prev_flags: int):
        bit = 1 << idx
        if prev_flags & FLAG_BLOCKED:
            self._bm_blocked &= ~bit
        if self._tile_flags[idx] & FLAG_BLOCKED:
            self._bm_blocked |= bit
        self._bm_walkable = self._board_mask & ~self._bm_wall & ~self._bm_blocked

    def _set_tile_entity_idx(self, idx: int, bid: int, etype: EntityType, team: Team):
        bit = 1 << idx
        prev_etype_idx = self._entity_type_idx[idx]
        if prev_etype_idx >= 0:
            self._bm_et[prev_etype_idx] &= ~bit
        prev_team_idx = self._entity_team_idx[idx]
        if prev_team_idx >= 0:
            self._bm_team[prev_team_idx] &= ~bit
        self._entity_id[idx] = bid
        etype_idx = _ET_INT[etype]
        team_idx = _TM_INT[team]
        self._entity_type_idx[idx] = etype_idx
        self._entity_team_idx[idx] = team_idx
        self._bm_et[etype_idx] |= bit
        self._bm_team[team_idx] |= bit
        self._bm_occupied |= bit

    def _clear_tile_entity_idx(self, idx: int):
        bit = 1 << idx
        self._entity_id[idx] = 0
        prev_etype_idx = self._entity_type_idx[idx]
        if prev_etype_idx >= 0:
            self._bm_et[prev_etype_idx] &= ~bit
        prev_team_idx = self._entity_team_idx[idx]
        if prev_team_idx >= 0:
            self._bm_team[prev_team_idx] &= ~bit
        self._entity_type_idx[idx] = -1
        self._entity_team_idx[idx] = -1
        self._bm_occupied &= ~bit


    def _is_visited_idx(self, idx: int) -> bool:
        return self._get_flag_idx(idx, FLAG_SEEN)

    def _get_tile_env_idx(self, idx: int) -> Environment | None:
        env_idx = self._env_idx[idx]
        return None if env_idx < 0 else _INT_ENV[env_idx]

    def _is_ore_idx(self, idx: int) -> bool:
        flags = self._tile_flags[idx]
        return bool(flags & (FLAG_ORE_TITANIUM | FLAG_ORE_AXIONITE))

    def _has_ore_harvester_idx(self, idx: int) -> bool:
        if self._entity_type_idx[idx] != _IDX_HARVESTER:
            return False
        env_idx = self._env_idx[idx]
        return env_idx == _IDX_ENV_ORE_TI or env_idx == _IDX_ENV_ORE_AX

    def _has_adjacent_ally_conveyor_idx(self, idx: int, my_team: Team) -> bool:
        my_team_idx = _TM_INT[my_team]
        x = idx % self.width
        y = idx // self.width
        for dx, dy in _CARDINAL_OFFSETS:
            nx = x + dx
            ny = y + dy
            if not on_map_coords(nx, ny, self.width, self.height):
                continue
            nidx = ny * self.width + nx
            if self._entity_type_idx[nidx] in _CONVEYOR_TYPE_IDXS and self._entity_team_idx[nidx] == my_team_idx:
                return True
        return False

    def has_entity(self, pos: Position) -> bool:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return False
        return self._entity_id[idx] != 0

    def get_tile_entity_id(self, pos: Position) -> int | None:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return None
        bid = self._entity_id[idx]
        return bid if bid != 0 else None

    def get_tile_entity_type(self, pos: Position) -> EntityType | None:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return None
        etype_idx = self._entity_type_idx[idx]
        return None if etype_idx < 0 else _INT_ET[etype_idx]

    def get_tile_entity_team(self, pos: Position) -> Team | None:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return None
        team_idx = self._entity_team_idx[idx]
        return None if team_idx < 0 else _INT_TM[team_idx]

    def get_tile_env_code(self, pos: Position) -> int:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return -1
        return self._env_idx[idx]

    def get_tile_entity_type_code(self, pos: Position) -> int:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return -1
        return self._entity_type_idx[idx]

    def get_tile_entity_team_code(self, pos: Position) -> int:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return -1
        return self._entity_team_idx[idx]

    def is_blocked_idx(self, idx: int) -> bool:
        return self._get_flag_idx(idx, FLAG_BLOCKED)

    def get_walkable_mask(self) -> int:
        return self._bm_walkable

    def get_visible_mask(self) -> int:
        return self._bm_visible

    def get_seen_mask(self) -> int:
        return self._bm_seen

    def get_titanium_ore_mask(self) -> int:
        return self._bm_ore_ti

    def get_axionite_ore_mask(self) -> int:
        return self._bm_ore_ax

    def get_entity_mask(self, etype: EntityType) -> int:
        return self._bm_et[_ET_INT[etype]]

    def get_team_mask(self, team: Team) -> int:
        return self._bm_team[_TM_INT[team]]

    def get_env_mask(self, env: Environment) -> int:
        return self._bm_env[_ENV_INT[env]]

    def get_occupied_mask(self) -> int:
        return self._bm_occupied

    def get_builder_standable_building_mask(self, team: Team) -> int:
        return (
            self._bm_et[_IDX_CONVEYOR]
            | self._bm_et[_IDX_ARMOURED_CONVEYOR]
            | self._bm_et[_IDX_BRIDGE]
            | self._bm_et[_IDX_SPLITTER]
            | self._bm_et[_IDX_ROAD]
            | (self._bm_et[_IDX_CORE] & self._bm_team[_TM_INT[team]])
        )

    def get_not_left_col_mask(self) -> int:
        return self._not_left_col

    def get_not_right_col_mask(self) -> int:
        return self._not_right_col

    def is_ally_barrier_idx(self, idx: int) -> bool:
        return self._get_flag_idx(idx, FLAG_ALLY_BARRIER)

    def is_ally_launcher_idx(self, idx: int) -> bool:
        return self._get_flag_idx(idx, FLAG_ALLY_LAUNCHER)

    def get_enemy_launcher_adj_count_idx(self, idx: int) -> int:
        return self._enemy_launcher_adj[idx]

    def get_enemy_launcher_adj_count(self, pos: Position) -> int:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return 0
        return self._enemy_launcher_adj[idx]

    def _adjust_enemy_launcher_adj_idx(self, launcher_idx: int, delta: int):
        px = launcher_idx % self.width
        py = launcher_idx // self.width
        for d in DIRECTIONS:
            dx, dy = DELTAS[d]
            x = px + dx
            y = py + dy
            if not on_map_coords(x, y, self.width, self.height):
                continue
            idx = y * self.width + x
            if delta > 0:
                if self._enemy_launcher_adj[idx] < 255:
                    self._enemy_launcher_adj[idx] += 1
            elif self._enemy_launcher_adj[idx] > 0:
                self._enemy_launcher_adj[idx] -= 1

    def get_conveyor_output(self, pos: Position) -> Position | None:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return None
        out_idx = self._output_idx[idx]
        if out_idx < 0:
            return None
        return self._pos(out_idx)

    def has_conveyor_output(self, pos: Position) -> bool:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return False
        return self._output_idx[idx] >= 0

    def _iter_mask_indices(self, mask: int):
        while mask:
            bit = mask & -mask
            yield bit.bit_length() - 1
            mask ^= bit

    def _iter_mask_positions(self, mask: int):
        for idx in self._iter_mask_indices(mask):
            yield self._pos(idx)

    def iter_conveyor_input_indices(self, pos: Position):
        return self._iter_mask_indices(self._input_masks[self._idx(pos)])

    def has_conveyor_inputs(self, pos: Position) -> bool:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return False
        return bool(self._input_masks[idx])

    def get_conveyor_input_count(self, pos: Position) -> int:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return 0
        return self._input_masks[idx].bit_count()

    def get_conveyor_input_positions(self, pos: Position) -> list[Position]:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return []
        return list(self._iter_mask_positions(self._input_masks[idx]))

    def iter_titanium_ores(self):
        return self._iter_mask_positions(self._bm_ore_ti)

    def iter_axionite_ores(self):
        return self._iter_mask_positions(self._bm_ore_ax)

    def is_titanium_ore(self, pos: Position) -> bool:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return False
        return bool(self._bm_ore_ti & (1 << idx))

    def is_axionite_ore(self, pos: Position) -> bool:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return False
        return bool(self._bm_ore_ax & (1 << idx))

    def is_wall(self, pos: Position) -> bool:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return False
        return bool(self._bm_wall & (1 << idx))

    def add_unreachable_harvester(self, pos: Position):
        idx = self._idx_if_on_map(pos)
        if idx is not None:
            self._bm_unreachable_harvesters |= 1 << idx

    def add_unreachable_ore(self, pos: Position):
        idx = self._idx_if_on_map(pos)
        if idx is not None:
            self._bm_unreachable_ores |= 1 << idx

    def discard_unreachable_ore(self, pos: Position):
        idx = self._idx_if_on_map(pos)
        if idx is not None:
            self._bm_unreachable_ores &= ~(1 << idx)

    def is_unreachable_harvester(self, pos: Position) -> bool:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return False
        return bool(self._bm_unreachable_harvesters & (1 << idx))

    def is_unreachable_ore(self, pos: Position) -> bool:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return False
        return bool(self._bm_unreachable_ores & (1 << idx))

    def _clear_runtime_caches(self):
        self._feeds_turret_cache.clear()
        self._feeds_building_cache.clear()
        self._feeds_building_in_vision_cache.clear()
        self._sabotage_downstream_cache.clear()
        self._chain_terminal_cache.clear()
        self._chain_last_visible_cache.clear()

    def _finalize_local_entity_update(self, idx: int, prev_output_idx: int, prev_was_ore_harvester: bool):
        dirty_cache_positions: set[int] = set()
        new_output_idx = self._output_idx[idx]
        if prev_output_idx != new_output_idx:
            if prev_output_idx >= 0:
                dirty_cache_positions.add(prev_output_idx)
            if new_output_idx >= 0:
                dirty_cache_positions.add(new_output_idx)

        new_is_ore_harvester = self._has_ore_harvester_idx(idx)
        if prev_was_ore_harvester != new_is_ore_harvester:
            x = idx % self.width
            y = idx // self.width
            for dx, dy in _CARDINAL_OFFSETS:
                nx = x + dx
                ny = y + dy
                if on_map_coords(nx, ny, self.width, self.height):
                    dirty_cache_positions.add(ny * self.width + nx)

        self._clear_runtime_caches()
        if dirty_cache_positions:
            self._recompute_input_chain_cache(dirty_cache_positions)

    def on_local_destroy(self, pos: Position):
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return
        prev_flags = self._tile_flags[idx]
        prev_output_idx = self._output_idx[idx]
        prev_was_ore_harvester = self._has_ore_harvester_idx(idx)
        if prev_output_idx >= 0:
            self._remove_conveyor_tracking(pos)
        self._clear_tile_entity_idx(idx)
        self._tile_flags[idx] &= _CLEAR_ENTITY_FLAGS
        if self._is_ore_idx(idx):
            self.discard_unreachable_ore(pos)
        self._sync_blocked_mask_idx(idx, prev_flags)
        if ((prev_flags ^ self._tile_flags[idx]) & FLAG_BLOCKED) != 0:
            self.movement_revision += 1
        self._finalize_local_entity_update(idx, prev_output_idx, prev_was_ore_harvester)

    def on_local_build(self, pos: Position, bid: int, etype: EntityType, team: Team, direction: Direction | None = None, output_target: Position | None = None):
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return
        prev_flags = self._tile_flags[idx]
        prev_output_idx = self._output_idx[idx]
        prev_was_ore_harvester = self._has_ore_harvester_idx(idx)
        if prev_output_idx >= 0:
            self._remove_conveyor_tracking(pos)

        self._set_tile_entity_idx(idx, bid, etype, team)
        self._tile_flags[idx] &= _CLEAR_ENTITY_FLAGS
        if self._is_ore_idx(idx):
            self.discard_unreachable_ore(pos)
        if etype == EntityType.BARRIER:
            self._tile_flags[idx] |= FLAG_ALLY_BARRIER
        elif etype == EntityType.LAUNCHER:
            self._tile_flags[idx] |= FLAG_ALLY_LAUNCHER
        elif etype not in CONVEYOR_TYPES and etype not in (EntityType.ROAD, EntityType.CORE, EntityType.BARRIER):
            self._tile_flags[idx] |= FLAG_BLOCKED

        if etype in CONVEYOR_TYPES:
            if etype == EntityType.BRIDGE:
                target = output_target
            elif direction is not None:
                target = pos.add(direction)
            else:
                target = None
            if target is not None and on_map(target, self.width, self.height):
                new_output_idx = self._idx(target)
                self._output_idx[idx] = new_output_idx
                self._input_masks[new_output_idx] |= 1 << idx

        self._sync_blocked_mask_idx(idx, prev_flags)
        if ((prev_flags ^ self._tile_flags[idx]) & FLAG_BLOCKED) != 0:
            self.movement_revision += 1
        self._finalize_local_entity_update(idx, prev_output_idx, prev_was_ore_harvester)

    def get_symmetric_idx(self, idx: int, symmetry: Symmetry) -> int:
        x = idx % self.width
        y = idx // self.width
        if symmetry == Symmetry.FLIP_X:
            return y * self.width + (self.width - 1 - x)
        elif symmetry == Symmetry.FLIP_Y:
            return (self.height - 1 - y) * self.width + x
        elif symmetry == Symmetry.ROTATE:
            return (self.height - 1 - y) * self.width + (self.width - 1 - x)
        return idx

    def get_symmetric_pos(self, pos: Position, symmetry: Symmetry):
        return self._pos(self.get_symmetric_idx(self._idx(pos), symmetry))

    def _set_symmetry(self, symmetry: Symmetry):
        if symmetry == Symmetry.UNKNOWN or self.symmetry == symmetry:
            return
        if self.symmetry == Symmetry.UNKNOWN:
            self.should_update_all_symmetric = True
            self.symmetric_update_x = 0
            self.symmetric_update_y = 0
        self.symmetry = symmetry

    def _apply_env_idx(self, idx: int, env: Environment):
        self._set_tile_env_idx(idx, env)
        bit = 1 << idx
        self._bm_ore_ti &= ~bit
        self._bm_ore_ax &= ~bit
        if env == Environment.ORE_TITANIUM:
            self._bm_ore_ti |= bit
        elif env == Environment.ORE_AXIONITE:
            self._bm_ore_ax |= bit
        
    def check_symmetry(self, pos: Position, env: Environment):
        idx = self._idx(pos)
        env_idx = _ENV_INT[env]
        if self.can_flip_x:
            sym_env_idx = self._env_idx[self.get_symmetric_idx(idx, Symmetry.FLIP_X)]
            if sym_env_idx >= 0 and sym_env_idx != env_idx:
                self.can_flip_x = False
        if self.can_flip_y:
            sym_env_idx = self._env_idx[self.get_symmetric_idx(idx, Symmetry.FLIP_Y)]
            if sym_env_idx >= 0 and sym_env_idx != env_idx:
                self.can_flip_y = False
        if self.can_rotate:
            sym_env_idx = self._env_idx[self.get_symmetric_idx(idx, Symmetry.ROTATE)]
            if sym_env_idx >= 0 and sym_env_idx != env_idx:
                self.can_rotate = False
                
    def check_core_symmetry(self, pos: Position):
        if self.symmetry != Symmetry.UNKNOWN:
            return
        idx = self._idx(pos)
        if self.can_flip_x:
            sym_etype_idx = self._entity_type_idx[self.get_symmetric_idx(idx, Symmetry.FLIP_X)]
            if sym_etype_idx >= 0 and sym_etype_idx != _IDX_CORE:
                self.can_flip_x = False
        if self.can_flip_y:
            sym_etype_idx = self._entity_type_idx[self.get_symmetric_idx(idx, Symmetry.FLIP_Y)]
            if sym_etype_idx >= 0 and sym_etype_idx != _IDX_CORE:
                self.can_flip_y = False
        if self.can_rotate:
            sym_etype_idx = self._entity_type_idx[self.get_symmetric_idx(idx, Symmetry.ROTATE)]
            if sym_etype_idx >= 0 and sym_etype_idx != _IDX_CORE:
                self.can_rotate = False

    def update_symmetry(self):
        if self.symmetry != Symmetry.UNKNOWN:
            return
        key = (self.can_flip_x, self.can_flip_y, self.can_rotate)
        if key in SYMMETRY_MAPPING:
            self._set_symmetry(SYMMETRY_MAPPING[key])

    def update_all_symmetric_tiles(self, ct: Controller):
        if not self.should_update_all_symmetric or self.symmetry == Symmetry.UNKNOWN:
            return

        width = self.width
        height = self.height
        
        log_time(ct, "Start of symmetric update")

        while self.symmetric_update_y < height:
            idx = self.symmetric_update_y * width + self.symmetric_update_x
            if idx % 50 == 0:
                budget = TURN_CPU_BUDGET_US - ct.get_cpu_time_elapsed() - END_TURN_RESERVE_US - CPU_SAFETY_MARGIN_US
                if budget <= 0:
                    return

            if self._env_idx[idx] < 0:
                sym_idx = self.get_symmetric_idx(idx, self.symmetry)
                sym_env_idx = self._env_idx[sym_idx]
                if sym_env_idx >= 0:
                    self._apply_env_idx(idx, _INT_ENV[sym_env_idx])

            self.symmetric_update_x += 1
            if self.symmetric_update_x == width:
                self.symmetric_update_x = 0
                self.symmetric_update_y += 1

        self.symmetric_update_x = 0
        self.symmetric_update_y = 0
        self.should_update_all_symmetric = False
        
        log_time(ct, "End of symmetric update")

    def _resource_to_mask(self, resource: ResourceType | None) -> int:
        if resource == ResourceType.TITANIUM:
            return RESOURCE_MASK_TITANIUM
        if resource == ResourceType.RAW_AXIONITE:
            return RESOURCE_MASK_AXIONITE
        return 0


    def _get_cached_resource_mask(self, pos: Position) -> int:
        return self._input_resource_masks[self._idx(pos)]

    def _get_cached_resource_mask_idx(self, idx: int) -> int:
        return self._input_resource_masks[idx]

    def _set_cached_resource_mask(self, pos: Position, mask: int):
        self._input_resource_masks[self._idx(pos)] = mask

    def _get_cached_chain_valid(self, pos: Position) -> bool:
        return bool(self._input_chain_valid[self._idx(pos)])

    def _get_cached_chain_valid_idx(self, idx: int) -> bool:
        return bool(self._input_chain_valid[idx])

    def _set_cached_chain_valid(self, pos: Position, valid: bool):
        self._input_chain_valid[self._idx(pos)] = 1 if valid else 0

    def input_chain_reaches_resource(self, pos: Position, resource: ResourceType) -> bool:
        return bool(self._get_cached_resource_mask(pos) & self._resource_to_mask(resource))

    def input_chain_reaches_resource_idx(self, idx: int, resource: ResourceType) -> bool:
        return bool(self._get_cached_resource_mask_idx(idx) & self._resource_to_mask(resource))

    def _record_conveyor_resource(self, pos: Position, resource: ResourceType):
        idx = self._idx(pos)
        self.conveyor_resources[idx].add(resource)
        self.conveyor_resources_last_seen[idx][resource] = self.current_round

    def get_recent_conveyor_resources(self, pos: Position, max_age: int = OBSERVED_RESOURCE_MAX_AGE) -> set[ResourceType]:
        recent = set()
        idx = self._idx(pos)
        last_seen = self.conveyor_resources_last_seen.get(idx)
        if last_seen is None:
            return recent

        stale_resources = []
        for resource, seen_round in last_seen.items():
            if self.current_round - seen_round <= max_age:
                recent.add(resource)
            else:
                stale_resources.append(resource)

        if stale_resources:
            tracked = self.conveyor_resources.get(idx)
            for resource in stale_resources:
                del last_seen[resource]
                if tracked is not None:
                    tracked.discard(resource)
            if tracked is not None and not tracked:
                self.conveyor_resources.pop(idx, None)
            if not last_seen:
                self.conveyor_resources_last_seen.pop(idx, None)

        return recent

    def has_recent_conveyor_resource(self, pos: Position, resource: ResourceType, max_age: int = OBSERVED_RESOURCE_MAX_AGE) -> bool:
        last_seen = self.conveyor_resources_last_seen.get(self._idx(pos))
        if last_seen is None:
            return False
        seen_round = last_seen.get(resource)
        return seen_round is not None and self.current_round - seen_round <= max_age

    def has_recent_conveyor_resource_idx(self, idx: int, resource: ResourceType, max_age: int = OBSERVED_RESOURCE_MAX_AGE) -> bool:
        last_seen = self.conveyor_resources_last_seen.get(idx)
        if last_seen is None:
            return False
        seen_round = last_seen.get(resource)
        return seen_round is not None and self.current_round - seen_round <= max_age

    def get_cached_conveyor_resources(self, pos: Position) -> set[ResourceType]:
        """Return cached resource evidence for a conveyor chain position."""
        resources = set(self.get_recent_conveyor_resources(pos))
        mask = self._get_cached_resource_mask(pos)
        if mask & RESOURCE_MASK_TITANIUM:
            resources.add(ResourceType.TITANIUM)
        if mask & RESOURCE_MASK_AXIONITE:
            resources.add(ResourceType.RAW_AXIONITE)
        return resources

    def get_conveyor_resource_evidence(self, pos: Position, ct: Controller) -> set[ResourceType]:
        """Return resource evidence for a conveyor, preferring live stored resource."""
        if ct.is_in_vision(pos):
            bid = self.get_tile_entity_id(pos)
            if bid is not None and self.get_tile_entity_type(pos) in CONVEYOR_TYPES:
                stored = ct.get_stored_resource(bid)
                if stored is not None:
                    return {stored}
        return self.get_cached_conveyor_resources(pos)

    def _get_conveyor_resource_state(self, pos: Position, ct: Controller, resource: ResourceType) -> int:
        """Return 0=no evidence, 1=only matching evidence, 2=conflicting or ambiguous evidence."""
        if ct.is_in_vision(pos):
            bid = self.get_tile_entity_id(pos)
            if bid is not None and self.get_tile_entity_type(pos) in CONVEYOR_TYPES:
                stored = ct.get_stored_resource(bid)
                if stored is not None:
                    return 1 if stored == resource else 2

        recent = self.get_recent_conveyor_resources(pos)
        mask = self._get_cached_resource_mask(pos)
        has_match = resource in recent or bool(mask & self._resource_to_mask(resource))

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

    def _get_conveyor_resource_state_idx(self, idx: int, ct: Controller, resource: ResourceType) -> int:
        """Idx-native version of _get_conveyor_resource_state for hot local scans."""
        if (self._bm_visible >> idx) & 1:
            bid = self._entity_id[idx]
            if bid != 0 and self._entity_type_idx[idx] in _CONVEYOR_TYPE_IDXS:
                stored = ct.get_stored_resource(bid)
                if stored is not None:
                    return 1 if stored == resource else 2

        recent = self.conveyor_resources_last_seen.get(idx)
        mask = self._input_resource_masks[idx]
        if resource == ResourceType.TITANIUM:
            has_match = (
                (recent is not None and ResourceType.TITANIUM in recent and self.current_round - recent[ResourceType.TITANIUM] <= OBSERVED_RESOURCE_MAX_AGE)
                or bool(mask & RESOURCE_MASK_TITANIUM)
            )
            has_other = (
                (recent is not None and (
                    (ResourceType.RAW_AXIONITE in recent and self.current_round - recent[ResourceType.RAW_AXIONITE] <= OBSERVED_RESOURCE_MAX_AGE)
                    or (ResourceType.REFINED_AXIONITE in recent and self.current_round - recent[ResourceType.REFINED_AXIONITE] <= OBSERVED_RESOURCE_MAX_AGE)
                ))
                or bool(mask & RESOURCE_MASK_AXIONITE)
            )
        elif resource == ResourceType.RAW_AXIONITE:
            has_match = (
                (recent is not None and ResourceType.RAW_AXIONITE in recent and self.current_round - recent[ResourceType.RAW_AXIONITE] <= OBSERVED_RESOURCE_MAX_AGE)
                or bool(mask & RESOURCE_MASK_AXIONITE)
            )
            has_other = (
                (recent is not None and (
                    (ResourceType.TITANIUM in recent and self.current_round - recent[ResourceType.TITANIUM] <= OBSERVED_RESOURCE_MAX_AGE)
                    or (ResourceType.REFINED_AXIONITE in recent and self.current_round - recent[ResourceType.REFINED_AXIONITE] <= OBSERVED_RESOURCE_MAX_AGE)
                ))
                or bool(mask & RESOURCE_MASK_TITANIUM)
            )
        else:
            has_match = False
            has_other = recent is not None and any(
                r != resource and self.current_round - seen_round <= OBSERVED_RESOURCE_MAX_AGE
                for r, seen_round in recent.items()
            )

        if has_other:
            return 2
        if has_match:
            return 1
        return 0

    def infer_chain_resource_at_output(self, output_pos: Position, ct: Controller) -> ResourceType | None:
        """Infer the resource for a broken chain gap, preferring live input storage."""
        live_resources = set()
        for input_idx in self.iter_conveyor_input_indices(output_pos):
            input_pos = self._pos(input_idx)
            if not ct.is_in_vision(input_pos):
                continue
            bid = self._entity_id[input_idx]
            if bid == 0 or self._entity_type_idx[input_idx] not in _CONVEYOR_TYPE_IDXS:
                continue
            stored = ct.get_stored_resource(bid)
            if stored is not None:
                live_resources.add(stored)
        if len(live_resources) == 1:
            return next(iter(live_resources))
        if len(live_resources) > 1:
            return None

        cached_resources = set(self.get_cached_conveyor_resources(output_pos))
        for input_idx in self.iter_conveyor_input_indices(output_pos):
            input_pos = self._pos(input_idx)
            cached_resources.update(self.get_cached_conveyor_resources(input_pos))
        if len(cached_resources) == 1:
            return next(iter(cached_resources))
        return None

    def is_unserviced_harvester(self, pos: Position, my_team: Team) -> bool:
        idx = self._idx_if_on_map(pos)
        if idx is None:
            return False
        return self._has_ore_harvester_idx(idx) and not self._has_adjacent_ally_conveyor_idx(idx, my_team)

    def _collect_downstream_indices(self, dirty_roots: set[int]) -> list[int]:
        positions = []
        seen = set()
        stack = list(dirty_roots)
        while stack:
            idx = stack.pop()
            if idx in seen:
                continue
            seen.add(idx)
            positions.append(idx)
            next_idx = self._output_idx[idx]
            if next_idx >= 0:
                stack.append(next_idx)
        return positions

    def _compute_cached_resource_mask_idx(self, idx: int) -> int:
        mask = 0
        width = self.width
        height = self.height
        x = idx % width
        y = idx // width
        env_idx_grid = self._env_idx
        entity_type_idxs = self._entity_type_idx
        input_chain_valid = self._input_chain_valid
        input_resource_masks = self._input_resource_masks

        for dx, dy in _CARDINAL_OFFSETS:
            nx = x + dx
            ny = y + dy
            if not on_map_coords(nx, ny, width, height):
                continue
            nidx = ny * width + nx
            if entity_type_idxs[nidx] != _IDX_HARVESTER:
                continue
            env_idx = env_idx_grid[nidx]
            if env_idx == _IDX_ENV_ORE_TI:
                mask |= RESOURCE_MASK_TITANIUM
            elif env_idx == _IDX_ENV_ORE_AX:
                mask |= RESOURCE_MASK_AXIONITE

        for input_idx in self._iter_mask_indices(self._input_masks[idx]):
            if not self._get_flag_idx(input_idx, FLAG_SEEN):
                continue
            if entity_type_idxs[input_idx] not in _CONVEYOR_TYPE_IDXS:
                continue
            if not input_chain_valid[input_idx]:
                continue

            mask |= input_resource_masks[input_idx]
            if mask == (RESOURCE_MASK_TITANIUM | RESOURCE_MASK_AXIONITE):
                break

        return mask

    def _compute_cached_chain_valid_idx(self, idx: int) -> bool:
        has_valid_feeder = False
        width = self.width
        height = self.height
        x = idx % width
        y = idx // width
        env_idx_grid = self._env_idx
        entity_type_idxs = self._entity_type_idx
        input_chain_valid = self._input_chain_valid

        for dx, dy in _CARDINAL_OFFSETS:
            nx = x + dx
            ny = y + dy
            if not on_map_coords(nx, ny, width, height):
                continue
            nidx = ny * width + nx
            if entity_type_idxs[nidx] != _IDX_HARVESTER:
                continue
            env_idx = env_idx_grid[nidx]
            if env_idx == _IDX_ENV_ORE_TI or env_idx == _IDX_ENV_ORE_AX:
                has_valid_feeder = True

        for input_idx in self._iter_mask_indices(self._input_masks[idx]):
            if not self._get_flag_idx(input_idx, FLAG_SEEN):
                # Unvisited input — optimistically assume valid
                has_valid_feeder = True
                continue
            if entity_type_idxs[input_idx] not in _CONVEYOR_TYPE_IDXS:
                continue  # broken input, but other inputs may still be valid
            if not input_chain_valid[input_idx]:
                continue  # invalid upstream, but other inputs may still be valid
            has_valid_feeder = True

        return has_valid_feeder

    def _recompute_input_chain_cache(self, dirty_roots: set[int]):
        positions = self._collect_downstream_indices(dirty_roots)
        if not positions:
            return

        pos_set = set(positions)

        # Build in-degree map (only counting edges within pos_set)
        in_degree: dict[int, int] = {idx: 0 for idx in positions}
        for idx in positions:
            for input_idx in self._iter_mask_indices(self._input_masks[idx]):
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
            output_idx = self._output_idx[idx]
            if output_idx >= 0 and output_idx in pos_set:
                in_degree[output_idx] -= 1
                if in_degree[output_idx] == 0:
                    queue.append(output_idx)

        # Positions not in topo_order are in cycles — mark invalid
        for idx in positions:
            if idx not in in_degree or in_degree[idx] != 0:
                pos = self._pos(idx)
                self._set_cached_chain_valid(pos, False)
                self._set_cached_resource_mask(pos, 0)

        # Single pass in topological order (upstream first)
        for idx in topo_order:
            pos = self._pos(idx)
            new_valid = self._compute_cached_chain_valid_idx(idx)
            new_mask = self._compute_cached_resource_mask_idx(idx) if new_valid else 0
            self._set_cached_chain_valid(pos, new_valid)
            self._set_cached_resource_mask(pos, new_mask)

    def _remove_conveyor_tracking(self, pos: Position):
        """Remove conveyor output/input/resource tracking for a position."""
        idx = self._idx(pos)
        old_output_idx = self._output_idx[idx]
        if old_output_idx >= 0:
            self._input_masks[old_output_idx] &= ~(1 << idx)
        self._output_idx[idx] = -1
        self.conveyor_resources.pop(idx, None)
        self.conveyor_resources_last_seen.pop(idx, None)

    def update_vision(self, ct: Controller, comms: Comms):
        log_time(ct, "Start of update vision")
        self.current_round = ct.get_current_round()
        self._feeds_turret_cache.clear()
        self._feeds_building_cache.clear()
        self._feeds_building_in_vision_cache.clear()
        self._sabotage_downstream_cache.clear()
        self._chain_terminal_cache.clear()
        self._chain_last_visible_cache.clear()
        my_team = ct.get_team()
        nearby = ct.get_nearby_tiles()
        dirty_cache_positions: set[int] = set()

        env_idx_grid = self._env_idx
        entity_ids = self._entity_id
        entity_type_idxs = self._entity_type_idx
        entity_team_idxs = self._entity_team_idx
        output_idx = self._output_idx
        input_masks = self._input_masks
        tile_flags = self._tile_flags
        width = self.width
        height = self.height
        nav_changed = False

        ct_get_tile_env = ct.get_tile_env
        ct_get_tile_building_id = ct.get_tile_building_id
        ct_get_entity_type = ct.get_entity_type
        ct_get_team = ct.get_team
        ct_get_marker_value = ct.get_marker_value
        ct_get_bridge_target = ct.get_bridge_target
        ct_get_direction = ct.get_direction
        ct_get_stored_resource = ct.get_stored_resource
        should_fill_symmetry = self.symmetry != Symmetry.UNKNOWN
        known_symmetry = self.symmetry
        visible_mask = 0
        my_team_idx = _TM_INT[my_team]
        
        log_time(ct, "After local variable assignment")

        for pos in nearby:
            x = pos.x
            y = pos.y
            idx = y * width + x
            visible_mask |= 1 << idx
            prev_output_idx = output_idx[idx]
            prev_was_enemy_launcher = entity_type_idxs[idx] == _IDX_LAUNCHER and entity_team_idxs[idx] != my_team_idx
            prev_was_ore_harvester = False
            if tile_flags[idx] & FLAG_SEEN and entity_type_idxs[idx] == _IDX_HARVESTER:
                prev_env_idx = env_idx_grid[idx]
                prev_was_ore_harvester = prev_env_idx == _IDX_ENV_ORE_TI or prev_env_idx == _IDX_ENV_ORE_AX

            env_idx = env_idx_grid[idx]
            prev_flags = tile_flags[idx]
            if env_idx < 0:
                env = ct_get_tile_env(pos)
                self._apply_env_idx(idx, env)
                env_idx = _ENV_INT[env]
                if should_fill_symmetry:
                    sym_idx = self.get_symmetric_idx(idx, known_symmetry)
                    if env_idx_grid[sym_idx] < 0:
                        self._apply_env_idx(sym_idx, env)
                else:
                    self.check_symmetry(pos, env)
                
            if env_idx == _IDX_ENV_WALL:
                self._clear_tile_entity_idx(idx)
                tile_flags[idx] = (tile_flags[idx] & _CLEAR_ENTITY_FLAGS) | FLAG_BLOCKED
                self._sync_blocked_mask_idx(idx, prev_flags)
                if ((tile_flags[idx] ^ prev_flags) & (FLAG_BLOCKED | FLAG_WALL)) != 0:
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
                        comms.read_marker(ct_get_marker_value(bid), pos, bid, self.current_round)
                    bid = None
            if bid is not None:
                if etype == EntityType.CORE:
                    if cached_bid != bid:
                        self.check_core_symmetry(pos)
                assert etype is not None
                assert team is not None
                is_enemy_launcher = etype_idx == _IDX_LAUNCHER and team_idx != my_team_idx
                if prev_was_enemy_launcher != is_enemy_launcher:
                    self._adjust_enemy_launcher_adj_idx(idx, 1 if is_enemy_launcher else -1)
                self._set_tile_entity_idx(idx, bid, etype, team)
                tile_flags[idx] &= _CLEAR_ENTITY_FLAGS
                if self._is_ore_idx(idx):
                    if team_idx != my_team_idx and (etype_idx == _IDX_BARRIER or etype_idx == _IDX_ARMOURED_CONVEYOR):
                        self.add_unreachable_ore(pos)
                    else:
                        self.discard_unreachable_ore(pos)
                if etype_idx == _IDX_BARRIER and team_idx == my_team_idx:
                    tile_flags[idx] |= FLAG_ALLY_BARRIER
                elif etype_idx == _IDX_LAUNCHER and team_idx == my_team_idx:
                    tile_flags[idx] |= FLAG_ALLY_LAUNCHER
                elif (
                    (etype_idx == _IDX_CORE and team_idx != my_team_idx)
                    or (etype_idx not in _CONVEYOR_TYPE_IDXS and etype_idx != _IDX_ROAD and etype_idx != _IDX_CORE and not (etype_idx == _IDX_BARRIER and team_idx == my_team_idx) and not (etype_idx == _IDX_LAUNCHER and team_idx == my_team_idx))
                ):
                    tile_flags[idx] |= FLAG_BLOCKED

                # Track conveyor outputs and resources
                if etype_idx in _CONVEYOR_TYPE_IDXS:
                    if cached_bid != 0 and cached_bid == bid:
                        new_output_idx = prev_output_idx
                    else:
                        new_output = ct_get_bridge_target(bid) if etype == EntityType.BRIDGE else pos.add(ct_get_direction(bid))
                        if 0 <= new_output.x < width and 0 <= new_output.y < height:
                            new_output_idx = new_output.y * width + new_output.x
                        else:
                            new_output_idx = -1
                    old_output_idx = output_idx[idx]
                    if old_output_idx != new_output_idx:
                        if old_output_idx >= 0:
                            input_masks[old_output_idx] &= ~(1 << idx)
                        output_idx[idx] = new_output_idx
                        if new_output_idx >= 0:
                            input_masks[new_output_idx] |= 1 << idx
                    resource = ct_get_stored_resource(bid)
                    if resource is not None:
                        self._record_conveyor_resource(pos, resource)
                elif output_idx[idx] >= 0:
                    self._remove_conveyor_tracking(pos)
            else:
                if prev_was_enemy_launcher:
                    self._adjust_enemy_launcher_adj_idx(idx, -1)
                self._clear_tile_entity_idx(idx)
                tile_flags[idx] &= _CLEAR_ENTITY_FLAGS
                if self._is_ore_idx(idx):
                    self.discard_unreachable_ore(pos)
                if output_idx[idx] >= 0:
                    self._remove_conveyor_tracking(pos)

            self._sync_blocked_mask_idx(idx, prev_flags)
            if ((tile_flags[idx] ^ prev_flags) & (FLAG_BLOCKED | FLAG_WALL)) != 0:
                nav_changed = True

            new_output_idx = output_idx[idx]
            if prev_output_idx != new_output_idx:
                if prev_output_idx >= 0:
                    dirty_cache_positions.add(prev_output_idx)
                if new_output_idx >= 0:
                    dirty_cache_positions.add(new_output_idx)

            new_is_ore_harvester = (
                entity_type_idxs[idx] == _IDX_HARVESTER
                and (env_idx_grid[idx] == _IDX_ENV_ORE_TI or env_idx_grid[idx] == _IDX_ENV_ORE_AX)
            )
            if prev_was_ore_harvester != new_is_ore_harvester:
                for dx, dy in _CARDINAL_OFFSETS:
                    nx = x + dx
                    ny = y + dy
                    if not on_map_coords(nx, ny, width, height):
                        continue
                    dirty_cache_positions.add(ny * width + nx)

        log_time(ct, "After processing nearby tiles")
        self._bm_visible = visible_mask
        if comms.symmetry is not None and self.symmetry == Symmetry.UNKNOWN:
            self._set_symmetry(comms.symmetry)
            log(f"symmetry from marker: {self.symmetry.name}")

        self.update_symmetry()
        
        log_time(ct, "After updating symmetry")
        
        self._recompute_input_chain_cache(dirty_cache_positions)
        if nav_changed:
            self.movement_revision += 1
        
        log_time(ct, "After recomputing conveyor cache")
    
    def _would_create_loop_idx(self, build_idx: int, output_idx: int) -> bool:
        cur_idx = self._output_idx[output_idx]
        if cur_idx < 0:
            return False

        seen = {build_idx}
        while cur_idx >= 0:
            if cur_idx in seen:
                return cur_idx == build_idx
            seen.add(cur_idx)
            cur_idx = self._output_idx[cur_idx]
        return False

    def _follow_chain_terminal_idx(self, start_idx: int) -> int:
        cache = self._chain_terminal_cache
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
            next_idx = self._output_idx[cur_idx]
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

    def _follow_chain_last_visible_idx(self, start_idx: int) -> int | None:
        cache = self._chain_last_visible_cache
        cached = cache.get(start_idx, _CACHE_MISS)
        if cached is not _CACHE_MISS:
            return cached  # type: ignore[return-value]

        if not self._is_visited_idx(start_idx):
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
            next_idx = self._output_idx[cur_idx]
            if next_idx < 0:
                result = cur_idx
                break
            path.append(cur_idx)
            seen.add(cur_idx)
            if not self._is_visited_idx(next_idx):
                result = cur_idx
                break
            cur_idx = next_idx

        if cache_result:
            for idx in path:
                cache[idx] = result
        return result

    def get_feeder_idxs(self, output_idx: int) -> list[tuple[int, EntityType]]:
        """Idx-native version of get_feeders."""
        feeders: list[tuple[int, EntityType]] = []

        x = output_idx % self.width
        y = output_idx // self.width
        for dx, dy in _CARDINAL_OFFSETS:
            nx = x + dx
            ny = y + dy
            if not on_map_coords(nx, ny, self.width, self.height):
                continue
            adj_idx = ny * self.width + nx
            if self._entity_type_idx[adj_idx] != _IDX_HARVESTER:
                continue
            env_idx = self._env_idx[adj_idx]
            if env_idx == _IDX_ENV_ORE_TI or env_idx == _IDX_ENV_ORE_AX:
                feeders.append((adj_idx, EntityType.HARVESTER))

        input_mask = self._input_masks[output_idx]
        for input_idx in self._iter_mask_indices(input_mask):
            if self._is_visited_idx(input_idx):
                etype_idx = self._entity_type_idx[input_idx]
                if etype_idx < 0 or etype_idx not in _CONVEYOR_TYPE_IDXS:
                    continue
                etype = _INT_ET[etype_idx]
            else:
                etype = EntityType.CONVEYOR
            feeders.append((input_idx, etype))

        return feeders

    def has_adjacent_harvester(self, pos: Position) -> bool:
        """True if a cardinally adjacent harvester on ore feeds pos."""
        x = pos.x
        y = pos.y
        for dx, dy in _CARDINAL_OFFSETS:
            nx = x + dx
            ny = y + dy
            if not on_map_coords(nx, ny, self.width, self.height):
                continue
            if self._entity_type_idx[ny * self.width + nx] == _IDX_HARVESTER:
                return True
        return False

    def has_valid_input_chain(self, pos: Position) -> bool:
        """Return True if all known upstream branches remain valid."""
        return self._get_cached_chain_valid(pos)

    def has_valid_input_chain_idx(self, idx: int) -> bool:
        """Idx-native version of has_valid_input_chain."""
        return self._get_cached_chain_valid_idx(idx)

    def feeds_ally_building(self, pos: Position, my_team: Team) -> bool:
        """Follow conveyor_outputs from pos. Returns True if the chain
        eventually reaches an ally building (any type)."""
        return self._feeds_ally_chain_idx(
            self._idx(pos),
            my_team,
            self._feeds_building_cache,
            lambda etype, team, cur: team == my_team,
        )

    def feeds_ally_building_idx(self, idx: int, my_team: Team) -> bool:
        """Idx-native version of feeds_ally_building."""
        return self._feeds_ally_chain_idx(
            idx,
            my_team,
            self._feeds_building_cache,
            lambda etype, team, cur: team == my_team,
        )

    def _feeds_ally_chain_idx(
        self,
        idx: int,
        my_team: Team,
        cache: dict[tuple[int, Team], bool],
        success_predicate,
        ct: Controller | None = None,
        core_pos: Position | None = None,
        require_visible: bool = False,
    ) -> bool:
        key = (idx, my_team)
        cached = cache.get(key)
        if cached is not None:
            return cached

        cur_idx = idx
        visited_idxs: set[int] = set()
        while self._output_idx[cur_idx] >= 0:
            if cur_idx in visited_idxs:
                cache[key] = False
                return False
            visited_idxs.add(cur_idx)

            next_idx = self._output_idx[cur_idx]
            if next_idx < 0:
                cache[key] = False
                return False

            cur_idx = next_idx
            if require_visible:
                assert ct is not None
                cur = self._pos(cur_idx)
                if not ct.is_in_vision(cur):
                    cache[key] = False
                    return False
                if is_core_tile(core_pos, cur):
                    cache[key] = True
                    return True

            if not self._is_visited_idx(cur_idx):
                cache[key] = False
                return False

            etype_idx = self._entity_type_idx[cur_idx]
            if etype_idx < 0:
                cache[key] = False
                return False

            etype = _INT_ET[etype_idx]
            team = _INT_TM[self._entity_team_idx[cur_idx]]
            cur = self._pos(cur_idx)
            if success_predicate(etype, team, cur):
                cache[key] = True
                return True
            if etype_idx not in _CONVEYOR_TYPE_IDXS:
                cache[key] = False
                return False

        cache[key] = False
        return False

    def feeds_ally_building_in_vision(self, pos: Position, my_team: Team, ct: Controller, core_pos: Position | None = None) -> bool:
        """Follow conveyor_outputs while outputs stay in current vision.
        Returns True iff the visible chain clearly reaches an allied terminal."""
        return self._feeds_ally_chain_idx(
            self._idx(pos),
            my_team,
            self._feeds_building_in_vision_cache,
            lambda etype, team, cur: team == my_team,
            ct=ct,
            core_pos=core_pos,
            require_visible=True,
        )

    def feeds_ally_building_in_vision_idx(self, idx: int, my_team: Team, ct: Controller, core_pos: Position | None = None) -> bool:
        """Idx-native version of feeds_ally_building_in_vision."""
        return self._feeds_ally_chain_idx(
            idx,
            my_team,
            self._feeds_building_in_vision_cache,
            lambda etype, team, cur: team == my_team,
            ct=ct,
            core_pos=core_pos,
            require_visible=True,
        )

    def feeds_ally_turret(self, pos: Position, my_team: Team) -> bool:
        """Follow conveyor_outputs from pos. Returns True if the chain
        eventually reaches an ally turret (SENTINEL, GUNNER, BREACH)."""
        return self._feeds_ally_chain_idx(
            self._idx(pos),
            my_team,
            self._feeds_turret_cache,
            lambda etype, team, cur: team == my_team and etype in TURRET_TYPES,
        )

    def feeds_ally_turret_idx(self, idx: int, my_team: Team) -> bool:
        """Idx-native version of feeds_ally_turret."""
        return self._feeds_ally_chain_idx(
            idx,
            my_team,
            self._feeds_turret_cache,
            lambda etype, team, cur: team == my_team and etype in TURRET_TYPES,
        )

    def get_sabotage_downstream_priority(self, pos: Position, my_team: Team) -> int:
        """Classify how valuable it is to sabotage a downstream enemy chain.
        Returns 3 for enemy core, 2 for enemy turret, 1 for a generic enemy
        chain or enemy foundry, and 0 only when the known downstream path
        clearly becomes invalid for sabotage."""
        key = (self._idx(pos), my_team)
        cached = self._sabotage_downstream_cache.get(key)
        if cached is not None:
            return cached

        cur = pos
        path_idxs: list[int] = []
        visited_idxs: set[int] = set()
        cache_result = True
        result = 1

        while self.has_conveyor_output(cur):
            cur_idx = self._idx(cur)
            if cur_idx in visited_idxs:
                break
            visited_idxs.add(cur_idx)
            path_idxs.append(cur_idx)

            next_pos = self.get_conveyor_output(cur)
            if next_pos is None:
                break
            if not self.is_visited(next_pos):
                break

            next_idx = self._idx(next_pos)
            etype_idx = self._entity_type_idx[next_idx]
            if etype_idx < 0:
                result = 0
                break

            team_idx = self._entity_team_idx[next_idx]
            if team_idx == _TM_INT[my_team]:
                result = 0
                break
            if etype_idx == _IDX_CORE:
                result = 3
                break
            if etype_idx in _TURRET_TYPE_IDXS:
                result = 2
                break
            if etype_idx == _IDX_FOUNDRY:
                result = 1
                break
            if etype_idx in _CONVEYOR_TYPE_IDXS:
                cur = next_pos
                continue

            result = 0
            break

        if cache_result:
            for path_idx in path_idxs:
                self._sabotage_downstream_cache[(path_idx, my_team)] = result
        return result

    def get_nearest_unserviced_harvester(self, pos: Position, ct: Controller, core_pos: Position | None = None) -> Position | None:
        my_team = ct.get_team()
        best_ti_dist = INF
        best_ti_core_dist = INF
        best_ti_idx = -1
        best_ax_dist = INF
        best_ax_core_dist = INF
        best_ax_idx = -1
        width = self.width
        px = pos.x
        py = pos.y
        core_x = core_pos.x if core_pos is not None else 0
        core_y = core_pos.y if core_pos is not None else 0
        for idx in self._iter_mask_indices(self._bm_ore_ti):
            if self._bm_unreachable_harvesters & (1 << idx):
                continue
            if self._entity_type_idx[idx] != _IDX_HARVESTER:
                continue
            env_idx = self._env_idx[idx]
            if env_idx != _IDX_ENV_ORE_TI and env_idx != _IDX_ENV_ORE_AX:
                continue
            x = idx % width
            y = idx // width
            dist = (px - x) * (px - x) + (py - y) * (py - y)
            core_dist = (x - core_x) * (x - core_x) + (y - core_y) * (y - core_y) if core_pos is not None else INF
            if dist > best_ti_dist or (dist == best_ti_dist and core_dist >= best_ti_core_dist):
                continue
            if self._has_adjacent_ally_conveyor_idx(idx, my_team):
                continue
            best_ti_dist = dist
            best_ti_core_dist = core_dist
            best_ti_idx = idx
            
        if best_ti_idx >= 0:
            return self._pos(best_ti_idx)

        for idx in self._iter_mask_indices(self._bm_ore_ax):
            if self._bm_unreachable_harvesters & (1 << idx):
                continue
            if self._entity_type_idx[idx] != _IDX_HARVESTER:
                continue
            env_idx = self._env_idx[idx]
            if env_idx != _IDX_ENV_ORE_TI and env_idx != _IDX_ENV_ORE_AX:
                continue
            x = idx % width
            y = idx // width
            dist = (px - x) * (px - x) + (py - y) * (py - y)
            core_dist = (x - core_x) * (x - core_x) + (y - core_y) * (y - core_y) if core_pos is not None else INF
            if dist > best_ax_dist or (dist == best_ax_dist and core_dist >= best_ax_core_dist):
                continue
            if self._has_adjacent_ally_conveyor_idx(idx, my_team):
                continue
            best_ax_dist = dist
            best_ax_core_dist = core_dist
            best_ax_idx = idx
        if ct.get_global_resources()[0] >= 1500 and best_ax_idx >= 0:
            return self._pos(best_ax_idx)
        return None

    def is_visited(self, pos: Position) -> bool:
        return self._get_flag_idx(self._idx(pos), FLAG_SEEN)
        
    def get_random_tile(self) -> Position:
        return Position(random.randint(0, self.width - 1), random.randint(0, self.height - 1))
    
    def get_nearest_ore_without_harvester(self, pos: Position, ct: Controller, core_pos: Position | None = None) -> Position | None:
        best_ti_dist = INF
        best_ti_core_dist = INF
        best_ti_idx = -1
        best_ax_dist = INF
        best_ax_core_dist = INF
        best_ax_idx = -1
        width = self.width
        height = self.height
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

            if self._entity_type_idx[ore_idx] in _CONVEYOR_TYPE_IDXS:
                if self._get_conveyor_resource_state_idx(ore_idx, ct, opposite) == 1:
                    return True

            x = ore_idx % width
            y = ore_idx // width
            for dx, dy in _CARDINAL_OFFSETS:
                nx = x + dx
                ny = y + dy
                if not on_map_coords(nx, ny, width, height):
                    continue
                adj_idx = ny * width + nx
                if not self._get_flag_idx(adj_idx, FLAG_SEEN):
                    continue
                if self._entity_type_idx[adj_idx] not in _CONVEYOR_TYPE_IDXS:
                    continue
                if self._get_conveyor_resource_state_idx(adj_idx, ct, opposite) == 1:
                    return True
            return False

        for idx in self._iter_mask_indices(self._bm_ore_ti):
            bit = 1 << idx
            if (self._bm_unreachable_ores | self._bm_unreachable_harvesters) & bit:
                continue
            if self._entity_type_idx[idx] == _IDX_HARVESTER:
                continue
            if _has_adjacent_opposite_resource_chain_idx(idx, ResourceType.TITANIUM):
                self._bm_unreachable_ores |= bit
                continue
            x = idx % width
            y = idx // width
            dist = (px - x) * (px - x) + (py - y) * (py - y)
            core_dist = (x - core_x) * (x - core_x) + (y - core_y) * (y - core_y) if core_pos is not None else INF
            if dist < best_ti_dist or (dist == best_ti_dist and core_dist < best_ti_core_dist):
                best_ti_dist = dist
                best_ti_core_dist = core_dist
                best_ti_idx = idx
                
        if best_ti_idx >= 0:
            return self._pos(best_ti_idx)

        for idx in self._iter_mask_indices(self._bm_ore_ax):
            bit = 1 << idx
            if (self._bm_unreachable_ores | self._bm_unreachable_harvesters) & bit:
                continue
            if self._entity_type_idx[idx] == _IDX_HARVESTER:
                continue
            if _has_adjacent_opposite_resource_chain_idx(idx, ResourceType.RAW_AXIONITE):
                self._bm_unreachable_ores |= bit
                continue
            x = idx % width
            y = idx // width
            dist = (px - x) * (px - x) + (py - y) * (py - y)
            core_dist = (x - core_x) * (x - core_x) + (y - core_y) * (y - core_y) if core_pos is not None else INF
            if dist < best_ax_dist or (dist == best_ax_dist and core_dist < best_ax_core_dist):
                best_ax_dist = dist
                best_ax_core_dist = core_dist
                best_ax_idx = idx

        if best_ax_idx >= 0 and ct.get_global_resources()[0] >= 1500:
            return self._pos(best_ax_idx)        
        return None

    def get_nearest_titanium_ore(self, pos: Position) -> Position | None:
        """Return the nearest known titanium ore position, or None."""
        best = None
        best_dist = INF
        for ti_pos in self.iter_titanium_ores():
            dist = pos.distance_squared(ti_pos)
            if dist < best_dist:
                best_dist = dist
                best = ti_pos
        return best

    def tag_conveyor_resource(self, pos: Position, resource: ResourceType):
        """Tag a conveyor position with an expected resource type."""
        self._record_conveyor_resource(pos, resource)

    def find_nearest_conveyor_with_resource(self, pos: Position, resource: ResourceType, my_team: Team | None = None, target_foundry: Position | None = None) -> Position | None:
        """Find the nearest conveyor that has been observed/tagged with the given resource."""
        best = None
        best_dist = INF
        for conv_idx in tuple(self.conveyor_resources):
            conv_pos = self._pos(conv_idx)
            if not self.has_recent_conveyor_resource(conv_pos, resource):
                continue
            if my_team is not None and self.feeds_other_ally_foundry(conv_pos, my_team, target_foundry):
                continue
            dist = pos.distance_squared(conv_pos)
            if dist < best_dist:
                best_dist = dist
                best = conv_pos
        return best

    def find_nearest_conveyor_with_resource_idx(self, pos_idx: int, resource: ResourceType, my_team: Team | None = None, target_foundry_idx: int | None = None) -> int | None:
        """Idx-native version of find_nearest_conveyor_with_resource."""
        best_idx = None
        best_dist = INF
        px = pos_idx % self.width
        py = pos_idx // self.width
        for conv_idx in tuple(self.conveyor_resources):
            if not self.has_recent_conveyor_resource_idx(conv_idx, resource):
                continue
            if my_team is not None and self.feeds_other_ally_foundry_idx(conv_idx, my_team, target_foundry_idx):
                continue
            cx = conv_idx % self.width
            cy = conv_idx // self.width
            dx = px - cx
            dy = py - cy
            dist = dx * dx + dy * dy
            if dist < best_dist:
                best_dist = dist
                best_idx = conv_idx
        return best_idx

    def feeds_other_ally_foundry(self, pos: Position, my_team: Team, target_foundry: Position | None) -> bool:
        """True if the chain from pos terminates at a different allied foundry."""
        return self.feeds_other_ally_foundry_idx(
            self._idx(pos),
            my_team,
            None if target_foundry is None else self._idx(target_foundry),
        )

    def feeds_other_ally_foundry_idx(self, idx: int, my_team: Team, target_foundry_idx: int | None) -> bool:
        """Idx-native version of feeds_other_ally_foundry."""
        terminal_idx = self._follow_chain_terminal_idx(idx)
        if terminal_idx < 0 or not self._is_visited_idx(terminal_idx):
            return False
        etype_idx = self._entity_type_idx[terminal_idx]
        return (
            etype_idx == _IDX_FOUNDRY
            and self._entity_team_idx[terminal_idx] == _TM_INT[my_team]
            and terminal_idx != target_foundry_idx
        )

    def is_single_input_foundry(self, pos: Position, my_team) -> bool:
        """True if pos has an ally foundry with at most 1 input and no titanium input."""
        idx = self._idx(pos)
        return self.is_single_input_foundry_idx(idx, my_team)

    def is_single_input_foundry_idx(self, idx: int, my_team) -> bool:
        """Idx-native version of is_single_input_foundry."""
        if self._entity_type_idx[idx] != _IDX_FOUNDRY or self._entity_team_idx[idx] != _TM_INT[my_team]:
            return False
        input_idxs = tuple(self._iter_mask_indices(self._input_masks[idx]))
        if len(input_idxs) > 1:
            return False
        return not any(
            self.has_recent_conveyor_resource_idx(input_idx, ResourceType.TITANIUM)
            for input_idx in input_idxs
        )

    def find_single_input_foundry(self, core_pos: Position, my_team) -> Position | None:
        """Find an ally foundry near core with at most 1 conveyor/bridge input."""
        for idx in get_foundry_position_idxs(core_pos, self.width, self.height):
            pos = self._pos(idx)
            if self.is_single_input_foundry(pos, my_team):
                return pos
        return None

    def is_ore(self, pos: Position) -> bool:
        """True if pos contains any ore."""
        return self._is_ore_idx(self._idx(pos))

    def is_adjacent_to_opposite_ore(self, pos: Position, resource: ResourceType | None) -> bool:
        """True if pos is adjacent to a harvester or ore of the opposite resource type."""
        return self.is_adjacent_to_opposite_ore_idx(self._idx(pos), resource)

    def is_adjacent_to_opposite_ore_idx(self, idx: int, resource: ResourceType | None) -> bool:
        """Idx-native version of is_adjacent_to_opposite_ore."""
        if resource is None:
            return False
        if resource == ResourceType.RAW_AXIONITE:
            opposite_mask = self._bm_ore_ti
        elif resource == ResourceType.TITANIUM:
            opposite_mask = self._bm_ore_ax
        else:
            return False
        x = idx % self.width
        y = idx // self.width
        for dx, dy in _CARDINAL_OFFSETS:
            nx = x + dx
            ny = y + dy
            if not on_map_coords(nx, ny, self.width, self.height):
                continue
            if opposite_mask & (1 << (ny * self.width + nx)):
                return True
        return False

    def has_adjacent_opposite_resource_chain(self, ore_pos: Position, resource: ResourceType | None, ct: Controller) -> bool:
        """True if an ore tile or any cardinally adjacent conveyor/bridge has
        positive evidence of carrying the opposite resource."""
        if resource == ResourceType.TITANIUM:
            opposite = ResourceType.RAW_AXIONITE
        elif resource == ResourceType.RAW_AXIONITE:
            opposite = ResourceType.TITANIUM
        else:
            return False

        ore_idx = self._idx(ore_pos)
        if self._entity_type_idx[ore_idx] in _CONVEYOR_TYPE_IDXS:
            if self._get_conveyor_resource_state(ore_pos, ct, opposite) == 1:
                return True

        for d in CARDINAL_DIRECTIONS:
            adj = ore_pos.add(d)
            if not on_map(adj, self.width, self.height) or not self.is_visited(adj):
                continue
            adj_idx = self._idx(adj)
            if self._entity_type_idx[adj_idx] not in _CONVEYOR_TYPE_IDXS:
                continue
            if self._get_conveyor_resource_state(adj, ct, opposite) == 1:
                return True
        return False
    
    def has_conflict(self, resource: ResourceType | None, pos: Position, ct: Controller) -> bool:
        return self.has_conflict_idx(resource, self._idx(pos), ct)

    def has_conflict_idx(self, resource: ResourceType | None, idx: int, ct: Controller) -> bool:
        if resource is None:
            return False
        return self._get_conveyor_resource_state_idx(idx, ct, resource) == 2

    def has_input_conflict(self, resource: ResourceType | None, pos: Position, ct: Controller) -> bool:
        """True if pos is the output of a known conveyor carrying the opposite resource.
        Use this for empty tiles that don't have a conveyor yet but are fed by one."""
        return self.has_input_conflict_idx(resource, self._idx(pos), ct)

    def has_input_conflict_idx(self, resource: ResourceType | None, idx: int, ct: Controller) -> bool:
        """Idx-native version of has_input_conflict."""
        if resource is None:
            return False
        for input_idx in self._iter_mask_indices(self._input_masks[idx]):
            if self._get_conveyor_resource_state_idx(input_idx, ct, resource) == 2:
                return True
        return False

    @staticmethod
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

    @staticmethod
    def _terminal_xy_from_idx_set(end_idx_set: set[int] | None, width: int) -> tuple[tuple[int, int], ...] | None:
        if not end_idx_set:
            return None
        return tuple((idx % width, idx // width) for idx in end_idx_set)

    def _score_output_candidate_idx(self, adj_idx, adj_x, adj_y, dist, build_dist,
                                    my_team, resource, ct, core_pos, end_idx_set,
                                    dist_to_terminal, check_splitter_dir=None):
        """Idx-based version of output scoring used by hot path selection."""
        adj_label = f"({adj_x}, {adj_y})"
        etype_idx = self._entity_type_idx[adj_idx]
        if etype_idx < 0:
            if self.has_input_conflict_idx(resource, adj_idx, ct):
                log(f"    {adj_label}: skip empty opposite-resource input")
                return None
            log(f"    {adj_label}: empty dist^2={dist}")
            return (dist, False)

        eteam_idx = self._entity_team_idx[adj_idx]
        my_team_idx = _TM_INT[my_team] if my_team is not None else -1
        etype = _INT_ET[etype_idx]
        eteam = _INT_TM[eteam_idx]

        if etype_idx in _CONVEYOR_TYPE_IDXS and my_team is not None and eteam_idx == my_team_idx:
            if check_splitter_dir is not None and etype_idx == _IDX_SPLITTER:
                splitter_output_idx = self._output_idx[adj_idx]
                if splitter_output_idx >= 0:
                    splitter_dir = DELTA_TO_DIRECTION[((splitter_output_idx % self.width) - adj_x, (splitter_output_idx // self.width) - adj_y)]
                    if check_splitter_dir != splitter_dir:
                        log(f"    {adj_label}: skip ally splitter facing {splitter_dir}")
                        return None

            if self.has_conflict_idx(resource, adj_idx, ct):
                log(f"    {adj_label}: skip ally {etype} wrong/no resource")
                return None

            ally_output_idx = self._output_idx[adj_idx]
            ally_output = self._pos(ally_output_idx) if ally_output_idx >= 0 else None
            if ally_output is not None and dist_to_terminal(ally_output) >= build_dist:
                log(f"    {adj_label}: skip ally {etype} output not closer")
                return None

            terminal_idx = self._follow_chain_terminal_idx(adj_idx)
            terminal = self._pos(terminal_idx)
            if end_idx_set is not None and terminal_idx not in end_idx_set:
                is_core = core_pos is not None and abs(terminal.x - core_pos.x) <= 1 and abs(terminal.y - core_pos.y) <= 1
                if is_core or (on_map(terminal, self.width, self.height) and self.has_entity(terminal)):
                    log(f"    {adj_label}: skip ally {etype} wrong terminal {terminal}")
                    return None

            effective_idx = self._follow_chain_last_visible_idx(adj_idx)
            if effective_idx is None:
                log(f"    {adj_label}: skip ally {etype} chain leaves vision")
                return None

            effective_pos = self._pos(effective_idx)
            eff_dist = dist_to_terminal(effective_pos)
            is_fallback = resource == ResourceType.TITANIUM and ResourceType.TITANIUM in self.get_conveyor_resource_evidence(Position(adj_x, adj_y), ct)
            if is_fallback:
                log(f"    {adj_label}: fallback ally {etype} observed titanium")
            else:
                log(f"    {adj_label}: chain ally {etype} eff_dist^2={eff_dist}")
            return (eff_dist, is_fallback)

        if etype_idx == _IDX_MARKER or (etype_idx == _IDX_ROAD and my_team is not None and eteam_idx == my_team_idx):
            if self.has_input_conflict_idx(resource, adj_idx, ct):
                log(f"    {adj_label}: skip road/marker opposite-resource input")
                return None
            log(f"    {adj_label}: road dist^2={dist}")
            return (dist, False)

        log(f"    {adj_label}: skip occupied by {eteam} {etype}")
        return None

    def _get_best_output_with_fallback(self, build_pos: Position, core_pos: Position | None, ct: Controller,
                                       offsets, my_team: Team | None = None, end_positions: set | None = None,
                                       end_position_idxs: set[int] | None = None,
                                       resource: ResourceType | None = None, check_splitter: bool = False,
                                       allow_far_terminals: bool = False, label: str = "output") -> tuple[Position | None, bool]:
        """Unified helper for conveyor/bridge output selection.
        Returns (best output Position or None, whether it is fallback)."""
        if core_pos is None:
            return (None, False)

        if end_position_idxs is None and end_positions is not None:
            end_position_idxs = {self._idx(p) for p in end_positions}
        terminal_positions_xy = self._terminal_xy_from_idx_set(end_position_idxs, self.width)
        dist_to_terminal, dist_to_terminal_xy = self._make_dist_fns(terminal_positions_xy, core_pos)
        build_dist = dist_to_terminal_xy(build_pos.x, build_pos.y)
        best_terminal = None
        best_terminal_dist = INF
        best_next = None
        best_next_dist = INF
        best_fallback = None
        best_fallback_dist = INF
        build_idx = self._idx(build_pos)
        end_idx_set = end_position_idxs
        width = self.width
        height = self.height
        log(f"  {label}: build={build_pos} core={core_pos} term_dist²={build_dist} res={resource}")

        for dx, dy in offsets:
            x = build_pos.x + dx
            y = build_pos.y + dy
            if not on_map_coords(x, y, width, height):
                continue
            adj_idx = y * width + x
            dist = dist_to_terminal_xy(x, y)
            if not allow_far_terminals and dist >= build_dist:
                continue
            if self._would_create_loop_idx(build_idx, adj_idx):
                continue

            if self._is_ore_idx(adj_idx):
                continue

            is_terminal = (adj_idx in end_idx_set) if end_idx_set is not None else (core_pos is not None and abs(x - core_pos.x) <= 1 and abs(y - core_pos.y) <= 1)
            if is_terminal:
                if dist < best_terminal_dist:
                    best_terminal_dist = dist
                    best_terminal = Position(x, y)
                continue

            if allow_far_terminals and dist >= build_dist:
                continue
            if self.is_adjacent_to_opposite_ore_idx(adj_idx, resource):
                continue
            if not self._is_visited_idx(adj_idx):
                continue
            if self._env_idx[adj_idx] == _IDX_ENV_WALL:
                continue

            splitter_dir = DELTA_TO_DIRECTION[(dx, dy)] if check_splitter else None
            candidate = self._score_output_candidate_idx(
                adj_idx, x, y, dist, build_dist,
                my_team, resource, ct, core_pos, end_idx_set,
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

    def _get_best_output(self, build_pos: Position, core_pos: Position | None, ct: Controller,
                         offsets, my_team: Team | None = None, end_positions: set | None = None,
                         end_position_idxs: set[int] | None = None,
                         resource: ResourceType | None = None, check_splitter: bool = False,
                         allow_far_terminals: bool = False, label: str = "output") -> Position | None:
        """Unified helper for conveyor/bridge output selection.
        Returns the best output Position, or None."""
        result, _ = self._get_best_output_with_fallback(
            build_pos, core_pos, ct, offsets,
            my_team=my_team, end_positions=end_positions, end_position_idxs=end_position_idxs,
            resource=resource, check_splitter=check_splitter,
            allow_far_terminals=allow_far_terminals, label=label,
        )
        return result

    def get_best_conveyor_output(self, build_pos: Position, core_pos: Position | None, ct: Controller, my_team: Team | None = None, end_positions: set | None = None, resource: ResourceType | None = None) -> tuple[Direction, Position] | None:
        """Find the best cardinal-adjacent tile for a conveyor at build_pos.
        Returns (direction, next_pos) or None."""
        result = self._get_best_output(build_pos, core_pos, ct, _CARDINAL_OFFSETS,
                                       my_team=my_team, end_positions=end_positions,
                                       resource=resource, check_splitter=True,
                                       allow_far_terminals=False, label="conv_output")
        if result is None:
            return None
        return (build_pos.direction_to(result), result)

    def get_best_conveyor_output_idx(self, build_pos: Position, core_pos: Position | None, ct: Controller, my_team: Team | None = None, end_position_idxs: set[int] | None = None, resource: ResourceType | None = None) -> tuple[Direction, Position] | None:
        """Idx-native version of get_best_conveyor_output."""
        result = self._get_best_output(build_pos, core_pos, ct, _CARDINAL_OFFSETS,
                                       my_team=my_team, end_position_idxs=end_position_idxs,
                                       resource=resource, check_splitter=True,
                                       allow_far_terminals=False, label="conv_output")
        if result is None:
            return None
        return (build_pos.direction_to(result), result)

    def get_best_bridge_output(self, bridge_pos: Position, core_pos: Position | None, ct: Controller, my_team: Team | None = None, end_positions: set | None = None, resource: ResourceType | None = None) -> Position | None:
        """Find the best output tile for a bridge at bridge_pos.
        Returns Position or None."""
        return self._get_best_output(bridge_pos, core_pos, ct, _BRIDGE_OFFSETS,
                                     my_team=my_team, end_positions=end_positions,
                                     resource=resource, check_splitter=False,
                                     allow_far_terminals=True, label="bridge_output")

    def get_best_bridge_output_idx(self, bridge_pos: Position, core_pos: Position | None, ct: Controller, my_team: Team | None = None, end_position_idxs: set[int] | None = None, resource: ResourceType | None = None) -> Position | None:
        """Idx-native version of get_best_bridge_output."""
        return self._get_best_output(bridge_pos, core_pos, ct, _BRIDGE_OFFSETS,
                                     my_team=my_team, end_position_idxs=end_position_idxs,
                                     resource=resource, check_splitter=False,
                                     allow_far_terminals=True, label="bridge_output")

    def get_best_conveyor_output_with_fallback(self, build_pos: Position, core_pos: Position | None, ct: Controller, my_team: Team | None = None, end_positions: set | None = None, resource: ResourceType | None = None) -> tuple[tuple[Direction, Position] | None, bool]:
        """Find the best cardinal-adjacent tile for a conveyor at build_pos.
        Returns ((direction, next_pos) or None, whether it is fallback)."""
        result, is_fallback = self._get_best_output_with_fallback(
            build_pos, core_pos, ct, _CARDINAL_OFFSETS,
            my_team=my_team, end_positions=end_positions,
            resource=resource, check_splitter=True,
            allow_far_terminals=False, label="conv_output",
        )
        if result is None:
            return (None, False)
        return ((build_pos.direction_to(result), result), is_fallback)

    def get_best_conveyor_output_with_fallback_idx(self, build_pos: Position, core_pos: Position | None, ct: Controller, my_team: Team | None = None, end_position_idxs: set[int] | None = None, resource: ResourceType | None = None) -> tuple[tuple[Direction, Position] | None, bool]:
        """Idx-native version of get_best_conveyor_output_with_fallback."""
        result, is_fallback = self._get_best_output_with_fallback(
            build_pos, core_pos, ct, _CARDINAL_OFFSETS,
            my_team=my_team, end_position_idxs=end_position_idxs,
            resource=resource, check_splitter=True,
            allow_far_terminals=False, label="conv_output",
        )
        if result is None:
            return (None, False)
        return ((build_pos.direction_to(result), result), is_fallback)

    def get_best_bridge_output_with_fallback(self, bridge_pos: Position, core_pos: Position | None, ct: Controller, my_team: Team | None = None, end_positions: set | None = None, resource: ResourceType | None = None) -> tuple[Position | None, bool]:
        """Find the best output tile for a bridge at bridge_pos.
        Returns (Position or None, whether it is fallback)."""
        return self._get_best_output_with_fallback(
            bridge_pos, core_pos, ct, _BRIDGE_OFFSETS,
            my_team=my_team, end_positions=end_positions,
            resource=resource, check_splitter=False,
            allow_far_terminals=True, label="bridge_output",
        )

    def get_best_bridge_output_with_fallback_idx(self, bridge_pos: Position, core_pos: Position | None, ct: Controller, my_team: Team | None = None, end_position_idxs: set[int] | None = None, resource: ResourceType | None = None) -> tuple[Position | None, bool]:
        """Idx-native version of get_best_bridge_output_with_fallback."""
        return self._get_best_output_with_fallback(
            bridge_pos, core_pos, ct, _BRIDGE_OFFSETS,
            my_team=my_team, end_position_idxs=end_position_idxs,
            resource=resource, check_splitter=False,
            allow_far_terminals=True, label="bridge_output",
        )

    def indicate_entity_map(self, ct: Controller, my_team: Team):
        """Draw colored indicator dots for all tracked entities. Purpose of this
        method is to show what the builder bot *thinks* is on the map.
        Red=enemy units, Orange=enemy conveyors, Yellow=other enemy non-road,
        Green=ally units, Blue=ally conveyors, Purple=other ally non-road."""
        _UNIT_TYPES = (EntityType.CORE, EntityType.BUILDER_BOT, *TURRET_TYPES, EntityType.LAUNCHER)
        for idx in range(self.tile_count):
            entity_id = self._entity_id[idx]
            if entity_id == 0:
                continue
            etype = _INT_ET[self._entity_type_idx[idx]]
            team = _INT_TM[self._entity_team_idx[idx]]
            x = idx % self.width
            y = idx // self.width
            if etype == EntityType.ROAD or etype == EntityType.MARKER:
                continue
            pos = Position(x, y)
            if team != my_team:
                if etype in _UNIT_TYPES:
                    ct.draw_indicator_dot(pos, 255, 0, 0)      # red
                elif etype in CONVEYOR_TYPES:
                    ct.draw_indicator_dot(pos, 255, 165, 0)    # orange
                else:
                    ct.draw_indicator_dot(pos, 255, 255, 0)    # yellow
            else:
                if etype in _UNIT_TYPES:
                    ct.draw_indicator_dot(pos, 0, 255, 0)      # green
                elif etype in CONVEYOR_TYPES:
                    ct.draw_indicator_dot(pos, 0, 100, 255)    # blue
                else:
                    ct.draw_indicator_dot(pos, 180, 0, 255)    # purple
    
    def indicate_seen(self, ct: Controller):
        for idx in range(self.tile_count):
            env_idx = self._env_idx[idx]
            if env_idx < 0:
                continue
            env = _INT_ENV[env_idx]
            x = idx % self.width
            y = idx // self.width
            pos = Position(x, y)
            if env == Environment.WALL:
                ct.draw_indicator_dot(pos, 255, 0, 0)      # red
            elif env == Environment.ORE_TITANIUM:
                ct.draw_indicator_dot(pos, 0, 255, 255)    # cyan
            elif env == Environment.ORE_AXIONITE:
                ct.draw_indicator_dot(pos, 255, 0, 255)    # magenta
