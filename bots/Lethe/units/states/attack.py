from cambc import *

import map_info
import pathing
from pathing import Pathing
import comms
import units.builder



rc: Controller = None
nav: Pathing = None

comm_flag = 6

MAX_SCORE = 8


def init(c: Controller):
    global rc, nav
    rc = c
    nav = units.builder.nav


SENTINEL_BUILDING_SCORE = [0] * map_info._NUM_ET
SENTINEL_BUILDING_SCORE[map_info._IDX_CORE] = 0
SENTINEL_BUILDING_SCORE[map_info._IDX_HARVESTER] = 12
SENTINEL_BUILDING_SCORE[map_info._IDX_FOUNDRY] = 16
SENTINEL_BUILDING_SCORE[map_info._IDX_GUNNER] = 20
SENTINEL_BUILDING_SCORE[map_info._IDX_SENTINEL] = 20
SENTINEL_BUILDING_SCORE[map_info._IDX_BREACH] = 24
SENTINEL_BUILDING_SCORE[map_info._IDX_LAUNCHER] = 8
SENTINEL_BUILDING_SCORE[map_info._IDX_CONVEYOR] = 2
SENTINEL_BUILDING_SCORE[map_info._IDX_ARMOURED_CONVEYOR] = 4
SENTINEL_BUILDING_SCORE[map_info._IDX_BARRIER] = 4
SENTINEL_BUILDING_SCORE[map_info._IDX_BRIDGE] = 2
SENTINEL_BUILDING_SCORE[map_info._IDX_SPLITTER] = 2

# Gunners snipe single high-value lanes: big bonus for core + backline turrets,
# smaller gain on clustered infra (sentinels already out-damage them there).
GUNNER_BUILDING_SCORE = [0] * map_info._NUM_ET
GUNNER_BUILDING_SCORE[map_info._IDX_CORE] = 128
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

def _build_scored_non_core(score_table):
    return [
        (map_info._IDX_FOUNDRY, score_table[map_info._IDX_FOUNDRY]),
        (map_info._IDX_GUNNER, score_table[map_info._IDX_GUNNER]),
        (map_info._IDX_SENTINEL, score_table[map_info._IDX_SENTINEL]),
        (map_info._IDX_BREACH, score_table[map_info._IDX_BREACH]),
        (map_info._IDX_LAUNCHER, score_table[map_info._IDX_LAUNCHER]),
        (map_info._IDX_HARVESTER, score_table[map_info._IDX_HARVESTER]),
        (map_info._IDX_CONVEYOR, score_table[map_info._IDX_CONVEYOR]),
        (map_info._IDX_ARMOURED_CONVEYOR, score_table[map_info._IDX_ARMOURED_CONVEYOR]),
        (map_info._IDX_BARRIER, score_table[map_info._IDX_BARRIER]),
        (map_info._IDX_BRIDGE, score_table[map_info._IDX_BRIDGE]),
        (map_info._IDX_SPLITTER, score_table[map_info._IDX_SPLITTER]),
    ]

_SCORED_NON_CORE_TYPES_SENTINEL = _build_scored_non_core(SENTINEL_BUILDING_SCORE)
_SCORED_NON_CORE_TYPES_GUNNER = _build_scored_non_core(GUNNER_BUILDING_SCORE)

_NUM_PLANES = 9  # up to 8191; gunner CORE(480) + turrets keeps per-dir sum well under this

SCORE_THRESHOLD_FACTOR = 0.25
MIN_ATTACK_SCORE = 16
THREAT_PENALTY = 4

cant_attack = 0


# ---------------------------------------------------------------------------
# Bit-sliced score plane helpers
# ---------------------------------------------------------------------------

_SCORE_BITS_CACHE: dict = {}

def _bits_of_score(c):
    b = _SCORE_BITS_CACHE.get(c)
    if b is None:
        b = []
        x, i = c, 0
        while x:
            if x & 1:
                b.append(i)
            x >>= 1
            i += 1
        _SCORE_BITS_CACHE[c] = b
    return b


def _add_const_to_planes(planes, c, mask):
    """Bit-sliced: add constant `c` to counters at every set bit of `mask`."""
    if not mask or not c:
        return
    for i in _bits_of_score(c):
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
    w = map_info._width
    shift_masks = map_info._turret_shift_masks
    bm_et = map_info._bm_et
    offsets_table = map_info._SENTINEL_OFFSETS

    core_mask = bm_et[map_info._IDX_CORE] & enemy_team_bm
    core_score = SENTINEL_BUILDING_SCORE[map_info._IDX_CORE]

    # Per-type (score, mask) list. Possible optimization: group by score and
    # OR-union the masks, so one _add_const_to_planes call covers all types
    # sharing a score (masks for types sharing a score are disjoint since one
    # building per tile). Left per-type for readability.
    type_contribs = []
    for t_idx, s in _SCORED_NON_CORE_TYPES_SENTINEL:
        bm_t = bm_et[t_idx] & enemy_team_bm
        if bm_t and s:
            type_contribs.append((s, bm_t))

    non_threat = map_info._board_mask & ~threat
    non_zero = 0
    all_planes = []
    for d in range(8):
        planes = [0] * _NUM_PLANES
        core_reach = 0
        for dx, dy in offsets_table[d]:
            sm = shift_masks.get((-dx, -dy))
            if sm is None:
                continue
            rev_off = -dx + (-dy) * w
            if core_mask:
                masked = core_mask & sm
                if masked:
                    if rev_off >= 0:
                        core_reach |= masked << rev_off
                    else:
                        core_reach |= masked >> (-rev_off)
            for s, bm_t in type_contribs:
                masked = bm_t & sm
                if not masked:
                    continue
                if rev_off >= 0:
                    contrib = masked << rev_off
                else:
                    contrib = masked >> (-rev_off)
                non_zero |= contrib
                _add_const_to_planes(planes, s, contrib)
        non_zero |= core_reach
        if core_reach:
            _add_const_to_planes(planes, core_score, core_reach)
        # Restrict every plane to placement-candidate tiles for this direction.
        # THREAT_PENALTY is baked after the loop using final non_zero.
        mask_d = sentinel_masks[d]
        for i in range(_NUM_PLANES):
            planes[i] &= mask_d
        all_planes.append(planes)

    if THREAT_PENALTY:
        for d in range(8):
            _add_const_to_planes(all_planes[d], THREAT_PENALTY,
                                 non_threat & non_zero & sentinel_masks[d])
    return all_planes


# ---------------------------------------------------------------------------
# Gunner: one plane-list. Either a single facing, or max over all 8 facings.
# ---------------------------------------------------------------------------

def _gunner_ray_blocked_mask():
    """Tiles that block a gunner ray: walls + allied non-road, non-marker
    buildings. A gunner can't shoot through its own infrastructure."""
    walls = map_info._bm_env[map_info._IDX_ENV_WALL]
    my_team = map_info._bm_team[map_info._my_team_idx]
    my_solid = (my_team
                & ~map_info._bm_et[map_info._IDX_ROAD]
                & ~map_info._bm_et[map_info._IDX_MARKER])
    return walls | my_solid


def _compute_gunner_dir_scores(enemy_team_bm, threat, gunner_masks):
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
    w = map_info._width
    shift_masks = map_info._turret_shift_masks
    bm_et = map_info._bm_et
    dir_vecs = map_info._DIR_VECS
    gunner_rays = map_info._GUNNER_RAYS
    not_blocked = map_info._board_mask & ~_gunner_ray_blocked_mask()

    core_mask = bm_et[map_info._IDX_CORE] & enemy_team_bm
    core_score = GUNNER_BUILDING_SCORE[map_info._IDX_CORE]

    # Per-type (score, mask) list. Possible optimization: group by score and
    # OR-union masks into one entry per distinct score (masks for types with
    # the same score are disjoint since one building per tile). Left per-type
    # for readability.
    type_initial = []
    for t_idx, s in _SCORED_NON_CORE_TYPES_GUNNER:
        bm_t = bm_et[t_idx] & enemy_team_bm
        if bm_t and s:
            type_initial.append((s, bm_t))

    non_threat = map_info._board_mask & ~threat
    non_zero = 0
    all_planes = []
    for d in range(8):
        planes = [0] * _NUM_PLANES
        dx, dy = dir_vecs[d]
        max_step = len(gunner_rays[d])
        sdx, sdy = -dx, -dy
        sm = shift_masks.get((sdx, sdy))
        if sm is None or max_step == 0:
            all_planes.append(planes)
            continue
        soff = sdx + sdy * w
        core_cur = core_mask
        type_cur = list(type_initial)
        core_reach = 0
        def _shift_one(m, _sm=sm, _soff=soff, _nb=not_blocked):
            masked = m & _sm & _nb
            return (masked << _soff if _soff >= 0 else masked >> (-_soff))
        for _ in range(max_step):
            if core_cur:
                core_cur = _shift_one(core_cur)
                if core_cur:
                    core_reach |= core_cur
            new_type_cur = []
            for s, bm_t in type_cur:
                shifted = _shift_one(bm_t)
                if shifted:
                    new_type_cur.append((s, shifted))
                    non_zero |= shifted
                    _add_const_to_planes(planes, s, shifted)
            type_cur = new_type_cur
            if not core_cur and not type_cur:
                break
        non_zero |= core_reach
        if core_reach:
            _add_const_to_planes(planes, core_score, core_reach)
        # Restrict every plane to placement-candidate tiles for this direction.
        # THREAT_PENALTY is baked after the loop so every plane gets it exactly
        # once, using the final non_zero union.
        mask_d = gunner_masks[d]
        for i in range(_NUM_PLANES):
            planes[i] &= mask_d
        all_planes.append(planes)

    # Sum the raw per-direction planes tile-wise, then bake THREAT_PENALTY
    # once on both the summed plane and each per-direction plane.
    summed = [0] * _NUM_PLANES
    for d in range(8):
        _add_planes_into(summed, all_planes[d])
    if THREAT_PENALTY:
        any_placeable = 0
        for d in range(8):
            any_placeable |= gunner_masks[d]
        _add_const_to_planes(summed, THREAT_PENALTY,
                             non_threat & non_zero & any_placeable)
        for d in range(8):
            _add_const_to_planes(all_planes[d], THREAT_PENALTY,
                                 non_threat & non_zero & gunner_masks[d])
    return all_planes, summed


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

    _ensure_score_planes()
    sent_planes_by_dir = _round_cache_sentinel_planes
    gun_planes_by_dir = _round_cache_gunner_planes
    gun_sum_plane = _round_cache_gunner_sum
    sentinel_masks = _round_cache_placement_masks[0]
    gunner_masks = _round_cache_placement_masks[1]

    directions = map_info._DIRECTIONS

    pass # log("AT POSITION", pos)

    # Sentinel: best valid-placement direction at pos.
    best_s_dir, best_s_score = Direction.NORTH, -1
    for d in range(8):
        if not (sentinel_masks[d] & bit):
            pass # log("  SENT", directions[d], "not a valid placement")
            continue
        s = _read_score(sent_planes_by_dir[d], n)
        pass # log("  SENT", directions[d], "score", s)
        if s > best_s_score:
            best_s_score = s
            best_s_dir = directions[d]

    # Gunner: sum plane is the decision basis.
    gun_sum = _read_score(gun_sum_plane, n) if gun_sum_plane is not None else 0
    gunner_any = 0
    for d in range(8):
        gunner_any |= gunner_masks[d]
    gunner_placeable = bool(gunner_any & bit)
    pass # log("  GUN sum", gun_sum, "placeable" if gunner_placeable else "not placeable")

    if not gunner_placeable or best_s_score >= gun_sum:
        return best_s_dir, EntityType.SENTINEL, best_s_score

    # Gunner wins: now pick its best facing from per-direction planes.
    best_g_dir, best_g_score = Direction.NORTH, -1
    for d in range(8):
        if not (gunner_masks[d] & bit):
            pass # log("  GUN", directions[d], "not a valid placement")
            continue
        g = _read_score(gun_planes_by_dir[d], n)
        pass # log("  GUN", directions[d], "score", g)
        if g > best_g_score:
            best_g_score = g
            best_g_dir = directions[d]
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
    my_team = map_info._bm_team[map_info._my_team_idx]
    turrets = (map_info._bm_et[map_info._IDX_GUNNER] | map_info._bm_et[map_info._IDX_SENTINEL]) & my_team
    if not turrets:
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
    return result


def _placement_candidates():
    """Returns (sentinel_masks, gunner_masks): two lists of 8 bitmasks, one per
    facing direction. Loader blockers are baked in:
      sentinel_masks[d] = tiles where a sentinel can face direction d
      gunner_masks[d]   = tiles where a gunner can face direction d
    Gunners with 2+ loader directions get the full-360 exemption."""
    my_team_idx = map_info._my_team_idx
    enemy_idx = 1 - my_team_idx
    my_team = map_info._bm_team[my_team_idx]
    enemy_team = map_info._bm_team[enemy_idx]

    w = map_info._width
    bm_et = map_info._bm_et
    shift_masks = map_info._turret_shift_masks
    dir_vecs = map_info._DIR_VECS

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
    empty = ~map_info._bm_any_building | map_info._bm_et[map_info._IDX_MARKER]

    my_clearable = (
        map_info._bm_et[map_info._IDX_BARRIER]
        | map_info._bm_et[map_info._IDX_ROAD]
        # | map_info._bm_et[map_info._IDX_CONVEYOR]
        # | map_info._bm_et[map_info._IDX_SPLITTER]
        # | map_info._bm_et[map_info._IDX_BRIDGE]
    ) & my_team

    enemy_clearable = (
        map_info._bm_et[map_info._IDX_ROAD]
        | map_info._bm_et[map_info._IDX_CONVEYOR]
        | map_info._bm_et[map_info._IDX_SPLITTER]
        | map_info._bm_et[map_info._IDX_BRIDGE]
    ) & enemy_team

    candidates &= (empty | my_clearable | enemy_clearable)
    candidates &= ~map_info._bm_env[map_info._IDX_ENV_WALL]

    my_bit = 1 << (map_info._my_pos.x + map_info._my_pos.y * map_info._width)
    all_bots = (map_info._bm_friendly_bots | map_info._bm_enemy_bots) & ~my_bit
    candidates &= ~all_bots

    danger_for_clearable = map_info._bm_enemy_launch_adj
    enemy_bots = map_info._bm_enemy_bots
    if enemy_bots:
        danger = enemy_bots
        for _ in range(2):
            danger = map_info.expand_chebyshev(danger)
        danger_for_clearable |= danger
        if map_info.expand_chebyshev(enemy_bots) & (1<<(map_info._my_pos.x + map_info._my_pos.y * map_info._width)):
            danger_for_clearable = map_info._board_mask #am being tracked
    candidates &= ~(danger_for_clearable & enemy_clearable)

    candidates &= ~cant_attack
    candidates &= ~_turret_feed_chains()

    # Facing blockers: block direction D at tile P if P+delta_D has a friendly
    # harvester/foundry (always blocks), or a conveyor whose output points back
    # at P (direction == opposite of D). Conveyors pointing away are fine.
    base_block = bm_et[map_info._IDX_HARVESTER] | bm_et[map_info._IDX_FOUNDRY]

    blockers = [0] * 8
    for d in range(0, 8, 2):
        dx, dy = dir_vecs[d]
        sm = shift_masks.get((-dx, -dy))
        if sm is None:
            continue
        incoming_conv = map_info._bm_conv_by_dir[(d + 4) & 7]
        src = (base_block | incoming_conv) & sm
        if not src:
            continue
        soff = -dx + (-dy) * w
        blockers[d] = (src << soff) if soff >= 0 else (src >> (-soff))

    # Sentinels have low dps and shouldn't sit in gunner/breach fire. Gunners
    # have high dps and can trade into hard threats.
    sentinel_cands = candidates & ~map_info._bm_enemy_hard_threat
    sentinel_masks = [sentinel_cands & ~blockers[d] for d in range(8)]
    gunner_masks   = [candidates & ~blockers[d] for d in range(8)]
    return sentinel_masks, gunner_masks


def _get_attack_candidates():
    """Return (preferred, fallback) candidate bitmasks.

    Threshold filter: keep only candidates whose best non-blocked sentinel
    direction score, OR whose gunner summed-across-facings score, is within
    SCORE_THRESHOLD_FACTOR of the per-track best. Sentinel and gunner tracks
    are on different scales (sentinel = single-dir, gunner = sum of 8) so
    thresholds are computed independently per track."""
    sentinel_masks, gunner_masks = _placement_candidates()
    _round_cache_placement_masks[0] = sentinel_masks
    _round_cache_placement_masks[1] = gunner_masks

    gunner_any = 0
    for d in range(8):
        gunner_any |= gunner_masks[d]
    filtered = gunner_any
    for d in range(8):
        filtered |= sentinel_masks[d]
    if not filtered:
        return 0, 0

    _ensure_score_planes()
    sent_planes_by_dir = _round_cache_sentinel_planes
    gun_sum_plane = _round_cache_gunner_sum

    can_afford_sent = rc.get_global_resources()[0] >= rc.get_sentinel_cost()[0]
    can_afford_gun = rc.get_global_resources()[0] >= rc.get_gunner_cost()[0]

    # Sentinel: per-direction max. Gunner: single summed plane over any
    # placeable direction. Separate thresholds to avoid the gunner-sum scale
    # (potentially 8x larger) from wiping out all sentinel candidates.
    sent_max = 0
    for d in range(8):
        if sentinel_masks[d] and can_afford_sent:
            s = _max_score_in_mask(sent_planes_by_dir[d], sentinel_masks[d])
            if s > sent_max:
                sent_max = s
    gun_max = 0
    if gun_sum_plane is not None and gunner_any and can_afford_gun:
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
_round_cache_placement_masks = [None, None]  # [sentinel_masks[8], gunner_masks[8]]


def _ensure_round_cache():
    global _round_cache_round, _round_cache_attack_candidates
    global _round_cache_sentinel_planes, _round_cache_gunner_planes, _round_cache_gunner_sum
    r = rc.get_current_round()
    if _round_cache_round == r:
        return
    _round_cache_round = r
    _round_cache_sentinel_planes = None
    _round_cache_gunner_planes = None
    _round_cache_gunner_sum = None
    _round_cache_attack_candidates = _get_attack_candidates()


def _ensure_score_planes():
    """Lazily build sentinel and gunner planes once per round. Requires the
    placement masks to already be populated in _round_cache_placement_masks."""
    global _round_cache_sentinel_planes, _round_cache_gunner_planes, _round_cache_gunner_sum
    if _round_cache_sentinel_planes is not None:
        return
    # Drop tiles already covered by one of my gunners' current ray — they're
    # being shot at already, no point scoring another turret on them.
    enemy_team_bm = map_info._bm_team[1 - map_info._my_team_idx] & ~map_info._bm_my_gunner_claims
    threat = (map_info._bm_enemy_soft_threat | map_info._bm_enemy_hard_threat)
    sentinel_masks, gunner_masks = _round_cache_placement_masks
    _round_cache_sentinel_planes = _compute_sentinel_dir_scores(
        enemy_team_bm, threat, sentinel_masks
    )
    _round_cache_gunner_planes, _round_cache_gunner_sum = _compute_gunner_dir_scores(
        enemy_team_bm, threat, gunner_masks
    )


# ---------------------------------------------------------------------------
# Claims + state hooks
# ---------------------------------------------------------------------------

def _my_claims():
    w = map_info._width
    my_mask = 1 << (map_info._my_pos.x + map_info._my_pos.y * w)
    _ensure_round_cache()
    preferred, fallback = _round_cache_attack_candidates
    combined = preferred | fallback
    claimed = pathing.voronoi_claim(my_mask, units.builder.claimed_senders[comm_flag], combined)
    return claimed & preferred, claimed & fallback


_cached_claims = (0, 0)

def score():
    global _cached_claims
    _cached_claims = _my_claims()
    preferred, fallback = _cached_claims
    if preferred:
        return 6
    if fallback:
        return 6
    return 0


def run():
    global cant_attack
    pass # log("ATTACK")
    preferred, fallback = _cached_claims

    if not preferred and not fallback:
        return

    width = map_info._width
    my_team_idx = map_info._my_team_idx
    best = None
    if preferred:
        best, _ = nav.closest(preferred)
    if best is None and fallback:
        best, _ = nav.closest(fallback)
    if best is None:
        cant_attack |= preferred | fallback
        return

    best_n = best.x + best.y * width
    best_bit = 1 << best_n
    direction, turret_type, _ = get_best_direction(best)
    is_fallback = not bool(preferred & best_bit)
    best_id = map_info._building_id[best_n]
    is_mine = bool(map_info._bm_team[my_team_idx] & best_bit)

    pass # log(f"Attack: best={best}, dir={direction}, type={turret_type}, fallback={is_fallback}")

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
            for d in map_info._ALL_DIRECTIONS:
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
                pass # log(f"Attack destroy own building at {best}")
                rc.destroy(best)
                map_info.update_at(best)

    if turret_type == EntityType.GUNNER:
        if rc.can_build_gunner(best, direction):
            rc.build_gunner(best, direction)
            map_info.update_at(best)
    else:
        if rc.can_build_sentinel(best, direction):
            rc.build_sentinel(best, direction)
            map_info.update_at(best)

    comms.mark(best.x + best.y * map_info._width, comm_flag)
