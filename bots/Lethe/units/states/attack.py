from cambc import *

import map_info
import pathing
from pathing import Pathing
import comms
import units.builder
from log import DRAW_DEBUG, log


rc: Controller = None
nav: Pathing = None

comm_flag = 6


def init(c: Controller):
    global rc, nav
    rc = c
    nav = Pathing(rc)


BUILDING_SCORE = [0] * map_info._NUM_ET
BUILDING_SCORE[map_info._IDX_CORE] = 96
BUILDING_SCORE[map_info._IDX_HARVESTER] = 12
BUILDING_SCORE[map_info._IDX_FOUNDRY] = 16
BUILDING_SCORE[map_info._IDX_GUNNER] = 20
BUILDING_SCORE[map_info._IDX_SENTINEL] = 20
BUILDING_SCORE[map_info._IDX_BREACH] = 24
BUILDING_SCORE[map_info._IDX_LAUNCHER] = 8
BUILDING_SCORE[map_info._IDX_CONVEYOR] = 2
BUILDING_SCORE[map_info._IDX_ARMOURED_CONVEYOR] = 4
BUILDING_SCORE[map_info._IDX_BARRIER] = 4
BUILDING_SCORE[map_info._IDX_BRIDGE] = 2
BUILDING_SCORE[map_info._IDX_SPLITTER] = 2

# Non-core scorable types. Core is handled separately (OR-based to avoid triple-count on its 3x3).
_SCORED_NON_CORE_TYPES = [
    (map_info._IDX_FOUNDRY, BUILDING_SCORE[map_info._IDX_FOUNDRY]),
    (map_info._IDX_GUNNER, BUILDING_SCORE[map_info._IDX_GUNNER]),
    (map_info._IDX_SENTINEL, BUILDING_SCORE[map_info._IDX_SENTINEL]),
    (map_info._IDX_BREACH, BUILDING_SCORE[map_info._IDX_BREACH]),
    (map_info._IDX_LAUNCHER, BUILDING_SCORE[map_info._IDX_LAUNCHER]),
    (map_info._IDX_HARVESTER, BUILDING_SCORE[map_info._IDX_HARVESTER]),
    (map_info._IDX_CONVEYOR, BUILDING_SCORE[map_info._IDX_CONVEYOR]),
    (map_info._IDX_ARMOURED_CONVEYOR, BUILDING_SCORE[map_info._IDX_ARMOURED_CONVEYOR]),
    (map_info._IDX_BARRIER, BUILDING_SCORE[map_info._IDX_BARRIER]),
    (map_info._IDX_BRIDGE, BUILDING_SCORE[map_info._IDX_BRIDGE]),
    (map_info._IDX_SPLITTER, BUILDING_SCORE[map_info._IDX_SPLITTER]),
]

_NUM_PLANES = 10  # enough for per-tile scores up to 1023

# Only candidate tiles whose best sentinel/breach direction score is at least
# `SCORE_THRESHOLD_FACTOR * global_best_score` survive the attack-candidate filter.
SCORE_THRESHOLD_FACTOR = 0.5

# Attack state refuses to enter at all if the global best sentinel/breach score
# across candidates is below this. Tune up to make attack pickier.
MIN_ATTACK_SCORE = 16

# Gunner raw score is multiplied by this factor when compared against sentinel/breach
# scores, to artificially prefer gunner placements. Tune up to bias harder.
GUNNER_SCORE_MULTIPLIER = 4

# Flat score penalty applied to candidate tiles inside an enemy turret's
# attack pattern (_bm_enemy_turret_threat). Baked into plane construction by
# adding +THREAT_PENALTY to NON-threat tiles — so threat tiles are effectively
# THREAT_PENALTY lower in score than equivalent non-threat tiles, with no
# per-read adjustment needed. Gunner (which is per-tile, not plane-based) still
# subtracts this value inline.
THREAT_PENALTY = 4

# Tiles previously unreachable via nav.closest are added here and excluded
# from future attack candidate filtering. Mirrors cant_harvest / cant_sabotage.
cant_attack = 0 


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
        # Add 2^i to counters at `mask` — XOR, carry propagates up.
        carry = planes[i] & mask
        planes[i] ^= mask
        j = i + 1
        while carry and j < _NUM_PLANES:
            new_carry = planes[j] & carry
            planes[j] ^= carry
            carry = new_carry
            j += 1


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
    eq = candidates  # equal-to-threshold so far, bit-by-bit from MSB
    gt = 0           # strictly greater-than
    for i in range(_NUM_PLANES - 1, -1, -1):
        p = planes[i]
        if (threshold >> i) & 1:
            eq &= p
        else:
            gt |= eq & p
            eq &= ~p
    return gt | eq


def _compute_dir_scores(offsets_table, enemy_team_bm, threat):
    """For each of 8 facing directions, compute per-tile turret score planes.
    Uses `_turret_shift_masks` to move enemy-building masks by (-dx, -dy)
    so each tile's planes sum its attackable enemies' BUILDING_SCOREs.
    Core reach is OR'd (counted once per position) to match `core_counted`.
    Threat penalty is baked into planes: non-threat tiles receive a
    +THREAT_PENALTY bonus so threat tiles are effectively scored
    THREAT_PENALTY lower."""
    w = map_info._width
    shift_masks = map_info._turret_shift_masks
    bm_et = map_info._bm_et

    core_mask = bm_et[map_info._IDX_CORE] & enemy_team_bm
    core_score = BUILDING_SCORE[map_info._IDX_CORE]

    # Group non-core types by score — within one offset the masks for types
    # sharing a score are disjoint (one building per tile), so OR-unioning them
    # lets us make one _add_const_to_planes call per (offset, score).
    score_to_union = {}
    for t_idx, s in _SCORED_NON_CORE_TYPES:
        bm_t = bm_et[t_idx] & enemy_team_bm
        if bm_t:
            score_to_union[s] = score_to_union.get(s, 0) | bm_t
    score_groups = list(score_to_union.items())

    non_threat = map_info._board_mask & ~threat

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
            for s, bm_group in score_groups:
                masked = bm_group & sm
                if not masked:
                    continue
                if rev_off >= 0:
                    contrib = masked << rev_off
                else:
                    contrib = masked >> (-rev_off)
                _add_const_to_planes(planes, s, contrib)
        if core_reach:
            _add_const_to_planes(planes, core_score, core_reach)
        # Threat penalty baked in: add +THREAT_PENALTY to non-threat tiles so
        # threat tiles are relatively THREAT_PENALTY lower. No per-read adjust.
        if THREAT_PENALTY:
            _add_const_to_planes(planes, THREAT_PENALTY, non_threat)
        all_planes.append(planes)
    return all_planes


def _compute_gunner_dir_scores(enemy_team_bm, threat):
    """For each of 8 gunner facings, compute per-tile score planes.
    Gunner rays are wall-blocked: if a wall sits between the gunner and a
    ray tile, that tile isn't reachable. Built by iterative shift-and-mask:
    start with the enemy bitmasks, shift by -(dx, dy) and AND with ~walls at
    each step up to the gunner's range for that direction. At step s the
    accumulator at position P represents `enemy at P+s*d AND no wall at any
    of P+1*d..P+(s-1)*d`. Summed across steps via bit-sliced addition, with
    BUILDING_SCORE pre-multiplied by GUNNER_SCORE_MULTIPLIER so the final
    plane values are already comparable to sentinel/breach planes."""
    w = map_info._width
    shift_masks = map_info._turret_shift_masks
    bm_et = map_info._bm_et
    dir_vecs = map_info._DIR_VECS
    gunner_rays = map_info._GUNNER_RAYS
    not_walls = map_info._board_mask & ~map_info._bm_env[map_info._IDX_ENV_WALL]

    core_mask = bm_et[map_info._IDX_CORE] & enemy_team_bm
    core_score = BUILDING_SCORE[map_info._IDX_CORE] * GUNNER_SCORE_MULTIPLIER

    # Group non-core types by score (pre-multiplied for gunner).
    score_to_union = {}
    for t_idx, s in _SCORED_NON_CORE_TYPES:
        bm_t = bm_et[t_idx] & enemy_team_bm
        if bm_t:
            gs = s * GUNNER_SCORE_MULTIPLIER
            score_to_union[gs] = score_to_union.get(gs, 0) | bm_t

    non_threat = map_info._board_mask & ~threat

    all_planes = []
    for d in range(8):
        planes = [0] * _NUM_PLANES
        dx, dy = dir_vecs[d]
        max_step = len(gunner_rays[d])
        # Per-step shift is (-dx, -dy): bit at P in shift(X, -d) = X at P+d.
        sdx, sdy = -dx, -dy
        sm = shift_masks.get((sdx, sdy))
        if sm is None or max_step == 0:
            if core_mask:
                # core still reaches step 0 (self), irrelevant — skip.
                pass
            if THREAT_PENALTY:
                _add_const_to_planes(planes, THREAT_PENALTY, non_threat)
            all_planes.append(planes)
            continue
        soff = sdx + sdy * w

        # Start with the raw enemy masks. Each iteration shifts by one step and
        # masks walls. `core_cur` / `type_cur[score]` at P after s iterations
        # = enemy at P+s*d AND no wall at P+k*d for k in 1..s-1 (and at P,
        # which is enforced as non-wall for candidate tiles anyway).
        core_cur = core_mask
        type_cur = dict(score_to_union)  # score -> mask
        core_reach = 0

        for _ in range(max_step):
            # Shift core and each type mask one step opposite the gunner's
            # facing, then AND with ~walls.
            def _shift_one(m):
                masked = m & sm
                return (masked << soff if soff >= 0 else masked >> (-soff)) & not_walls
            if core_cur:
                core_cur = _shift_one(core_cur)
                if core_cur:
                    core_reach |= core_cur
            new_type_cur = {}
            for gs, bm_t in type_cur.items():
                shifted = _shift_one(bm_t)
                if shifted:
                    new_type_cur[gs] = shifted
                    _add_const_to_planes(planes, gs, shifted)
            type_cur = new_type_cur
            if not core_cur and not type_cur:
                break

        if core_reach:
            _add_const_to_planes(planes, core_score, core_reach)
        if THREAT_PENALTY:
            _add_const_to_planes(planes, THREAT_PENALTY, non_threat)
        all_planes.append(planes)
    return all_planes


def _compute_loader_blockers():
    """Per-direction bitmask of tiles where a loader occupies that direction,
    so a turret at that tile can't face that direction (sentinel/breach rules).
    Harvesters cardinally adjacent to an existing friendly sentinel are
    considered 'already taken' and don't count as loaders for a new turret —
    each harvester should only support one sentinel."""
    w = map_info._width
    bm_et = map_info._bm_et
    shift_masks = map_info._turret_shift_masks
    dir_vecs = map_info._DIR_VECS

    my_team_bm = map_info._bm_team[map_info._my_team_idx]
    my_sentinels = bm_et[map_info._IDX_SENTINEL] & my_team_bm
    # A harvester cardinal-adjacent to any of my sentinels is "taken" and
    # shouldn't block a new turret placement.
    if my_sentinels:
        taken_harvesters = map_info.expand_manhattan(my_sentinels) & bm_et[map_info._IDX_HARVESTER]
    else:
        taken_harvesters = 0
    harvesters = bm_et[map_info._IDX_HARVESTER] & ~taken_harvesters
    conv_bm = bm_et[map_info._IDX_CONVEYOR] | bm_et[map_info._IDX_ARMOURED_CONVEYOR]

    # Split conveyors by direction.
    conv_by_dir = [0] * 8
    building_dir = map_info._building_dir
    m = conv_bm
    while m:
        lsb = m & -m
        n = lsb.bit_length() - 1
        conv_by_dir[building_dir[n]] |= lsb
        m ^= lsb

    blockers = [0] * 8
    for D in range(8):
        dx, dy = dir_vecs[D]
        sm = shift_masks.get((-dx, -dy))
        if sm is None:
            continue
        offset = -dx + (-dy) * w
        # Harvester: cardinal only (D even). Loader at P+delta → shift harvesters by -delta.
        if D % 2 == 0:
            h = harvesters & sm
            if offset >= 0:
                blockers[D] |= h << offset
            else:
                blockers[D] |= h >> (-offset)
        # Conveyor at P+delta with output direction opposite-of-D (back toward P).
        opp = (D + 4) & 7
        c = conv_by_dir[opp] & sm
        if c:
            if offset >= 0:
                blockers[D] |= c << offset
            else:
                blockers[D] |= c >> (-offset)
    return blockers


def get_best_direction(pos):
    """Pick the best (direction, turret_type) for a turret at pos.
    Blocked: turret cannot face toward a loading building.
    Exception: gunner with 2+ loaders can face any direction.
    Score = sum of BUILDING_SCORE for enemy buildings the turret can hit."""
    w = map_info._width
    h = map_info._height
    px, py = pos.x, pos.y

    my_team_idx = map_info._my_team_idx
    my_buildings = map_info._bm_team[my_team_idx]

    my_foundries = map_info._bm_et[map_info._IDX_FOUNDRY] & my_buildings
    adj_foundry = False
    for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
        nx, ny = px + dx, py + dy
        if 0 <= nx < w and 0 <= ny < h and (my_foundries & (1 << (nx + ny * w))):
            adj_foundry = True
            break

    _ensure_score_planes()
    n = px + py * w
    bit = 1 << n
    sent_planes = _round_cache_sentinel_planes
    brch_planes = _round_cache_breach_planes  # may be None
    gun_planes = _round_cache_gunner_planes
    blockers = _round_cache_loader_blockers
    # Per-tile loader info from cached blocker masks.
    loader_count = 0
    blocked_dirs = 0  # bitmask over 0..7
    for d in range(8):
        if blockers[d] & bit:
            blocked_dirs |= 1 << d
            loader_count += 1
    gunner_allows_all = loader_count >= 2

    best_b_dir, best_b_score = Direction.NORTH, -1
    best_s_dir, best_s_score = Direction.NORTH, -1
    best_g_dir, best_g_score = Direction.NORTH, -1

    directions = map_info._DIRECTIONS

    for di in range(8):
        direction_blocked = bool(blocked_dirs & (1 << di))
        if not direction_blocked:
            if brch_planes is not None:
                b_score = _read_score(brch_planes[di], n)
                if b_score > best_b_score:
                    best_b_score = b_score
                    best_b_dir = directions[di]
            s_score = _read_score(sent_planes[di], n)
            if s_score > best_s_score:
                best_s_score = s_score
                best_s_dir = directions[di]

        # Gunner: read from precomputed planes (wall-blocked via iterative
        # shift-and-mask in _compute_gunner_dir_scores). Loader rule: gunner
        # with ≥2 loaders can face any direction; otherwise same blocking as
        # sentinel/breach.
        if gun_planes is not None and (gunner_allows_all or not direction_blocked):
            g_score = _read_score(gun_planes[di], n)
            if g_score > best_g_score:
                best_g_score = g_score
                best_g_dir = directions[di]

    if adj_foundry:
        if best_b_score > 0:
            return best_b_dir, EntityType.BREACH, best_b_score
        if best_s_score > 0:
            return best_s_dir, EntityType.SENTINEL, best_s_score
        return best_g_dir, EntityType.GUNNER, best_g_score

    if best_s_score >= best_g_score:
        return best_s_dir, EntityType.SENTINEL, best_s_score
    return best_g_dir, EntityType.GUNNER, best_g_score


def _placement_candidates():
    """Bitmask of tiles where a turret could be placed."""
    my_team_idx = map_info._my_team_idx
    enemy_idx = 1 - my_team_idx
    my_team = map_info._bm_team[my_team_idx]
    enemy_team = map_info._bm_team[enemy_idx]

    # Location filter: conveyor outputs + cardinal adj to harvesters
    candidates = map_info._bm_ti_fed | map_info._bm_ax_fed
    harvesters = (map_info._bm_et[map_info._IDX_HARVESTER]&map_info._bm_env[map_info._IDX_ENV_ORE_TI]) | map_info._bm_et[map_info._IDX_FOUNDRY]  # double for safety margin
    if harvesters:
        candidates |= map_info.expand_manhattan(harvesters)

    # Tile content filter: empty, or clearable
    empty = ~map_info._bm_any_building

    my_clearable = (
        map_info._bm_et[map_info._IDX_BARRIER]
        | map_info._bm_et[map_info._IDX_ROAD]
        | map_info._bm_et[map_info._IDX_MARKER]
    ) & my_team

    enemy_clearable = (
        map_info._bm_et[map_info._IDX_MARKER]
        | map_info._bm_et[map_info._IDX_ROAD]
    ) & enemy_team

    candidates &= (empty | my_clearable | enemy_clearable)

    # Exclusions
    candidates &= ~map_info._bm_env[map_info._IDX_ENV_WALL]

    # Exclude tiles with any builder bots (except me)
    my_bit = 1 << (map_info._my_pos.x + map_info._my_pos.y * map_info._width)
    all_bots = (map_info._bm_friendly_bots | map_info._bm_enemy_bots) & ~my_bit
    candidates &= ~all_bots

    # Danger zones that only disqualify enemy-road candidates (we'd have to
    # commit the builder to stand on the road and be vulnerable there):
    #   - 4-Chebyshev reach of any enemy builder bot
    #   - 1-Chebyshev adjacency to any enemy launcher (could throw us away)
    enemy_roads = map_info._bm_et[map_info._IDX_ROAD] & enemy_team
    danger_for_roads = map_info._bm_enemy_launch_adj
    enemy_bots = map_info._bm_enemy_bots
    if enemy_bots:
        danger = enemy_bots
        for _ in range(4):
            danger = map_info.expand_chebyshev(danger)
        danger_for_roads |= danger
    candidates &= ~(danger_for_roads & enemy_roads)

    # Previously-unreachable candidates are permanently excluded.
    candidates &= ~cant_attack

    return candidates


def _get_attack_candidates():
    """Return (non_roaded, roaded) candidate bitmasks."""
    candidates = _placement_candidates()
    if not candidates:
        return 0, 0
    filtered = candidates

    # Determine whether breach is worth evaluating: breach only wins in
    # `get_best_direction` when the candidate is cardinally adjacent to one of
    # our foundries. If no candidate is, skip breach plane construction and
    # breach participation in the threshold filter.
    global _round_cache_need_breach
    my_team_idx_ = map_info._my_team_idx
    my_foundries = map_info._bm_et[map_info._IDX_FOUNDRY] & map_info._bm_team[my_team_idx_]
    foundry_adj = map_info.expand_manhattan(my_foundries) if my_foundries else 0
    _round_cache_need_breach = bool(filtered & foundry_adj)

    # Threshold filter: keep only candidates whose best non-blocked sentinel/breach
    # direction score is within SCORE_THRESHOLD_FACTOR of the global best.
    # Threat penalty is baked into planes (non-threat tiles get +THREAT_PENALTY
    # during plane construction), so no per-tile adjustment here.
    _ensure_score_planes()
    sent_planes = _round_cache_sentinel_planes
    brch_planes = _round_cache_breach_planes  # may be None
    gun_planes = _round_cache_gunner_planes
    blockers = _round_cache_loader_blockers
    max_score = 0
    for d in range(8):
        allowed = filtered & ~blockers[d]
        if not allowed:
            continue
        s = _max_score_in_mask(sent_planes[d], allowed)
        if s > max_score:
            max_score = s
        if brch_planes is not None:
            b = _max_score_in_mask(brch_planes[d], allowed)
            if b > max_score:
                max_score = b
        if gun_planes is not None:
            g = _max_score_in_mask(gun_planes[d], allowed)
            if g > max_score:
                max_score = g
    global _round_cache_threshold
    _round_cache_threshold = 0
    if max_score < MIN_ATTACK_SCORE:
        return 0, 0
    if max_score > 0:
        threshold = int(max_score * SCORE_THRESHOLD_FACTOR)
        _round_cache_threshold = threshold
        keep = 0
        for d in range(8):
            allowed = filtered & ~blockers[d]
            if not allowed:
                continue
            keep |= _ge_threshold_mask(sent_planes[d], threshold, allowed)
            if brch_planes is not None:
                keep |= _ge_threshold_mask(brch_planes[d], threshold, allowed)
            if gun_planes is not None:
                keep |= _ge_threshold_mask(gun_planes[d], threshold, allowed)
        filtered &= keep
        if not filtered:
            return 0, 0

    # Split into non-enemy-roaded vs enemy-roaded
    my_team_idx = map_info._my_team_idx
    enemy_idx = 1 - my_team_idx
    enemy_roads = map_info._bm_et[map_info._IDX_ROAD] & map_info._bm_team[enemy_idx]

    roaded = filtered & enemy_roads
    non_roaded = filtered & ~enemy_roads

    return non_roaded, roaded


_round_cache_round = -1
_round_cache_attack_candidates = (0, 0)
_round_cache_sentinel_planes = None
_round_cache_breach_planes = None
_round_cache_gunner_planes = None
_round_cache_threshold = 0
_round_cache_loader_blockers = None
_round_cache_need_breach = False

def _ensure_round_cache():
    global _round_cache_round, _round_cache_attack_candidates
    global _round_cache_sentinel_planes, _round_cache_breach_planes, _round_cache_gunner_planes
    global _round_cache_loader_blockers
    global _round_cache_need_breach
    r = rc.get_current_round()
    if _round_cache_round == r:
        return
    _round_cache_round = r
    # Invalidate planes first; _get_attack_candidates may re-populate them
    # as part of the threshold filter.
    _round_cache_sentinel_planes = None
    _round_cache_breach_planes = None
    _round_cache_gunner_planes = None
    _round_cache_need_breach = False
    _round_cache_loader_blockers = _compute_loader_blockers()
    _round_cache_attack_candidates = _get_attack_candidates()
    if DRAW_DEBUG:
        non_roaded, roaded = _round_cache_attack_candidates
        combined = non_roaded | roaded
        if combined and _round_cache_sentinel_planes is not None:
            _draw_attack_candidates(combined,
                                    _round_cache_sentinel_planes,
                                    _round_cache_breach_planes)


def _draw_attack_candidates(mask, sent_planes, brch_planes):
    """Debug: for every (tile, direction) where sentinel/breach plane score >=
    threshold, draw a white length-1 line. For every tile where any gunner
    direction's plane score >= threshold, draw a red dot. (Gunners can
    rotate, so direction is arbitrary — the dot is the per-tile marker.)"""
    w = map_info._width
    h = map_info._height
    dir_vecs = map_info._DIR_VECS
    blockers = _round_cache_loader_blockers
    threshold = _round_cache_threshold
    gun_planes = _round_cache_gunner_planes

    # Sentinel/breach: one white length-1 line per (tile, direction) whose
    # plane score meets the threshold (threat penalty already baked in).
    for d in range(8):
        allowed = mask & ~blockers[d]
        if not allowed:
            continue
        sb_above = _ge_threshold_mask(sent_planes[d], threshold, allowed)
        if brch_planes is not None:
            sb_above |= _ge_threshold_mask(brch_planes[d], threshold, allowed)
        if not sb_above:
            continue
        dx, dy = dir_vecs[d]
        m = sb_above
        while m:
            lsb = m & -m
            n = lsb.bit_length() - 1
            x, y = n % w, n // w
            ex, ey = x + dx, y + dy
            if 0 <= ex < w and 0 <= ey < h:
                rc.draw_indicator_line(Position(x, y), Position(ex, ey), 255, 255, 255)
            m ^= lsb

    # Gunner: union across all 8 directions of tiles where the plane score meets
    # the threshold, respecting the loader rule (<2 loaders → blocked dirs count;
    # ≥2 loaders → any direction allowed). One red dot per qualifying tile.
    if gun_planes is not None:
        # Tiles with ≥2 loaders: OR of pairwise ANDs of blocker masks.
        ge2 = 0
        for i in range(8):
            for j in range(i + 1, 8):
                ge2 |= blockers[i] & blockers[j]
        gunner_any = 0
        for d in range(8):
            # If the tile has ≥2 loaders (in ge2), gunner can face this
            # direction regardless; otherwise it's blocked if blockers[d] hits.
            allowed = mask & (ge2 | ~blockers[d])
            if not allowed:
                continue
            gunner_any |= _ge_threshold_mask(gun_planes[d], threshold, allowed)
        m = gunner_any
        while m:
            lsb = m & -m
            n = lsb.bit_length() - 1
            rc.draw_indicator_dot(Position(n % w, n // w), 255, 0, 0)
            m ^= lsb


def _ensure_score_planes():
    """Lazily build the per-direction sentinel/breach/gunner score planes once
    per round. Breach planes only built if at least one candidate is adjacent
    to a friendly foundry (otherwise breach never wins in `get_best_direction`)."""
    global _round_cache_sentinel_planes, _round_cache_breach_planes, _round_cache_gunner_planes
    if _round_cache_sentinel_planes is not None:
        return
    enemy_team_bm = map_info._bm_team[1 - map_info._my_team_idx]
    threat = map_info._bm_enemy_turret_threat
    _round_cache_sentinel_planes = _compute_dir_scores(
        map_info._SENTINEL_OFFSETS, enemy_team_bm, threat
    )
    if _round_cache_need_breach:
        _round_cache_breach_planes = _compute_dir_scores(
            map_info._BREACH_OFFSETS, enemy_team_bm, threat
        )
    else:
        _round_cache_breach_planes = None
    _round_cache_gunner_planes = _compute_gunner_dir_scores(enemy_team_bm, threat)


def _my_claims():
    w = map_info._width
    my_mask = 1 << (map_info._my_pos.x + map_info._my_pos.y * w)
    _ensure_round_cache()
    non_roaded, roaded = _round_cache_attack_candidates
    combined = non_roaded | roaded
    claimed = pathing.voronoi_claim(my_mask, units.builder.claimed_senders[comm_flag], combined)
    return claimed & non_roaded, claimed & roaded

_cached_claims = (0, 0)  # set by score(), reused by run()

def score():
    global _cached_claims
    if rc.get_global_resources()[0] < rc.get_sentinel_cost()[0]:
        _cached_claims = (0, 0)
        return 0
    _cached_claims = _my_claims()
    non_roaded, roaded = _cached_claims
    if non_roaded:
        return 8
    if roaded:
        return 6
    return 0


def run():
    global cant_attack
    log("ATTACK")
    non_roaded, roaded = _cached_claims

    if not non_roaded and not roaded:
        return

    width = map_info._width
    my_team_idx = map_info._my_team_idx
    candidates = non_roaded | roaded

    # Evaluate all adjacent candidate tiles and pick highest scoring
    my_pos = map_info._my_pos
    best = None
    best_score = -1
    best_direction = Direction.NORTH
    best_turret_type = EntityType.SENTINEL
    best_is_enemy_road = False

    enemy_roads = map_info._bm_et[map_info._IDX_ROAD] & map_info._bm_team[1 - my_team_idx]

    mask = candidates
    while mask:
        lsb = mask & -mask
        n = lsb.bit_length() - 1
        px, py = n % width, n // width
        if max(abs(px - my_pos.x), abs(py - my_pos.y)) <= 1:
            pos = Position(px, py)
            direction, turret_type, dir_score = get_best_direction(pos)
            # Prefer non-roaded tiles
            is_er = bool(enemy_roads & lsb)
            adj_score = (0 if is_er else 1, dir_score)
            if adj_score > (0 if best_is_enemy_road else 1, best_score):
                best = pos
                best_score = dir_score
                best_direction = direction
                best_turret_type = turret_type
                best_is_enemy_road = is_er
        mask ^= lsb

    if best is None:
        # No adjacent candidates, move toward closest — prefer non-roaded.
        if non_roaded:
            best, _ = nav.closest(non_roaded)
        if best is None and roaded:
            best, _ = nav.closest(roaded)
        if best is None:
            cant_attack |= non_roaded | roaded
            return
        best_direction, best_turret_type, _ = get_best_direction(best)
        best_n = best.x + best.y * width
        best_is_enemy_road = bool(enemy_roads & (1 << best_n))

    best_n = best.x + best.y * width
    best_bit = 1 << best_n
    best_id = map_info._building_id[best_n]
    is_mine = bool(map_info._bm_team[my_team_idx] & best_bit)

    direction = best_direction
    turret_type = best_turret_type
    is_enemy_road = best_is_enemy_road
    log(f"Attack: best={best}, dir={direction}, type={turret_type}, enemy_road={is_enemy_road}")

    # Enemy builder bot within Chebyshev 2 (distance² ≤ 4)?
    zone = 1 << (map_info._my_pos.x + map_info._my_pos.y * width)
    zone = map_info.expand_chebyshev(map_info.expand_chebyshev(zone))
    enemy_bot_nearby = bool(map_info._bm_enemy_bots & zone)

    if is_enemy_road:
        # Move onto enemy road, fire it, step off
        nav.move_to(best)
        if rc.can_fire(best):
            if not enemy_bot_nearby or rc.get_hp(best_id) <= 2: # bait them to move away
                rc.fire(best)
        for d in map_info._ALL_DIRECTIONS:
            if d == Direction.CENTRE:
                continue
            if rc.can_move(d):
                rc.move(d)
                map_info.update_move()
                break
    else:
        # Move adjacent and destroy own building if needed
        nav.move_adjacent(best)
        if best_id and is_mine:
            if not map_info.has_builder_bot(best) and rc.can_destroy(best) and rc.get_action_cooldown() == 0:
                log(f"Attack destroy own building at {best}")
                rc.destroy(best)
                map_info.update_at(best)

    # Place turret
    if turret_type == EntityType.GUNNER:
        if rc.can_build_gunner(best, direction):
            rc.build_gunner(best, direction)
            map_info.update_at(best)
    elif turret_type == EntityType.BREACH:
        if rc.can_build_breach(best, direction):
            rc.build_breach(best, direction)
            map_info.update_at(best)
    else:
        if rc.can_build_sentinel(best, direction):
            rc.build_sentinel(best, direction)
            map_info.update_at(best)

    comms.mark(best.x + best.y * map_info._width, comm_flag)
