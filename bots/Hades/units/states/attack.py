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
_GUNNER_SUM_CACHE_KEY = None
_GUNNER_SUM_CACHE = None
_GUNNER_PER_DIR_CACHE_KEY = None
_GUNNER_PER_DIR_CACHE = None



def init(c: Controller):
    global rc, nav
    rc = c
    nav = units.builder.nav
    _ensure_attack_shift_plans()


SENTINEL_BUILDING_SCORE = [0] * map_info._NUM_ET
SENTINEL_BUILDING_SCORE[map_info._IDX_CORE] = 0
SENTINEL_BUILDING_SCORE[map_info._IDX_HARVESTER] = 0
SENTINEL_BUILDING_SCORE[map_info._IDX_FOUNDRY] = 16
SENTINEL_BUILDING_SCORE[map_info._IDX_GUNNER] = 20
SENTINEL_BUILDING_SCORE[map_info._IDX_SENTINEL] = 20
SENTINEL_BUILDING_SCORE[map_info._IDX_BREACH] = 24
SENTINEL_BUILDING_SCORE[map_info._IDX_LAUNCHER] = 8
SENTINEL_BUILDING_SCORE[map_info._IDX_CONVEYOR] = 8
SENTINEL_BUILDING_SCORE[map_info._IDX_ARMOURED_CONVEYOR] = 12
SENTINEL_BUILDING_SCORE[map_info._IDX_BARRIER] = 8
SENTINEL_BUILDING_SCORE[map_info._IDX_BRIDGE] = 8
SENTINEL_BUILDING_SCORE[map_info._IDX_SPLITTER] = 8

# Gunners snipe single high-value lanes: big bonus for core + backline turrets,
# smaller gain on clustered infra (sentinels already out-damage them there).
GUNNER_BUILDING_SCORE = [0] * map_info._NUM_ET
GUNNER_BUILDING_SCORE[map_info._IDX_CORE] = 256
GUNNER_BUILDING_SCORE[map_info._IDX_HARVESTER] = 0
GUNNER_BUILDING_SCORE[map_info._IDX_FOUNDRY] = 56
GUNNER_BUILDING_SCORE[map_info._IDX_GUNNER] = 100
GUNNER_BUILDING_SCORE[map_info._IDX_SENTINEL] = 100
GUNNER_BUILDING_SCORE[map_info._IDX_BREACH] = 120
GUNNER_BUILDING_SCORE[map_info._IDX_LAUNCHER] = 16
GUNNER_BUILDING_SCORE[map_info._IDX_CONVEYOR] = 4
GUNNER_BUILDING_SCORE[map_info._IDX_ARMOURED_CONVEYOR] = 8
GUNNER_BUILDING_SCORE[map_info._IDX_BARRIER] = 16
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

_NUM_PLANES = 12  # up to 8191; gunner CORE(480) + turrets keeps per-dir sum well under this

SCORE_THRESHOLD_FACTOR = 0
MIN_ATTACK_SCORE = 16
THREAT_PENALTY = 4
WANTED_ATTACK_THRESHOLD = 48

_cant_attack_map: dict[int, int] = {}  # tile index -> round recorded
CANT_ATTACK_TTL = 100

def _friendly_distance_score(n: int) -> int:
    """Negative squared distance from tile n to the nearest friendly builder
    bot. Since `closest_impl` prefers lower scores, negating biases the
    tiebreak toward tiles that are FARTHEST from any friendly bot."""
    fb = map_info._bm_friendly_bots
    if not fb:
        return 0
    w = map_info._width
    px = n % w
    py = n // w
    best = None
    m = fb
    while m:
        lsb = m & -m
        bn = lsb.bit_length() - 1
        m ^= lsb
        bx = bn % w
        by = bn // w
        dx = bx - px
        dy = by - py
        d2 = dx * dx + dy * dy
        if best is None or d2 < best:
            best = d2
    return -best


def cant_attack():
    """Bitmask of tiles we recently failed to attack; entries expire after CANT_ATTACK_TTL rounds."""
    current = rc.get_current_round()
    result = 0
    stale = []
    for n, turn in _cant_attack_map.items():
        if turn + CANT_ATTACK_TTL < current:
            stale.append(n)
            continue
        result |= 1 << n
    for n in stale:
        del _cant_attack_map[n]
    return result


def _mark_cant_attack(mask):
    if not mask:
        return
    current = rc.get_current_round()
    m = mask
    while m:
        lsb = m & -m
        n = lsb.bit_length() - 1
        _cant_attack_map[n] = current
        m ^= lsb


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

_SENTINEL_SCORE_GROUPS = _build_score_groups(SENTINEL_BUILDING_SCORE)
_GUNNER_SCORE_GROUPS = _build_score_groups(GUNNER_BUILDING_SCORE)

_THREAT_PENALTY_BITS = _bits_of(THREAT_PENALTY)
_SENT_CORE_BITS = _bits_of(SENTINEL_BUILDING_SCORE[map_info._IDX_CORE])
_GUN_CORE_BITS = _bits_of(GUNNER_BUILDING_SCORE[map_info._IDX_CORE])


def _ensure_attack_shift_plans():
    """Precompute static shift plans used by the hot attack scorers."""
    global _SHIFT_PLAN_WIDTH, _SHIFT_PLAN_HEIGHT
    global _SENTINEL_REACH_SHIFTS, _GUNNER_STEP_SHIFTS, _CARDINAL_BLOCKER_SHIFTS

    w = map_info._width
    h = map_info._height
    if _SHIFT_PLAN_WIDTH == w and _SHIFT_PLAN_HEIGHT == h:
        return

    shift_masks = map_info._turret_shift_masks

    sentinel_plans = []
    for d in range(8):
        steps = []
        for dx, dy in map_info._SENTINEL_OFFSETS[d]:
            sdx = -dx
            sdy = -dy
            sm = shift_masks.get((sdx, sdy))
            if sm is None:
                continue
            steps.append((sm, sdx + sdy * w))
        sentinel_plans.append(tuple(steps))

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


def _get_cached_gunner_sum(enemy_team_bm: int, threat: int, gunner_masks: tuple[int, ...]):
    """Gunner summed score plane, cached across rounds by exact masks."""
    global _GUNNER_SUM_CACHE_KEY, _GUNNER_SUM_CACHE

    key = (map_info._struct_version, gunner_masks)
    if key != _GUNNER_SUM_CACHE_KEY:
        _, _GUNNER_SUM_CACHE = _compute_gunner_dir_scores(
            enemy_team_bm, threat, gunner_masks, include_per_dir=False
        )
        _GUNNER_SUM_CACHE_KEY = key
    return _GUNNER_SUM_CACHE


def _get_cached_gunner_per_dir(enemy_team_bm: int, threat: int, gunner_masks: tuple[int, ...]):
    """Gunner per-direction planes, cached across rounds by exact masks."""
    global _GUNNER_PER_DIR_CACHE_KEY, _GUNNER_PER_DIR_CACHE
    global _GUNNER_SUM_CACHE_KEY, _GUNNER_SUM_CACHE

    key = (map_info._struct_version, gunner_masks)
    if key != _GUNNER_PER_DIR_CACHE_KEY:
        per_dir, summed = _compute_gunner_dir_scores(
            enemy_team_bm, threat, gunner_masks, include_per_dir=True
        )
        _GUNNER_PER_DIR_CACHE = per_dir
        _GUNNER_PER_DIR_CACHE_KEY = key
        # Keep sum cache coherent for the same key.
        _GUNNER_SUM_CACHE = summed
        _GUNNER_SUM_CACHE_KEY = key
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

    core_mask = bm_et[map_info._IDX_CORE] & enemy_team_bm
    type_contribs, _ = _enemy_score_group_masks(enemy_team_bm)
    sent_core_bits = _SENT_CORE_BITS

    non_threat = map_info._board_mask & ~threat
    non_zero = 0
    all_planes = []
    for d in range(8):
        mask_d = sentinel_masks[d]
        if not mask_d:
            all_planes.append([0] * _NUM_PLANES)
            continue
        planes = [0] * _NUM_PLANES
        core_reach = 0
        for sm, rev_off in _SENTINEL_REACH_SHIFTS[d]:
            if core_mask:
                masked = core_mask & sm
                if masked:
                    if rev_off >= 0:
                        core_reach |= masked << rev_off
                    else:
                        core_reach |= masked >> (-rev_off)
            for _s, bits, bm_t in type_contribs:
                masked = bm_t & sm
                if not masked:
                    continue
                if rev_off >= 0:
                    contrib = masked << rev_off
                else:
                    contrib = masked >> (-rev_off)
                non_zero |= contrib
                restricted = contrib & mask_d
                if restricted:
                    _add_bits_to_planes(planes, bits, restricted)
        non_zero |= core_reach
        if core_reach and sent_core_bits:
            core_restricted = core_reach & mask_d
            if core_restricted:
                _add_bits_to_planes(planes, sent_core_bits, core_restricted)
        all_planes.append(planes)

    if _THREAT_PENALTY_BITS:
        for d in range(8):
            baked = non_threat & non_zero & sentinel_masks[d]
            if baked:
                _add_bits_to_planes(all_planes[d], _THREAT_PENALTY_BITS, baked)
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


def _compute_gunner_dir_scores(enemy_team_bm, threat, gunner_masks, include_per_dir=True):
    """Compute per-tile gunner score planes. Returns (per_dir, summed):
      per_dir: list of 8 plane-lists, one per facing direction. Reading position
        n from the d-th inner list gives the gunner's score if placed at n
        facing d — 0 if n is not a valid placement for d (per gunner_masks[d]).
      summed: single plane-list holding the tile-wise sum of the raw
        (pre-penalty) per-direction scores, representing total lane value if a
        gunner is placed at that tile. 0 on tiles not placeable as a gunner.

    Gunner rays are blocked by walls AND by allied non-road, non-marker
    buildings. Scores come from GUNNER_BUILDING_SCORE. THREAT_PENALTY is baked
    exactly ONCE at the end — once on the summed plane and once on each
    per-direction plane — using the final non_zero union. This gives every
    reached, placeable, non-threat tile a single-PEN gap on each plane
    regardless of direction iteration order."""
    bm_et = map_info._bm_et
    _ensure_attack_shift_plans()
    not_blocked = map_info._board_mask & ~_gunner_ray_blocked_mask()

    core_mask = bm_et[map_info._IDX_CORE] & enemy_team_bm
    gun_core_bits = _GUN_CORE_BITS
    _, type_initial = _enemy_score_group_masks(enemy_team_bm)

    non_threat = map_info._board_mask & ~threat
    non_zero = 0
    summed = [0] * _NUM_PLANES
    all_planes = []
    any_placeable = 0
    # Pre-extract type fields once: bits is constant across iterations, the
    # bitmask values are mutated in-place per direction. This avoids
    # rebuilding new_type_cur as a fresh list of tuples every BFS step.
    n_types = len(type_initial)
    type_bits_arr = [t[1] for t in type_initial]
    type_bm_initial = [t[2] for t in type_initial]
    type_bms = [0] * n_types
    for d in range(8):
        planes = [0] * _NUM_PLANES
        mask_d = gunner_masks[d]
        any_placeable |= mask_d
        sm, soff, max_step = _GUNNER_STEP_SHIFTS[d]
        if not sm or max_step == 0 or not mask_d:
            if include_per_dir:
                all_planes.append(planes)
            continue
        combined_sm = sm & not_blocked
        core_cur = core_mask
        # Reset per-direction bm scratch in place — single buffer reused.
        for j in range(n_types):
            type_bms[j] = type_bm_initial[j]
        core_reach = 0
        if soff >= 0:
            for _ in range(max_step):
                if core_cur:
                    core_cur = (core_cur & combined_sm) << soff
                    if core_cur:
                        core_reach |= core_cur
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
                            _add_bits_to_planes(planes, type_bits_arr[j], restricted)
                if not core_cur and not any_alive:
                    break
        else:
            nsoff = -soff
            for _ in range(max_step):
                if core_cur:
                    core_cur = (core_cur & combined_sm) >> nsoff
                    if core_cur:
                        core_reach |= core_cur
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
                            _add_bits_to_planes(planes, type_bits_arr[j], restricted)
                if not core_cur and not any_alive:
                    break
        non_zero |= core_reach
        if core_reach and gun_core_bits:
            core_restricted = core_reach & mask_d
            if core_restricted:
                _add_bits_to_planes(planes, gun_core_bits, core_restricted)
        _add_planes_into(summed, planes)
        if include_per_dir:
            all_planes.append(planes)

    if _THREAT_PENALTY_BITS:
        baked_sum = non_threat & non_zero & any_placeable
        if baked_sum:
            _add_bits_to_planes(summed, _THREAT_PENALTY_BITS, baked_sum)
        if include_per_dir:
            for d in range(8):
                baked = non_threat & non_zero & gunner_masks[d]
                if baked:
                    _add_bits_to_planes(all_planes[d], _THREAT_PENALTY_BITS, baked)
    return (all_planes if include_per_dir else None), summed


def _best_gunner_direction_at(n: int, bit: int, enemy_team_bm: int, threat: int, gunner_masks):
    """Best gunner facing at a single tile, without building full-map per-dir planes."""
    width = map_info._width
    height = map_info._height
    px = n % width
    py = n // width
    blocked = _gunner_ray_blocked_mask()
    building_et_idx = map_info._building_et_idx
    non_threat_tile = not (threat & bit)

    best_dir = Direction.NORTH
    best_score = -1
    for d in range(8):
        if not (gunner_masks[d] & bit):
            continue
        score = 0
        hit_any = False
        for dx, dy in map_info._GUNNER_RAYS[d]:
            tx = px + dx
            ty = py + dy
            if not (0 <= tx < width and 0 <= ty < height):
                break
            tn = tx + ty * width
            tbit = 1 << tn
            if blocked & tbit:
                break
            if enemy_team_bm & tbit:
                tile_score = GUNNER_BUILDING_SCORE[building_et_idx[tn]]
                score += tile_score
                if tile_score:
                    hit_any = True
        if hit_any and non_threat_tile:
            score += THREAT_PENALTY
        if score > best_score:
            best_score = score
            best_dir = map_info._DIRECTIONS[d]
    return best_dir, best_score


# ---------------------------------------------------------------------------
# Per-tile "best direction / best type" pick
# ---------------------------------------------------------------------------

def get_best_direction(pos):
    """Pick (Direction, turret_type, score) for a turret at pos.

    Decide sentinel vs gunner using the gunner SUMMED-across-facings score as
    the decision basis (total lane value if a gunner sits here). Only descend
    into gunner per-direction scores if gunner wins.

    Breach is ignored for now — never returned."""
    w = map_info._width
    px, py = pos.x, pos.y
    n = px + py * w
    bit = 1 << n

    _ensure_sentinel_planes()
    _ensure_gunner_scores(include_per_dir=False)
    sent_planes_by_dir = _round_cache_sentinel_planes
    gun_sum_plane = _round_cache_gunner_sum
    sentinel_masks = _round_cache_placement_masks[0]
    gunner_masks = _round_cache_placement_masks[1]
    gunner_any = _round_cache_gunner_any

    directions = map_info._DIRECTIONS

    # log("AT POSITION", pos)

    # Sentinel: best valid-placement direction at pos.
    best_s_dir, best_s_score = Direction.NORTH, -1
    for d in range(8):
        if not (sentinel_masks[d] & bit):
            # log("  SENT", directions[d], "not a valid placement")
            continue
        s = _read_score(sent_planes_by_dir[d], n)
        # log("  SENT", directions[d], "score", s)
        if s > best_s_score:
            best_s_score = s
            best_s_dir = directions[d]

    # Gunner: sum plane is the decision basis.
    gun_sum = _read_score(gun_sum_plane, n) if gun_sum_plane is not None else 0
    gunner_placeable = bool(gunner_any & bit)
    # log("  GUN sum", gun_sum, "placeable" if gunner_placeable else "not placeable")

    if not gunner_placeable or best_s_score >= gun_sum:
        return best_s_dir, EntityType.SENTINEL, best_s_score

    # Gunner wins: now pick its best facing locally for this tile only.
    enemy_team_bm, threat = _round_cache_enemy_inputs()
    best_g_dir, _best_g_score = _best_gunner_direction_at(n, bit, enemy_team_bm, threat, gunner_masks)
    return best_g_dir, EntityType.GUNNER, gun_sum


# ---------------------------------------------------------------------------
# Candidate generation
# ---------------------------------------------------------------------------

def _turret_feed_chains(max_steps: int = 8) -> int:
    """Bitmask of my conveyor-like tiles that feed into my gunners/sentinels,
    walking upstream up to max_steps hops. First hop: cardinal conveyors
    pointing into each turret (turrets don't have a _conv_reverse entry).
    Subsequent hops: upstream via _conv_reverse on the conveyor tiles.
    Stops when the next hop yields no tile that's a conveyor-like type."""
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
    w = map_info._width
    conv_types = (
        map_info._bm_et[map_info._IDX_CONVEYOR]
        | map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR]
        | map_info._bm_et[map_info._IDX_BRIDGE]
        | map_info._bm_et[map_info._IDX_SPLITTER]
    )
    nlc = map_info._not_left_col
    nrc = map_info._not_right_col
    board = map_info._board_mask
    conv_by_dir = map_info._bm_conv_by_dir

    # First hop: for each cardinal direction d, a conveyor facing d sits
    # opposite-of-d from the turret. Shift turrets to the source tile and
    # intersect with conv_by_dir[d].
    # d=0 NORTH (delta 0,-1): source is south of turret  -> turrets << w
    # d=2 EAST  (delta 1, 0): source is west of turret   -> (turrets & nlc) >> 1
    # d=4 SOUTH (delta 0, 1): source is north of turret  -> turrets >> w
    # d=6 WEST  (delta -1,0): source is east of turret   -> (turrets & nrc) << 1
    frontier = (
        ((turrets << w) & board & conv_by_dir[0])
        | (((turrets & nlc) >> 1) & conv_by_dir[2])
        | ((turrets >> w) & conv_by_dir[4])
        | (((turrets & nrc) << 1) & board & conv_by_dir[6])
    )
    frontier &= conv_types
    if not frontier:
        _TURRET_FEED_CACHE_VERSION = sv
        _TURRET_FEED_CACHE_MASK = 0
        return 0

    reverse = map_info._conv_reverse
    result = frontier
    for _ in range(max_steps - 1):
        next_frontier = 0
        m = frontier
        while m:
            lsb = m & -m
            n = lsb.bit_length() - 1
            next_frontier |= reverse[n]
            m ^= lsb
        next_frontier &= conv_types & ~result
        if not next_frontier:
            break
        result |= next_frontier
        frontier = next_frontier
    _TURRET_FEED_CACHE_VERSION = sv
    _TURRET_FEED_CACHE_MASK = result
    return result


def _placement_candidates(require_feed: bool = True):
    """Returns (sentinel_masks, gunner_masks): two tuples of 8 bitmasks, one
    per facing direction. Loader blockers are baked in:
      sentinel_masks[d] = tiles where a sentinel can face direction d
      gunner_masks[d]   = tiles where a gunner can face direction d
    Gunners with 2+ loader directions get the full-360 exemption.

    When require_feed is False, the harvester/foundry adjacency restriction is
    omitted — exposes the full set of placement-legal tiles regardless of
    whether they're currently fed. Used by wanted_attack_tiles()."""
    my_team_idx = map_info._my_team_idx
    enemy_idx = 1 - my_team_idx
    my_team = map_info._bm_team[my_team_idx]
    enemy_team = map_info._bm_team[enemy_idx]

    bm_et = map_info._bm_et
    _ensure_attack_shift_plans()

    if require_feed:
        candidates = (map_info._bm_ti_fed | map_info._bm_ax_fed) & map_info._bm_seen_observed
        my_sentinels = bm_et[map_info._IDX_SENTINEL] & my_team
        if my_sentinels:
            sources = bm_et[map_info._IDX_HARVESTER] | bm_et[map_info._IDX_FOUNDRY]
            taken = map_info.expand_manhattan(my_sentinels) & sources
            if taken:
                candidates &= ~map_info.expand_manhattan(taken)
    else:
        candidates = map_info._bm_seen_observed & map_info._board_mask
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
        tracked_zone = map_info.expand_chebyshev(enemy_bots)
        danger = map_info.expand_chebyshev(tracked_zone)
        danger_for_clearable |= danger
        if danger & my_bit:
            danger_for_clearable = map_info._board_mask #am being tracked
    candidates &= ~(danger_for_clearable & enemy_clearable)
    if not candidates:
        return _EMPTY_CANDIDATE_MASKS, _EMPTY_CANDIDATE_MASKS

    candidates &= ~cant_attack()
    if not candidates:
        return _EMPTY_CANDIDATE_MASKS, _EMPTY_CANDIDATE_MASKS
    feed_chains = _turret_feed_chains()
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


_wanted_attack_round = -1
_wanted_attack_mask = 0


def wanted_attack_tiles():
    """Bitmask of tiles where a turret would score >= MIN_ATTACK_SCORE +
    THREAT_PENALTY, computed against the broad placement set (no harvester/feed
    adjacency restriction). Cached per round.

    Used by secure/harvest to recognize ore tiles whose harvesting would unlock
    a wanted attack position, and skip the routing-cost portion of affordability
    checks for those tiles."""
    global _wanted_attack_round, _wanted_attack_mask
    r = rc.get_current_round()
    if _wanted_attack_round == r:
        return _wanted_attack_mask
    _wanted_attack_round = r

    sentinel_masks, gunner_masks = _placement_candidates(require_feed=False)
    sent_any = 0
    gun_any = 0
    for d in range(8):
        sent_any |= sentinel_masks[d]
        gun_any |= gunner_masks[d]
    if not (sent_any | gun_any):
        _wanted_attack_mask = 0
        return 0

    enemy_team_bm, threat = _round_cache_enemy_inputs()
    threshold = WANTED_ATTACK_THRESHOLD
    result = 0
    if sent_any:
        sent_planes = _get_cached_sentinel_scores(enemy_team_bm, threat, sentinel_masks)
        for d in range(8):
            if sentinel_masks[d]:
                result |= _ge_threshold_mask(sent_planes[d], threshold, sentinel_masks[d])
    if gun_any:
        gun_sum = _get_cached_gunner_sum(enemy_team_bm, threat, gunner_masks)
        result |= _ge_threshold_mask(gun_sum, threshold, gun_any)

    _wanted_attack_mask = result
    return result


def _get_attack_candidates():
    """Return (preferred, fallback) candidate bitmasks.

    Threshold filter: keep only candidates whose best non-blocked sentinel
    direction score, OR whose gunner summed-across-facings score, is within
    SCORE_THRESHOLD_FACTOR of the per-track best. Sentinel and gunner tracks
    are on different scales (sentinel = single-dir, gunner = sum of 8) so
    thresholds are computed independently per track."""
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
    units.builder.draw_mask(sentinel_masks[0], 255, 0, 0)
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
    global _round_cache_sentinel_any, _round_cache_gunner_any
    _round_cache_sentinel_any = sent_any
    _round_cache_gunner_any = gunner_any
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
    gun_sum_plane = None
    if gunner_any and can_afford_gun:
        _ensure_gunner_scores(include_per_dir=False)
        gun_sum_plane = _round_cache_gunner_sum
        gun_max = _max_score_in_mask(gun_sum_plane, gunner_any)

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
    _round_cache_threshold = max(sent_threshold, gun_threshold)
    keep = 0
    if sent_max > 0:
        for d in range(8):
            if sentinel_masks[d]:
                keep |= _ge_threshold_mask(sent_planes_by_dir[d], sent_threshold, sentinel_masks[d])
    if gun_max > 0 and gun_sum_plane is not None:
        keep |= _ge_threshold_mask(gun_sum_plane, gun_threshold, gunner_any)
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
_round_cache_gunner_sum = None         # single plane-list: sum across 8 facings
_round_cache_threshold = 0
_round_cache_placement_masks = [None, None]  # [sentinel_masks tuple, gunner_masks tuple]
_round_cache_can_afford_sent = False
_round_cache_can_afford_gun = False
_round_cache_sentinel_any = 0
_round_cache_gunner_any = 0


def _ensure_round_cache():
    global _round_cache_round, _round_cache_attack_candidates
    global _round_cache_sentinel_planes, _round_cache_gunner_planes, _round_cache_gunner_sum
    global _round_cache_can_afford_sent, _round_cache_can_afford_gun
    global _round_cache_sentinel_any, _round_cache_gunner_any
    r = rc.get_current_round()
    if _round_cache_round == r:
        return
    _round_cache_round = r
    _round_cache_sentinel_planes = None
    _round_cache_gunner_planes = None
    _round_cache_gunner_sum = None
    _round_cache_sentinel_any = 0
    _round_cache_gunner_any = 0
    ti = rc.get_global_resources()[0]
    _round_cache_can_afford_sent = ti >= rc.get_sentinel_cost()[0]
    _round_cache_can_afford_gun = ti >= rc.get_gunner_cost()[0]
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


def _ensure_gunner_scores(include_per_dir=False):
    """Lazily build gunner score planes once per round."""
    global _round_cache_gunner_planes, _round_cache_gunner_sum
    if _round_cache_gunner_sum is not None and (
        _round_cache_gunner_planes is not None or not include_per_dir
    ):
        return
    enemy_team_bm, threat = _round_cache_enemy_inputs()
    gunner_masks = _round_cache_placement_masks[1]
    _round_cache_gunner_sum = _get_cached_gunner_sum(enemy_team_bm, threat, gunner_masks)
    if include_per_dir:
        _round_cache_gunner_planes = _get_cached_gunner_per_dir(enemy_team_bm, threat, gunner_masks)


def _ensure_score_planes():
    """Compatibility wrapper for callers that need the full score cache."""
    _ensure_sentinel_planes()
    _ensure_gunner_scores(include_per_dir=True)


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
        # log(f"Candidate at ({x}, {y}): dir={direction}, type={turret_type}, score={score}")
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
    """Override: if a preferred tile is adjacent to a tile I can reach this
    turn without placing a road (existing walkable infra or my current tile),
    pick the highest-scoring such preferred and act on it directly."""
    if not preferred:
        return False

    width = map_info._width
    my_pos = map_info._my_pos
    my_n = my_pos.x + my_pos.y * width
    my_bit = 1 << my_n

    walkable_infra = (
        map_info._bm_my_core_area
        | map_info._bm_et[map_info._IDX_ROAD]
        | map_info._bm_et[map_info._IDX_CONVEYOR]
        | map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR]
        | map_info._bm_et[map_info._IDX_BRIDGE]
        | map_info._bm_et[map_info._IDX_SPLITTER]
    )
    other_bots = (map_info._bm_friendly_bots | map_info._bm_enemy_bots) & ~my_bit
    walkable_infra &= ~other_bots

    reach = map_info.expand_chebyshev(my_bit)
    walkable_step1 = (walkable_infra & reach) | my_bit

    near_walkable = map_info.expand_chebyshev(walkable_step1)
    candidates = preferred & near_walkable
    if not candidates:
        return False

    best_score = -1
    best_pos = None
    best_n = -1
    best_dir = None
    best_type = None
    m = candidates
    while m:
        lsb = m & -m
        n = lsb.bit_length() - 1
        m ^= lsb
        pos = Position(n % width, n // width)
        d, t, score = get_best_direction(pos)
        if score > best_score:
            best_score = score
            best_pos = pos
            best_n = n
            best_dir = d
            best_type = t

    if best_pos is None:
        return False

    best_bit = 1 << best_n
    neighbors_of_best = map_info.expand_chebyshev(best_bit) & ~best_bit
    valid_stands = walkable_step1 & neighbors_of_best
    if not valid_stands:
        return False

    if valid_stands & my_bit:
        target_bit = my_bit
    else:
        target_bit = valid_stands & -valid_stands

    log(f"Attack-instant: best={best_pos}, dir={best_dir}, type={best_type}, score={best_score}")

    if target_bit != my_bit:
        target_n = target_bit.bit_length() - 1
        target_pos = Position(target_n % width, target_n // width)
        nav.move_to(target_pos)

    my_team_idx = map_info._my_team_idx
    best_id = map_info._building_id[best_n]
    is_mine = bool(map_info._bm_team[my_team_idx] & best_bit)
    if best_id and is_mine:
        if (
            not map_info.has_builder_bot(best_pos)
            and rc.can_destroy(best_pos)
            and rc.get_action_cooldown() == 0
            and rc.get_unit_count() < GameConstants.MAX_TEAM_UNITS
        ):
            log(f"Attack-instant destroy own building at {best_pos}")
            rc.destroy(best_pos)
            map_info.update_at(best_pos)

    if best_type == EntityType.GUNNER:
        if rc.can_build_gunner(best_pos, best_dir):
            rc.build_gunner(best_pos, best_dir)
            map_info.update_at(best_pos)
    else:
        if rc.can_build_sentinel(best_pos, best_dir):
            rc.build_sentinel(best_pos, best_dir)
            map_info.update_at(best_pos)
    return True


def run():
    log("ATTACK")
    preferred, fallback = _cached_claims

    if not preferred and not fallback:
        return

    if _try_instant_preferred(preferred):
        return

    width = map_info._width
    my_team_idx = map_info._my_team_idx
    best = None
    units.builder.draw_mask(fallback, 255, 0, 0)
    score_fn = _friendly_distance_score if map_info._bm_friendly_bots else None
    if preferred:
        best, _ = nav.closest(preferred, tiebreak_score=score_fn)
    if best is None and fallback:
        best, _ = nav.closest(fallback, tiebreak_score=score_fn)
    if best is None:
        _mark_cant_attack(preferred | fallback)
        return

    best_n = best.x + best.y * width
    best_bit = 1 << best_n
    direction, turret_type, _ = get_best_direction(best)
    is_fallback = not bool(preferred & best_bit)
    best_id = map_info._building_id[best_n]
    is_mine = bool(map_info._bm_team[my_team_idx] & best_bit)

    log(f"Attack: best={best}, dir={direction}, type={turret_type}, fallback={is_fallback}")

    zone = 1 << (map_info._my_pos.x + map_info._my_pos.y * width)
    zone = map_info.expand_chebyshev(zone)
    enemy_bot_nearby = bool(map_info._bm_enemy_bots & zone)
    if is_fallback:
        nav.move_to(best)
        if rc.can_fire(best):
            if not enemy_bot_nearby or rc.get_hp(best_id) <= 2:
                rc.fire(best)
                map_info.update_at(best)
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
            if (
                not map_info.has_builder_bot(best)
                and rc.can_destroy(best)
                and rc.get_action_cooldown() == 0
                and rc.get_unit_count() < GameConstants.MAX_TEAM_UNITS
            ):
                log(f"Attack destroy own building at {best}")
                rc.destroy(best)
                map_info.update_at(best)
    if turret_type == EntityType.GUNNER:
        log("gunner cost", rc.get_gunner_cost(), rc.get_global_resources())
        if rc.can_build_gunner(best, direction):
            rc.build_gunner(best, direction)
            map_info.update_at(best)
    else:
        log("sentinel cost", rc.get_sentinel_cost(), rc.get_global_resources())
        if rc.can_build_sentinel(best, direction):
            rc.build_sentinel(best, direction)
            map_info.update_at(best)
