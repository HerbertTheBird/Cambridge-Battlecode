from __future__ import annotations
from typing import Optional, Set, Tuple
from cambc import Controller, Position, Environment, EntityType, Team, Direction, ResourceType, GameError, GameConstants
from collections import deque
import pathing
import units.builder as builder
import comms
from log import log

_HAS_DIRECTION  = frozenset(e for e in (EntityType.ARMOURED_CONVEYOR, EntityType.BREACH, EntityType.CONVEYOR, EntityType.GUNNER, EntityType.SENTINEL, EntityType.SPLITTER))
_CONVEYOR_TYPES = frozenset(
    e for e in (
        EntityType.CONVEYOR, 
        EntityType.ARMOURED_CONVEYOR,
        EntityType.BRIDGE, 
        EntityType.SPLITTER
    )
)

_ACCEPT_ORE = frozenset(
    e for e in (
        EntityType.CONVEYOR, 
        EntityType.ARMOURED_CONVEYOR,
        EntityType.BRIDGE, 
        EntityType.SPLITTER, 
        EntityType.BREACH, 
        EntityType.CORE, 
        EntityType.FOUNDRY, 
        EntityType.GUNNER, 
        EntityType.SENTINEL
    )
)

_TURRET_TYPES = frozenset(
    e for e in (
        EntityType.LAUNCHER, 
        EntityType.GUNNER,
        EntityType.SENTINEL, 
        EntityType.BREACH
    )
)

_ET_ROAD              = EntityType.ROAD
_ET_MARKER            = EntityType.MARKER
_ET_BARRIER           = EntityType.BARRIER
_ET_CONVEYOR          = EntityType.CONVEYOR
_ET_ARMOURED_CONVEYOR = EntityType.ARMOURED_CONVEYOR
_ET_BRIDGE            = EntityType.BRIDGE
_ET_SPLITTER          = EntityType.SPLITTER
_ET_CORE              = EntityType.CORE
_ET_BUILDER_BOT       = EntityType.BUILDER_BOT
_ET_HARVESTER         = EntityType.HARVESTER
_ET_FOUNDRY           = EntityType.FOUNDRY
_ET_LAUNCHER          = EntityType.LAUNCHER
_ET_GUNNER            = EntityType.GUNNER
_ET_SENTINEL          = EntityType.SENTINEL
_ET_BREACH            = EntityType.BREACH
_RT_AXIONITE          = ResourceType.RAW_AXIONITE
_RT_TITANIUM          = ResourceType.TITANIUM
_ENV_EMPTY   = Environment.EMPTY
_ENV_ORE_AX  = Environment.ORE_AXIONITE
_ENV_ORE_TI  = Environment.ORE_TITANIUM
_ET_INT =   {t: i for i, t in enumerate(EntityType)}
_INT_ET =   {i: t for i, t in enumerate(EntityType)}
_RT_INT =   {t: i for i, t in enumerate(ResourceType)}
_INT_RT =   {i: t for i, t in enumerate(ResourceType)}
_ENV_INT =  {t: i for i, t in enumerate(Environment)}
_INT_ENV =  {i: t for i, t in enumerate(Environment)}
_DIR_INT =  {t: i for i, t in enumerate(Direction)}
_INT_DIR =  {i: t for i, t in enumerate(Direction)}
_TM_INT =   {t: i for i, t in enumerate(Team)}
_INT_TM =   {i: t for i, t in enumerate(Team)}

# Claude gen'ed explanation:
# Fast enum->int lists: index by id(enum)//16 & mask, but simpler:
# use a list where list[enum_int_index] = int_index.  We build these
# as identity since _ET_INT already maps enum->sequential int.
# For the hot path we want:  et_idx = _ET_TO_IDX[et]  where et is the enum.
# Python enums from cambc don't have a .value that's an int index, so we
# keep the dict lookups for the initial et->et_idx conversion, but replace
# all *subsequent* frozenset membership tests with bool-list indexing.

# Pre-computed indices for fast list access
_IDX_CONVEYOR          = _ET_INT[EntityType.CONVEYOR]
_IDX_ARMOURED_CONVEYOR = _ET_INT[EntityType.ARMOURED_CONVEYOR]
_IDX_BRIDGE            = _ET_INT[EntityType.BRIDGE]
_IDX_SPLITTER          = _ET_INT[EntityType.SPLITTER]
_IDX_CORE              = _ET_INT[EntityType.CORE]
_IDX_HARVESTER         = _ET_INT[EntityType.HARVESTER]
_IDX_FOUNDRY           = _ET_INT[EntityType.FOUNDRY]
_IDX_ROAD              = _ET_INT[EntityType.ROAD]
_IDX_BARRIER           = _ET_INT[EntityType.BARRIER]
_IDX_MARKER            = _ET_INT[EntityType.MARKER]
_IDX_GUNNER            = _ET_INT[EntityType.GUNNER]
_IDX_SENTINEL          = _ET_INT[EntityType.SENTINEL]
_IDX_BREACH            = _ET_INT[EntityType.BREACH]
_IDX_LAUNCHER          = _ET_INT[EntityType.LAUNCHER]

_IDX_BUILDER_BOT       = _ET_INT[EntityType.BUILDER_BOT]

_MAX_HP_BY_IDX = [0] * len(EntityType)
_MAX_HP_BY_IDX[_IDX_CONVEYOR]           = GameConstants.CONVEYOR_MAX_HP
_MAX_HP_BY_IDX[_IDX_ARMOURED_CONVEYOR]  = GameConstants.ARMOURED_CONVEYOR_MAX_HP
_MAX_HP_BY_IDX[_IDX_BRIDGE]             = GameConstants.BRIDGE_MAX_HP
_MAX_HP_BY_IDX[_IDX_SPLITTER]           = GameConstants.SPLITTER_MAX_HP
_MAX_HP_BY_IDX[_IDX_HARVESTER]          = GameConstants.HARVESTER_MAX_HP
_MAX_HP_BY_IDX[_IDX_FOUNDRY]            = GameConstants.FOUNDRY_MAX_HP
_MAX_HP_BY_IDX[_IDX_ROAD]               = GameConstants.ROAD_MAX_HP
_MAX_HP_BY_IDX[_IDX_BARRIER]            = GameConstants.BARRIER_MAX_HP
_MAX_HP_BY_IDX[_IDX_GUNNER]             = GameConstants.GUNNER_MAX_HP
_MAX_HP_BY_IDX[_IDX_SENTINEL]           = GameConstants.SENTINEL_MAX_HP
_MAX_HP_BY_IDX[_IDX_BREACH]             = GameConstants.BREACH_MAX_HP
_MAX_HP_BY_IDX[_IDX_LAUNCHER]           = GameConstants.LAUNCHER_MAX_HP
_MAX_HP_BY_IDX[_IDX_CORE]               = GameConstants.CORE_MAX_HP

_IDX_ENV_EMPTY  = _ENV_INT[Environment.EMPTY]
_IDX_ENV_WALL   = _ENV_INT[Environment.WALL]
_IDX_ENV_ORE_TI = _ENV_INT[Environment.ORE_TITANIUM]
_IDX_ENV_ORE_AX = _ENV_INT[Environment.ORE_AXIONITE]

_NUM_ET   = len(EntityType)
_NUM_TEAM = len(Team)
_NUM_ENV  = len(Environment)

# Bool lookup tables indexed by et_idx — avoid frozenset hashing in hot paths
_IS_CONVEYOR = [False] * _NUM_ET
for _e in _CONVEYOR_TYPES: _IS_CONVEYOR[_ET_INT[_e]] = True

_HAS_DIR = [False] * _NUM_ET
for _e in _HAS_DIRECTION: _HAS_DIR[_ET_INT[_e]] = True

_IS_BLOCKED = [False] * _NUM_ET
for _e in (EntityType.HARVESTER, EntityType.FOUNDRY, EntityType.GUNNER,
           EntityType.SENTINEL, EntityType.BREACH, EntityType.LAUNCHER):
    _IS_BLOCKED[_ET_INT[_e]] = True

_DIR_CENTRE = Direction.CENTRE
_ALL_DIRECTIONS = tuple(Direction)
_DIRECTIONS = (
    Direction.NORTH, Direction.NORTHEAST, Direction.EAST, Direction.SOUTHEAST,
    Direction.SOUTH, Direction.SOUTHWEST, Direction.WEST, Direction.NORTHWEST,
)
_CARDINAL = (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST)
_DIRECTION_DELTAS = {d: d.delta() for d in Direction}
# Int-indexed version: _DIRECTION_DELTAS_I[dir_int] = (dx, dy)
_DIRECTION_DELTAS_I = [d.delta() for d in Direction]

def pos_add(pos: Position, d: Direction) -> Position:
    """Fast Position.add() replacement using cached deltas."""
    dx, dy = _DIRECTION_DELTAS[d]
    return Position(pos.x + dx, pos.y + dy)

def direction_to(src: Position, dst: Position) -> Direction:
    """Fast nearest-octant replacement for Position.direction_to()."""
    dx = dst.x - src.x
    dy = dst.y - src.y
    if dx == 0 and dy == 0:
        return Direction.CENTRE

    ax = dx if dx >= 0 else -dx
    ay = dy if dy >= 0 else -dy

    # tan(22.5 deg) ~= 0.41421356, using integer math to avoid trig.
    if ay * 100000 <= ax * 41422:
        return Direction.EAST if dx > 0 else Direction.WEST
    if ax * 100000 <= ay * 41422:
        return Direction.SOUTH if dy > 0 else Direction.NORTH
    if dx > 0:
        return Direction.SOUTHEAST if dy > 0 else Direction.NORTHEAST
    return Direction.SOUTHWEST if dy > 0 else Direction.NORTHWEST

_rc: Controller
_width = _height = 0
_MAP_CENTER = None
_prev_pos: Position = None
_my_pos: Position = None           # cached rc.get_position(), updated on move
_my_team: Team = None
_my_team_idx: int = 0

# Per-tile arrays (scalar values that can't be bitmasks)
_building_id: list[int] = []
_building_et_idx: list[int] = []
_building_hp: list[int] = []
_building_dir: list[int] = []
_building_conv_target: list[int] = []
_conv_reverse: list[int] = []   # reverse[tn] = bitmask of my conveyors whose output target is tile tn

# Bitmask lists indexed by _ET_INT / _TM_INT / _ENV_INT
_bm_et: list[int] = []      # one bitmask per EntityType
_bm_team: list[int] = []    # one bitmask per Team
_bm_env: list[int] = []     # one bitmask per Environment
_bm_seen: int = 0           # seen tiles (observed OR derived via symmetry)
_bm_seen_observed: int = 0  # seen tiles (directly observed only)
_bm_any_building: int = 0   # union of all tracked building bitmasks

# Derived bitmasks
_bm_blocked: int = 0            # walls + non-passable buildings + enemy core area
_bm_conveyors: int = 0          # all conveyor-type buildings + my core area
_bm_conveyor_targets: int = 0   # output target tiles of conveyors
_bm_my_core_area: int = 0       # my core 3x3
_bm_their_core_area: int = 0    # enemy core 3x3
_bm_enemy_launch_adj: int = 0   # tiles adjacent to enemy launchers
_bm_routable: int = 0           # my team's conveyor-type buildings
_bm_route_targets: int = 0      # tiles route state can path toward
_bm_conv_loaded: int = 0        # conveyor-type buildings with a stored resource
_bm_conv_raw_ax: int = 0        # conveyors observed containing raw axionite
_bm_conv_ti: int = 0            # conveyors observed containing titanium
_bm_conv_refined: int = 0       # conveyors observed containing refined axionite
_bm_ti_fed: int = 0             # targets of conveyors believed to carry titanium
_bm_ax_fed: int = 0             # targets of conveyors believed to carry refined axionite
_bm_dead_end: int = 0           # routable conveyors whose output is not connected to ore-accepting network
_bm_enemy_turret_threat: int = 0  # tiles enemy turrets can shoot (soft | hard), kept for back-compat
_bm_enemy_soft_threat: int = 0    # tiles enemy sentinels can shoot (low dps)
_bm_enemy_hard_threat: int = 0    # tiles enemy gunners/breaches can shoot (high dps)
_bm_my_gunner_claims: int = 0     # tiles already covered by one of my gunners' current ray
_bm_conv_by_dir: list[int] = []   # per facing (0..7): CONVEYOR|ARMOURED_CONVEYOR tiles facing that direction
_board_mask: int = 0              # (1 << (w*h)) - 1, cached
_bm_visible: int = 0              # tiles visible this turn
_nearby_tiles: list = []           # cached rc.get_nearby_tiles() for this round
_nearby_tiles_pos = None           # position at which _nearby_tiles was computed
_bm_damaged: int = 0              # buildings not at full HP
_bm_very_damaged: int = 0         # buildings with > 2 damage

# Builder bot tracking
_bm_friendly_bots: int = 0       # bitmask of known friendly builder bot positions
_bm_enemy_bots: int = 0          # bitmask of known enemy builder bot positions
_bot_pos: dict[int, int] = {}    # uid -> tile index (both teams)
_bot_team: dict[int, int] = {}   # uid -> team_idx
_bot_at: dict[int, int] = {}    # tile index -> uid

_max_id_by_round: list[int] = []  # max_id_by_round[round] = max entity id seen up to that round
_max_id_seen: int = 0
_new_marker_messages: list[tuple[int, Position, Position, int, int]] = []

# Per-turn cached derived masks (rebuilt in update())
_bm_others_5x5: int = 0          # union of other friendly bots' 2-Chebyshev zones
_bm_others_3x3: int = 0          # union of other friendly bots' 1-Chebyshev zones
_bm_harv_adj: int = 0            # Manhattan adjacency of all harvesters
_bm_passable_FFF: int = 0        # cached (_board_mask & ~get_avoid(False, False, False))

# Structural state version — bumped on any structural map change (build/destroy
# of a tracked building). Used to cheaply invalidate caches that only change on
# structural updates. HP / loaded-resource transitions are NOT counted.
_struct_version: int = 0

# --- Turret attack offset tables (dir_idx 0-7 -> list of (dx,dy)) ---
_DIR_VECS = [(0,-1),(1,-1),(1,0),(1,1),(0,1),(-1,1),(-1,0),(-1,-1)]
def _precompute_breach_offsets():
    """Breach: r²≤BREACH_ATTACK_RADIUS_SQ, 180° semicircle centered on facing direction."""
    result = [[] for _ in range(8)]
    for di in range(8):
        ddx, ddy = _DIR_VECS[di]
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                if dx == 0 and dy == 0:
                    continue
                if dx*dx + dy*dy > GameConstants.BREACH_ATTACK_RADIUS_SQ:
                    continue
                dot = dx * ddx + dy * ddy
                if dot >= 0:
                    result[di].append((dx, dy))
    return result

def _precompute_sentinel_offsets():
    """Sentinel: cardinal=line of 4, diagonal=line of 3, each point expanded 3×3."""
    result = [[] for _ in range(8)]
    for di in range(8):
        ddx, ddy = _DIR_VECS[di]
        is_cardinal = (ddx == 0 or ddy == 0)
        line_len = 4 if is_cardinal else 3
        tiles = set()
        for step in range(1, line_len + 1):
            cx, cy = ddx * step, ddy * step
            for ey in range(-1, 2):
                for ex in range(-1, 2):
                    px, py = cx + ex, cy + ey
                    if px == 0 and py == 0:
                        continue
                    tiles.add((px, py))
        result[di] = list(tiles)
    return result

def _precompute_gunner_rays():
    """Gunner: straight line rays in all 8 directions, ordered by distance.
    Returns dict keyed by facing dir_idx -> list of (ray_dir_idx, [(dx,dy)...])."""
    rays = []
    for di in range(8):
        ddx, ddy = _DIR_VECS[di]
        ray = []
        for step in range(1, 8):
            px, py = ddx * step, ddy * step
            if px*px + py*py > GameConstants.GUNNER_VISION_RADIUS_SQ:
                break
            ray.append((px, py))
        rays.append(ray)
    return rays

_BREACH_OFFSETS = _precompute_breach_offsets()
_SENTINEL_OFFSETS = _precompute_sentinel_offsets()
_GUNNER_RAYS = _precompute_gunner_rays()

_not_left_col: int = 0   # mask with all bits EXCEPT x=0 column
_not_right_col: int = 0  # mask with all bits EXCEPT x=width-1 column
_not_left_col_2: int = 0   # mask with all bits EXCEPT x in {0, 1}
_not_right_col_2: int = 0  # mask with all bits EXCEPT x in {w-2, w-1}
_not_left_col_3: int = 0   # mask with all bits EXCEPT x in {0, 1, 2}
_not_right_col_3: int = 0  # mask with all bits EXCEPT x in {w-3, w-2, w-1}

_my_core: Position | None = None
_their_core: Position | None = None
_predicted_enemy_core: Position | None = None
_core_id: int | None = None
_hor_sym = True
_ver_sym = True
_rot_sym = True
_solved_sym = False
_rush_tiebroken = 0

def ground_at(x, y):
    bit = 1 << (x + y * _width)
    if _bm_env[_IDX_ENV_WALL] & bit: return Environment.WALL
    if _bm_env[_IDX_ENV_ORE_TI] & bit: return Environment.ORE_TITANIUM
    if _bm_env[_IDX_ENV_ORE_AX] & bit: return Environment.ORE_AXIONITE
    return Environment.EMPTY
def seen_at(x, y):
    return bool(_bm_seen & (1 << (x + y * _width)))
def id_at(x, y):
    return _building_id[x+y*_width]
def type_at(x, y):
    et_idx = _building_et_idx[x + y * _width]
    if et_idx >= 0:
        return _INT_ET[et_idx]
    return None
def hp_at(x, y):
    return _building_hp[x+y*_width]
def team_at(x, y):
    bit = 1 << (x + y * _width)
    if _bm_team[0] & bit: return _INT_TM[0]
    if _bm_team[1] & bit: return _INT_TM[1]
    return None
def dir_at(x, y):
    return _INT_DIR[_building_dir[x+y*_width]]
def conv_target_at(x, y):
    return Position(_building_conv_target[x+y*_width]%_width, _building_conv_target[x+y*_width]//_width)
def is_conveyor(type):
    return type in _CONVEYOR_TYPES
def is_turret(type):
    return type in _TURRET_TYPES
def in_bounds(pos: Position) -> bool:
    return 0 <= pos.x < _width and 0 <= pos.y < _height
def in_bounds_coords(x, y) -> bool:
    return 0 <= x < _width and 0 <= y < _height


def positions_to_mask(positions) -> int:
    """Convert an iterable of Positions to a bitmask."""
    mask = 0
    w = _width
    for p in positions:
        mask |= 1 << (p.x + p.y * w)
    return mask

def iter_mask(mask):
    """Yield Positions from a bitmask."""
    w = _width
    while mask:
        lsb = mask & -mask
        n = lsb.bit_length() - 1
        yield Position(n % w, n // w)
        mask ^= lsb


def expand_chebyshev(mask: int) -> int:
    """Expand a bitmask by 1 Chebyshev step (king-move flood)."""
    w = _width
    h = mask | ((mask & _not_right_col) << 1) | ((mask & _not_left_col) >> 1)
    return (h | (h << w) | (h >> w)) & _board_mask


def expand_manhattan(mask: int) -> int:
    """Expand a bitmask by 1 Manhattan step (4-directional flood)."""
    w = _width
    return (mask | ((mask & _not_right_col) << 1) | ((mask & _not_left_col) >> 1) | (mask << w) | (mask >> w)) & _board_mask


# Shift masks for turret aggregate computation (initialized in init())
_turret_shift_masks: dict[tuple[int,int], int] = {}

def _build_turret_shift_masks():
    """Build column-aware shift masks for each unique (dx,dy) offset used by turrets."""
    global _turret_shift_masks
    w = _width
    h = _height
    offsets = set()
    for di in range(8):
        for dx, dy in _BREACH_OFFSETS[di]:
            offsets.add((dx, dy))
        for dx, dy in _SENTINEL_OFFSETS[di]:
            offsets.add((dx, dy))
            offsets.add((-dx, -dy))  # reversed for attack reachability
    _turret_shift_masks = {}
    for dx, dy in offsets:
        if abs(dx) >= w or abs(dy) >= h:
            continue
        x0 = max(0, -dx)
        x1 = min(w, w - dx)
        y0 = max(0, -dy)
        y1 = min(h, h - dy)
        if x0 >= x1 or y0 >= y1:
            continue
        row_bits = ((1 << (x1 - x0)) - 1) << x0
        nrows = y1 - y0
        block = row_bits * ((1 << (nrows * w)) - 1) // ((1 << w) - 1)
        _turret_shift_masks[(dx, dy)] = block << (y0 * w)


def turret_attack_mask(pos_n: int, dir_idx: int, turret_type: int) -> int:
    """Return bitmask of tiles a turret at pos_n facing dir_idx can attack.
    turret_type: _IDX_BREACH, _IDX_GUNNER, or _IDX_SENTINEL."""
    w = _width
    h = _height
    px = pos_n % w
    py = pos_n // w
    result = 0

    if turret_type == _IDX_BREACH:
        for dx, dy in _BREACH_OFFSETS[dir_idx]:
            nx, ny = px + dx, py + dy
            if 0 <= nx < w and 0 <= ny < h:
                result |= 1 << (nx + ny * w)
    elif turret_type == _IDX_SENTINEL:
        for dx, dy in _SENTINEL_OFFSETS[dir_idx]:
            nx, ny = px + dx, py + dy
            if 0 <= nx < w and 0 <= ny < h:
                result |= 1 << (nx + ny * w)
    elif turret_type == _IDX_GUNNER:
        walls = _bm_env[_IDX_ENV_WALL]
        for ray_di in range(8):
            for dx, dy in _GUNNER_RAYS[ray_di]:
                nx, ny = px + dx, py + dy
                if not (0 <= nx < w and 0 <= ny < h):
                    break
                bit = 1 << (nx + ny * w)
                if walls & bit:
                    break
                result |= bit
    return result


_turret_threat_cache_version: int = -1
_turret_threat_cache: tuple[int, int] = (0, 0)


def _compute_enemy_turret_threat() -> tuple[int, int]:
    """Compute (soft, hard) threat bitmasks.

    Soft: sentinels (low dps).
    Hard: gunners + breaches (high dps).

    Sentinel/breach/gunner all use bit-parallel shift: group turrets by facing,
    then fold all offsets for that facing into one accumulator. Gunner rays
    additionally blank wall bits each step, stopping those rays without
    branching."""
    global _turret_threat_cache_version, _turret_threat_cache
    if _struct_version == _turret_threat_cache_version:
        return _turret_threat_cache

    w = _width
    enemy_idx = 1 - _my_team_idx
    soft = 0
    hard = 0
    building_dir = _building_dir
    bm_team_enemy = _bm_team[enemy_idx]
    shift_masks = _turret_shift_masks

    for turret_idx, offsets_table, is_hard in (
        (_IDX_SENTINEL, _SENTINEL_OFFSETS, False),
        (_IDX_BREACH, _BREACH_OFFSETS, True),
    ):
        turrets = _bm_et[turret_idx] & bm_team_enemy
        if not turrets:
            continue
        dir_masks = [0] * 8
        m = turrets
        while m:
            lsb = m & -m
            n = lsb.bit_length() - 1
            dir_masks[building_dir[n]] |= lsb
            m ^= lsb
        acc = 0
        for di in range(8):
            dm = dir_masks[di]
            if not dm:
                continue
            for dx, dy in offsets_table[di]:
                shift_mask = shift_masks.get((dx, dy))
                if shift_mask is None:
                    continue
                offset = dx + dy * w
                if offset > 0:
                    acc |= (dm & shift_mask) << offset
                else:
                    acc |= (dm & shift_mask) >> (-offset)
        if is_hard:
            hard |= acc
        else:
            soft |= acc

    gunners = _bm_et[_IDX_GUNNER] & bm_team_enemy
    if gunners:
        walls = _bm_env[_IDX_ENV_WALL]
        not_blocked = _board_mask & ~walls
        dir_gunners = [0] * 8
        m = gunners
        while m:
            lsb = m & -m
            n = lsb.bit_length() - 1
            dir_gunners[building_dir[n]] |= lsb
            m ^= lsb
        for d in range(8):
            cur = dir_gunners[d]
            if not cur:
                continue
            dx, dy = _DIR_VECS[d]
            sm = shift_masks.get((dx, dy))
            if sm is None:
                continue
            offset = dx + dy * w
            max_step = len(_GUNNER_RAYS[d])
            for _ in range(max_step):
                cur &= sm
                if not cur:
                    break
                if offset >= 0:
                    cur <<= offset
                else:
                    cur >>= -offset
                cur &= not_blocked  # bits at walls zero out: ray dies there
                if not cur:
                    break
                hard |= cur

    _turret_threat_cache_version = _struct_version
    _turret_threat_cache = (soft, hard)
    return _turret_threat_cache


_my_gunner_claims_cache_version: int = -1
_my_gunner_claims_cache: int = 0


def _compute_my_gunner_claims() -> int:
    """Bitmask of tiles already covered by one of my gunners' current ray.
    Batched per-facing shift; wall bits zero out so those rays die in-place."""
    global _my_gunner_claims_cache_version, _my_gunner_claims_cache
    if _struct_version == _my_gunner_claims_cache_version:
        return _my_gunner_claims_cache

    w = _width
    gunners = _bm_et[_IDX_GUNNER] & _bm_team[_my_team_idx]
    if not gunners:
        _my_gunner_claims_cache_version = _struct_version
        _my_gunner_claims_cache = 0
        return 0

    walls = _bm_env[_IDX_ENV_WALL]
    not_blocked = _board_mask & ~walls
    building_dir = _building_dir
    shift_masks = _turret_shift_masks

    dir_gunners = [0] * 8
    m = gunners
    while m:
        lsb = m & -m
        n = lsb.bit_length() - 1
        dir_gunners[building_dir[n]] |= lsb
        m ^= lsb

    claims = 0
    for d in range(8):
        cur = dir_gunners[d]
        if not cur:
            continue
        dx, dy = _DIR_VECS[d]
        sm = shift_masks.get((dx, dy))
        if sm is None:
            continue
        offset = dx + dy * w
        max_step = len(_GUNNER_RAYS[d])
        for _ in range(max_step):
            cur &= sm
            if not cur:
                break
            if offset >= 0:
                cur <<= offset
            else:
                cur >>= -offset
            cur &= not_blocked
            if not cur:
                break
            claims |= cur

    _my_gunner_claims_cache_version = _struct_version
    _my_gunner_claims_cache = claims
    return claims


_conv_by_dir_cache_version: int = -1
_conv_by_dir_cache: list[int] = [0] * 8


def _compute_conv_by_dir() -> list[int]:
    """Per facing (0..7): CONVEYOR|ARMOURED_CONVEYOR tiles with that output
    direction. Cached on _struct_version — only rebuilt on structural changes
    (conveyor build/destroy)."""
    global _conv_by_dir_cache_version, _conv_by_dir_cache
    if _struct_version == _conv_by_dir_cache_version:
        return _conv_by_dir_cache

    result = [0] * 8
    bd = _building_dir
    convs = _bm_et[_IDX_CONVEYOR] | _bm_et[_IDX_ARMOURED_CONVEYOR]
    m = convs
    while m:
        lsb = m & -m
        n = lsb.bit_length() - 1
        d = bd[n]
        if 0 <= d < 8:
            result[d] |= lsb
        m ^= lsb

    _conv_by_dir_cache_version = _struct_version
    _conv_by_dir_cache = result
    return result


def update_at(pos: Position) -> None:
    """Re-scan a single tile from the controller and update all bitmasks. Call after any build/destroy."""
    global _bm_blocked, _bm_conveyors, _bm_conveyor_targets, _bm_conv_loaded, _bm_conv_raw_ax, _bm_conv_ti, _bm_conv_refined, _bm_dead_end, _bm_damaged, _bm_very_damaged, _bm_any_building, _bm_harv_adj, _struct_version
    if not in_bounds(pos):
        return

    rc = _rc
    n = pos.x + pos.y * _width
    bit = 1 << n
    old_id = _building_id[n]
    old_et_idx = _building_et_idx[n]
    old_dir = _building_dir[n]
    old_target = _building_conv_target[n]

    had_harvester = old_et_idx == _IDX_HARVESTER

    # Clear old bitmasks
    if old_et_idx >= 0:
        _bm_et[old_et_idx] &= ~bit
        _bm_any_building &= ~bit
        if _IS_CONVEYOR[old_et_idx]:
            tn = _building_conv_target[n]
            if tn >= 0:
                _bm_conveyor_targets &= ~(1 << tn)
            if (_bm_team[_my_team_idx] & bit) and tn >= 0:
                _conv_reverse[tn] &= ~bit
        for i in range(_NUM_TEAM):
            if _bm_team[i] & bit:
                _bm_team[i] &= ~bit
                break
        _bm_blocked &= ~bit
        _bm_conveyors &= ~bit
        _bm_conv_loaded &= ~bit
        _bm_conv_raw_ax &= ~bit
        _bm_conv_ti &= ~bit
        _bm_conv_refined &= ~bit
        _bm_dead_end &= ~bit
        _bm_damaged &= ~bit
        _bm_very_damaged &= ~bit
        _building_id[n] = 0
        _building_et_idx[n] = -1
        _building_hp[n] = 0
        _building_dir[n] = 0
        _building_conv_target[n] = 0

    # Read current state from controller
    entity_id = rc.get_tile_building_id(pos)
    if entity_id is None:
        if old_id or old_et_idx >= 0 or old_target >= 0:
            _struct_version += 1
        return
    global _max_id_seen
    if entity_id > _max_id_seen:
        _max_id_seen = entity_id
    et = rc.get_entity_type(entity_id)
    if et == EntityType.MARKER:
        if old_id or old_et_idx >= 0 or old_target >= 0:
            _struct_version += 1
        return

    et_idx = _ET_INT[et]
    direction = rc.get_direction(entity_id) if _HAS_DIR[et_idx] else None
    team_idx = _TM_INT[rc.get_team(entity_id)]

    target = None
    if et == EntityType.BRIDGE:
        target = rc.get_bridge_target(entity_id)
    elif _IS_CONVEYOR[et_idx] and direction is not None:
        dx, dy = _DIRECTION_DELTAS_I[_DIR_INT[direction]]
        target = Position(pos.x + dx, pos.y + dy)

    _building_id[n] = entity_id
    _building_et_idx[n] = et_idx
    _building_hp[n] = rc.get_hp(entity_id)
    _building_dir[n] = _DIR_INT[direction] if direction else 0
    _building_conv_target[n] = (target.x + target.y * _width) if target else -1

    _bm_et[et_idx] |= bit
    _bm_team[team_idx] |= bit
    _bm_any_building |= bit

    _freshly_loaded = False
    if _IS_CONVEYOR[et_idx]:
        _bm_conveyors |= bit
        res = rc.get_stored_resource(entity_id)
        if res is not None:
            _bm_conv_loaded |= bit
            _freshly_loaded = True
            if res == ResourceType.RAW_AXIONITE:
                _bm_conv_raw_ax |= bit
                _bm_conv_ti &= ~bit
                _bm_conv_refined &= ~bit
            elif res == ResourceType.TITANIUM:
                _bm_conv_ti |= bit
                _bm_conv_raw_ax &= ~bit
                _bm_conv_refined &= ~bit
            else:
                _bm_conv_refined |= bit
                _bm_conv_raw_ax &= ~bit
                _bm_conv_ti &= ~bit
        if _building_conv_target[n]:
            _bm_conveyor_targets |= (1 << _building_conv_target[n])
            if team_idx == _my_team_idx:
                _conv_reverse[_building_conv_target[n]] |= bit

    if _IS_BLOCKED[et_idx]:
        _bm_blocked |= bit

    # Damaged check
    max_hp = _MAX_HP_BY_IDX[et_idx]
    hp = _building_hp[n]
    if hp < max_hp:
        _bm_damaged |= bit
    if hp < max_hp - 2:
        _bm_very_damaged |= bit

    if _freshly_loaded:
        res_ax = bool(_bm_conv_raw_ax & bit)
        res_ti = not res_ax and bool(_bm_conv_ti & bit)
        tn = _building_conv_target[n]
        for _ in range(3):
            if tn < 0:
                break
            tbit = 1 << tn
            if not (_bm_conveyors & tbit):
                break
            if res_ax:
                if not ((_bm_conv_ti | _bm_conv_refined) & tbit):
                    _bm_conv_raw_ax |= tbit
            elif res_ti:
                _bm_conv_ti |= tbit
                _bm_conv_raw_ax &= ~tbit
                _bm_conv_refined &= ~tbit
            else:
                _bm_conv_refined |= tbit
                _bm_conv_raw_ax &= ~tbit
                _bm_conv_ti &= ~tbit
            tn = _building_conv_target[tn]

    # Refresh harvester adjacency if a harvester was added or removed
    has_harvester = _building_et_idx[n] == _IDX_HARVESTER
    if had_harvester != has_harvester:
        harv = _bm_et[_IDX_HARVESTER]
        _bm_harv_adj = expand_manhattan(harv) if harv else 0

    if (
        old_id != _building_id[n]
        or old_et_idx != _building_et_idx[n]
        or old_dir != _building_dir[n]
        or old_target != _building_conv_target[n]
    ):
        _struct_version += 1

def update_env_at(pos: Position) -> None:
    """Record directly observed terrain for a single tile."""
    global _bm_seen, _bm_seen_observed
    if not in_bounds(pos):
        return

    n = pos.x + pos.y * _width
    bit = 1 << n
    _bm_seen_observed |= bit
    if _bm_seen & bit:
        return

    env = _rc.get_tile_env(pos)
    env_idx = _ENV_INT[env]
    _bm_env[env_idx] |= bit
    _bm_seen |= bit

def update_move() -> None:
    """After moving, re-scan tiles that are now visible but weren't from the previous position."""
    global _bm_visible, _prev_pos, _nearby_tiles, _nearby_tiles_pos, _my_pos
    rc = _rc
    new_pos = rc.get_position()
    _my_pos = new_pos
    if new_pos == _prev_pos:
        return
    _prev_pos = new_pos

    width = _width
    if _nearby_tiles_pos == new_pos:
        nearby = _nearby_tiles
    else:
        nearby = rc.get_nearby_tiles()
        _nearby_tiles = nearby
        _nearby_tiles_pos = new_pos
    new_visible = 0
    for tile in nearby:
        new_visible |= 1 << (tile.x + tile.y * width)

    newly_visible = new_visible & ~_bm_visible
    _bm_visible = new_visible

    if not newly_visible:
        return

    mask = newly_visible
    while mask:
        lsb = mask & -mask
        n = lsb.bit_length() - 1
        pos = Position(n % width, n // width)
        update_env_at(pos)
        update_at(pos)
        mask ^= lsb
    
    pathing.rebuild_broken_barriers(rc)


def init(c: Controller):
    global _rc, _width, _height
    global _my_team, _my_team_idx
    global _building_id, _building_et_idx, _building_hp, _building_dir, _building_conv_target, _conv_reverse
    global _bm_et, _bm_team, _bm_env, _bm_seen, _bm_seen_observed, _bm_any_building
    global _bm_blocked, _bm_conveyors, _bm_conveyor_targets
    global _bm_my_core_area, _bm_their_core_area, _bm_enemy_launch_adj
    global _not_left_col, _not_right_col, _not_left_col_2, _not_right_col_2, _not_left_col_3, _not_right_col_3
    global _MAP_CENTER, _board_mask, _bm_conv_by_dir, _bm_passable_FFF
    global _struct_version
    global _turret_threat_cache_version, _turret_threat_cache
    global _my_gunner_claims_cache_version, _my_gunner_claims_cache
    global _conv_by_dir_cache_version, _conv_by_dir_cache
    _rc = c
    _my_team = _rc.get_team()
    _my_team_idx = _TM_INT[_my_team]
    _width = _rc.get_map_width()
    _height = _rc.get_map_height()
    _MAP_CENTER = Position(_width // 2, _height // 2)
    tiles = _width * _height
    _board_mask = (1 << tiles) - 1
    _building_id          = [0] * tiles
    _building_et_idx      = [-1] * tiles
    _building_hp          = [0] * tiles
    _building_dir         = [0] * tiles
    _building_conv_target = [0] * tiles
    _conv_reverse         = [0] * tiles

    _bm_et   = [0] * _NUM_ET
    _bm_team = [0] * _NUM_TEAM
    _bm_env  = [0] * _NUM_ENV
    _bm_seen = 0
    _bm_seen_observed = 0
    _bm_any_building = 0
    _bm_blocked = 0
    _bm_conveyors = 0
    _bm_conveyor_targets = 0
    _bm_conv_by_dir = [0] * 8
    _bm_passable_FFF = 0
    _struct_version = 0
    _turret_threat_cache_version = -1
    _turret_threat_cache = (0, 0)
    _my_gunner_claims_cache_version = -1
    _my_gunner_claims_cache = 0
    _conv_by_dir_cache_version = -1
    _conv_by_dir_cache = [0] * 8

    # Column masks for safe bit-shifting (prevent wrap-around)
    left_col = 0
    right_col = 0
    for y in range(_height):
        left_col |= 1 << (y * _width)
        right_col |= 1 << ((_width - 1) + y * _width)
    _not_left_col = ~left_col
    _not_right_col = ~right_col
    left_col_2 = left_col | (left_col << 1)
    right_col_2 = right_col | (right_col >> 1)
    _not_left_col_2 = ~left_col_2
    _not_right_col_2 = ~right_col_2
    left_col_3 = left_col_2 | (left_col << 2)
    right_col_3 = right_col_2 | (right_col >> 2)
    _not_left_col_3 = ~left_col_3
    _not_right_col_3 = ~right_col_3
    _build_turret_shift_masks()
    global _bm_friendly_bots, _bm_enemy_bots, _bot_pos, _bot_team, _bot_at
    _bm_my_core_area = 0
    _bm_their_core_area = 0
    _bm_enemy_launch_adj = 0
    _bm_friendly_bots = 0
    _bm_enemy_bots = 0
    _bot_pos = {}
    _bot_team = {}
    _bot_at = {}

def hor_flip(pos: Position):
    return Position(_width - 1 - pos.x, pos.y)
def ver_flip(pos: Position):
    return Position(pos.x, _height - 1 - pos.y)
def rot_flip(pos: Position):
    return Position(_width - 1 - pos.x, _height - 1 - pos.y)

def update_symmetry_from_comms(sym_bits):
    """Update symmetry from comms. Each bit represents a possible symmetry."""
    global _hor_sym, _ver_sym, _rot_sym
    if not (sym_bits & 1):
        _hor_sym = False
    if not (sym_bits & 2):
        _ver_sym = False
    if not (sym_bits & 4):
        _rot_sym = False

def determine_known_map() -> None:
    from known_maps import KNOWN_MAPS
    global _their_core, _predicted_enemy_core, _solved_sym
    global _hor_sym, _ver_sym, _rot_sym, _rush_tiebroken
    global _bm_seen, _bm_seen_observed, _bm_env, _bm_any_building, _struct_version

    if _my_core is None:
        return

    candidates = KNOWN_MAPS.get((_height, _width))
    if not candidates:
        return

    spawn_matches = [entry for entry in candidates if _my_core in entry["spawns"]]
    if not spawn_matches:
        return

    vision_matches = []
    visible = _bm_seen_observed
    if not visible:
        return
    for entry in spawn_matches:
        matches_vision = True
        env_masks = entry["env_masks"]
        for env_idx in range(_NUM_ENV):
            if (_bm_env[env_idx] & visible) != (env_masks[env_idx] & visible):
                matches_vision = False
                break
        if matches_vision:
            vision_matches.append(entry)

    if len(vision_matches) != 1:
        return

    match = vision_matches[0]
    spawn_a, spawn_b = match["spawns"]
    opposite_core = spawn_b if _my_core == spawn_a else spawn_a

    if not _solved_sym:
        _hor_sym = match["hor_sym"]
        _ver_sym = match["ver_sym"]
        _rot_sym = match["rot_sym"]
        if int(_hor_sym) + int(_ver_sym) + int(_rot_sym) == 1:
            _solved_sym = True

    _their_core = opposite_core
    _predicted_enemy_core = opposite_core
    _rush_tiebroken = 0

    enemy_core_n = opposite_core.x + opposite_core.y * _width
    enemy_core_bit = 1 << enemy_core_n
    enemy_team_idx = 1 - _my_team_idx
    _building_id[enemy_core_n] = -1
    _building_et_idx[enemy_core_n] = _IDX_CORE
    _building_hp[enemy_core_n] = GameConstants.CORE_MAX_HP
    _bm_et[_IDX_CORE] |= enemy_core_bit
    _bm_team[enemy_team_idx] |= enemy_core_bit
    _bm_any_building |= enemy_core_bit
    build_core_areas()

    _bm_env = list(match["env_masks"])
    _bm_seen = _board_mask

    _struct_version += 1
    recompute_derived()
    print(match["name"])

def _env_at_idx(n):
    """Return the env list index for tile n."""
    bit = 1 << n
    for i in range(_NUM_ENV):
        if _bm_env[i] & bit:
            return i
    return _IDX_ENV_EMPTY

def flip(pos: Position):
    if not _solved_sym:
        return None
    if _hor_sym:
        return hor_flip(pos)
    if _ver_sym:
        return ver_flip(pos)
    if _rot_sym:
        return rot_flip(pos)
    return None
def core_center(core_id: int, tile: Position) -> Position | None:
    def empty(pos: Position) -> bool:
        return not in_bounds(pos) or (_rc.is_in_vision(pos) and _rc.get_tile_building_id(pos) != core_id)
    up    = empty(Position(tile.x,     tile.y - 1))
    down  = empty(Position(tile.x,     tile.y + 1))
    left  = empty(Position(tile.x - 1, tile.y))
    right = empty(Position(tile.x + 1, tile.y))
    if up and left:   return Position(tile.x + 1, tile.y + 1)
    if up and right:  return Position(tile.x - 1, tile.y + 1)
    if down and left: return Position(tile.x + 1, tile.y - 1)
    if down and right:return Position(tile.x - 1, tile.y - 1)
    return None

def build_core_areas() -> None:
    global _bm_my_core_area, _bm_their_core_area, _bm_conveyors, _bm_any_building
    _bm_my_core_area = 0
    _bm_their_core_area = 0
    bm_et = _bm_et
    bm_team = _bm_team
    num_et = _NUM_ET
    num_team = _NUM_TEAM
    if _my_core is not None:
        n = _my_core.x+_my_core.y*_width
        my_team_idx = _my_team_idx
        for x in range(_my_core.x - 1, _my_core.x + 2):
            for y in range(_my_core.y - 1, _my_core.y + 2):
                m = x+y*_width
                bit = 1 << m
                # Clear any old entity/team bits at this tile
                for i in range(num_et):
                    bm_et[i] &= ~bit
                for i in range(num_team):
                    bm_team[i] &= ~bit
                _building_id[m] = _building_id[n]
                _building_et_idx[m] = _IDX_CORE
                _building_hp[m] = _building_hp[n]
                _bm_my_core_area |= bit
                _bm_any_building |= bit
                bm_et[_IDX_CORE] |= bit
                bm_team[my_team_idx] |= bit
    if _their_core is not None:
        n = _their_core.x+_their_core.y*_width
        enemy_team_idx = 1 - _my_team_idx
        for x in range(_their_core.x - 1, _their_core.x + 2):
            for y in range(_their_core.y - 1, _their_core.y + 2):
                    m = x+y*_width
                    bit = 1 << m
                    for i in range(num_et):
                        bm_et[i] &= ~bit
                    for i in range(num_team):
                        bm_team[i] &= ~bit
                    _building_id[m] = _building_id[n]
                    _building_et_idx[m] = _IDX_CORE
                    _building_hp[m] = _building_hp[n]
                    _bm_their_core_area |= bit
                    _bm_any_building |= bit
                    bm_et[_IDX_CORE] |= bit
                    bm_team[enemy_team_idx] |= bit

def _compute_route_targets() -> int:
    """Bitmask of tiles the route state can path toward.
    Core is always routable. Empty conveyors and their downstream
    chain are routable only if the chain ends at my core or is unseen.
    Also propagates upstream from valid conveyors, stopping at intersections
    (tiles where >1 conveyor feeds in).
    Side effect: sets _bm_dead_end."""
    global _bm_dead_end
    result = _bm_my_core_area
    my_convs = _bm_routable

    conv_target = _building_conv_target
    my_team_idx = _my_team_idx
    tiles = _width * _height

    valid_end = _bm_my_core_area
    bm_my = _bm_team[my_team_idx]

    reverse = _conv_reverse

    # Ore-accepting: my conveyors, turrets, core, foundry
    ore_accepting = (
        _bm_et[_IDX_CONVEYOR] | _bm_et[_IDX_ARMOURED_CONVEYOR]
        | _bm_et[_IDX_BRIDGE] | _bm_et[_IDX_SPLITTER]
        | _bm_et[_IDX_SENTINEL] | _bm_et[_IDX_GUNNER]
        | _bm_et[_IDX_BREACH] | _bm_et[_IDX_CORE]
        | _bm_et[_IDX_FOUNDRY]
    ) & bm_my

    enemy_idx = 1 - my_team_idx
    bm_enemy = _bm_team[enemy_idx]
    # Enemy non-marker buildings
    enemy_hard = bm_enemy & ~_bm_et[_IDX_MARKER]

    # All conveyors (any team) for dead-end check
    all_convs = (
        _bm_et[_IDX_CONVEYOR] | _bm_et[_IDX_ARMOURED_CONVEYOR]
        | _bm_et[_IDX_BRIDGE] | _bm_et[_IDX_SPLITTER]
    )

    ti_harvesters = _bm_et[_IDX_HARVESTER] & bm_my & _bm_env[_IDX_ENV_ORE_TI]
    ti_harv_adj = expand_manhattan(ti_harvesters) if ti_harvesters else 0

    dead_ends = 0

    mask = all_convs
    while mask:
        lsb = mask & -mask
        n = lsb.bit_length() - 1
        tn = conv_target[n]
        is_my_conv = bool(bm_my & lsb)

        # Dead-end: output not pointing into an ore-accepting building
        if 0 <= tn < tiles:
            tbit = 1 << tn
            # Enemy conveyors: NOT dead-end if pointing into enemy non-marker building
            if not is_my_conv and (enemy_hard & tbit):
                pass
            elif not (ore_accepting & tbit):
                dead_ends |= lsb
            elif (_bm_conv_raw_ax & lsb) and not (_bm_et[_IDX_FOUNDRY] & tbit) and (((_bm_conv_ti | _bm_conv_refined) & tbit) or (ti_harv_adj & tbit)):
                dead_ends |= lsb
        else:
            dead_ends |= lsb
        mask ^= lsb

    _bm_dead_end = dead_ends

    # --- Downstream: validate chains from empty conveyors ---
    empty_convs = my_convs & ~_bm_conv_loaded
    if not empty_convs:
        return result

    valid_convs = 0
    bm_seen = _bm_seen
    bm_visible = _bm_visible

    mask = empty_convs
    while mask:
        lsb = mask & -mask
        mask ^= lsb

        chain = 0
        cur = lsb
        cur_n = cur.bit_length() - 1
        chain_valid = False
        downstream = 0

        while True:
            if chain & cur:
                break
            chain |= cur

            tn = conv_target[cur_n]
            if tn < 0 or tn >= tiles:
                break

            tbit = 1 << tn

            if my_convs & tbit:
                downstream += 1
                if downstream >= 4:
                    break
                cur = tbit
                cur_n = tn
                continue

            if valid_end & tbit:
                chain_valid = True
            elif not (bm_seen & tbit):
                chain_valid = True
            elif (cur & bm_visible) and not (tbit & bm_visible):
                # Conveyor in vision but target is not — treat as unseen
                chain_valid = True
            break

        if chain_valid:
            valid_convs |= chain

    # --- Upstream: propagate from valid conveyors ---
    to_visit = valid_convs
    visited = valid_convs
    while to_visit:
        next_visit = 0
        m = to_visit
        while m:
            lsb = m & -m
            n = lsb.bit_length() - 1
            feeders = reverse[n] & ~visited
            if feeders:
                visited |= feeders
                valid_convs |= feeders
                next_visit |= feeders
            m ^= lsb
        to_visit = next_visit

    result |= valid_convs
    return result

def recompute_derived() -> None:
    """Rebuild derived bitmasks from the current tracked map state."""
    global _bm_blocked, _bm_conveyors, _bm_conveyor_targets, _bm_ti_fed, _bm_ax_fed
    global _bm_enemy_launch_adj, _bm_routable, _bm_route_targets
    global _bm_enemy_turret_threat, _bm_enemy_soft_threat, _bm_enemy_hard_threat
    global _bm_my_gunner_claims, _bm_conv_by_dir, _bm_harv_adj, _bm_passable_FFF

    width = _width
    height = _height
    my_team_idx = _my_team_idx
    bm_et = _bm_et
    bm_team = _bm_team
    bm_env = _bm_env
    building_conv_target = _building_conv_target

    # Conveyors (all conveyor-type buildings + my core area)
    _bm_conveyors = (
        bm_et[_IDX_CONVEYOR]
        | bm_et[_IDX_ARMOURED_CONVEYOR]
        | bm_et[_IDX_BRIDGE]
        | bm_et[_IDX_SPLITTER]
    )

    # Routable = my team's conveyor-type buildings
    _bm_routable = _bm_conveyors & bm_team[my_team_idx]

    _bm_route_targets = _compute_route_targets()

    # Blocked = walls + non-passable buildings + enemy core area
    _bm_blocked = bm_env[_IDX_ENV_WALL]
    _bm_blocked |= bm_et[_IDX_HARVESTER] | bm_et[_IDX_FOUNDRY]
    _bm_blocked |= bm_et[_IDX_GUNNER] | bm_et[_IDX_SENTINEL]
    _bm_blocked |= bm_et[_IDX_BREACH] | bm_et[_IDX_LAUNCHER]
    _bm_blocked |= bm_et[_IDX_BARRIER] & ~bm_team[my_team_idx]  # enemy barriers only
    _bm_blocked |= _bm_their_core_area

    # Conveyor targets + fed bitmasks
    _bm_conveyor_targets = 0
    _bm_ti_fed = 0
    _bm_ax_fed = 0
    bm_conv_ti_local = _bm_conv_ti
    bm_conv_refined_local = _bm_conv_refined
    mask = _bm_conveyors
    while mask:
        lsb = mask & -mask
        cn = lsb.bit_length() - 1
        tn = building_conv_target[cn]
        if tn >= 0:
            tbit = 1 << tn
            _bm_conveyor_targets |= tbit
            if bm_conv_ti_local & lsb:
                _bm_ti_fed |= tbit
            if bm_conv_refined_local & lsb:
                _bm_ax_fed |= tbit
        mask ^= lsb

    # Enemy launcher adjacency
    enemy_launchers = bm_et[_IDX_LAUNCHER] & ~bm_team[my_team_idx]
    _bm_enemy_launch_adj = 0
    mask = enemy_launchers
    while mask:
        lsb = mask & -mask
        ln = lsb.bit_length() - 1
        lx = ln % width
        ly = ln // width
        for dx, dy in _DIRECTION_DELTAS.values():
            nx = lx + dx
            ny = ly + dy
            if 0 <= nx < width and 0 <= ny < height:
                _bm_enemy_launch_adj |= 1 << (nx + ny * width)
        mask ^= lsb

    # Enemy turret threat
    _bm_enemy_soft_threat, _bm_enemy_hard_threat = _compute_enemy_turret_threat()
    _bm_enemy_turret_threat = _bm_enemy_soft_threat | _bm_enemy_hard_threat

    # My gunner coverage (for attack scoring)
    _bm_my_gunner_claims = _compute_my_gunner_claims()

    # Conveyor masks by facing direction (for attack placement candidates)
    _bm_conv_by_dir = _compute_conv_by_dir()

    # Harvester adjacency (for _is_bad_marker_spot)
    harv = bm_et[_IDX_HARVESTER]
    _bm_harv_adj = expand_manhattan(harv) if harv else 0

    # Cached (~get_avoid(False, False, False)) board mask for voronoi-style BFS.
    _bm_passable_FFF = _board_mask & ~(_bm_blocked | _bm_enemy_launch_adj)

def update(recompute: bool = True) -> None:
    global _my_core, _their_core, _core_id, _solved_sym
    global _hor_sym, _ver_sym, _rot_sym
    global _rush_tiebroken, _predicted_enemy_core
    global _bm_blocked, _bm_conveyors, _bm_conveyor_targets, _bm_enemy_launch_adj, _bm_routable, _bm_route_targets, _bm_conv_loaded, _bm_conv_raw_ax, _bm_conv_ti, _bm_conv_refined, _bm_dead_end, _bm_enemy_turret_threat, _bm_damaged, _bm_very_damaged, _conv_reverse, _bm_any_building
    global _bm_seen, _bm_seen_observed, _bm_visible, _prev_pos, _nearby_tiles, _nearby_tiles_pos, _my_pos
    global _bm_friendly_bots, _bm_enemy_bots
    global _bm_others_5x5, _bm_others_3x3
    global _max_id_seen
    global _new_marker_messages
    global _struct_version
    rc = _rc
    building_id = _building_id
    building_et_idx = _building_et_idx
    building_hp = _building_hp
    building_dir = _building_dir
    building_conv_target = _building_conv_target

    bm_et = _bm_et
    bm_team = _bm_team
    bm_env = _bm_env
    bm_seen = _bm_seen
    bm_seen_observed = _bm_seen_observed
    bm_conv_loaded = 0
    bm_conv_raw_ax = _bm_conv_raw_ax
    bm_conv_ti = _bm_conv_ti
    bm_conv_refined = _bm_conv_refined
    conv_reverse = _conv_reverse
    my_team_idx_local = _my_team_idx
    num_team = _NUM_TEAM
    is_conv = _IS_CONVEYOR
    has_dir = _HAS_DIR
    deltas_i = _DIRECTION_DELTAS_I

    width = _width
    height = _height

    my_team       = _my_team
    my_team_idx   = _my_team_idx
    my_pos        = rc.get_position()
    _my_pos       = my_pos

    if _nearby_tiles_pos == my_pos:
        visible_tiles = _nearby_tiles
        bm_visible = _bm_visible
    else:
        visible_tiles = rc.get_nearby_tiles()
        _nearby_tiles = visible_tiles
        _nearby_tiles_pos = my_pos
        bm_visible = 0
        for tile in visible_tiles:
            bm_visible |= 1 << (tile.x + tile.y * width)
        _bm_visible = bm_visible
    _prev_pos     = my_pos
    rc_get_tile_building_id   = rc.get_tile_building_id
    rc_get_entity_type        = rc.get_entity_type
    rc_get_stored_resource    = rc.get_stored_resource
    rc_get_team               = rc.get_team
    rc_get_hp                 = rc.get_hp
    rc_get_direction          = rc.get_direction
    rc_get_bridge_target      = rc.get_bridge_target
    rc_get_tile_env           = rc.get_tile_env
    freshly_loaded = 0
    _new_marker_messages = []
    structural_changed = False

    for tile in visible_tiles:
        x = tile.x
        y = tile.y
        n = x+y*width
        bit = 1 << n
        bm_seen_observed |= bit
        if not (bm_seen & bit):
            env = rc_get_tile_env(tile)
            env_idx = _ENV_INT[env]
            bm_env[env_idx] |= bit
            bm_seen |= bit
            if _solved_sym:
                # Symmetry committed — skip verification and propagate env to the flipped tile.
                if _hor_sym:
                    fx, fy = width-1 - x, y
                elif _ver_sym:
                    fx, fy = x, height-1 - y
                else:
                    fx, fy = width-1 - x, height-1 - y
                fn = fx+fy*width
                fbit = 1 << fn
                bm_env[env_idx] |= fbit
                bm_seen |= fbit
            else:
                rx = width-1-x
                ry = height-1-y
                if _hor_sym:
                    fn = rx+y*width
                    fbit = 1 << fn
                    if (bm_seen & fbit) and not (bm_env[env_idx] & fbit):
                        _hor_sym = False
                if _ver_sym:
                    fn = x+ry*width
                    fbit = 1 << fn
                    if (bm_seen & fbit) and not (bm_env[env_idx] & fbit):
                        _ver_sym = False
                if _rot_sym:
                    fn = rx+ry*width
                    fbit = 1 << fn
                    if (bm_seen & fbit) and not (bm_env[env_idx] & fbit):
                        _rot_sym = False
        entity_id = rc_get_tile_building_id(tile)
        if entity_id is not None and entity_id > _max_id_seen:
            _max_id_seen = entity_id
        if entity_id is None:
            old_et_idx = building_et_idx[n]
            if old_et_idx >= 0:
                structural_changed = True
                old_tn = building_conv_target[n]
                if old_tn >= 0 and (conv_reverse[old_tn] & bit):
                    conv_reverse[old_tn] &= ~bit
                building_conv_target[n] = 0
                bm_et[old_et_idx] &= ~bit
                _bm_any_building &= ~bit
                for i in range(num_team):
                    if bm_team[i] & bit:
                        bm_team[i] &= ~bit
                        break
                building_id[n] = 0
                building_et_idx[n] = -1
            _bm_damaged &= ~bit
            _bm_very_damaged &= ~bit
            continue
        # Fast path: same building as before — skip get_entity_type/get_team/get_direction
        if building_id[n] == entity_id:
            et_idx = building_et_idx[n]
            if et_idx == _IDX_GUNNER:
                new_dir = _DIR_INT[rc_get_direction(entity_id)]
                if new_dir != building_dir[n]:
                    building_dir[n] = new_dir
                    structural_changed = True
            hp = rc_get_hp(entity_id)
            building_hp[n] = hp
            max_hp = _MAX_HP_BY_IDX[et_idx]
            if hp < max_hp:
                _bm_damaged |= bit
            else:
                _bm_damaged &= ~bit
            if hp < max_hp - 2:
                _bm_very_damaged |= bit
            else:
                _bm_very_damaged &= ~bit
            if is_conv[et_idx]:
                res = rc_get_stored_resource(entity_id)
                if res is not None:
                    bm_conv_loaded |= bit
                    freshly_loaded |= bit
                    if res == ResourceType.RAW_AXIONITE:
                        bm_conv_raw_ax |= bit
                        bm_conv_ti &= ~bit
                        bm_conv_refined &= ~bit
                    elif res == ResourceType.TITANIUM:
                        bm_conv_ti |= bit
                        bm_conv_raw_ax &= ~bit
                        bm_conv_refined &= ~bit
                    else:
                        bm_conv_refined |= bit
                        bm_conv_raw_ax &= ~bit
                        bm_conv_ti &= ~bit
        elif comms._marker_id_at[n] == entity_id:
            # Already-seen marker — skip all controller calls
            continue
        else:
            # Different building ID — need full controller queries
            old_id = building_id[n]
            old_et_idx_prev = building_et_idx[n]
            old_dir = building_dir[n]
            old_target = building_conv_target[n]
            et = rc_get_entity_type(entity_id)
            if et == EntityType.MARKER:
                if rc_get_team(entity_id) == my_team:
                    message = comms.decode_visible_marker(entity_id, tile)
                    if message is not None:
                        estimated_turn = comms.estimate_turn(entity_id)
                        _new_marker_messages.append((*message, estimated_turn))
                old_et_idx = building_et_idx[n]
                if old_et_idx >= 0:
                    old_tn = building_conv_target[n]
                    if old_tn >= 0 and (conv_reverse[old_tn] & bit):
                        conv_reverse[old_tn] &= ~bit
                    building_conv_target[n] = 0
                    bm_et[old_et_idx] &= ~bit
                    _bm_any_building &= ~bit
                    for i in range(num_team):
                        if bm_team[i] & bit:
                            bm_team[i] &= ~bit
                            break
                building_id[n] = 0
                building_et_idx[n] = -1
                _bm_damaged &= ~bit
                _bm_very_damaged &= ~bit
                if old_id or old_et_idx_prev >= 0 or old_target >= 0:
                    structural_changed = True
                continue
            et_idx = _ET_INT[et]

            # Clear old bits if replacing a different building
            old_et_idx = building_et_idx[n]
            if old_et_idx >= 0:
                old_tn = building_conv_target[n]
                if old_tn >= 0 and (conv_reverse[old_tn] & bit):
                    conv_reverse[old_tn] &= ~bit
                bm_et[old_et_idx] &= ~bit
                _bm_any_building &= ~bit
                for i in range(num_team):
                    if bm_team[i] & bit:
                        bm_team[i] &= ~bit
                        break

            direction     = rc_get_direction(entity_id) if has_dir[et_idx] else None
            team_val = rc_get_team(entity_id)
            team_idx = _TM_INT[team_val]
            target = None
            if et == EntityType.BRIDGE:
                target = rc_get_bridge_target(entity_id)
            elif is_conv[et_idx] and direction is not None:
                _ddx, _ddy = deltas_i[_DIR_INT[direction]]
                target = Position(tile.x + _ddx, tile.y + _ddy)
            building_id[n] = entity_id
            building_et_idx[n] = et_idx
            hp = rc_get_hp(entity_id)
            building_hp[n] = hp
            building_dir[n] = _DIR_INT[direction] if direction else 0
            new_tn = (target.x + target.y * width) if target else -1
            building_conv_target[n] = new_tn
            if new_tn >= 0 and is_conv[et_idx] and team_idx == my_team_idx_local:
                conv_reverse[new_tn] |= bit

            # Set new bitmask bits
            bm_et[et_idx] |= bit
            bm_team[team_idx] |= bit
            _bm_any_building |= bit
            max_hp = _MAX_HP_BY_IDX[et_idx]
            if hp < max_hp:
                _bm_damaged |= bit
            else:
                _bm_damaged &= ~bit
            if hp < max_hp - 2:
                _bm_very_damaged |= bit
            else:
                _bm_very_damaged &= ~bit

            if is_conv[et_idx]:
                res = rc_get_stored_resource(entity_id)
                if res is not None:
                    bm_conv_loaded |= bit
                    freshly_loaded |= bit
                    if res == ResourceType.RAW_AXIONITE:
                        bm_conv_raw_ax |= bit
                        bm_conv_ti &= ~bit
                        bm_conv_refined &= ~bit
                    elif res == ResourceType.TITANIUM:
                        bm_conv_ti |= bit
                        bm_conv_raw_ax &= ~bit
                        bm_conv_refined &= ~bit
                    else:
                        bm_conv_refined |= bit
                        bm_conv_raw_ax &= ~bit
                        bm_conv_ti &= ~bit

            if et is EntityType.CORE:
                if _my_core is None and team_val == my_team:
                    _my_core = core_center(entity_id, tile)
                    _core_id = entity_id
                    build_core_areas()
                elif _their_core is None and team_val != my_team:
                    _their_core = core_center(entity_id, tile)
                    build_core_areas()
            if (
                old_id != building_id[n]
                or old_et_idx_prev != building_et_idx[n]
                or old_dir != building_dir[n]
                or old_target != building_conv_target[n]
            ):
                structural_changed = True

    # Write back bm_seen to global (int is immutable, local was a copy)
    _bm_seen = bm_seen
    _bm_seen_observed = bm_seen_observed

    possible_syms = int(_hor_sym) + int(_ver_sym) + int(_rot_sym)
    if possible_syms == 1 and not _solved_sym:
        _solved_sym = True
        if _my_core:
            _their_core = flip(_my_core)
            if _their_core is not None:
                pos = _their_core.x+_their_core.y*width
                pbit = 1 << pos
                building_id[pos] = -1
                building_et_idx[pos] = _IDX_CORE
                bm_et[_IDX_CORE] |= pbit
                _bm_any_building |= pbit
                enemy_team_idx = 1 - my_team_idx
                bm_team[enemy_team_idx] |= pbit
                building_hp[pos] = GameConstants.CORE_MAX_HP
            build_core_areas()
        for x in range(width):
            for y in range(height):
                n = x+y*width
                nbit = 1 << n
                if bm_seen & nbit:
                    if _ver_sym:
                        flipped = (x)+(height-1-y)*width
                    elif _hor_sym:
                        flipped = (width-1-x)+(y)*width
                    else:
                        flipped = (width-1-x)+(height-1-y)*width
                    fbit = 1 << flipped
                    if not (bm_seen & fbit):
                        # Copy env from source tile to flipped tile
                        for env_i in range(_NUM_ENV):
                            if bm_env[env_i] & nbit:
                                bm_env[env_i] |= fbit
                                break
                        bm_seen |= fbit
        _bm_seen = bm_seen

    if _my_core:
        if _their_core:
            _predicted_enemy_core = _their_core
        else:
            if _rot_sym:
                _predicted_enemy_core = rot_flip(_my_core)
            else:
                hsym_core = hor_flip(_my_core)
                vsym_core = ver_flip(_my_core)
                if _rush_tiebroken == 1 and _ver_sym:
                    _predicted_enemy_core = vsym_core
                elif _rush_tiebroken == 2 and _hor_sym:
                    _predicted_enemy_core = hsym_core
                elif _ver_sym and _hor_sym:
                    if abs(my_pos.x - hsym_core.x) + abs(my_pos.y - hsym_core.y) < abs(my_pos.x - vsym_core.x) + abs(my_pos.y - vsym_core.y):
                        _predicted_enemy_core = hsym_core
                        _rush_tiebroken = 2
                        log("Tiebreaking enemy core sym - HORIZONTAL")
                    else:
                        _predicted_enemy_core = vsym_core
                        _rush_tiebroken = 1
                        log("Tiebreaking enemy core sym - VERTICAL")
                elif _ver_sym:
                    _predicted_enemy_core = vsym_core
                else:
                    _predicted_enemy_core = hsym_core

    mask = freshly_loaded
    while mask:
        lsb = mask & -mask
        n = lsb.bit_length() - 1
        res_ax = bool(bm_conv_raw_ax & lsb)
        res_ti = not res_ax and bool(bm_conv_ti & lsb)
        tn = building_conv_target[n]
        for _ in range(3):
            if tn < 0:
                break
            tbit = 1 << tn
            if not (_bm_conveyors & tbit):
                break
            if res_ax:
                if not ((bm_conv_ti | bm_conv_refined) & tbit):
                    bm_conv_raw_ax |= tbit
            elif res_ti:
                bm_conv_ti |= tbit
                bm_conv_raw_ax &= ~tbit
                bm_conv_refined &= ~tbit
            else:
                bm_conv_refined |= tbit
                bm_conv_raw_ax &= ~tbit
                bm_conv_ti &= ~tbit
            tn = building_conv_target[tn]
        mask ^= lsb

    _bm_conv_loaded = bm_conv_loaded
    _bm_conv_raw_ax = bm_conv_raw_ax
    _bm_conv_ti = bm_conv_ti
    _bm_conv_refined = bm_conv_refined

    if structural_changed:
        _struct_version += 1

    # --- Update builder bot tracking ---
    _bm_friendly_bots = 0
    _bm_enemy_bots = 0
    seen_uids = set()
    for uid in rc.get_nearby_units():
        if uid > _max_id_seen:
            _max_id_seen = uid
        if rc.get_entity_type(uid) != _ET_BUILDER_BOT:
            continue
        if uid == rc.get_id():
            continue
        ep = rc.get_position(uid)
        n = ep.x + ep.y * width
        team_idx = _TM_INT[rc.get_team(uid)]
        # If tracked at a different position, clear old
        old_n = _bot_pos.get(uid)
        if old_n is not None and old_n != n:
            if _bot_at.get(old_n) == uid:
                del _bot_at[old_n]
        _bot_pos[uid] = n
        _bot_team[uid] = team_idx
        _bot_at[n] = uid
        seen_uids.add(uid)
    # Invalidate tracked bots whose old position is now visible but they're gone
    to_remove = []
    for uid, n in _bot_pos.items():
        if uid in seen_uids:
            continue
        bit = 1 << n
        if bm_visible & bit:
            to_remove.append(uid)
    for uid in to_remove:
        n = _bot_pos[uid]
        if _bot_at.get(n) == uid:
            del _bot_at[n]
        del _bot_pos[uid]
        del _bot_team[uid]

    # Rebuild bitmasks from tracked positions
    for uid, n in _bot_pos.items():
        bit = 1 << n
        if _bot_team[uid] == my_team_idx:
            _bm_friendly_bots |= bit
        else:
            _bm_enemy_bots |= bit

    # Precompute other-bots zone masks for cant_claim().
    # expand_chebyshev distributes over OR, so one call per layer suffices.
    my_bit = 1 << (my_pos.x + my_pos.y * width)
    friendly_others = _bm_friendly_bots & ~my_bit
    if friendly_others:
        _bm_others_3x3 = expand_chebyshev(friendly_others)
        _bm_others_5x5 = expand_chebyshev(_bm_others_3x3)
    else:
        _bm_others_3x3 = 0
        _bm_others_5x5 = 0

    current_round = rc.get_current_round()
    while len(_max_id_by_round) <= current_round:
        _max_id_by_round.append(0)
    _max_id_by_round[current_round] = _max_id_seen

    if recompute:
        recompute_derived()


def is_tile_empty(pos: Position):
    if not in_bounds(pos):
        return False
    if _rc.is_tile_empty(pos):
        return True
    bid = _rc.get_tile_building_id(pos)
    return bid is not None and _rc.get_entity_type(bid) is EntityType.MARKER


def has_builder_bot(pos: Position, include_self: bool = False) -> bool:
    if not in_bounds(pos):
        return False
    if include_self and pos == _my_pos:
        return True
    n = pos.x + pos.y * _width
    bit = 1 << n
    return bool((_bm_friendly_bots | _bm_enemy_bots) & bit)

def can_place_at_restrictive(pos: Position):
    if not in_bounds(pos): 
        return False
    if is_tile_empty(pos): 
        return True
    if not _rc.can_destroy(pos): 
        return False
    bid = _rc.get_tile_building_id(pos)
    return bid is not None and _rc.get_entity_type(bid) is EntityType.ROAD

def is_passable(pos: Position):
    if not in_bounds(pos): return False
    n = pos.x + pos.y * _width
    bit = 1 << n
    if _bm_env[_IDX_ENV_WALL] & bit: return False
    if _building_id[n] == 0: return True
    my_team_idx = _my_team_idx
    return bool(
        (_bm_et[_IDX_CONVEYOR] | _bm_et[_IDX_ARMOURED_CONVEYOR]
         | _bm_et[_IDX_BRIDGE] | _bm_et[_IDX_SPLITTER]
         | _bm_et[_IDX_ROAD] | _bm_et[_IDX_MARKER]
         | (_bm_et[_IDX_BARRIER] & _bm_team[my_team_idx])
         | (_bm_et[_IDX_CORE] & _bm_team[my_team_idx])
        ) & bit
    )

def get_avoid(
    avoid_conveyors: bool,
    avoid_builders: bool,
    avoid_ore: bool,
) -> int:
    """Return a bitmask of tiles to avoid during pathfinding."""
    # avoid_core = _rc.get_tile_building_id(_rc.get_position()) != _core_id
    mask = _bm_blocked
    if avoid_conveyors:
        mask |= _bm_conveyors | _bm_conveyor_targets | _bm_my_core_area
    if avoid_ore:
        ore = _bm_env[_IDX_ENV_ORE_TI] | _bm_env[_IDX_ENV_ORE_AX]
        w = _width
        landlocked = ore & (ore >> 1 & _not_right_col) & (ore << 1 & _not_left_col) & (ore >> w) & (ore << w)
        mask |= ore & ~landlocked & builder._harvest_zone
    # if avoid_core:
    #     mask |= _bm_my_core_area
    if avoid_builders:
        mask |= _bm_friendly_bots | _bm_enemy_bots
    # threat = _bm_enemy_turret_threat
    # pos = _my_pos
    # my_bit = 1 << (pos.x + pos.y * _width)
    # if not (threat & my_bit):
    #     mask |= threat
    mask |= _bm_enemy_launch_adj
    return mask
