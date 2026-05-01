from __future__ import annotations
from cambc import Controller, Position, Environment, EntityType, Team, Direction, ResourceType, GameError, GameConstants
import pathing
import units.builder as builder
import comms
from log import log

_HAS_DIRECTION = frozenset({
    EntityType.ARMOURED_CONVEYOR,
    EntityType.BREACH,
    EntityType.CONVEYOR,
    EntityType.GUNNER,
    EntityType.SENTINEL,
    EntityType.SPLITTER,
})

_CONVEYOR_TYPES = frozenset({
    EntityType.CONVEYOR,
    EntityType.ARMOURED_CONVEYOR,
    EntityType.BRIDGE,
    EntityType.SPLITTER,
})

_ACCEPT_ORE = frozenset({
    EntityType.CONVEYOR,
    EntityType.ARMOURED_CONVEYOR,
    EntityType.BRIDGE,
    EntityType.SPLITTER,
    EntityType.BREACH,
    EntityType.CORE,
    EntityType.FOUNDRY,
    EntityType.GUNNER,
    EntityType.SENTINEL,
})

_TURRET_TYPES = frozenset({
    EntityType.LAUNCHER,
    EntityType.GUNNER,
    EntityType.SENTINEL,
    EntityType.BREACH,
})

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
_DIRECTIONS = (
    Direction.NORTH, Direction.NORTHEAST, Direction.EAST, Direction.SOUTHEAST,
    Direction.SOUTH, Direction.SOUTHWEST, Direction.WEST, Direction.NORTHWEST,
)
_CARDINAL = (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST)
_DIRECTION_DELTAS = {d: d.delta() for d in Direction}
# Int-indexed version: _DIRECTION_DELTAS_I[dir_int] = (dx, dy)
_DIRECTION_DELTAS_I = [d.delta() for d in Direction]

_DIR_N  = _DIR_INT[Direction.NORTH]
_DIR_NE = _DIR_INT[Direction.NORTHEAST]
_DIR_E  = _DIR_INT[Direction.EAST]
_DIR_SE = _DIR_INT[Direction.SOUTHEAST]
_DIR_S  = _DIR_INT[Direction.SOUTH]
_DIR_SW = _DIR_INT[Direction.SOUTHWEST]
_DIR_W  = _DIR_INT[Direction.WEST]
_DIR_NW = _DIR_INT[Direction.NORTHWEST]
_DIR_C  = _DIR_INT[Direction.CENTRE]

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
_bm_dir: list[int] = []   # per facing

# Derived bitmasks
_bm_blocked: int = 0            # walls + non-passable buildings + enemy core area
_bm_conveyors: int = 0          # all conveyor-type buildings
_bm_conveyor_targets: int = 0   # output target tiles of conveyors
_bm_my_core_area: int = 0       # my core 3x3 (update only in update)
_bm_their_core_area: int = 0    # enemy core 3x3
_bm_enemy_launch_adj: int = 0   # tiles adjacent to enemy launchers (update only in update)
_bm_route_targets: int = 0      # tiles route state can path toward (update only in update)
_bm_conv_raw_ax: int = 0        # conveyors observed containing raw axionite
_bm_conv_ti: int = 0            # conveyors observed containing titanium
_bm_conv_refined: int = 0       # conveyors observed containing refined axionite
_bm_ti_carrying: int = 0       # conveyors believed to carry titanium (within 3 up/downstream of an observed ti conveyor)
_bm_raw_ax_carrying: int = 0   # conveyors believed to carry raw axionite
_bm_refined_carrying: int = 0  # conveyors believed to carry refined axionite
_bm_dead_end: int = 0           # possible places to route from, defined by the targets of any conveyor types heading into nothing or a building that is not a (conveyor type, my foundry, my core, my sentinel, my gunner, or my breach). also includes my conveyors pointing into an enemy non road non marker building (update only in update)
_bm_feeding_enemy: int = 0      # loaded conveyors whose target is an enemy gunner/sentinel/breach (excludes launcher; launchers don't get fed)
_bm_enemy_soft_threat: int = 0    # tiles enemy sentinels can shoot (low dps) (update only in update)
_bm_enemy_hard_threat: int = 0    # tiles enemy gunners/breaches can shoot (high dps) (update only in update)
_bm_my_gunner_claims: int = 0     # tiles already covered by one of my gunners' current ray (update only in update)
_bm_guard_conveyor: int = 0   # CONVEYOR|ARMOURED_CONVEYOR tiles whose target is an ore tile
_bm_conv_into_open_ore: int = 0   # CONVEYOR|ARMOURED_CONVEYOR tiles whose target is an open (non-landlocked) ore tile
_bm_conv_by_dir: list[int] = [0] * 8  # per facing: CONVEYOR|ARMOURED_CONVEYOR tiles with that direction
_bm_ti_fed: int = 0              # tiles where a turret placed there would be fed Ti BY A CONVEYOR (4-hop forward propagation; harvester-adjacent seed conveyors propagate but are NOT included — handled separately in attack)
_bm_ax_fed: int = 0              # tiles where a turret placed there would be fed refined Ax BY A CONVEYOR (4-hop forward propagation; foundry-adjacent seed conveyors propagate but are NOT included — handled separately in attack)
_bm_enemy_turret_threat: int = 0 # union of enemy soft + hard threat
_bm_others_5x5: int = 0          # 5x5 around other friendly builder bots
_bm_others_3x3: int = 0          # 3x3 around other friendly builder bots
_bm_passable_FFF: int = 0        # cached (_board_mask & ~get_avoid(False, False, False))

# Structural state version — bumped on any structural map change (build/destroy
# of a tracked building, or symmetry-solved insertion). Used to cheaply
# invalidate caches that only change on structural updates. HP /
# loaded-resource transitions are NOT counted.
_struct_version: int = 0
_board_mask: int = 0              # (1 << (w*h)) - 1, cached
_bm_visible: int = 0              # tiles visible this turn
_nearby_tiles: list = []           # cached rc.get_nearby_tiles() for this round
_bm_damaged: int = 0              # buildings not at full HP
_bm_very_damaged: int = 0         # buildings with > 2 damage
_bm_landlocked: int = 0

# Builder bot tracking
_bm_friendly_bots: int = 0       # bitmask of known friendly builder bot positions
_bm_enemy_bots: int = 0          # bitmask of known enemy builder bot positions
_bot_pos: dict[int, int] = {}    # uid -> tile index (both teams)
_bot_team: dict[int, int] = {}   # uid -> team_idx
_bot_at: dict[int, int] = {}    # tile index -> uid
_bot_last_seen: dict[int, int] = {}   # uid -> round it was last seen alive in vision

_max_id_by_round: list[int] = []  # max_id_by_round[round] = max entity id seen up to that round
_max_id_seen: int = 0
_new_marker_messages: list[tuple[int, Position, Position, int, int]] = []
_nearby_tiles_pos: Position | None = None

_left_col: int = 0
_right_col: int = 0
_bottom_row: int = 0
_top_row: int = 0
_not_left_col: int = 0   # mask with all bits EXCEPT x=0 column
_not_right_col: int = 0  # mask with all bits EXCEPT x=width-1 column
_not_bottom_row: int = 0
_not_top_row: int = 0



_my_core: Position | None = None
_their_core: Position | None = None
_predicted_enemy_core: Position | None = None
_core_id: int | None = None
_hor_sym = True
_ver_sym = True
_rot_sym = True
_solved_sym = False
_rush_tiebroken = 0

def _precompute_breach_offsets():
    """Breach: r²≤BREACH_ATTACK_RADIUS_SQ, 180° semicircle centered on facing direction."""
    result = [[] for _ in range(8)]
    for di in range(8):
        ddx, ddy = _DIRECTION_DELTAS_I[di]
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
        ddx, ddy = _DIRECTION_DELTAS_I[di]
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
        ddx, ddy = _DIRECTION_DELTAS_I[di]
        ray = []
        for step in range(1, 4):
            px, py = ddx * step, ddy * step
            if px*px + py*py > GameConstants.GUNNER_VISION_RADIUS_SQ:
                break
            ray.append((px, py))
        rays.append(ray)
    return rays

_BREACH_OFFSETS = _precompute_breach_offsets()
_SENTINEL_OFFSETS = _precompute_sentinel_offsets()
_GUNNER_RAYS = _precompute_gunner_rays()



def ground_at(x, y):
    bit = 1 << (x + y * _width)
    if not _bm_seen&bit:
        return None
    if _bm_env[_IDX_ENV_WALL] & bit: return Environment.WALL
    if _bm_env[_IDX_ENV_ORE_TI] & bit: return Environment.ORE_TITANIUM
    if _bm_env[_IDX_ENV_ORE_AX] & bit: return Environment.ORE_AXIONITE
    return Environment.EMPTY
def type_at(x, y):
    et_idx = _building_et_idx[x + y * _width]
    if et_idx >= 0:
        return _INT_ET[et_idx]
    return None
def team_at(x, y):
    bit = 1 << (x + y * _width)
    if _bm_team[0] & bit: return _INT_TM[0]
    if _bm_team[1] & bit: return _INT_TM[1]
    return None
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


def expand_chebyshev(mask: int, times:int = 1) -> int:
    w = _width
    for i in range(times):
        h = mask | ((mask & _not_right_col) << 1) | ((mask & _not_left_col) >> 1)
        mask = (h | (h << w) | (h >> w)) & _board_mask
    return mask


def expand_manhattan(mask: int, times:int = 1) -> int:
    w = _width
    for i in range(times):
        mask = (mask | ((mask & _not_right_col) << 1) | ((mask & _not_left_col) >> 1) | (mask << w) | (mask >> w)) & _board_mask
    return mask


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
    _turret_shift_masks = {}
    for dx, dy in offsets:
        x0 = max(0, -dx)
        x1 = min(w, w - dx)
        y0 = max(0, -dy)
        y1 = min(h, h - dy)
        row_bits = ((1 << (x1 - x0)) - 1) << x0
        nrows = y1 - y0
        block = row_bits * ((1 << (nrows * w)) - 1) // ((1 << w) - 1)
        _turret_shift_masks[(dx, dy)] = block << (y0 * w)

_turret_threat_cache_version: int = -1
_turret_threat_cache: tuple[int, int] = (0, 0)


def _compute_enemy_turret_threat() -> tuple[int, int]:
    """Compute (soft, hard) threat bitmasks.

    Soft: sentinels (low dps).
    Hard: gunners + breaches (high dps).

    Sentinel/breach use bitmask shifting (no wall blocking).
    Gunner uses per-turret ray in current facing only (wall blocking)."""
    global _turret_threat_cache_version, _turret_threat_cache
    if _struct_version == _turret_threat_cache_version:
        return _turret_threat_cache

    w = _width
    h = _height
    enemy_idx = 1 - _my_team_idx
    soft = 0
    hard = 0
    bm_team_enemy = _bm_team[enemy_idx]

    for turret_idx, offsets_table, is_hard in (
        (_IDX_SENTINEL, _SENTINEL_OFFSETS, False),
        (_IDX_BREACH, _BREACH_OFFSETS, True),
    ):
        turrets = _bm_et[turret_idx] & bm_team_enemy
        if not turrets:
            continue
        acc = 0
        for di in range(8):
            dm = turrets&_bm_dir[di]
            if not dm:
                continue
            for dx, dy in offsets_table[di]:
                shift_mask = _turret_shift_masks.get((dx, dy))
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
        not_walls = _board_mask & ~_bm_env[_IDX_ENV_WALL]
        acc = 0
        for di in range(8):
            dm = gunners&_bm_dir[di]
            if not dm:
                continue
            dx, dy = _DIRECTION_DELTAS_I[di]
            length = 3 - di%2
            shift_mask = _turret_shift_masks.get((dx, dy))
            for i in range(1, length+1):
                offset = dx + dy * w
                if offset > 0:
                    dm = ((dm & shift_mask) << offset) & not_walls
                else:
                    dm = ((dm & shift_mask) >> (-offset)) & not_walls
                acc |= dm
        hard |= acc

    _turret_threat_cache_version = _struct_version
    _turret_threat_cache = (soft, hard)
    return _turret_threat_cache


_my_gunner_claims_cache_version: int = -1
_my_gunner_claims_cache: int = 0


def _compute_my_gunner_claims() -> int:
    """Bitmask of tiles already covered by one of my gunners' current ray."""
    global _my_gunner_claims_cache_version, _my_gunner_claims_cache
    if _struct_version == _my_gunner_claims_cache_version:
        return _my_gunner_claims_cache

    w = _width
    gunners = _bm_et[_IDX_GUNNER] & _bm_team[_my_team_idx]
    claimed = 0
    if gunners:
        not_walls = _board_mask & ~_bm_env[_IDX_ENV_WALL]
        for di in range(8):
            dm = gunners&_bm_dir[di]
            if not dm:
                continue
            dx, dy = _DIRECTION_DELTAS_I[di]
            length = 3 - di%2
            shift_mask = _turret_shift_masks.get((dx, dy))
            for i in range(1, length+1):
                offset = dx + dy * w
                if offset > 0:
                    dm = ((dm & shift_mask) << offset) & not_walls
                else:
                    dm = ((dm & shift_mask) >> (-offset)) & not_walls
                claimed |= dm
    _my_gunner_claims_cache_version = _struct_version
    _my_gunner_claims_cache = claimed
    return claimed


def _compute_fed() -> tuple[int, int]:
    """Return (ti_fed, ax_fed) — tiles where a turret placed there would be
    loaded by a CONVEYOR carrying the matching resource.

    Source-adjacent tiles (turrets loaded directly by a harvester/foundry) are
    NOT included here — attack.py adds those separately so it can apply its own
    exclusions (e.g. harvesters already covered by a friendly sentinel).

    Seeds for forward conveyor propagation (seeds themselves are then SUBTRACTED
    from the result if they are merely source-adjacent, since they're fed by
    the source rather than by a conveyor):
      - conveyors the source actually feeds (adj minus pointing-back)
      - conveyors observed carrying the matching resource, but only if they
        have an upstream feeder OR are adjacent to the source. An orphan
        carrying conveyor (no reverse, not adjacent to source) is excluded — its
        resource is transient.

    Propagation runs 4 hops forward through cardinal conveyors and bridges.
    Chain terminal targets are included even if non-conveyor (a turret there
    still receives the delivered resource).

    Both teams' harvesters/foundries seed (adjacency to enemy economy is also a
    strategic placement signal)."""
    bm_conveyors = _bm_conveyors
    if not bm_conveyors:
        return 0, 0
    bm_et = _bm_et
    bm_env = _bm_env
    harvesters = bm_et[_IDX_HARVESTER]
    foundries = bm_et[_IDX_FOUNDRY]
    ti_harv = harvesters & bm_env[_IDX_ENV_ORE_TI]

    conv_target = _building_conv_target
    tiles = _width * _height
    w = _width
    board = _board_mask
    cardinal = (
        bm_et[_IDX_CONVEYOR]
        | bm_et[_IDX_ARMOURED_CONVEYOR]
        | bm_et[_IDX_SPLITTER]
    )
    dir_mask = _bm_dir
    convs_e = cardinal & dir_mask[_DIR_E]
    convs_w = cardinal & dir_mask[_DIR_W]
    convs_s = cardinal & dir_mask[_DIR_S]
    convs_n = cardinal & dir_mask[_DIR_N]
    bridges = bm_et[_IDX_BRIDGE]
    nlc = _not_left_col
    nrc = _not_right_col
    ntr = _not_top_row
    nbr = _not_bottom_row

    def adj_seed(src):
        if not src:
            return 0
        adj = expand_manhattan(src) & bm_conveyors
        pointing_back = (
            (((src & nlc) >> 1) & convs_e)
            | (((src & nrc) << 1) & convs_w)
            | (((src & ntr) >> w) & convs_s)
            | (((src & nbr) << w) & convs_n)
        )
        return adj & ~pointing_back

    has_reverse = (
        ((convs_e & nrc) << 1)
        | ((convs_w & nlc) >> 1)
        | ((convs_s & nbr) << w)
        | ((convs_n & ntr) >> w)
    ) & board
    m = bridges
    while m:
        lsb = m & -m
        n = lsb.bit_length() - 1
        tn = conv_target[n]
        if 0 <= tn < tiles:
            has_reverse |= 1 << tn
        m ^= lsb

    ti_harv_adj = adj_seed(ti_harv)
    foundry_adj = adj_seed(foundries)
    ti_carry = (_bm_conv_ti & bm_conveyors) & (has_reverse | ti_harv_adj)
    ax_carry = ((_bm_conv_raw_ax | _bm_conv_refined) & bm_conveyors) & (has_reverse | foundry_adj)

    ti_seed = ti_harv_adj | ti_carry
    ax_seed = foundry_adj | ax_carry
    if not ti_seed and not ax_seed:
        return 0, 0

    def fwd(seed, exclude):
        if not seed:
            return 0
        expanded = seed
        cur = seed
        for _ in range(4):
            targets = (
                ((cur & convs_e & nrc) << 1)
                | ((cur & convs_w & nlc) >> 1)
                | ((cur & convs_s & nbr) << w)
                | ((cur & convs_n & ntr) >> w)
            ) & board
            m = cur & bridges
            while m:
                lsb = m & -m
                n = lsb.bit_length() - 1
                tn = conv_target[n]
                if 0 <= tn < tiles:
                    targets |= 1 << tn
                m ^= lsb
            new_targets = targets & ~expanded
            if not new_targets:
                break
            expanded |= new_targets
            cur = new_targets & bm_conveyors
            if not cur:
                break
        return expanded & ~exclude

    ti_fed = fwd(ti_seed, ti_harv_adj)
    ax_fed = fwd(ax_seed, foundry_adj)
    return ti_fed, ax_fed


def _conveyor_target_tiles(source_mask: int) -> int:
    """Return the union of output target tiles for the given conveyor-like
    sources. Cardinal outputs are shifted in bulk; bridges fall back to their
    arbitrary target lookup."""
    if not source_mask:
        return 0

    w = _width
    board = _board_mask
    dir_mask = _bm_dir
    cardinal = source_mask & (
        _bm_et[_IDX_CONVEYOR]
        | _bm_et[_IDX_ARMOURED_CONVEYOR]
        | _bm_et[_IDX_SPLITTER]
    )
    targets = (
        ((cardinal & dir_mask[_DIR_E] & _not_right_col) << 1)
        | ((cardinal & dir_mask[_DIR_W] & _not_left_col) >> 1)
        | ((cardinal & dir_mask[_DIR_S] & _not_bottom_row) << w)
        | ((cardinal & dir_mask[_DIR_N] & _not_top_row) >> w)
    ) & board

    bridges = source_mask & _bm_et[_IDX_BRIDGE]
    if not bridges:
        return targets

    conv_target = _building_conv_target
    tiles = _width * _height
    m = bridges
    while m:
        lsb = m & -m
        n = lsb.bit_length() - 1
        tn = conv_target[n]
        if 0 <= tn < tiles:
            targets |= 1 << tn
        m ^= lsb
    return targets


def _compute_predicted_enemy_core() -> Position | None:
    """Return the enemy core position when known. Symmetry-based prediction is
    left to `update()`, since the flip helpers aren't available here."""
    if _my_core is None:
        return None
    if _their_core is not None:
        return _their_core
    return _predicted_enemy_core


_conv_by_dir_cache_version: int = -1
_conv_by_dir_cache: list[int] = [0] * 8


def _compute_conv_by_dir() -> list[int]:
    """Per facing (0..7): CONVEYOR|ARMOURED_CONVEYOR tiles with that output
    direction. Cached on _struct_version — only rebuilt on structural changes
    (conveyor build/destroy/redirect)."""
    global _conv_by_dir_cache_version, _conv_by_dir_cache
    if _struct_version == _conv_by_dir_cache_version:
        return _conv_by_dir_cache

    convs = _bm_et[_IDX_CONVEYOR] | _bm_et[_IDX_ARMOURED_CONVEYOR]
    result = [convs & _bm_dir[d] for d in range(8)]

    _conv_by_dir_cache_version = _struct_version
    _conv_by_dir_cache = result
    return result


_conv_into_open_ore_cache_version: int = -1
_conv_into_open_ore_cache: int = 0


def _compute_conv_into_open_ore() -> int:
    """CONVEYOR|ARMOURED_CONVEYOR tiles whose target is a non-landlocked ore tile."""
    global _conv_into_open_ore_cache_version, _conv_into_open_ore_cache
    if _struct_version == _conv_into_open_ore_cache_version:
        return _conv_into_open_ore_cache
    convs = _bm_et[_IDX_CONVEYOR] | _bm_et[_IDX_ARMOURED_CONVEYOR]
    if not convs:
        _conv_into_open_ore_cache_version = _struct_version
        _conv_into_open_ore_cache = 0
        return 0
    w = _width
    ore = (_bm_env[_IDX_ENV_ORE_TI] | _bm_env[_IDX_ENV_ORE_AX]) & ~_bm_landlocked
    right = convs & _bm_dir[_DIR_E] & ((_not_right_col & ore) >> 1)
    left = convs & _bm_dir[_DIR_W] & ((_not_left_col & ore) << 1)
    up = convs & _bm_dir[_DIR_N] & ((_not_bottom_row & ore) << w)
    down = convs & _bm_dir[_DIR_S] & ((_not_top_row & ore) >> w)
    result = right | left | up | down
    _conv_into_open_ore_cache_version = _struct_version
    _conv_into_open_ore_cache = result
    return result


_carrying_cache_key: tuple | None = None
_carrying_cache: tuple[int, int, int] = (0, 0, 0)


def _carrying_expand(
    seed, bm_conveyors, convs_e, convs_w, convs_s, convs_n, bridges,
    reverse, conv_target, w, board, tiles,
    not_left_col, not_right_col, not_top_row, not_bottom_row,
):
    expanded = seed
    # Upstream (reverse chain).
    cur = seed
    for _ in range(3):
        not_expanded = ~expanded
        bridges_ne = bridges & not_expanded
        nxt = (
            ((cur & not_left_col) >> 1) & convs_e
            | ((cur & not_right_col) << 1) & convs_w
            | ((cur & not_top_row) >> w) & convs_s
            | ((cur & not_bottom_row) << w) & convs_n
        ) & bm_conveyors & not_expanded
        m = cur
        while m:
            lsb = m & -m
            n = lsb.bit_length() - 1
            nxt |= reverse[n] & bridges_ne
            m ^= lsb
        if not nxt:
            break
        expanded |= nxt
        cur = nxt
    # Downstream (conv_target chain).
    cur = seed
    for _ in range(3):
        nxt = (
            ((cur & convs_e & not_right_col) << 1)
            | ((cur & convs_w & not_left_col) >> 1)
            | ((cur & convs_s & not_bottom_row) << w)
            | ((cur & convs_n & not_top_row) >> w)
        ) & board & bm_conveyors & ~expanded
        m = cur & bridges
        while m:
            lsb = m & -m
            n = lsb.bit_length() - 1
            tn = conv_target[n]
            if 0 <= tn < tiles:
                tbit = 1 << tn
                if (bm_conveyors & tbit) and not (expanded & tbit):
                    nxt |= tbit
            m ^= lsb
        if not nxt:
            break
        expanded |= nxt
        cur = nxt
    return expanded


def _compute_carrying() -> tuple[int, int, int]:
    """Bitmasks of conveyors believed to carry titanium / raw ax / refined ax.

    A conveyor Y is believed to carry X if any conveyor within 3 upstream OR 3
    downstream hops of Y (inclusive) is observed carrying X.
    """
    global _carrying_cache_key, _carrying_cache
    key = (_struct_version, _bm_conv_ti, _bm_conv_raw_ax, _bm_conv_refined)
    if key == _carrying_cache_key:
        return _carrying_cache
    bm_conveyors = _bm_conveyors
    if not bm_conveyors:
        _carrying_cache_key = key
        _carrying_cache = (0, 0, 0)
        return _carrying_cache
    conv_target = _building_conv_target
    reverse = _conv_reverse
    tiles = _width * _height
    w = _width
    board = _board_mask
    cardinal = (
        _bm_et[_IDX_CONVEYOR]
        | _bm_et[_IDX_ARMOURED_CONVEYOR]
        | _bm_et[_IDX_SPLITTER]
    )
    dir_mask = _bm_dir
    convs_e = cardinal & dir_mask[_DIR_E]
    convs_w = cardinal & dir_mask[_DIR_W]
    convs_s = cardinal & dir_mask[_DIR_S]
    convs_n = cardinal & dir_mask[_DIR_N]
    bridges = _bm_et[_IDX_BRIDGE]
    nlc = _not_left_col
    nrc = _not_right_col
    ntr = _not_top_row
    nbr = _not_bottom_row

    ti_seed = _bm_conv_ti & bm_conveyors
    raw_ax_seed = _bm_conv_raw_ax & bm_conveyors
    refined_seed = _bm_conv_refined & bm_conveyors
    expand = _carrying_expand
    result = (
        expand(ti_seed, bm_conveyors, convs_e, convs_w, convs_s, convs_n, bridges,
               reverse, conv_target, w, board, tiles, nlc, nrc, ntr, nbr) if ti_seed else 0,
        expand(raw_ax_seed, bm_conveyors, convs_e, convs_w, convs_s, convs_n, bridges,
               reverse, conv_target, w, board, tiles, nlc, nrc, ntr, nbr) if raw_ax_seed else 0,
        expand(refined_seed, bm_conveyors, convs_e, convs_w, convs_s, convs_n, bridges,
               reverse, conv_target, w, board, tiles, nlc, nrc, ntr, nbr) if refined_seed else 0,
    )
    _carrying_cache_key = key
    _carrying_cache = result
    return result


_guard_conv_cache_version: int = -1
_guard_conv_cache: int = 0


def _compute_guard_conv() -> int:
    """Bitmask of CONVEYOR|ARMOURED_CONVEYOR tiles whose output target is a
    titanium/axionite ore tile not occupied by a conveyor-type building (conveyor,
    armoured conveyor, bridge, splitter) or a sentinel/gunner/breach/foundry."""
    global _guard_conv_cache_version, _guard_conv_cache
    if _struct_version == _guard_conv_cache_version:
        return _guard_conv_cache
    convs = _bm_et[_IDX_CONVEYOR] | _bm_et[_IDX_ARMOURED_CONVEYOR]
    if not convs:
        _guard_conv_cache_version = _struct_version
        _guard_conv_cache = 0
        return 0
    w = _width
    ore = (_bm_env[_IDX_ENV_ORE_TI] | _bm_env[_IDX_ENV_ORE_AX]) & ~_bm_landlocked
    right = convs & _bm_dir[_DIR_E] & ((_not_right_col & ore)>>1)
    left = convs & _bm_dir[_DIR_W] & ((_not_left_col & ore)<<1)
    up = convs & _bm_dir[_DIR_N] & ((_not_bottom_row & ore)<<w)
    down = convs & _bm_dir[_DIR_S] & ((_not_top_row & ore)>>w)
    result = right | left | up | down
    _guard_conv_cache_version = _struct_version
    _guard_conv_cache = result
    return result


def update_at(pos: Position) -> None:
    """Re-scan a single tile from the controller and update all per-tile state.

    Maintains env/seen/symmetry tracking, raw building state, marker decoding,
    core detection, and conveyor resource observation. Does NOT touch derived
    bitmasks rebuilt by `recompute_derived()` (e.g. `_bm_blocked`,
    `_bm_conveyors`, `_bm_conveyor_targets`, `_bm_ti_fed`, `_bm_ax_fed`,
    `_bm_guard_conveyor`); callers are expected to call `recompute_derived()`
    after iterating.
    """
    global _bm_seen, _bm_seen_observed, _bm_any_building
    global _bm_conv_raw_ax, _bm_conv_ti, _bm_conv_refined
    global _bm_damaged, _bm_very_damaged
    global _hor_sym, _ver_sym, _rot_sym
    global _max_id_seen, _my_core, _their_core, _core_id, _predicted_enemy_core
    global _struct_version

    rc = _rc
    width = _width
    height = _height
    n = pos.x + pos.y * width
    bit = 1 << n

    # Core-area tiles are owned by build_core_areas(); leave them alone.
    if (_bm_my_core_area | _bm_their_core_area) & bit:
        return

    nbit = ~bit

    # --- Environment / seen / symmetry tracking ---
    _bm_seen_observed |= bit
    if not (_bm_seen & bit):
        env_idx = _ENV_INT[rc.get_tile_env(pos)]
        _bm_env[env_idx] |= bit
        _bm_seen |= bit
        if _solved_sym:
            if _hor_sym:
                fx, fy = width - 1 - pos.x, pos.y
            elif _ver_sym:
                fx, fy = pos.x, height - 1 - pos.y
            else:
                fx, fy = width - 1 - pos.x, height - 1 - pos.y
            fbit = 1 << (fx + fy * width)
            _bm_env[env_idx] |= fbit
            _bm_seen |= fbit
        else:
            rx = width - 1 - pos.x
            ry = height - 1 - pos.y
            if _hor_sym:
                fbit = 1 << (rx + pos.y * width)
                if (_bm_seen & fbit) and not (_bm_env[env_idx] & fbit):
                    _hor_sym = False
            if _ver_sym:
                fbit = 1 << (pos.x + ry * width)
                if (_bm_seen & fbit) and not (_bm_env[env_idx] & fbit):
                    _ver_sym = False
            if _rot_sym:
                fbit = 1 << (rx + ry * width)
                if (_bm_seen & fbit) and not (_bm_env[env_idx] & fbit):
                    _rot_sym = False
        # Newly-observed walls block gunner rays in _compute_enemy_turret_threat
        # and _compute_my_gunner_claims, so invalidate those caches.
        if env_idx == _IDX_ENV_WALL:
            _struct_version += 1

    # Walls can never hold buildings and never change. Skip the controller
    # building lookup and all building-state work — saves get_tile_building_id
    # calls (one of the heaviest controller methods in the profile).
    if _bm_env[_IDX_ENV_WALL] & bit:
        return

    # --- Building state ---
    entity_id = rc.get_tile_building_id(pos)
    if entity_id is not None and entity_id > _max_id_seen:
        _max_id_seen = entity_id

    if entity_id is None:
        # No building — clear old
        old_et_idx = _building_et_idx[n]
        if old_et_idx >= 0:
            _bm_et[old_et_idx] &= nbit
            _bm_any_building &= nbit
            _bm_team[0] &= nbit
            _bm_team[1] &= nbit
            if _HAS_DIR[old_et_idx]:
                _bm_dir[_building_dir[n]] &= nbit
            if _IS_CONVEYOR[old_et_idx]:
                old_tn = _building_conv_target[n]
                if old_tn >= 0:
                    _conv_reverse[old_tn] &= nbit
                _bm_conv_ti &= nbit
                _bm_conv_raw_ax &= nbit
                _bm_conv_refined &= nbit
            _building_id[n] = 0
            _building_et_idx[n] = -1
            _building_hp[n] = 0
            _building_dir[n] = -1
            _building_conv_target[n] = -1
            _struct_version += 1
        _bm_damaged &= nbit
        _bm_very_damaged &= nbit
        return

    # Fast path: same building as before — skip re-reading type/team/direction
    if _building_id[n] == entity_id:
        et_idx = _building_et_idx[n]
        hp = rc.get_hp(entity_id)
        _building_hp[n] = hp
        max_hp = _MAX_HP_BY_IDX[et_idx]
        if hp < max_hp:
            _bm_damaged |= bit
        else:
            _bm_damaged &= nbit
        if hp < max_hp - 2:
            _bm_very_damaged |= bit
        else:
            _bm_very_damaged &= nbit
        if _IS_CONVEYOR[et_idx]:
            res = rc.get_stored_resource(entity_id)
            if res is not None:
                if res is _RT_AXIONITE:
                    _bm_conv_raw_ax |= bit
                    _bm_conv_ti &= nbit
                    _bm_conv_refined &= nbit
                elif res is _RT_TITANIUM:
                    _bm_conv_ti |= bit
                    _bm_conv_raw_ax &= nbit
                    _bm_conv_refined &= nbit
                else:
                    _bm_conv_refined |= bit
                    _bm_conv_raw_ax &= nbit
                    _bm_conv_ti &= nbit
            else:
                _bm_conv_raw_ax &= nbit
                _bm_conv_ti &= nbit
                _bm_conv_refined &= nbit
        return

    # Skip re-decode of already-seen markers
    if comms._marker_id_at[n] == entity_id:
        return

    et = rc.get_entity_type(entity_id)
    if et is _ET_MARKER:
        if rc.get_team(entity_id) == _my_team:
            message = comms.decode_visible_marker(entity_id, pos)
            if message is not None:
                estimated_turn = comms.estimate_turn(entity_id)
                _new_marker_messages.append((*message, estimated_turn))
        # Clear non-marker building state at this tile
        old_et_idx = _building_et_idx[n]
        if old_et_idx >= 0:
            _bm_et[old_et_idx] &= nbit
            _bm_any_building &= nbit
            _bm_team[0] &= nbit
            _bm_team[1] &= nbit
            if _HAS_DIR[old_et_idx]:
                _bm_dir[_building_dir[n]] &= nbit
            if _IS_CONVEYOR[old_et_idx]:
                old_tn = _building_conv_target[n]
                if old_tn >= 0:
                    _conv_reverse[old_tn] &= nbit
                _bm_conv_ti &= nbit
                _bm_conv_raw_ax &= nbit
                _bm_conv_refined &= nbit
            _building_id[n] = 0
            _building_et_idx[n] = -1
            _building_hp[n] = 0
            _building_dir[n] = -1
            _building_conv_target[n] = -1
            _struct_version += 1
        _bm_damaged &= nbit
        _bm_very_damaged &= nbit
        return

    # Different building — clear old state before writing new
    old_et_idx = _building_et_idx[n]
    if old_et_idx >= 0:
        _bm_et[old_et_idx] &= nbit
        _bm_any_building &= nbit
        _bm_team[0] &= nbit
        _bm_team[1] &= nbit
        if _HAS_DIR[old_et_idx]:
            _bm_dir[_building_dir[n]] &= nbit
        if _IS_CONVEYOR[old_et_idx]:
            old_tn = _building_conv_target[n]
            if old_tn >= 0:
                _conv_reverse[old_tn] &= nbit
            _bm_conv_ti &= nbit
            _bm_conv_raw_ax &= nbit
            _bm_conv_refined &= nbit

    et_idx = _ET_INT[et]
    direction = rc.get_direction(entity_id) if _HAS_DIR[et_idx] else None
    team_val = rc.get_team(entity_id)
    team_idx = _TM_INT[team_val]

    target = None
    if et is _ET_BRIDGE:
        target = rc.get_bridge_target(entity_id)
    elif _IS_CONVEYOR[et_idx] and direction is not None:
        dx, dy = _DIRECTION_DELTAS_I[_DIR_INT[direction]]
        target = Position(pos.x + dx, pos.y + dy)

    _building_id[n] = entity_id
    _building_et_idx[n] = et_idx
    hp = rc.get_hp(entity_id)
    _building_hp[n] = hp
    new_dir_idx = _DIR_INT[direction] if direction is not None else -1
    _building_dir[n] = new_dir_idx
    new_tn = (target.x + target.y * width) if target is not None else -1
    _building_conv_target[n] = new_tn

    _bm_et[et_idx] |= bit
    _bm_team[team_idx] |= bit
    _bm_any_building |= bit
    if direction is not None:
        _bm_dir[new_dir_idx] |= bit

    if _IS_CONVEYOR[et_idx] and new_tn >= 0 and team_idx == _my_team_idx:
        _conv_reverse[new_tn] |= bit

    max_hp = _MAX_HP_BY_IDX[et_idx]
    if hp < max_hp:
        _bm_damaged |= bit
    else:
        _bm_damaged &= nbit
    if hp < max_hp - 2:
        _bm_very_damaged |= bit
    else:
        _bm_very_damaged &= nbit

    if _IS_CONVEYOR[et_idx]:
        res = rc.get_stored_resource(entity_id)
        if res is not None:
            if res is _RT_AXIONITE:
                _bm_conv_raw_ax |= bit
                _bm_conv_ti &= nbit
                _bm_conv_refined &= nbit
            elif res is _RT_TITANIUM:
                _bm_conv_ti |= bit
                _bm_conv_raw_ax &= nbit
                _bm_conv_refined &= nbit
            else:
                _bm_conv_refined |= bit
                _bm_conv_raw_ax &= nbit
                _bm_conv_ti &= nbit
        else:
            _bm_conv_ti &= nbit
            _bm_conv_raw_ax &= nbit
            _bm_conv_refined &= nbit

    # First-sight core detection
    if et is _ET_CORE:
        if _my_core is None and team_val == _my_team:
            _my_core = core_center(entity_id, pos)
            _core_id = entity_id
            build_core_areas()
            _predicted_enemy_core = _compute_predicted_enemy_core()
        elif _their_core is None and team_val != _my_team:
            _their_core = core_center(entity_id, pos)
            build_core_areas()
            _predicted_enemy_core = _compute_predicted_enemy_core()

    # Different-building path always writes new structural state.
    _struct_version += 1

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
    old_visible = _bm_visible
    new_visible = 0
    saw_newly_visible = False
    for tile in nearby:
        bit = 1 << (tile.x + tile.y * width)
        new_visible |= bit
        if not (old_visible & bit):
            update_at(tile)
            saw_newly_visible = True
    _bm_visible = new_visible

    if not saw_newly_visible:
        return

    pathing.rebuild_broken_barriers(rc)
    recompute_derived()


def init(c: Controller):
    global _rc, _width, _height
    global _my_team, _my_team_idx
    global _prev_pos, _my_pos
    global _my_team, _my_team_idx
    global _building_id, _building_et_idx, _building_hp, _building_dir, _building_conv_target, _conv_reverse
    global _bm_et, _bm_team, _bm_env
    global _left_col, _right_col, _bottom_row, _top_row, _not_left_col, _not_right_col, _not_bottom_row, _not_top_row
    global _board_mask, _bm_dir
    global _struct_version
    global _turret_threat_cache_version, _turret_threat_cache
    global _my_gunner_claims_cache_version, _my_gunner_claims_cache
    global _conv_by_dir_cache_version, _conv_by_dir_cache
    global _route_targets_cache_key, _route_targets_cache
    global _route_reaches_core_cache_version, _route_reaches_core_cache
    global _recompute_structural_cache_version, _recompute_loaded_cache_key, _recompute_visible_cache_key
    _rc = c
    _my_team = _rc.get_team()
    _my_team_idx = _TM_INT[_my_team]
    _width = _rc.get_map_width()
    _height = _rc.get_map_height()
    tiles = _width * _height
    _board_mask = (1 << tiles) - 1
    _building_id          = [0] * tiles
    _building_et_idx      = [-1] * tiles
    _building_hp          = [-1] * tiles
    _building_dir         = [-1] * tiles
    _building_conv_target = [-1] * tiles
    _conv_reverse         = [0] * tiles

    _bm_et   = [0] * _NUM_ET
    _bm_team = [0] * _NUM_TEAM
    _bm_env  = [0] * _NUM_ENV
    _bm_dir  = [0] * len(Direction)

    _struct_version = 0
    _turret_threat_cache_version = -1
    _turret_threat_cache = (0, 0)
    _my_gunner_claims_cache_version = -1
    _my_gunner_claims_cache = 0
    _conv_by_dir_cache_version = -1
    _conv_by_dir_cache = [0] * 8
    _route_targets_cache_key = None
    _route_targets_cache = (0, 0, 0)
    _route_reaches_core_cache_version = -1
    _route_reaches_core_cache = (0, [])
    _recompute_structural_cache_version = -1
    _recompute_loaded_cache_key = None
    _recompute_visible_cache_key = None

    # Column masks for safe bit-shifting (prevent wrap-around)
    _left_col = _board_mask//((1<<_width)-1)
    _right_col = _left_col << (_width-1)
    _not_left_col = _board_mask & ~_left_col
    _not_right_col = _board_mask & ~_right_col
    _top_row = (1<<_width)-1
    _bottom_row = _top_row << (_width*(_height-1))
    _not_top_row = _board_mask & ~_top_row
    _not_bottom_row = _board_mask & ~_bottom_row
    _build_turret_shift_masks()

def update_symmetry_from_comms(sym_bits):
    """Update symmetry from comms. Each bit represents a possible symmetry."""
    global _hor_sym, _ver_sym, _rot_sym
    if not (sym_bits & 1):
        _hor_sym = False
    if not (sym_bits & 2):
        _ver_sym = False
    if not (sym_bits & 4):
        _rot_sym = False

def hor_flip(pos: Position) -> Position:
    return Position(_width - 1 - pos.x, pos.y)
def ver_flip(pos: Position) -> Position:
    return Position(pos.x, _height - 1 - pos.y)
def rot_flip(pos: Position) -> Position:
    return Position(_width - 1 - pos.x, _height - 1 - pos.y)

def flip(pos: Position) -> Position | None:
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
                _building_id[m] = _core_id
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

_route_targets_cache_key: tuple | None = None
_route_targets_cache: tuple[int, int, int] = (0, 0, 0)  # (route_targets, dead_end, feeding_enemy)
_route_reaches_core_cache_version: int = -1
_route_reaches_core_cache: tuple[int, list[int]] = (0, [])


def _compute_route_reaches_core() -> tuple[int, tuple[int, ...]]:
    global _route_reaches_core_cache_version, _route_reaches_core_cache
    if _struct_version == _route_reaches_core_cache_version:
        return _route_reaches_core_cache

    my_convs = _bm_conveyors & _bm_team[_my_team_idx]
    reverse = _conv_reverse
    reaches_core = 0
    order: list[int] = []

    layer = 0
    c_mask = _bm_my_core_area
    while c_mask:
        lsb = c_mask & -c_mask
        n = lsb.bit_length() - 1
        layer |= reverse[n] & my_convs
        c_mask ^= lsb

    # Single walk per layer: append to `order` and accumulate `next_layer`
    # in the same LSB-extraction loop, instead of two separate passes.
    while layer:
        reaches_core |= layer
        next_layer = 0
        m = layer
        order_append = order.append
        while m:
            lsb = m & -m
            n = lsb.bit_length() - 1
            order_append(n)
            next_layer |= reverse[n]
            m ^= lsb
        layer = next_layer & my_convs & ~reaches_core

    # Cache as list — callers only iterate, no need to pay for tuple().
    result = (reaches_core, order)
    _route_reaches_core_cache_version = _struct_version
    _route_reaches_core_cache = result
    return result


def _compute_route_targets() -> int:
    """Bitmask of tiles the route state can path toward.

    Route targets = my conveyors whose downstream chain reaches my core area,
    minus any that are part of a connected run of 4+ believed-loaded conveyors,
    minus guard conveyors. My core area is always routable.

    Side effect: sets `_bm_dead_end` to the targets of any *loaded* conveyor
    whose output is nothing or a building not in (conveyor-type, my core,
    my sentinel, my gunner, my breach). Also includes my conveyors pointing
    into an enemy non-road non-marker building.
    """
    my_team_idx = _my_team_idx
    bm_my = _bm_team[my_team_idx]
    my_convs = _bm_conveyors & bm_my
    loaded_union = _bm_conv_ti | _bm_conv_raw_ax | _bm_conv_refined
    visible_loaded_mine = my_convs & loaded_union & _bm_visible
    global _bm_dead_end, _bm_feeding_enemy
    global _route_targets_cache_key, _route_targets_cache
    key = (
        _struct_version,
        _bm_conv_ti,
        _bm_conv_raw_ax,
        _bm_conv_refined,
        visible_loaded_mine,
    )
    if key == _route_targets_cache_key:
        rt, de, fe = _route_targets_cache
        _bm_dead_end = de
        _bm_feeding_enemy = fe
        return rt
    conv_target = _building_conv_target
    tiles = _width * _height
    reverse = _conv_reverse
    all_convs = _bm_conveyors

    accepting = (
        _bm_et[_IDX_CONVEYOR] | _bm_et[_IDX_ARMOURED_CONVEYOR]
        | _bm_et[_IDX_BRIDGE] | _bm_et[_IDX_SPLITTER]
        | ((_bm_et[_IDX_CORE] | _bm_et[_IDX_SENTINEL]
            | _bm_et[_IDX_GUNNER] | _bm_et[_IDX_BREACH]
            | _bm_et[_IDX_FOUNDRY]) & bm_my)
    )
    enemy_hard = _bm_team[1 - my_team_idx] & ~_bm_et[_IDX_MARKER] & ~_bm_et[_IDX_ROAD]
    enemy_bm = _bm_team[1 - my_team_idx]
    enemy_fed_turret = (
        _bm_et[_IDX_GUNNER] | _bm_et[_IDX_SENTINEL] | _bm_et[_IDX_BREACH]
    ) & enemy_bm
    hard_block = (
        enemy_fed_turret
        | ((_bm_et[_IDX_LAUNCHER] | _bm_et[_IDX_BARRIER] | _bm_et[_IDX_CORE]) & enemy_bm)
        | _bm_et[_IDX_HARVESTER]
    )

    loaded_sources = all_convs & loaded_union

    # --- Dead-ends: targets of any *loaded* conveyor whose output isn't
    # accepting (or, for my conveyors, points into enemy non-road non-marker).
    # Loaded guard conveyors (pointing into open ore) mark themselves as dead
    # ends since the ore target tile is unbuildable.
    dead_ends = 0
    feeding_enemy = 0
    guard = _bm_guard_conveyor
    mask = loaded_sources
    while mask:
        lsb = mask & -mask
        n = lsb.bit_length() - 1
        tn = conv_target[n]
        tbit = 1 << tn
        if guard & lsb:
            pass
        elif guard & tbit:
            dead_ends |= tbit
        elif hard_block & tbit:
            dead_ends |= lsb
            if enemy_fed_turret & tbit:
                feeding_enemy |= lsb
        elif not (accepting & tbit):
            dead_ends |= tbit
        elif (bm_my & lsb) and (enemy_hard & tbit):
            dead_ends |= tbit
        mask ^= lsb
    _bm_dead_end = dead_ends
    _bm_feeding_enemy = feeding_enemy

    # --- Overlay loaded/visible state on top of the structural conveyor graph.
    reaches_core, reaches_core_order = _compute_route_reaches_core()
    loaded_mine = my_convs & loaded_union
    unroutable = 0
    if loaded_mine:
        run_loaded_arr = [0] * tiles
        ext_roots = 0
        run_visible_arr = [0] * tiles if visible_loaded_mine else None

        for n in reaches_core_order:
            lsb = 1 << n
            p = conv_target[n]
            if p >= 0 and (reaches_core & (1 << p)):
                p_loaded = run_loaded_arr[p]
                p_visible = run_visible_arr[p] if run_visible_arr is not None else 0
            else:
                p_loaded = 0
                p_visible = 0
            if loaded_mine & lsb:
                rl = p_loaded + 1
                run_loaded_arr[n] = rl
            else:
                rl = 0
            if run_visible_arr is not None and (visible_loaded_mine & lsb):
                rv = p_visible + 1
                run_visible_arr[n] = rv
                if rv >= 4:
                    ext_roots |= lsb
            if rl >= 4:
                if rl == 4:
                    cur = n
                    for _ in range(4):
                        unroutable |= 1 << cur
                        cur = conv_target[cur]
                        if cur < 0:
                            break
                else:
                    unroutable |= lsb

        # builder.draw_mask(unroutable, 255, 0, 0)

        # --- A visible 4-run jams the full chain: extend through all my conveyors
        # both upstream and downstream from each ext_root.
        if ext_roots:
            extended = ext_roots
            frontier = ext_roots
            while frontier:
                new_frontier = 0
                m = frontier
                while m:
                    lb = m & -m
                    n = lb.bit_length() - 1
                    tn = conv_target[n]
                    if 0 <= tn < tiles:
                        tbit = 1 << tn
                        if (my_convs & tbit) and not (extended & tbit):
                            new_frontier |= tbit
                    new_frontier |= reverse[n] & my_convs & ~extended
                    m ^= lb
                extended |= new_frontier
                frontier = new_frontier
            # builder.draw_mask(extended & ~unroutable, 255, 255, 255)
            unroutable |= extended

    # Color conveyors by unroutability reason (later draws win when overlapping):
    #   red     = part of a loaded run of 4+ along the chain toward core
    #   white   = propagated from a visible 4-run (already drawn above)
    #   orange  = my conveyor whose chain does not reach the core
    #   magenta = guard conveyor (points into open ore)
    # builder.draw_mask(my_convs & ~reaches_core, 255, 128, 0)
    # builder.draw_mask(_bm_guard_conveyor & my_convs, 255, 0, 255)

    # --- Extra dead-ends: raw-ax foundry sites (no foundry placed yet) whose
    # inbound conveyors are inferred to be carrying raw axionite.
    if builder.nav is not None:
        sites = builder.nav.raw_ax_foundry_sites()
        if sites and _bm_raw_ax_carrying:
            carrying = _bm_raw_ax_carrying
            rev = reverse
            m = sites
            while m:
                lsb = m & -m
                n = lsb.bit_length() - 1
                if rev[n] & carrying:
                    _bm_dead_end |= lsb
                m ^= lsb

    # Dead ends must never be enemy conveyor tiles themselves (targets of enemy
    # conveyors are still allowed).
    _bm_dead_end &= ~(_bm_conveyors & ~bm_my)

    result = _bm_my_core_area | (reaches_core & ~unroutable & ~_bm_guard_conveyor)
    _route_targets_cache_key = key
    _route_targets_cache = (result, _bm_dead_end, _bm_feeding_enemy)
    return result

_recompute_structural_cache_version: int = -1
_recompute_loaded_cache_key: tuple | None = None
_recompute_visible_cache_key: tuple | None = None


def _recompute_derived_structural() -> None:
    global _bm_blocked, _bm_conveyors, _bm_conveyor_targets
    global _bm_enemy_launch_adj
    global _bm_enemy_turret_threat, _bm_enemy_soft_threat, _bm_enemy_hard_threat
    global _bm_my_gunner_claims, _bm_conv_by_dir, _bm_conv_into_open_ore
    global _bm_guard_conveyor, _bm_passable_FFF
    global _recompute_structural_cache_version

    if _struct_version == _recompute_structural_cache_version:
        return
    _recompute_structural_cache_version = _struct_version

    width = _width
    height = _height
    my_team_idx = _my_team_idx
    bm_et = _bm_et
    bm_team = _bm_team
    bm_env = _bm_env

    _bm_conveyors = (
        bm_et[_IDX_CONVEYOR]
        | bm_et[_IDX_ARMOURED_CONVEYOR]
        | bm_et[_IDX_BRIDGE]
        | bm_et[_IDX_SPLITTER]
    )
    _bm_guard_conveyor = _compute_guard_conv()
    _bm_conveyor_targets = _conveyor_target_tiles(_bm_conveyors)

    _bm_blocked = bm_env[_IDX_ENV_WALL]
    _bm_blocked |= bm_et[_IDX_HARVESTER] | bm_et[_IDX_FOUNDRY]
    _bm_blocked |= bm_et[_IDX_GUNNER] | bm_et[_IDX_SENTINEL]
    _bm_blocked |= bm_et[_IDX_BREACH] | bm_et[_IDX_LAUNCHER]
    _bm_blocked |= bm_et[_IDX_BARRIER] & ~bm_team[my_team_idx]
    _bm_blocked |= _bm_their_core_area

    enemy_launchers = bm_et[_IDX_LAUNCHER] & ~bm_team[my_team_idx]
    _bm_enemy_launch_adj = 0
    mask = enemy_launchers
    while mask:
        lsb = mask & -mask
        ln = lsb.bit_length() - 1
        lx = ln % width
        ly = ln // width
        for dx, dy in _DIRECTION_DELTAS_I:
            nx = lx + dx
            ny = ly + dy
            if 0 <= nx < width and 0 <= ny < height:
                _bm_enemy_launch_adj |= 1 << (nx + ny * width)
        mask ^= lsb

    _bm_enemy_soft_threat, _bm_enemy_hard_threat = _compute_enemy_turret_threat()
    _bm_enemy_turret_threat = _bm_enemy_soft_threat | _bm_enemy_hard_threat
    _bm_my_gunner_claims = _compute_my_gunner_claims()
    _bm_conv_by_dir = _compute_conv_by_dir()
    _bm_conv_into_open_ore = _compute_conv_into_open_ore()
    _bm_passable_FFF = _board_mask & ~(_bm_blocked | _bm_enemy_launch_adj)


def _recompute_derived_loaded() -> None:
    global _bm_ti_fed, _bm_ax_fed
    global _bm_ti_carrying, _bm_raw_ax_carrying, _bm_refined_carrying
    global _recompute_loaded_cache_key

    key = (_struct_version, _bm_conv_ti, _bm_conv_raw_ax, _bm_conv_refined)
    if key == _recompute_loaded_cache_key:
        return
    _recompute_loaded_cache_key = key

    _bm_ti_fed, _bm_ax_fed = _compute_fed()
    _bm_ti_carrying, _bm_raw_ax_carrying, _bm_refined_carrying = _compute_carrying()


def _recompute_derived_visible() -> None:
    global _bm_route_targets, _recompute_visible_cache_key

    key = (_struct_version, _bm_conv_ti, _bm_conv_raw_ax, _bm_conv_refined, _bm_visible)
    if key == _recompute_visible_cache_key:
        return
    _recompute_visible_cache_key = key
    _bm_route_targets = _compute_route_targets()


def recompute_derived() -> None:
    """Rebuild derived bitmasks from the current tracked map state."""
    _recompute_derived_structural()
    _recompute_derived_loaded()
    _recompute_derived_visible()


def update(recompute: bool = True) -> None:
    global _my_core, _their_core, _core_id, _solved_sym
    global _hor_sym, _ver_sym, _rot_sym
    global _rush_tiebroken, _predicted_enemy_core
    global _bm_any_building
    global _bm_seen, _bm_visible, _prev_pos, _nearby_tiles, _nearby_tiles_pos, _my_pos
    global _bm_friendly_bots, _bm_enemy_bots
    global _bm_others_5x5, _bm_others_3x3
    global _max_id_seen
    global _new_marker_messages
    global _struct_version
    rc = _rc
    building_id = _building_id
    building_et_idx = _building_et_idx
    building_hp = _building_hp

    bm_et = _bm_et
    bm_team = _bm_team
    bm_env = _bm_env

    width = _width
    height = _height

    my_team_idx   = _my_team_idx
    my_pos        = rc.get_position()
    _my_pos       = my_pos

    visible_cached = (_nearby_tiles_pos == my_pos)
    if visible_cached:
        visible_tiles = _nearby_tiles
    else:
        visible_tiles = rc.get_nearby_tiles()
        _nearby_tiles = visible_tiles
        _nearby_tiles_pos = my_pos
    _prev_pos = my_pos
    _new_marker_messages = []

    if visible_cached:
        bm_visible = _bm_visible
        for tile in visible_tiles:
            update_at(tile)
    else:
        bm_visible = 0
        for tile in visible_tiles:
            bit = 1 << (tile.x + tile.y * width)
            bm_visible |= bit
            update_at(tile)
        _bm_visible = bm_visible

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
        bm_seen = _bm_seen
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
        # Symmetry-solve mirrored walls into _bm_env[WALL] and may have inserted
        # the predicted enemy core into _bm_et[CORE] / _bm_team / _building_*.
        # Both can affect cached compute_* outputs, so invalidate.
        _struct_version += 1

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

    # --- Update builder bot tracking ---
    _bm_friendly_bots = 0
    _bm_enemy_bots = 0
    seen_uids = set()
    cur_round = rc.get_current_round()
    self_id = rc.get_id()
    for uid in rc.get_nearby_units():
        if uid > _max_id_seen:
            _max_id_seen = uid
        if rc.get_entity_type(uid) != _ET_BUILDER_BOT:
            continue
        if uid == self_id:
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
        _bot_last_seen[uid] = cur_round
        seen_uids.add(uid)
    # Single pass: purge stale bots (visible-but-gone after grace) and rebuild
    # the friendly/enemy bitmasks from the survivors.
    grace_cutoff = cur_round - 1
    to_remove = []
    for uid, n in _bot_pos.items():
        bit = 1 << n
        if uid not in seen_uids and (bm_visible & bit):
            if _bot_last_seen.get(uid, -1) < grace_cutoff:
                to_remove.append(uid)
                continue
        if _bot_team[uid] == my_team_idx:
            _bm_friendly_bots |= bit
        else:
            _bm_enemy_bots |= bit
    for uid in to_remove:
        n = _bot_pos[uid]
        if _bot_at.get(n) == uid:
            del _bot_at[n]
        del _bot_pos[uid]
        del _bot_team[uid]
        _bot_last_seen.pop(uid, None)

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

    while len(_max_id_by_round) <= cur_round:
        _max_id_by_round.append(0)
    _max_id_by_round[cur_round] = _max_id_seen

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
        mask |= (_bm_conveyors&~_bm_conv_into_open_ore) | _bm_conveyor_targets | _bm_my_core_area
        enemy_roads = _bm_et[_IDX_ROAD] & _bm_team[1 - _my_team_idx]
        if enemy_roads and _bm_enemy_bots:
            mask |= enemy_roads & expand_chebyshev(_bm_enemy_bots)
    if avoid_ore:
        ore = _bm_env[_IDX_ENV_ORE_TI] | _bm_env[_IDX_ENV_ORE_AX]
        w = _width
        landlocking = ore | ~_bm_seen&_board_mask
        landlocked = landlocking & (landlocking >> 1 & _not_right_col) & (landlocking << 1 & _not_left_col) & (landlocking >> w) & (landlocking << w)
        mask |= ore & ~landlocked & builder._harvest_zone
    # if avoid_core:
    #     mask |= _bm_my_core_area
    if avoid_builders:
        mask |= _bm_friendly_bots | _bm_enemy_bots
    threat = _bm_enemy_hard_threat
    pos = _my_pos
    my_bit = 1 << (pos.x + pos.y * _width)
    if not (threat & my_bit):
        mask |= threat
    mask |= _bm_enemy_launch_adj
    return mask
