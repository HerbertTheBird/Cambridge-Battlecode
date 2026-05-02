from cambc import *

import map_info
import pathing
from pathing import Pathing
import units.builder
from log import DRAW_DEBUG, log

rc: Controller = None
nav: Pathing = None

_SHIFT_PLAN_WIDTH = -1
_SHIFT_PLAN_HEIGHT = -1
_SENTINEL_REACH_SHIFTS = ()
_SENTINEL_REACH_POS_SHIFTS = ()
_SENTINEL_REACH_NEG_SHIFTS = ()
_GUNNER_STEP_SHIFTS = ()
_CARDINAL_BLOCKER_SHIFTS = ()

_GROUP_MASK_CACHE_VERSION = -1
_GROUP_MASK_CACHE_ENEMY = -1
_SENTINEL_GROUP_MASKS = ()
_GUNNER_GROUP_MASKS = ()

_GUNNER_BLOCKED_CACHE_VERSION = -1
_GUNNER_BLOCKED_MASK = 0

_TURRET_FEED_CACHE_VERSION = -1
_TURRET_FEED_CACHE_MASK = 0

_EMPTY_CANDIDATE_MASKS = (0,) * 8

_SENTINEL_SCORE_CACHE_KEY = None
_SENTINEL_SCORE_CACHE = None
_GUNNER_PER_DIR_CACHE_KEY = None
_GUNNER_PER_DIR_CACHE = None



def init(c: Controller):
    global rc, nav
    rc = c
    nav = units.builder.nav
    _ensure_attack_shift_plans()


SENTINEL_BUILDING_SCORE = [0] * map_info._NUM_ET
SENTINEL_BUILDING_SCORE[map_info._IDX_CORE] = 6
SENTINEL_BUILDING_SCORE[map_info._IDX_HARVESTER] = 0
SENTINEL_BUILDING_SCORE[map_info._IDX_FOUNDRY] = 16
SENTINEL_BUILDING_SCORE[map_info._IDX_GUNNER] = 20
SENTINEL_BUILDING_SCORE[map_info._IDX_SENTINEL] = 20
SENTINEL_BUILDING_SCORE[map_info._IDX_BREACH] = 24
SENTINEL_BUILDING_SCORE[map_info._IDX_LAUNCHER] = 8
SENTINEL_BUILDING_SCORE[map_info._IDX_CONVEYOR] = 8
SENTINEL_BUILDING_SCORE[map_info._IDX_ARMOURED_CONVEYOR] = 4
SENTINEL_BUILDING_SCORE[map_info._IDX_BARRIER] = 8
SENTINEL_BUILDING_SCORE[map_info._IDX_BRIDGE] = 8
SENTINEL_BUILDING_SCORE[map_info._IDX_SPLITTER] = 8

# Gunners snipe single high-value lanes: big bonus for core + backline turrets,
# smaller gain on clustered infra (sentinels already out-damage them there).
GUNNER_BUILDING_SCORE = [0] * map_info._NUM_ET
GUNNER_BUILDING_SCORE[map_info._IDX_CORE] = 128
GUNNER_BUILDING_SCORE[map_info._IDX_HARVESTER] = 0
GUNNER_BUILDING_SCORE[map_info._IDX_FOUNDRY] = 56
GUNNER_BUILDING_SCORE[map_info._IDX_GUNNER] = 100
GUNNER_BUILDING_SCORE[map_info._IDX_SENTINEL] = 100
GUNNER_BUILDING_SCORE[map_info._IDX_BREACH] = 120
GUNNER_BUILDING_SCORE[map_info._IDX_LAUNCHER] = 14
GUNNER_BUILDING_SCORE[map_info._IDX_CONVEYOR] = 4
GUNNER_BUILDING_SCORE[map_info._IDX_ARMOURED_CONVEYOR] = 4
GUNNER_BUILDING_SCORE[map_info._IDX_BARRIER] = 14
GUNNER_BUILDING_SCORE[map_info._IDX_BRIDGE] = 4
GUNNER_BUILDING_SCORE[map_info._IDX_SPLITTER] = 4

_NON_CORE_TYPE_INDICES = (
    map_info._IDX_FOUNDRY,
    map_info._IDX_GUNNER,
    map_info._IDX_SENTINEL,
    map_info._IDX_BREACH,
    map_info._IDX_LAUNCHER,
    map_info._IDX_HARVESTER,
    map_info._IDX_CONVEYOR,
    map_info._IDX_ARMOURED_CONVEYOR,
    map_info._IDX_BARRIER,
    map_info._IDX_BRIDGE,
    map_info._IDX_SPLITTER,
)

_NUM_PLANES = 9  # max 511; per-dir scores stay well under in realistic cases

SCORE_THRESHOLD_FACTOR = 0.25
MIN_ATTACK_SCORE = 16
THREAT_PENALTY = 4
NON_GOOD_TILE_BUFF = 6

# Gunner-only knobs. Distance discount: enemy at ray-step k counts as
# round(score * 0.9^k); k=0 is the tile directly in front of the gunner.
# Rotation bonus: each direction at a tile gains (sum_8_directions >> 3),
# approximating 0.1 * total — represents value accessible by rotating.
_DISCOUNT_NUM = 9
_DISCOUNT_DEN = 10
_MAX_DISCOUNT_STEPS = 3  # gunner ray length (vision radius squared 13)
_ROTATION_SHIFT = 3

cant_attack = 0


# ---------------------------------------------------------------------------
# Bit-sliced score plane helpers
# ---------------------------------------------------------------------------

def _bits_of(c):
    """Tuple of bit positions set in c. Pure function; cache at module load."""
    result = []
    x, i = c, 0
    while x:
        if x & 1:
            result.append(i)
        x >>= 1
        i += 1
    return tuple(result)


def _build_score_groups(score_table):
    """Group non-core type indices by equal score."""
    groups: dict[int, list[int]] = {}
    for t_idx in _NON_CORE_TYPE_INDICES:
        s = score_table[t_idx]
        if s:
            groups.setdefault(s, []).append(t_idx)
    return [(s, _bits_of(s), tuple(idxs)) for s, idxs in groups.items()]


def _step_score(score, step):
    """Round-half-up integer of score * (_DISCOUNT_NUM / _DISCOUNT_DEN)^step."""
    val = score
    for _ in range(step):
        val = (val * _DISCOUNT_NUM + _DISCOUNT_DEN // 2) // _DISCOUNT_DEN
    return val


def _step_bits_tuple(score):
    """Tuple of bit-position tuples for the discounted score at each ray step."""
    return tuple(_bits_of(_step_score(score, k)) for k in range(_MAX_DISCOUNT_STEPS))


def _build_gunner_score_groups(score_table):
    """Same shape as _build_score_groups, but `bits` is a per-step tuple of
    bit-position tuples — one entry per ray step, holding the bits of the
    discounted score at that step."""
    groups: dict[int, list[int]] = {}
    for t_idx in _NON_CORE_TYPE_INDICES:
        s = score_table[t_idx]
        if s:
            groups.setdefault(s, []).append(t_idx)
    return [(s, _step_bits_tuple(s), tuple(idxs)) for s, idxs in groups.items()]


_SENTINEL_SCORE_GROUPS = _build_score_groups(SENTINEL_BUILDING_SCORE)
_GUNNER_SCORE_GROUPS = _build_gunner_score_groups(GUNNER_BUILDING_SCORE)

_THREAT_PENALTY_BITS = _bits_of(THREAT_PENALTY)
_SENT_CORE_BITS = _bits_of(SENTINEL_BUILDING_SCORE[map_info._IDX_CORE])
_GUN_CORE_BITS_BY_STEP = _step_bits_tuple(GUNNER_BUILDING_SCORE[map_info._IDX_CORE])

def _ensure_attack_shift_plans():
    """Precompute static shift plans used by the hot attack scorers."""
    global _SHIFT_PLAN_WIDTH, _SHIFT_PLAN_HEIGHT
    global _SENTINEL_REACH_SHIFTS, _SENTINEL_REACH_POS_SHIFTS, _SENTINEL_REACH_NEG_SHIFTS
    global _GUNNER_STEP_SHIFTS, _CARDINAL_BLOCKER_SHIFTS

    w = map_info._width
    h = map_info._height
    if _SHIFT_PLAN_WIDTH == w and _SHIFT_PLAN_HEIGHT == h:
        return

    shift_masks = map_info._turret_shift_masks

    sentinel_plans = []
    sentinel_pos_plans = []
    sentinel_neg_plans = []
    for d in range(8):
        steps = []
        pos_steps = []
        neg_steps = []
        for dx, dy in map_info._SENTINEL_OFFSETS[d]:
            sdx = -dx
            sdy = -dy
            sm = shift_masks.get((sdx, sdy))
            if sm is None:
                continue
            rev_off = sdx + sdy * w
            steps.append((sm, rev_off))
            if rev_off >= 0:
                pos_steps.append((sm, rev_off))
            else:
                neg_steps.append((sm, -rev_off))
        sentinel_plans.append(tuple(steps))
        sentinel_pos_plans.append(tuple(pos_steps))
        sentinel_neg_plans.append(tuple(neg_steps))

    gunner_plans = []
    blocker_plans = [None] * 8
    for d in range(8):
        dx, dy = map_info._DIRECTION_DELTAS_I[d]
        sdx = -dx
        sdy = -dy
        sm = shift_masks.get((sdx, sdy))
        if sm is None:
            gunner_plans.append((0, 0, 0))
        else:
            gunner_plans.append((sm, sdx + sdy * w, len(map_info._GUNNER_RAYS[d])))
        if (d & 1) == 0 and sm is not None:
            blocker_plans[d] = (sm, sdx + sdy * w)

    _SENTINEL_REACH_SHIFTS = tuple(sentinel_plans)
    _SENTINEL_REACH_POS_SHIFTS = tuple(sentinel_pos_plans)
    _SENTINEL_REACH_NEG_SHIFTS = tuple(sentinel_neg_plans)
    _GUNNER_STEP_SHIFTS = tuple(gunner_plans)
    _CARDINAL_BLOCKER_SHIFTS = tuple(blocker_plans)
    _SHIFT_PLAN_WIDTH = w
    _SHIFT_PLAN_HEIGHT = h


def _enemy_score_group_masks(enemy_team_bm):
    """Grouped enemy masks shared by sentinel/gunner scoring for this layout."""
    global _GROUP_MASK_CACHE_VERSION, _GROUP_MASK_CACHE_ENEMY
    global _SENTINEL_GROUP_MASKS, _GUNNER_GROUP_MASKS

    sv = map_info._struct_version
    if _GROUP_MASK_CACHE_VERSION == sv and _GROUP_MASK_CACHE_ENEMY == enemy_team_bm:
        return _SENTINEL_GROUP_MASKS, _GUNNER_GROUP_MASKS

    bm_et = map_info._bm_et

    sentinel_groups = []
    for s, bits, idxs in _SENTINEL_SCORE_GROUPS:
        bm_group = 0
        for t_idx in idxs:
            bm_group |= bm_et[t_idx]
        bm_group &= enemy_team_bm
        if bm_group:
            sentinel_groups.append((s, bits, bm_group))

    gunner_groups = []
    for s, bits, idxs in _GUNNER_SCORE_GROUPS:
        bm_group = 0
        for t_idx in idxs:
            bm_group |= bm_et[t_idx]
        bm_group &= enemy_team_bm
        if bm_group:
            gunner_groups.append((s, bits, bm_group))

    _GROUP_MASK_CACHE_VERSION = sv
    _GROUP_MASK_CACHE_ENEMY = enemy_team_bm
    _SENTINEL_GROUP_MASKS = tuple(sentinel_groups)
    _GUNNER_GROUP_MASKS = tuple(gunner_groups)
    return _SENTINEL_GROUP_MASKS, _GUNNER_GROUP_MASKS


def _add_bits_to_planes(planes, bits, mask):
    """Bit-sliced: add the constant whose set bits are `bits` to counters."""
    if not bits or not mask:
        return
    for i in bits:
        carry = planes[i] & mask
        planes[i] ^= mask
        j = i + 1
        while carry and j < _NUM_PLANES:
            new_carry = planes[j] & carry
            planes[j] ^= carry
            carry = new_carry
            j += 1


def _add_planes_into(dst, src):
    """Bit-sliced plane-list sum: dst += src, tile-wise. Full-adder rippled
    across planes; top-plane overflow is discarded. Caller ensures totals fit
    in _NUM_PLANES bits."""
    carry = 0
    for i in range(_NUM_PLANES):
        a = dst[i]
        b = src[i]
        dst[i] = a ^ b ^ carry
        carry = (a & b) | (carry & (a ^ b))


def _read_score(planes, tile_n):
    """Read the integer score stored at `tile_n` across the planes."""
    score = 0
    for i in range(_NUM_PLANES):
        if (planes[i] >> tile_n) & 1:
            score |= 1 << i
    return score


def _max_score_in_mask(planes, mask):
    """Maximum counter value among tiles whose bit is set in `mask`. Bit-parallel."""
    if not mask:
        return 0
    max_val = 0
    cur = mask
    for i in range(_NUM_PLANES - 1, -1, -1):
        hi = planes[i] & cur
        if hi:
            max_val |= 1 << i
            cur = hi
    return max_val


def _ge_threshold_mask(planes, threshold, candidates):
    """Bitmask of tiles in `candidates` whose counter >= `threshold`. Bit-parallel."""
    if threshold <= 0:
        return candidates
    eq = candidates
    gt = 0
    for i in range(_NUM_PLANES - 1, -1, -1):
        p = planes[i]
        if (threshold >> i) & 1:
            eq &= p
        else:
            gt |= eq & p
            eq &= ~p
    return gt | eq

def _get_cached_sentinel_scores(enemy_team_bm: int, threat: int, sentinel_masks: tuple[int, ...]):
    """Sentinel per-direction score planes, cached across rounds by exact masks."""
    global _SENTINEL_SCORE_CACHE_KEY, _SENTINEL_SCORE_CACHE

    key = (map_info._struct_version, sentinel_masks)
    if key != _SENTINEL_SCORE_CACHE_KEY:
        _SENTINEL_SCORE_CACHE = _compute_sentinel_dir_scores(
            enemy_team_bm, threat, sentinel_masks
        )
        _SENTINEL_SCORE_CACHE_KEY = key
    return _SENTINEL_SCORE_CACHE


def _get_cached_gunner_per_dir(enemy_team_bm: int, threat: int, gunner_masks: tuple[int, ...]):
    """Gunner per-direction planes, cached across rounds by exact masks."""
    global _GUNNER_PER_DIR_CACHE_KEY, _GUNNER_PER_DIR_CACHE

    key = (map_info._struct_version, gunner_masks)
    if key != _GUNNER_PER_DIR_CACHE_KEY:
        _GUNNER_PER_DIR_CACHE = _compute_gunner_dir_scores(
            enemy_team_bm, threat, gunner_masks
        )
        _GUNNER_PER_DIR_CACHE_KEY = key
    return _GUNNER_PER_DIR_CACHE


# ---------------------------------------------------------------------------
# Sentinel: returns 8 plane-lists, one per facing direction
# ---------------------------------------------------------------------------

def _compute_sentinel_dir_scores(enemy_team_bm, threat, sentinel_masks):
    """For each of 8 facing directions, compute a per-tile sentinel score plane
    list. Returns: list of 8 plane-lists (list[list[int]]). Reading position n
    from the d-th inner list yields the sentinel's total damage-score if
    placed at n facing direction d — but ONLY if n is a valid placement tile
    for that direction (per `sentinel_masks[d]`); otherwise the score reads 0.

    Scores sum SENTINEL_BUILDING_SCORE for each enemy building in the
    sentinel's offset pattern. THREAT_PENALTY is baked in exactly once per
    plane at the end — applied to non-threat reached placeable tiles using the
    FINAL non_zero union, so the bake count doesn't depend on direction
    iteration order."""
    _ensure_attack_shift_plans()
    bm_et = map_info._bm_et
    add_bits_to_planes = _add_bits_to_planes
    num_planes = _NUM_PLANES
    core_idx = map_info._IDX_CORE
    board_mask = map_info._board_mask
    pos_shifts = _SENTINEL_REACH_POS_SHIFTS
    neg_shifts = _SENTINEL_REACH_NEG_SHIFTS

    core_mask = bm_et[core_idx] & enemy_team_bm
    type_contribs, _ = _enemy_score_group_masks(enemy_team_bm)
    sent_core_bits = _SENT_CORE_BITS
    threat_penalty_bits = _THREAT_PENALTY_BITS

    non_threat = board_mask & ~threat
    non_zero = 0
    all_planes = []
    append_planes = all_planes.append
    for d in range(8):
        mask_d = sentinel_masks[d]
        if not mask_d:
            append_planes([0] * num_planes)
            continue
        planes = [0] * num_planes
        core_reach = 0
        for sm, rev_off in pos_shifts[d]:
            if core_mask:
                masked = core_mask & sm
                if masked:
                    core_reach |= masked << rev_off
            for _s, bits, bm_t in type_contribs:
                masked = bm_t & sm
                if not masked:
                    continue
                contrib = masked << rev_off
                non_zero |= contrib
                restricted = contrib & mask_d
                if restricted:
                    add_bits_to_planes(planes, bits, restricted)
        for sm, rev_off in neg_shifts[d]:
            if core_mask:
                masked = core_mask & sm
                if masked:
                    core_reach |= masked >> rev_off
            for _s, bits, bm_t in type_contribs:
                masked = bm_t & sm
                if not masked:
                    continue
                contrib = masked >> rev_off
                non_zero |= contrib
                restricted = contrib & mask_d
                if restricted:
                    add_bits_to_planes(planes, bits, restricted)
        non_zero |= core_reach
        if core_reach and sent_core_bits:
            core_restricted = core_reach & mask_d
            if core_restricted:
                add_bits_to_planes(planes, sent_core_bits, core_restricted)
        append_planes(planes)

    if threat_penalty_bits:
        baked_base = non_threat & non_zero
        for d, planes in enumerate(all_planes):
            baked = baked_base & sentinel_masks[d]
            if baked:
                add_bits_to_planes(planes, threat_penalty_bits, baked)
    return all_planes


# ---------------------------------------------------------------------------
# Gunner: one plane-list. Either a single facing, or max over all 8 facings.
# ---------------------------------------------------------------------------

def _gunner_ray_blocked_mask():
    """Tiles that block a gunner ray: walls + allied non-road, non-marker
    buildings. A gunner can't shoot through its own infrastructure."""
    global _GUNNER_BLOCKED_CACHE_VERSION, _GUNNER_BLOCKED_MASK

    sv = map_info._struct_version
    if _GUNNER_BLOCKED_CACHE_VERSION == sv:
        return _GUNNER_BLOCKED_MASK

    walls = map_info._bm_env[map_info._IDX_ENV_WALL]
    my_team = map_info._bm_team[map_info._my_team_idx]
    my_solid = (
        my_team
        & ~map_info._bm_et[map_info._IDX_ROAD]
        & ~map_info._bm_et[map_info._IDX_MARKER]
    )
    _GUNNER_BLOCKED_MASK = walls | my_solid
    _GUNNER_BLOCKED_CACHE_VERSION = sv
    return _GUNNER_BLOCKED_MASK


def _compute_gunner_dir_scores(enemy_team_bm, threat, gunner_masks):
    """For each of 8 facing directions, compute a per-tile gunner score plane
    list. Returns: list of 8 plane-lists (list[list[int]]). Reading position n
    from the d-th inner list yields the gunner's score if placed at n facing
    direction d — but ONLY if n is a valid placement tile for that direction
    (per `gunner_masks[d]`); otherwise the score reads 0.

    Gunner rays are blocked by walls AND by allied non-road, non-marker
    buildings. Scores come from GUNNER_BUILDING_SCORE, applied with a per-step
    distance discount (round(score * 0.9^k) for an enemy at ray-step k from
    the gunner — k=0 is the adjacent tile). Each gunner tile additionally
    gains a rotation bonus equal to (sum_of_8_directions >> _ROTATION_SHIFT),
    weighting tiles whose other facings carry value too.

    Core is single-counted per gunner per direction at the closest hit step
    (matches prior behavior, just discounted by that step's factor).

    THREAT_PENALTY is baked exactly once per plane at the end on non-threat
    reached placeable tiles using the FINAL non_zero union."""
    bm_et = map_info._bm_et
    _ensure_attack_shift_plans()
    add_bits_to_planes = _add_bits_to_planes
    add_planes_into = _add_planes_into
    num_planes = _NUM_PLANES
    board_mask = map_info._board_mask
    not_blocked = board_mask & ~_gunner_ray_blocked_mask()
    step_shifts = _GUNNER_STEP_SHIFTS
    threat_penalty_bits = _THREAT_PENALTY_BITS

    core_mask = bm_et[map_info._IDX_CORE] & enemy_team_bm
    gun_core_bits_by_step = _GUN_CORE_BITS_BY_STEP
    _, type_initial = _enemy_score_group_masks(enemy_team_bm)

    non_threat = board_mask & ~threat
    non_zero = 0
    all_planes = []
    append_planes = all_planes.append
    n_types = len(type_initial)
    type_bits_by_step_arr = [t[1] for t in type_initial]
    type_bm_initial = [t[2] for t in type_initial]
    type_bms = [0] * n_types
    for d in range(8):
        planes = [0] * num_planes
        mask_d = gunner_masks[d]
        sm, soff, max_step = step_shifts[d]
        if not sm or max_step == 0 or not mask_d:
            append_planes(planes)
            continue
        combined_sm = sm & not_blocked
        core_cur = core_mask
        for j in range(n_types):
            type_bms[j] = type_bm_initial[j]
        core_seen = 0  # mask_d gunners that already had core scored at a closer step
        if soff >= 0:
            for step in range(max_step):
                if core_cur:
                    core_cur = (core_cur & combined_sm) << soff
                    if core_cur:
                        non_zero |= core_cur
                        first_hits = core_cur & mask_d & ~core_seen
                        if first_hits and gun_core_bits_by_step[step]:
                            add_bits_to_planes(planes, gun_core_bits_by_step[step], first_hits)
                            core_seen |= first_hits
                any_alive = False
                for j in range(n_types):
                    bm_t = type_bms[j]
                    if not bm_t:
                        continue
                    shifted = (bm_t & combined_sm) << soff
                    type_bms[j] = shifted
                    if shifted:
                        any_alive = True
                        non_zero |= shifted
                        restricted = shifted & mask_d
                        if restricted:
                            step_bits = type_bits_by_step_arr[j][step]
                            if step_bits:
                                add_bits_to_planes(planes, step_bits, restricted)
                if not core_cur and not any_alive:
                    break
        else:
            nsoff = -soff
            for step in range(max_step):
                if core_cur:
                    core_cur = (core_cur & combined_sm) >> nsoff
                    if core_cur:
                        non_zero |= core_cur
                        first_hits = core_cur & mask_d & ~core_seen
                        if first_hits and gun_core_bits_by_step[step]:
                            add_bits_to_planes(planes, gun_core_bits_by_step[step], first_hits)
                            core_seen |= first_hits
                any_alive = False
                for j in range(n_types):
                    bm_t = type_bms[j]
                    if not bm_t:
                        continue
                    shifted = (bm_t & combined_sm) >> nsoff
                    type_bms[j] = shifted
                    if shifted:
                        any_alive = True
                        non_zero |= shifted
                        restricted = shifted & mask_d
                        if restricted:
                            step_bits = type_bits_by_step_arr[j][step]
                            if step_bits:
                                add_bits_to_planes(planes, step_bits, restricted)
                if not core_cur and not any_alive:
                    break
        append_planes(planes)

    # Rotation bonus: add (sum_8_dirs >> _ROTATION_SHIFT) to each direction's
    # plane. Approximates 0.1 * total. Computed before threat penalty so the
    # bonus reflects raw enemy-damage potential, not threat-tile preference.
    summed = [0] * num_planes
    for d_planes in all_planes:
        add_planes_into(summed, d_planes)
    bonus_planes = [0] * num_planes
    for i in range(num_planes - _ROTATION_SHIFT):
        bonus_planes[i] = summed[i + _ROTATION_SHIFT]
    if any(bonus_planes):
        for d_planes in all_planes:
            add_planes_into(d_planes, bonus_planes)

    if threat_penalty_bits:
        baked_base = non_threat & non_zero
        for d, planes in enumerate(all_planes):
            baked = baked_base & gunner_masks[d]
            if baked:
                add_bits_to_planes(planes, threat_penalty_bits, baked)
    return all_planes


def _good_conveyor_mask() -> int:
    """Friendly infra tiles we prefer not to replace with attack builds."""
    my_team = map_info._bm_team[map_info._my_team_idx]
    bm_et = map_info._bm_et
    friendly_conveyors = (
        bm_et[map_info._IDX_CONVEYOR]
        | bm_et[map_info._IDX_ARMOURED_CONVEYOR]
    ) & my_team
    friendly_bridges = bm_et[map_info._IDX_BRIDGE] & my_team
    return friendly_bridges | (friendly_conveyors & ~map_info._bm_guard_conveyor)


def _selection_bias_for_bit(bit: int) -> int:
    return 0 if (_good_conveyor_mask() & bit) else NON_GOOD_TILE_BUFF


# ---------------------------------------------------------------------------
# Per-tile "best direction / best type" pick
# ---------------------------------------------------------------------------

def get_best_direction(pos):
    """Pick (Direction, turret_type, score) for a turret at pos.

    Sentinel and gunner both use their best valid-placement direction score
    as the decision basis. Non-good tiles get a uniform selection bias so
    friendly "good" conveyors/bridges are less likely to be sacrificed for
    low-value attacks.

    Breach is ignored for now — never returned."""
    w = map_info._width
    px, py = pos.x, pos.y
    n = px + py * w
    bit = 1 << n

    _ensure_sentinel_planes()
    _ensure_gunner_scores()
    sent_planes_by_dir = _round_cache_sentinel_planes
    gun_planes_by_dir = _round_cache_gunner_planes
    sentinel_masks = _round_cache_placement_masks[0]
    gunner_masks = _round_cache_placement_masks[1]

    directions = map_info._DIRECTIONS

    # Sentinel: best valid-placement direction at pos.
    best_s_dir, best_s_score = Direction.NORTH, -1
    for d in range(8):
        if not (sentinel_masks[d] & bit):
            continue
        s = _read_score(sent_planes_by_dir[d], n)
        if s > best_s_score:
            best_s_score = s
            best_s_dir = directions[d]

    # Gunner: best valid-placement direction at pos.
    best_g_dir, best_g_score = Direction.NORTH, -1
    for d in range(8):
        if not (gunner_masks[d] & bit):
            continue
        s = _read_score(gun_planes_by_dir[d], n)
        if s > best_g_score:
            best_g_score = s
            best_g_dir = directions[d]

    bias = _selection_bias_for_bit(bit)
    best_s_effective = best_s_score + bias if best_s_score > 0 else best_s_score
    best_g_effective = best_g_score + bias if best_g_score > 0 else best_g_score

    if best_g_score < 0 or best_s_effective >= best_g_effective:
        return best_s_dir, EntityType.SENTINEL, best_s_effective
    return best_g_dir, EntityType.GUNNER, best_g_effective


# ---------------------------------------------------------------------------
# Candidate generation
# ---------------------------------------------------------------------------

def _turret_feed_chains(max_steps: int = 8) -> int:
    """Bitmask of conveyor-type tiles (either team) that feed into my
    gunners/sentinels, walking upstream up to max_steps hops via
    `_conv_reverse`. Includes conveyors, armoured conveyors, bridges, and
    splitters (whatever `_conv_reverse` registers)."""
    global _TURRET_FEED_CACHE_VERSION, _TURRET_FEED_CACHE_MASK

    sv = map_info._struct_version
    if _TURRET_FEED_CACHE_VERSION == sv:
        return _TURRET_FEED_CACHE_MASK

    my_team = map_info._bm_team[map_info._my_team_idx]
    turrets = (map_info._bm_et[map_info._IDX_GUNNER] | map_info._bm_et[map_info._IDX_SENTINEL]) & my_team
    if not turrets:
        _TURRET_FEED_CACHE_VERSION = sv
        _TURRET_FEED_CACHE_MASK = 0
        return 0

    reverse = map_info._conv_reverse
    visited = 0
    frontier = turrets
    for _ in range(max_steps):
        next_frontier = 0
        m = frontier
        while m:
            lsb = m & -m
            n = lsb.bit_length() - 1
            next_frontier |= reverse[n]
            m ^= lsb
        next_frontier &= ~visited
        if not next_frontier:
            break
        visited |= next_frontier
        frontier = next_frontier
    _TURRET_FEED_CACHE_VERSION = sv
    _TURRET_FEED_CACHE_MASK = visited
    return visited


def _placement_candidates():
    """Returns (sentinel_masks, gunner_masks): two tuples of 8 bitmasks, one
    per facing direction. Loader blockers are baked in:
      sentinel_masks[d] = tiles where a sentinel can face direction d
      gunner_masks[d]   = tiles where a gunner can face direction d
    Gunners with 2+ loader directions get the full-360 exemption."""
    my_team_idx = map_info._my_team_idx
    enemy_idx = 1 - my_team_idx
    my_team = map_info._bm_team[my_team_idx]
    enemy_team = map_info._bm_team[enemy_idx]

    bm_et = map_info._bm_et
    _ensure_attack_shift_plans()

    my_sentinels = bm_et[map_info._IDX_SENTINEL] & my_team
    if my_sentinels:
        taken_harvesters = map_info.expand_manhattan(my_sentinels) & bm_et[map_info._IDX_HARVESTER]
    else:
        taken_harvesters = 0
    candidates = map_info._bm_ti_fed | map_info._bm_ax_fed
    harvesters = (map_info._bm_et[map_info._IDX_HARVESTER] & map_info._bm_env[map_info._IDX_ENV_ORE_TI] & ~taken_harvesters) | map_info._bm_et[map_info._IDX_FOUNDRY]
    if harvesters:
        candidates |= (map_info.expand_manhattan(harvesters))
    candidates &= map_info._bm_seen_observed
    if not candidates:
        return _EMPTY_CANDIDATE_MASKS, _EMPTY_CANDIDATE_MASKS
    empty = ~map_info._bm_any_building | map_info._bm_et[map_info._IDX_MARKER]

    my_clearable = (
        map_info._bm_et[map_info._IDX_BARRIER]
        | map_info._bm_et[map_info._IDX_ROAD]
        | map_info._bm_et[map_info._IDX_CONVEYOR]
        | map_info._bm_et[map_info._IDX_SPLITTER]
        | map_info._bm_et[map_info._IDX_BRIDGE]
    ) & my_team

    enemy_clearable = (
        map_info._bm_et[map_info._IDX_ROAD]
        | map_info._bm_et[map_info._IDX_CONVEYOR]
        | map_info._bm_et[map_info._IDX_SPLITTER]
        | map_info._bm_et[map_info._IDX_BRIDGE]
    ) & enemy_team

    candidates &= (empty | my_clearable | enemy_clearable)
    candidates &= ~map_info._bm_env[map_info._IDX_ENV_WALL]
    if not candidates:
        return _EMPTY_CANDIDATE_MASKS, _EMPTY_CANDIDATE_MASKS

    my_bit = 1 << (map_info._my_pos.x + map_info._my_pos.y * map_info._width)
    all_bots = (map_info._bm_friendly_bots | map_info._bm_enemy_bots) & ~my_bit
    candidates &= ~all_bots
    if not candidates:
        return _EMPTY_CANDIDATE_MASKS, _EMPTY_CANDIDATE_MASKS

    danger_for_clearable = map_info._bm_enemy_launch_adj
    enemy_bots = map_info._bm_enemy_bots
    if enemy_bots:
        # 2-step BFS from enemy bots through their passable graph, treating our
        # launcher 3x3s as impassable (they get thrown back). Layer 1 = tracked
        # zone (am I being tracked?); layer 2 = danger.
        my_launchers = bm_et[map_info._IDX_LAUNCHER] & my_team
        my_launcher_zone = (
            map_info.expand_chebyshev(my_launchers) | my_launchers
        )
        enemy_passable = (
            ~map_info.get_avoid(False, False, False, enemy_pov=True)
            & map_info._board_mask
            & ~my_launcher_zone
        )
        visited = enemy_bots
        frontier = enemy_bots
        next_frontier = (
            map_info.expand_chebyshev(frontier) & enemy_passable & ~visited
        )
        visited |= next_frontier
        tracked_zone = visited
        frontier = next_frontier
        if frontier:
            next_frontier = (
                map_info.expand_chebyshev(frontier) & enemy_passable & ~visited
            )
            visited |= next_frontier
        danger = visited

        danger_for_clearable |= danger
        if tracked_zone & my_bit:
            danger_for_clearable = map_info._board_mask  # am being tracked
    candidates &= ~(danger_for_clearable & enemy_clearable)
    if not candidates:
        return _EMPTY_CANDIDATE_MASKS, _EMPTY_CANDIDATE_MASKS

    candidates &= ~cant_attack
    if not candidates:
        return _EMPTY_CANDIDATE_MASKS, _EMPTY_CANDIDATE_MASKS
    feed_chains = _turret_feed_chains()
    if DRAW_DEBUG and feed_chains:
        for p in map_info.iter_mask(feed_chains):
            rc.draw_indicator_dot(p, 0, 200, 200)
    if feed_chains:
        candidates &= ~feed_chains
        if not candidates:
            return _EMPTY_CANDIDATE_MASKS, _EMPTY_CANDIDATE_MASKS

    # Facing blockers: block direction D at tile P if P+delta_D has a friendly
    # harvester/foundry (always blocks), or a conveyor whose output points back
    # at P (direction == opposite of D). Conveyors pointing away are fine.
    base_block = bm_et[map_info._IDX_HARVESTER] | bm_et[map_info._IDX_FOUNDRY]

    blockers = [0] * 8
    for d in range(0, 8, 2):
        plan = _CARDINAL_BLOCKER_SHIFTS[d]
        if plan is None:
            continue
        sm, soff = plan
        incoming_conv = map_info._bm_conv_by_dir[(d + 4) & 7]
        src = (base_block | incoming_conv) & sm
        if not src:
            continue
        blockers[d] = (src << soff) if soff >= 0 else (src >> (-soff))

    # Sentinels have low dps and shouldn't sit in gunner/breach fire. Gunners
    # have high dps and can trade into hard threats.
    sentinel_cands = candidates & ~map_info._bm_enemy_hard_threat
    sentinel_masks = tuple(sentinel_cands & ~blockers[d] for d in range(8))
    gunner_masks = tuple(candidates & ~blockers[d] for d in range(8))
    return sentinel_masks, gunner_masks


def _get_attack_candidates():
    """Return (preferred, fallback) candidate bitmasks.

    Threshold filter: keep only candidates whose best non-blocked sentinel
    direction score, OR whose gunner summed-across-facings score, is within
    SCORE_THRESHOLD_FACTOR of the per-track best. Sentinel and gunner tracks
    are on different scales (sentinel = single-dir, gunner = sum of 8) so
    thresholds are computed independently per track. Friendly "good"
    conveyors/bridges must clear an extra NON_GOOD_TILE_BUFF margin, which is
    equivalent to buffing every other tile by that amount."""
    can_afford_sent = _round_cache_can_afford_sent
    can_afford_gun = _round_cache_can_afford_gun
    if not can_afford_sent and not can_afford_gun:
        _round_cache_placement_masks[0] = _EMPTY_CANDIDATE_MASKS
        _round_cache_placement_masks[1] = _EMPTY_CANDIDATE_MASKS
        return 0, 0

    enemy_idx = 1 - map_info._my_team_idx
    enemy_scorable = (
        map_info._bm_team[enemy_idx]
        & ~map_info._bm_et[map_info._IDX_ROAD]
        & ~map_info._bm_et[map_info._IDX_MARKER]
    )
    if not enemy_scorable:
        _round_cache_placement_masks[0] = _EMPTY_CANDIDATE_MASKS
        _round_cache_placement_masks[1] = _EMPTY_CANDIDATE_MASKS
        return 0, 0

    sentinel_masks, gunner_masks = _placement_candidates()
    if not can_afford_sent:
        sentinel_masks = _EMPTY_CANDIDATE_MASKS
    if not can_afford_gun:
        gunner_masks = _EMPTY_CANDIDATE_MASKS
    _round_cache_placement_masks[0] = sentinel_masks
    _round_cache_placement_masks[1] = gunner_masks

    gunner_any = 0
    sent_any = 0
    for d in range(8):
        gunner_any |= gunner_masks[d]
        sent_any |= sentinel_masks[d]
    filtered = gunner_any | sent_any
    if not filtered:
        return 0, 0

    sent_max = 0
    if can_afford_sent:
        _ensure_sentinel_planes()
        sent_planes_by_dir = _round_cache_sentinel_planes
        for d in range(8):
            if sentinel_masks[d]:
                s = _max_score_in_mask(sent_planes_by_dir[d], sentinel_masks[d])
                if s > sent_max:
                    sent_max = s
    gun_max = 0
    gun_planes_by_dir = None
    if gunner_any and can_afford_gun:
        _ensure_gunner_scores()
        gun_planes_by_dir = _round_cache_gunner_planes
        for d in range(8):
            if gunner_masks[d]:
                s = _max_score_in_mask(gun_planes_by_dir[d], gunner_masks[d])
                if s > gun_max:
                    gun_max = s

    global _round_cache_threshold
    _round_cache_threshold = 0
    max_score = max(sent_max, gun_max)
    if max_score < MIN_ATTACK_SCORE+THREAT_PENALTY:
        return 0, 0
    # THREAT_PENALTY is baked on non-threat tiles as a flat bonus; a tile whose
    # ONLY contribution is that bonus has 0 real enemy damage. Require
    # threshold > THREAT_PENALTY to exclude those.
    sent_threshold = max(int(sent_max * SCORE_THRESHOLD_FACTOR), MIN_ATTACK_SCORE+THREAT_PENALTY)
    gun_threshold = max(int(gun_max * SCORE_THRESHOLD_FACTOR), MIN_ATTACK_SCORE+THREAT_PENALTY)
    bias = NON_GOOD_TILE_BUFF
    _round_cache_threshold = max(sent_threshold, gun_threshold) + bias
    good_conveyors = _good_conveyor_mask()
    keep = 0
    if sent_max > 0:
        for d in range(8):
            if sentinel_masks[d]:
                sent_good = sentinel_masks[d] & good_conveyors
                sent_other = sentinel_masks[d] & ~good_conveyors
                if sent_other:
                    keep |= _ge_threshold_mask(sent_planes_by_dir[d], sent_threshold, sent_other)
                if sent_good:
                    keep |= _ge_threshold_mask(sent_planes_by_dir[d], sent_threshold + bias, sent_good)
    if gun_max > 0 and gun_planes_by_dir is not None:
        for d in range(8):
            if gunner_masks[d]:
                gun_good = gunner_masks[d] & good_conveyors
                gun_other = gunner_masks[d] & ~good_conveyors
                if gun_other:
                    keep |= _ge_threshold_mask(gun_planes_by_dir[d], gun_threshold, gun_other)
                if gun_good:
                    keep |= _ge_threshold_mask(gun_planes_by_dir[d], gun_threshold + bias, gun_good)
    filtered &= keep
    if not filtered:
        return 0, 0

    my_team_idx = map_info._my_team_idx
    enemy_idx = 1 - my_team_idx
    enemy_clearable = (
        map_info._bm_et[map_info._IDX_ROAD]
        | map_info._bm_et[map_info._IDX_CONVEYOR]
        | map_info._bm_et[map_info._IDX_SPLITTER]
        | map_info._bm_et[map_info._IDX_BRIDGE]
    ) & map_info._bm_team[enemy_idx]
    fallback_mask = enemy_clearable

    fallback = filtered & fallback_mask
    preferred = filtered & ~fallback_mask
    return preferred, fallback


# ---------------------------------------------------------------------------
# Round cache
# ---------------------------------------------------------------------------

_round_cache_round = -1
_round_cache_attack_candidates = (0, 0)
_round_cache_sentinel_planes = None    # list of 8 plane-lists, one per direction
_round_cache_gunner_planes = None      # list of 8 plane-lists, one per direction
_round_cache_threshold = 0
_round_cache_placement_masks = [None, None]  # [sentinel_masks tuple, gunner_masks tuple]
_round_cache_can_afford_sent = False
_round_cache_can_afford_gun = False


def _ensure_round_cache():
    global _round_cache_round, _round_cache_attack_candidates
    global _round_cache_sentinel_planes, _round_cache_gunner_planes
    global _round_cache_can_afford_sent, _round_cache_can_afford_gun
    r = rc.get_current_round()
    if _round_cache_round == r:
        return
    _round_cache_round = r
    _round_cache_sentinel_planes = None
    _round_cache_gunner_planes = None
    ti = rc.get_global_resources()[0]
    reserve = map_info.builder_ti_reserve()
    _round_cache_can_afford_sent = ti >= rc.get_sentinel_cost()[0] + reserve
    _round_cache_can_afford_gun = ti >= rc.get_gunner_cost()[0] + reserve
    _round_cache_attack_candidates = _get_attack_candidates()
    if DRAW_DEBUG:
        preferred, fallback = _round_cache_attack_candidates
        if preferred | fallback:
            _draw_attack_candidates(preferred | fallback)


def _round_cache_enemy_inputs():
    """Inputs shared by sentinel and gunner scoring."""
    enemy_team_bm = map_info._bm_team[1 - map_info._my_team_idx] & ~map_info._bm_my_gunner_claims
    threat = (map_info._bm_enemy_soft_threat | map_info._bm_enemy_hard_threat)
    return enemy_team_bm, threat


def _ensure_sentinel_planes():
    """Lazily build sentinel planes once per round when needed."""
    global _round_cache_sentinel_planes
    if _round_cache_sentinel_planes is not None:
        return
    enemy_team_bm, threat = _round_cache_enemy_inputs()
    sentinel_masks = _round_cache_placement_masks[0]
    _round_cache_sentinel_planes = _get_cached_sentinel_scores(enemy_team_bm, threat, sentinel_masks)


def _ensure_gunner_scores():
    """Lazily build gunner per-direction score planes once per round."""
    global _round_cache_gunner_planes
    if _round_cache_gunner_planes is not None:
        return
    enemy_team_bm, threat = _round_cache_enemy_inputs()
    gunner_masks = _round_cache_placement_masks[1]
    _round_cache_gunner_planes = _get_cached_gunner_per_dir(enemy_team_bm, threat, gunner_masks)


# ---------------------------------------------------------------------------
# Debug drawing
# ---------------------------------------------------------------------------

def _draw_attack_candidates(filtered):
    """Debug: for each filtered attack candidate tile, draw what run() would
    pick. Sentinel wins → white length-1 line in its facing direction. Gunner
    wins → red dot."""
    w = map_info._width
    h = map_info._height
    dir_deltas = map_info._DIRECTION_DELTAS
    m = filtered
    while m:
        lsb = m & -m
        n = lsb.bit_length() - 1
        x, y = n % w, n // w
        direction, turret_type, score = get_best_direction(Position(x, y))
        log(f"Candidate at ({x}, {y}): dir={direction}, type={turret_type}, score={score}")
        dx, dy = dir_deltas[direction]
        ex, ey = x + dx, y + dy
        if turret_type == EntityType.GUNNER:
            r = 255
            g = 0
            b = 0
        else:
            r = 0
            g = 0
            b = 255
        if 0 <= ex < w and 0 <= ey < h:
            rc.draw_indicator_line(Position(x, y), Position(ex, ey), r, g, b)
        m ^= lsb

# ---------------------------------------------------------------------------
# Claims + state hooks
# ---------------------------------------------------------------------------

def _my_claims():
    w = map_info._width
    my_mask = 1 << (map_info._my_pos.x + map_info._my_pos.y * w)
    _ensure_round_cache()
    preferred, fallback = _round_cache_attack_candidates
    if units.builder._stay_near_core:
        near = units.builder.near_core_mask()
        preferred &= near
        fallback &= near
    combined = preferred | fallback
    claimed = pathing.claim_subset(
        my_mask,
        map_info._bm_friendly_bots,
        combined,
        passable=map_info._bm_passable_FFF,
        tie_self=True,
    )
    return claimed & preferred, claimed & fallback


_cached_claims = (0, 0)
MAX_SCORE = 9

def score():
    global _cached_claims
    _cached_claims = _my_claims()
    preferred, fallback = _cached_claims
    if preferred:
        return 9
    if fallback:
        if units.builder._harvest_zone & (1<<(rc.get_position().x + rc.get_position().y*map_info._width)):
            return 6
        else:
            return 8
    return 0


def _try_instant_preferred(preferred: int) -> bool:
    """Fast-path: if from my current tile (or one step onto an existing
    road/conveyor of any team) I can place a turret on a preferred candidate
    in my action radius, do so this turn. Picks the highest-scoring such
    placement (must be non-zero and in `preferred`). Returns True if a turret
    was built."""
    if not preferred or rc.get_action_cooldown() != 0:
        return False
    bm_et = map_info._bm_et
    w = map_info._width
    my_team_idx = map_info._my_team_idx
    my_team = map_info._bm_team[my_team_idx]
    walkable_types = (
        bm_et[map_info._IDX_ROAD]
        | bm_et[map_info._IDX_CONVEYOR]
        | bm_et[map_info._IDX_ARMOURED_CONVEYOR]
        | bm_et[map_info._IDX_SPLITTER]
        | bm_et[map_info._IDX_BRIDGE]
    )
    my_n = map_info._my_pos.x + map_info._my_pos.y * w
    my_bit = 1 << my_n
    adj_walkable = (
        map_info.expand_chebyshev(my_bit)
        & walkable_types
        & map_info._bm_passable_FFF
    )
    walkable_set = my_bit | adj_walkable
    candidates = map_info.expand_chebyshev(walkable_set) & preferred
    if not candidates:
        return False

    best_pos = None
    best_score = 0
    best_dir = None
    best_type = None
    m = candidates
    while m:
        lsb = m & -m
        n = lsb.bit_length() - 1
        m ^= lsb
        pos = Position(n % w, n // w)
        direction, ttype, score = get_best_direction(pos)
        if score > best_score:
            best_score = score
            best_pos = pos
            best_dir = direction
            best_type = ttype
    if best_pos is None or best_score <= 0:
        return False

    best_n = best_pos.x + best_pos.y * w
    adj_to_best = map_info.expand_chebyshev(1 << best_n) & walkable_set
    if not adj_to_best:
        return False
    if not (adj_to_best & my_bit):
        lsb = adj_to_best & -adj_to_best
        target_n = lsb.bit_length() - 1
        target_pos = Position(target_n % w, target_n // w)
        move_dir = map_info._my_pos.direction_to(target_pos)
        if not rc.can_move(move_dir):
            return False
        rc.move(move_dir)
        map_info.update_move()

    best_id = map_info._building_id[best_n]
    if best_id and (my_team & (1 << best_n)):
        if not map_info.has_builder_bot(best_pos) and rc.can_destroy(best_pos):
            rc.destroy(best_pos)
            map_info.update_at(best_pos)

    reserve = map_info.builder_ti_reserve()
    ti_have = rc.get_global_resources()[0]
    if best_type == EntityType.GUNNER:
        if rc.can_build_gunner(best_pos, best_dir) and ti_have >= rc.get_gunner_cost()[0] + reserve:
            log(f"InstantAttack gunner at {best_pos} dir={best_dir} score={best_score}")
            rc.build_gunner(best_pos, best_dir)
            map_info.update_at(best_pos)
            return True
    elif best_type == EntityType.SENTINEL:
        if rc.can_build_sentinel(best_pos, best_dir) and ti_have >= rc.get_sentinel_cost()[0] + reserve:
            log(f"InstantAttack sentinel at {best_pos} dir={best_dir} score={best_score}")
            rc.build_sentinel(best_pos, best_dir)
            map_info.update_at(best_pos)
            return True
    return False


def _try_launcher_lockdown(target: Position) -> bool:
    """If `target` is an enemy conveyor and a visible enemy builder would heal
    it before we finish destroying it, look for an adjacent buildable tile
    where placing a launcher (or barrier) maximally increases the closest
    enemy bot's pathing distance to us. Tiebreak: barrier > launcher (cheaper).
    Skip placement if no candidate strictly increases the distance."""
    if rc.get_action_cooldown() != 0:
        return False
    ti_have, _ = rc.get_global_resources()
    reserve = map_info.builder_ti_reserve()
    can_afford_barrier = ti_have >= rc.get_barrier_cost()[0] + reserve
    can_afford_launcher = ti_have >= rc.get_launcher_cost()[0] + reserve
    if not can_afford_barrier and not can_afford_launcher:
        return False

    w = map_info._width
    target_n = target.x + target.y * w
    target_bit = 1 << target_n
    enemy_team_bm = map_info._bm_team[1 - map_info._my_team_idx]
    if not (target_bit & map_info._bm_conveyors & enemy_team_bm):
        return False

    visible_enemy_bots = map_info._bm_enemy_bots & map_info._bm_visible
    if not visible_enemy_bots:
        return False

    bm_et = map_info._bm_et
    my_team_idx = map_info._my_team_idx
    my_team = map_info._bm_team[my_team_idx]

    # Existing friendly launcher 3x3s are already impassable to the enemy.
    friendly_launchers = bm_et[map_info._IDX_LAUNCHER] & my_team
    friendly_launcher_zone = (
        map_info.expand_chebyshev(friendly_launchers) | friendly_launchers
    )

    # Baseline enemy distance: BFS starts at the target conveyor and finds the
    # closest enemy bot through the enemy's passable mask (side=False).
    _, baseline_dist = nav.closest(
        visible_enemy_bots, pos=target, avoid=friendly_launcher_zone, side=False
    )
    if baseline_dist == -1:
        return False  # already unreachable; nothing to lock down

    hp = map_info._building_hp[target_n]
    if hp <= 0:
        return False
    my_n_for_gate = map_info._my_pos.x + map_info._my_pos.y * w
    on_target = (my_n_for_gate == target_n)
    if on_target and hp // 2 <= baseline_dist - 2:
        return False  # already in firing position and will finish before they arrive

    walls = map_info._bm_env[map_info._IDX_ENV_WALL]
    my_road = bm_et[map_info._IDX_ROAD] & my_team
    my_barrier = bm_et[map_info._IDX_BARRIER] & my_team

    my_pos = map_info._my_pos
    my_bit = 1 << (my_pos.x + my_pos.y * w)

    # Adjacent buildable tiles: empty, our own road, or our own barrier
    # (the latter only used when placing a launcher; we'll destroy first).
    candidates = map_info.expand_chebyshev(my_bit) & ~my_bit
    candidates &= ((~map_info._bm_any_building) | my_road | my_barrier) & ~walls
    candidates &= ~map_info._bm_friendly_bots & ~map_info._bm_enemy_bots
    candidates &= ~map_info._bm_enemy_hard_threat
    if not candidates:
        return False

    UNREACHABLE = 1 << 30

    def _dist_with_extra(extra: int) -> int:
        _, d = nav.closest(
            visible_enemy_bots,
            pos=target,
            avoid=friendly_launcher_zone | extra,
            side=False,
        )
        return UNREACHABLE if d == -1 else d

    def _my_dist_with_extra(extra: int) -> int:
        _, d = nav.closest(target_bit, avoid=extra, side=True)
        return UNREACHABLE if d == -1 else d

    my_baseline_dist = _my_dist_with_extra(0)

    # Score every (candidate, kind) pair. Tuple sort key: (-delta, barrier_priority)
    # — higher delta wins; on equal delta, barrier (priority 1) beats launcher (0).
    log(f"AttackLockdown baseline_dist={baseline_dist} my_baseline={my_baseline_dist} target={target}")
    options = []
    m = candidates
    while m:
        lsb = m & -m
        n = lsb.bit_length() - 1
        m ^= lsb
        p = Position(n % w, n // w)

        # Reject placements that detour our own approach to the target.
        # Both barrier and launcher only block the lsb tile from our pathing.
        my_new_dist = _my_dist_with_extra(lsb)
        if my_new_dist > my_baseline_dist:
            log(f"  skip @ {p}: my_dist {my_baseline_dist} -> {my_new_dist}")
            continue

        if can_afford_barrier and not (lsb & my_barrier):
            barrier_dist = _dist_with_extra(lsb)
            barrier_delta = barrier_dist - baseline_dist
            log(f"  barrier @ {p}: {baseline_dist} -> {barrier_dist} (delta={barrier_delta})")
            if barrier_delta >= 2:
                options.append((barrier_delta, 1, "barrier", p, lsb))
        if can_afford_launcher:
            launcher_zone = map_info.expand_chebyshev(lsb) | lsb
            launcher_dist = _dist_with_extra(launcher_zone)
            launcher_delta = launcher_dist - baseline_dist
            log(f"  launcher @ {p}: {baseline_dist} -> {launcher_dist} (delta={launcher_delta})")
            if launcher_delta >= 5:
                options.append((launcher_delta, 0, "launcher", p, lsb))

    if not options:
        return False
    options.sort(key=lambda o: (-o[0], -o[1]))
    delta, _, kind, best_p, best_lsb = options[0]

    if (best_lsb & (my_road | my_barrier)) and rc.can_destroy(best_p):
        rc.destroy(best_p)
        map_info.update_at(best_p)

    built = False
    reserve = map_info.builder_ti_reserve()
    ti_have = rc.get_global_resources()[0]
    if kind == "barrier" and rc.can_build_barrier(best_p) and ti_have >= rc.get_barrier_cost()[0] + reserve:
        log(f"AttackLockdown barrier at {best_p} delta={delta} for {target}")
        rc.build_barrier(best_p)
        map_info.update_at(best_p)
        built = True
    elif kind == "launcher" and rc.can_build_launcher(best_p) and ti_have >= rc.get_launcher_cost()[0] + reserve:
        log(f"AttackLockdown launcher at {best_p} delta={delta} for {target}")
        rc.build_launcher(best_p)
        map_info.update_at(best_p)
        built = True

    if built:
        # Build uses action cooldown; move cooldown is independent — keep advancing.
        nav.move_to(target)
        return True
    return False


def run():
    global cant_attack
    log("ATTACK")
    preferred, fallback = _cached_claims

    if not preferred and not fallback:
        return

    if preferred and _try_instant_preferred(preferred):
        return

    width = map_info._width
    my_team_idx = map_info._my_team_idx
    excluded_this_turn = 0

    while True:
        eff_preferred = preferred & ~excluded_this_turn
        eff_fallback = fallback & ~excluded_this_turn

        if not eff_preferred and not eff_fallback:
            return

        best = None
        if eff_preferred:
            best, _ = nav.closest(eff_preferred)
        if best is None and eff_fallback:
            best, _ = nav.closest(eff_fallback)
        if best is None:
            cant_attack |= eff_preferred | eff_fallback
            return

        # Lead-metric refinement: among (preferred|fallback) tiles within 2
        # pathing steps of me, pick the one maximizing
        # (enemy's BFS dist to a tile adjacent to it - my BFS dist to it).
        # Their distance uses adjacency because enemies threaten the conveyor
        # by being adjacent (heal range), not by standing on it.
        # Identity: BFS dist from cand to closest enemy = D; the BFS
        # predecessor of cand is a neighbor at D-1 from that enemy and no
        # neighbor/enemy pair is closer, so min(dist(neighbor, enemy)) = D-1
        # exactly (edge case: D=0 means enemy on cand, neighbors at dist 1).
        all_candidates = eff_preferred | eff_fallback
        best_lead = None
        best_lead_tile = None
        remaining = all_candidates
        lead_log = []
        # Treat our friendly launchers' 3x3 zones as impassable for the enemy —
        # any enemy entering one gets yeeted, so for security purposes the
        # zones are effectively walls. Same convention as _try_launcher_lockdown.
        _friendly_launchers_lead = (
            map_info._bm_et[map_info._IDX_LAUNCHER] & map_info._bm_team[my_team_idx]
        )
        _friendly_launcher_zone_lead = (
            map_info.expand_chebyshev(_friendly_launchers_lead) | _friendly_launchers_lead
        )
        while remaining:
            cand, my_d = nav.closest_within(remaining, max_dist=2)
            if cand is None:
                break
            cand_n = cand.x + cand.y * width
            remaining &= ~(1 << cand_n)
            _, d_to_cand = nav.closest(
                map_info._bm_enemy_bots,
                pos=cand,
                avoid=_friendly_launcher_zone_lead,
                side=False,
            )
            if d_to_cand == -1:
                their_d = 1 << 30
                their_d_str = "inf"
            elif d_to_cand == 0:
                their_d = 1
                their_d_str = "1"
            else:
                their_d = d_to_cand - 1
                their_d_str = str(their_d)
            lead = their_d - my_d
            lead_log.append(f"{cand}:my={my_d},their={their_d_str},lead={lead}")
            if best_lead is None or lead > best_lead:
                best_lead = lead
                best_lead_tile = cand
        if lead_log:
            log(f"Attack lead-metric: [{'; '.join(lead_log)}] -> pick={best_lead_tile} lead={best_lead}")
        if best_lead_tile is not None:
            best = best_lead_tile

        best_n = best.x + best.y * width
        best_bit = 1 << best_n
        direction, turret_type, _ = get_best_direction(best)
        is_fallback = not bool(preferred & best_bit)
        best_id = map_info._building_id[best_n]
        is_mine = bool(map_info._bm_team[my_team_idx] & best_bit)

        log(f"Attack: best={best}, dir={direction}, type={turret_type}, fallback={is_fallback}")

        # High-priority lockdown: if target is an enemy conveyor and we can drop a
        # launcher that (combined with non-walkable buildings) covers the whole 3x3
        # around it, do that this turn instead of firing.
        if is_fallback and _try_launcher_lockdown(best):
            return

        _, _enemy_bot_pathing_dist = nav.closest_within(map_info._bm_enemy_bots, max_dist=1)
        enemy_bot_nearby = (_enemy_bot_pathing_dist != -1)
        if is_fallback:
            can_attack_despite_enemy = False

            # if we have >= 2 builder bots (including ourselves) close by (within 5 tiles)
            # for only one opponent bot within 2 tiles we attack
            my_pos = rc.get_position()
            my_id = rc.get_id()
            friendly_builders_nearby_count = 1  # Counting myself
            friendly_builders_nearby_positions = []
            my_team = rc.get_team()
            for unit_id in rc.get_nearby_units():
                if unit_id == my_id:
                    continue
                if rc.get_team(unit_id) == my_team and rc.get_entity_type(unit_id) == EntityType.BUILDER_BOT:
                    unit_pos = rc.get_position(unit_id)
                    if my_pos.distance_squared(unit_pos) <= 25:
                        friendly_builders_nearby_count += 1
                        friendly_builders_nearby_positions.append(unit_pos)
            log(f"AttackFallback friendlies_nearby_positions={friendly_builders_nearby_positions}")

            # Count enemy bots reachable within 2 BFS steps via repeated closest-within calls.
            _remaining_enemies = map_info._bm_enemy_bots
            num_enemy_bots_very_close = 0
            while _remaining_enemies:
                _ep, _ed = nav.closest_within(_remaining_enemies, max_dist=2)
                if _ep is None:
                    break
                num_enemy_bots_very_close += 1
                _remaining_enemies ^= 1 << (_ep.x + _ep.y * width)

            despite_reason = None
            if friendly_builders_nearby_count >= 2 and num_enemy_bots_very_close == 1:
                can_attack_despite_enemy = True
                despite_reason = "outnumbering"

            # if allied sentinel in sight also attack instead of waiting for opponent to leave
            if not can_attack_despite_enemy:
                my_sentinels = map_info._bm_team[my_team_idx] & map_info._bm_et[map_info._IDX_SENTINEL]
                if my_sentinels & map_info._bm_seen_observed:
                    can_attack_despite_enemy = True
                    despite_reason = "ally_sentinel_seen"

            # If every tile in target's 3x3 is a wall, non-walkable building, or
            # inside one of our launchers' 3x3, no enemy can reach to heal — attack.
            if not can_attack_despite_enemy:
                walkable_types_run = (
                    map_info._bm_et[map_info._IDX_ROAD]
                    | map_info._bm_et[map_info._IDX_CONVEYOR]
                    | map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR]
                    | map_info._bm_et[map_info._IDX_SPLITTER]
                    | map_info._bm_et[map_info._IDX_BRIDGE]
                )
                non_walkable_run = map_info._bm_any_building & ~walkable_types_run
                walls_run = map_info._bm_env[map_info._IDX_ENV_WALL]
                my_launchers_run = (
                    map_info._bm_et[map_info._IDX_LAUNCHER]
                    & map_info._bm_team[my_team_idx]
                )
                launcher_zone_run = (
                    map_info.expand_chebyshev(my_launchers_run) | my_launchers_run
                )
                target_zone_run = map_info.expand_chebyshev(best_bit) | best_bit
                sealed = walls_run | non_walkable_run | launcher_zone_run
                if (target_zone_run & ~sealed) == 0:
                    can_attack_despite_enemy = True
                    despite_reason = "target_sealed"

            stuck_no_fire = False
            nav.move_to(best)
            if rc.can_fire(best):
                target_hp = rc.get_hp(best_id)
                if not enemy_bot_nearby:
                    fire_reason = "no_enemy_nearby"
                elif can_attack_despite_enemy:
                    fire_reason = f"despite={despite_reason}"
                elif target_hp <= 2:
                    fire_reason = f"target_hp={target_hp}"
                else:
                    fire_reason = None
                log(
                    f"AttackFallback enemy_bot_dist={_enemy_bot_pathing_dist} "
                    f"friendlies_nearby={friendly_builders_nearby_count} "
                    f"enemies_within_2={num_enemy_bots_very_close} "
                    f"target_hp={target_hp} fire={fire_reason}"
                )
                if fire_reason is not None:
                    rc.fire(best)
                    map_info.update_at(best)
                elif rc.get_position() == best:
                    log(f"AttackFallback retry: stuck on {best}, excluding for this turn")
                    excluded_this_turn |= best_bit
                    stuck_no_fire = True
            if stuck_no_fire:
                continue
            if rc.get_position() == best and map_info._building_id[best_n] != best_id:
                for d in map_info._DIRECTIONS:
                    if d == Direction.CENTRE:
                        continue
                    if rc.can_move(d):
                        rc.move(d)
                        map_info.update_move()
                        break
        else:
            nav.move_adjacent(best)
            if best_id and is_mine:
                if not map_info.has_builder_bot(best) and rc.can_destroy(best) and rc.get_action_cooldown() == 0:
                    log(f"Attack destroy own building at {best}")
                    rc.destroy(best)
                    map_info.update_at(best)
        reserve = map_info.builder_ti_reserve()
        ti_have = rc.get_global_resources()[0]
        if turret_type == EntityType.GUNNER:
            log("gunner cost", rc.get_gunner_cost(), rc.get_global_resources())
            if rc.can_build_gunner(best, direction) and ti_have >= rc.get_gunner_cost()[0] + reserve:
                rc.build_gunner(best, direction)
                map_info.update_at(best)
        else:
            log("sentinel cost", rc.get_sentinel_cost(), rc.get_global_resources())
            if rc.can_build_sentinel(best, direction) and ti_have >= rc.get_sentinel_cost()[0] + reserve:
                rc.build_sentinel(best, direction)
                map_info.update_at(best)
        break
