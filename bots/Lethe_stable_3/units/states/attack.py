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

MAX_SCORE = 8


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

_NUM_PLANES = 13  # fits per-direction gunner scores (~500) summed across 8 dirs (~4000)

SCORE_THRESHOLD_FACTOR = 0.25
MIN_ATTACK_SCORE = 16
GUNNER_SCORE_MULTIPLIER = 4
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

    Scores sum BUILDING_SCORE for each enemy building in the sentinel's
    offset pattern. THREAT_PENALTY is baked in: non-threat tiles get
    +THREAT_PENALTY so threat tiles read THREAT_PENALTY lower."""
    w = map_info._width
    shift_masks = map_info._turret_shift_masks
    bm_et = map_info._bm_et
    offsets_table = map_info._SENTINEL_OFFSETS

    core_mask = bm_et[map_info._IDX_CORE] & enemy_team_bm
    core_score = BUILDING_SCORE[map_info._IDX_CORE]

    # Group non-core enemy types by score; within a single offset, the masks
    # for types sharing a score are disjoint (one building per tile), so we
    # can OR-union them and do one _add_const_to_planes per (offset, score).
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
        if THREAT_PENALTY:
            _add_const_to_planes(planes, THREAT_PENALTY, non_threat)
        # Restrict every plane to placement-candidate tiles for this direction.
        mask_d = sentinel_masks[d]
        for i in range(_NUM_PLANES):
            planes[i] &= mask_d
        all_planes.append(planes)
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
    """For each of 8 facing directions, compute a per-tile gunner score plane
    list. Returns: list of 8 plane-lists. Reading position n from the d-th
    inner list yields the gunner's total damage-score if placed at n facing
    direction d — but ONLY if n is a valid placement tile for that direction
    (per `gunner_masks[d]`); otherwise the score reads 0.

    Gunner rays are blocked by walls AND by allied non-road, non-marker
    buildings. Scores are pre-multiplied by GUNNER_SCORE_MULTIPLIER so they
    compare directly with sentinel scores. THREAT_PENALTY is baked in:
    non-threat tiles get +THREAT_PENALTY so threat tiles read THREAT_PENALTY
    lower."""
    w = map_info._width
    shift_masks = map_info._turret_shift_masks
    bm_et = map_info._bm_et
    dir_vecs = map_info._DIR_VECS
    gunner_rays = map_info._GUNNER_RAYS
    not_blocked = map_info._board_mask & ~_gunner_ray_blocked_mask()

    core_mask = bm_et[map_info._IDX_CORE] & enemy_team_bm
    core_score_mult = BUILDING_SCORE[map_info._IDX_CORE] * GUNNER_SCORE_MULTIPLIER

    score_to_union_mult = {}
    for t_idx, s in _SCORED_NON_CORE_TYPES:
        bm_t = bm_et[t_idx] & enemy_team_bm
        if bm_t:
            gs = s * GUNNER_SCORE_MULTIPLIER
            score_to_union_mult[gs] = score_to_union_mult.get(gs, 0) | bm_t

    non_threat = map_info._board_mask & ~threat

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
        type_cur = dict(score_to_union_mult)
        core_reach = 0
        for _ in range(max_step):
            def _shift_one(m, _sm=sm, _soff=soff, _nb=not_blocked):
                masked = m & _sm
                return (masked << _soff if _soff >= 0 else masked >> (-_soff)) & _nb
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
            _add_const_to_planes(planes, core_score_mult, core_reach)
        if THREAT_PENALTY:
            _add_const_to_planes(planes, THREAT_PENALTY, non_threat)
        # Restrict every plane to placement-candidate tiles for this direction.
        mask_d = gunner_masks[d]
        for i in range(_NUM_PLANES):
            planes[i] &= mask_d
        all_planes.append(planes)
    return all_planes


# ---------------------------------------------------------------------------
# Per-tile "best direction / best type" pick
# ---------------------------------------------------------------------------

def get_best_direction(pos):
    """Pick (Direction, turret_type, score) for a turret at pos.

    Sentinel: iterate the 8 sentinel plane-lists, pick the best non-blocked
    direction by reading the score at this tile.
    Gunner: read the single gunner max-plane at this tile for the cross-dir
    score, then call get_best_gunner_dir() to pick the actual facing.

    Breach is ignored for now — never returned."""
    w = map_info._width
    px, py = pos.x, pos.y
    n = px + py * w
    bit = 1 << n

    _ensure_score_planes()
    sent_planes_by_dir = _round_cache_sentinel_planes
    gun_planes_by_dir = _round_cache_gunner_planes
    sentinel_masks = _round_cache_placement_masks[0]
    gunner_masks = _round_cache_placement_masks[1]

    directions = map_info._DIRECTIONS

    # Sentinel: per-direction planes are already placement-filtered; only
    # read a direction where `pos` is a valid placement for it.
    best_s_dir, best_s_score = Direction.NORTH, -1
    for d in range(8):
        if not (sentinel_masks[d] & bit):
            continue
        s = _read_score(sent_planes_by_dir[d], n)
        if s > best_s_score:
            best_s_score = s
            best_s_dir = directions[d]

    # Gunner: same pattern, using per-direction gunner planes.
    best_g_dir, best_g_score = Direction.NORTH, -1
    if gun_planes_by_dir is not None:
        for d in range(8):
            if not (gunner_masks[d] & bit):
                continue
            g = _read_score(gun_planes_by_dir[d], n)
            if g > best_g_score:
                best_g_score = g
                best_g_dir = directions[d]

    if best_s_score >= best_g_score:
        return best_s_dir, EntityType.SENTINEL, best_s_score
    return best_g_dir, EntityType.GUNNER, best_g_score


# ---------------------------------------------------------------------------
# Candidate generation
# ---------------------------------------------------------------------------

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
        candidates |= map_info.expand_manhattan(harvesters)

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
    candidates &= ~map_info._bm_env[map_info._IDX_ENV_WALL]

    my_bit = 1 << (map_info._my_pos.x + map_info._my_pos.y * map_info._width)
    all_bots = (map_info._bm_friendly_bots | map_info._bm_enemy_bots) & ~my_bit
    candidates &= ~all_bots

    enemy_roads = map_info._bm_et[map_info._IDX_ROAD] & enemy_team
    danger_for_roads = map_info._bm_enemy_launch_adj
    enemy_bots = map_info._bm_enemy_bots
    if enemy_bots:
        danger = enemy_bots
        for _ in range(2):
            danger = map_info.expand_chebyshev(danger)
        danger_for_roads |= danger
    candidates &= ~(danger_for_roads & enemy_roads)

    candidates &= ~cant_attack

    # Facing blockers: block direction D at tile P if P+delta_D has a friendly
    # harvester/foundry (always blocks), or a conveyor whose output points back
    # at P (direction == opposite of D). Conveyors pointing away are fine.
    base_block = bm_et[map_info._IDX_HARVESTER] | bm_et[map_info._IDX_FOUNDRY]

    blockers = [0] * 8
    for d in range(8):
        dx, dy = dir_vecs[d]
        sm = shift_masks.get((-dx, -dy))
        if sm is None:
            continue
        incoming_conv = map_info._bm_conv_by_dir[(d + 4) & 7] & my_team
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
    """Return (non_roaded, roaded) candidate bitmasks.

    Threshold filter: keep only candidates whose best non-blocked sentinel
    direction score, OR whose gunner max-score, is within
    SCORE_THRESHOLD_FACTOR of the global best. Threat penalty is baked into
    both plane representations already."""
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
    gun_planes_by_dir = _round_cache_gunner_planes

    # NOTE: gunner SUM planes would double-count THREAT_PENALTY once per
    # direction (8x) and report non-zero for tiles with no enemy damage. Use
    # per-direction max for filtering — matches get_best_direction's pick.
    max_score = 0
    for d in range(8):
        if sentinel_masks[d]:
            s = _max_score_in_mask(sent_planes_by_dir[d], sentinel_masks[d])
            if s > max_score:
                max_score = s
        if gun_planes_by_dir is not None and gunner_masks[d]:
            g = _max_score_in_mask(gun_planes_by_dir[d], gunner_masks[d])
            if g > max_score:
                max_score = g

    global _round_cache_threshold
    _round_cache_threshold = 0
    if max_score < MIN_ATTACK_SCORE:
        return 0, 0
    if max_score > 0:
        # THREAT_PENALTY is baked into every non-threat tile as a flat bonus;
        # a tile whose ONLY contribution is that bonus has 0 real enemy damage.
        # Require threshold > THREAT_PENALTY to exclude those.
        threshold = max(int(max_score * SCORE_THRESHOLD_FACTOR), MIN_ATTACK_SCORE, THREAT_PENALTY + 1)
        _round_cache_threshold = threshold
        keep = 0
        for d in range(8):
            if sentinel_masks[d]:
                keep |= _ge_threshold_mask(sent_planes_by_dir[d], threshold, sentinel_masks[d])
            if gun_planes_by_dir is not None and gunner_masks[d]:
                keep |= _ge_threshold_mask(gun_planes_by_dir[d], threshold, gunner_masks[d])
        filtered &= keep
        if not filtered:
            return 0, 0

    my_team_idx = map_info._my_team_idx
    enemy_idx = 1 - my_team_idx
    enemy_roads = (map_info._bm_et[map_info._IDX_ROAD] & map_info._bm_team[enemy_idx]) | ~map_info._bm_seen_observed

    roaded = filtered & enemy_roads
    non_roaded = filtered & ~enemy_roads
    # units.builder.draw_mask(non_roaded, 0, 255, 0)
    return non_roaded, roaded


# ---------------------------------------------------------------------------
# Round cache
# ---------------------------------------------------------------------------

_round_cache_round = -1
_round_cache_attack_candidates = (0, 0)
_round_cache_sentinel_planes = None    # list of 8 plane-lists, one per direction
_round_cache_gunner_planes = None      # list of 8 plane-lists, one per direction
_round_cache_threshold = 0
_round_cache_placement_masks = [None, None]  # [sentinel_masks[8], gunner_masks[8]]


def _ensure_round_cache():
    global _round_cache_round, _round_cache_attack_candidates
    global _round_cache_sentinel_planes, _round_cache_gunner_planes
    r = rc.get_current_round()
    if _round_cache_round == r:
        return
    _round_cache_round = r
    _round_cache_sentinel_planes = None
    _round_cache_gunner_planes = None
    _round_cache_attack_candidates = _get_attack_candidates()
    if DRAW_DEBUG:
        non_roaded, roaded = _round_cache_attack_candidates
        if non_roaded | roaded:
            _draw_attack_candidates(non_roaded | roaded)


def _ensure_score_planes():
    """Lazily build sentinel and gunner planes once per round. Requires the
    placement masks to already be populated in _round_cache_placement_masks."""
    global _round_cache_sentinel_planes, _round_cache_gunner_planes
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
    _round_cache_gunner_planes = _compute_gunner_dir_scores(
        enemy_team_bm, threat, gunner_masks
    )


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
        direction, turret_type, _ = get_best_direction(Position(x, y))
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
    non_roaded, roaded = _round_cache_attack_candidates
    combined = non_roaded | roaded
    claimed = pathing.voronoi_claim(my_mask, units.builder.claimed_senders[comm_flag], combined)
    return claimed & non_roaded, claimed & roaded


_cached_claims = (0, 0)

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

    zone = 1 << (map_info._my_pos.x + map_info._my_pos.y * width)
    zone = map_info.expand_chebyshev(map_info.expand_chebyshev(zone))
    enemy_bot_nearby = bool(map_info._bm_enemy_bots & zone)

    if is_enemy_road:
        nav.move_to(best)
        if rc.can_fire(best):
            if not enemy_bot_nearby or rc.get_hp(best_id) <= 2:
                rc.fire(best)
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
                log(f"Attack destroy own building at {best}")
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
import map_info
# import pathing
# from pathing import Pathing
# import comms
# import units.builder
# from cambc import *
# from log import log


# rc: Controller = None
# nav: Pathing = None

# comm_flag = 6


# def init(c: Controller):
#     global rc, nav
#     rc = c
#     nav = Pathing(rc)


# BUILDING_SCORE = [0] * map_info._NUM_ET
# BUILDING_SCORE[map_info._IDX_CORE] = 100
# BUILDING_SCORE[map_info._IDX_HARVESTER] = 10
# BUILDING_SCORE[map_info._IDX_FOUNDRY] = 15
# BUILDING_SCORE[map_info._IDX_GUNNER] = 20
# BUILDING_SCORE[map_info._IDX_SENTINEL] = 20
# BUILDING_SCORE[map_info._IDX_BREACH] = 25
# BUILDING_SCORE[map_info._IDX_LAUNCHER] = 15
# BUILDING_SCORE[map_info._IDX_CONVEYOR] = 2
# BUILDING_SCORE[map_info._IDX_ARMOURED_CONVEYOR] = 3
# BUILDING_SCORE[map_info._IDX_BARRIER] = 1
# BUILDING_SCORE[map_info._IDX_BRIDGE] = 2
# BUILDING_SCORE[map_info._IDX_SPLITTER] = 2


# def _get_loaders(pos):
#     """Return list of direction indices (0-7) from pos toward buildings that feed it."""
#     w = map_info._width
#     h = map_info._height
#     px, py = pos.x, pos.y
#     pos_n = px + py * w
#     loaders = []

#     harvesters = map_info._bm_et[map_info._IDX_HARVESTER]
#     conveyors = (map_info._bm_et[map_info._IDX_CONVEYOR]
#                  | map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR])

#     # Cardinal-adjacent harvesters
#     for di, (dx, dy) in zip([0, 2, 4, 6], [(0, -1), (1, 0), (0, 1), (-1, 0)]):
#         nx, ny = px + dx, py + dy
#         if 0 <= nx < w and 0 <= ny < h:
#             if harvesters & (1 << (nx + ny * w)):
#                 loaders.append(di)

#     # Any neighbor conveyor whose output targets this tile
#     for di in range(8):
#         dx, dy = map_info._DIR_VECS[di]
#         nx, ny = px + dx, py + dy
#         if 0 <= nx < w and 0 <= ny < h:
#             nn = nx + ny * w
#             if (conveyors & (1 << nn)) and map_info._building_conv_target[nn] == pos_n:
#                 if di not in loaders:
#                     loaders.append(di)

#     return loaders


# def get_best_direction(pos):
#     """Pick the best (direction, turret_type) for a turret at pos.
#     Blocked: turret cannot face toward a loading building.
#     Exception: gunner with 2+ loaders can face any direction.
#     Score = sum of BUILDING_SCORE for enemy buildings the turret can hit."""
#     w = map_info._width
#     h = map_info._height
#     px, py = pos.x, pos.y

#     my_team_idx = map_info._my_team_idx
#     enemy_buildings = map_info._bm_team[1 - my_team_idx]
#     my_buildings = map_info._bm_team[my_team_idx]
#     walls = map_info._bm_env[map_info._IDX_ENV_WALL]

#     loaders = _get_loaders(pos)
#     loader_dirs = set(loaders)
#     sentinel_blocked = loader_dirs
#     breach_blocked = loader_dirs
#     gunner_blocked = set() if len(loaders) >= 2 else loader_dirs

#     my_foundries = map_info._bm_et[map_info._IDX_FOUNDRY] & my_buildings
#     adj_foundry = False
#     for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
#         nx, ny = px + dx, py + dy
#         if 0 <= nx < w and 0 <= ny < h and (my_foundries & (1 << (nx + ny * w))):
#             adj_foundry = True
#             break

#     best_b_dir, best_b_score = Direction.NORTH, -1
#     best_s_dir, best_s_score = Direction.NORTH, -1
#     best_g_dir, best_g_score = Direction.NORTH, -1

#     for di in range(8):
#         # Breach score
#         if di not in breach_blocked:
#             core_counted = False
#             b_score = 0
#             for dx, dy in map_info._BREACH_OFFSETS[di]:
#                 sx, sy = px + dx, py + dy
#                 if 0 <= sx < w and 0 <= sy < h:
#                     sbit = 1 << (sx + sy * w)
#                     if enemy_buildings & sbit:
#                         et_idx = map_info._building_et_idx[sx + sy * w]
#                         if et_idx >= 0 and (not core_counted or et_idx != map_info._IDX_CORE):
#                             b_score += BUILDING_SCORE[et_idx]
#                             if et_idx == map_info._IDX_CORE:
#                                 core_counted = True
#             if b_score > best_b_score:
#                 best_b_score = b_score
#                 best_b_dir = map_info._DIRECTIONS[di]

#         # Sentinel score
#         if di not in sentinel_blocked:
#             core_counted = False
#             s_score = 0
#             for dx, dy in map_info._SENTINEL_OFFSETS[di]:
#                 sx, sy = px + dx, py + dy
#                 if 0 <= sx < w and 0 <= sy < h:
#                     sbit = 1 << (sx + sy * w)
#                     if enemy_buildings & sbit:
#                         et_idx = map_info._building_et_idx[sx + sy * w]
#                         if et_idx >= 0 and (not core_counted or et_idx != map_info._IDX_CORE):
#                             s_score += BUILDING_SCORE[et_idx]
#                             if et_idx == map_info._IDX_CORE:
#                                 core_counted = True
#             if s_score > best_s_score:
#                 best_s_score = s_score
#                 best_s_dir = map_info._DIRECTIONS[di]

#         # Gunner score — single ray, wall/friendly-blocked
#         if di not in gunner_blocked:
#             g_score = 0
#             for dx, dy in map_info._GUNNER_RAYS[di]:
#                 sx, sy = px + dx, py + dy
#                 if not (0 <= sx < w and 0 <= sy < h):
#                     break
#                 sbit = 1 << (sx + sy * w)
#                 if walls & sbit:
#                     break
#                 if my_buildings & sbit:
#                     if not map_info._bm_et[map_info._IDX_ROAD] & sbit:
#                         break
#                 if enemy_buildings & sbit:
#                     et_idx = map_info._building_et_idx[sx + sy * w]
#                     if et_idx >= 0:
#                         g_score += BUILDING_SCORE[et_idx]
#             g_score *= 5
#             if g_score > best_g_score:
#                 best_g_score = g_score
#                 best_g_dir = map_info._DIRECTIONS[di]

#     if adj_foundry:
#         if best_b_score > 0:
#             return best_b_dir, EntityType.BREACH, best_b_score
#         if best_s_score > 0:
#             return best_s_dir, EntityType.SENTINEL, best_s_score
#         return best_g_dir, EntityType.GUNNER, best_g_score

#     if best_s_score >= best_g_score:
#         return best_s_dir, EntityType.SENTINEL, best_s_score
#     return best_g_dir, EntityType.GUNNER, best_g_score


# def _my_turret_coverage():
#     """Bitmask of all tiles my turrets can attack (regardless of ammo)."""
#     my_team_idx = map_info._my_team_idx
#     my_team_bm = map_info._bm_team[my_team_idx]
#     w = map_info._width
#     h = map_info._height
#     coverage = 0

#     for turret_idx, offsets_table in ((map_info._IDX_BREACH, map_info._BREACH_OFFSETS),
#                                       (map_info._IDX_SENTINEL, map_info._SENTINEL_OFFSETS)):
#         turrets = map_info._bm_et[turret_idx] & my_team_bm
#         if not turrets:
#             continue
#         dir_masks = [0] * 8
#         m = turrets
#         while m:
#             lsb = m & -m
#             n = lsb.bit_length() - 1
#             di = map_info._building_dir[n]
#             dir_masks[di] |= lsb
#             m ^= lsb
#         for di in range(8):
#             dm = dir_masks[di]
#             if not dm:
#                 continue
#             for dx, dy in offsets_table[di]:
#                 shift_mask = map_info._turret_shift_masks.get((dx, dy))
#                 if shift_mask is None:
#                     continue
#                 offset = dx + dy * w
#                 if offset > 0:
#                     coverage |= (dm & shift_mask) << offset
#                 else:
#                     coverage |= (dm & shift_mask) >> (-offset)

#     gunners = map_info._bm_et[map_info._IDX_GUNNER] & my_team_bm
#     if gunners:
#         walls = map_info._bm_env[map_info._IDX_ENV_WALL]
#         m = gunners
#         while m:
#             lsb = m & -m
#             n = lsb.bit_length() - 1
#             px = n % w
#             py = n // w
#             for ray_di in range(8):
#                 for dx, dy in map_info._GUNNER_RAYS[ray_di]:
#                     nx, ny = px + dx, py + dy
#                     if not (0 <= nx < w and 0 <= ny < h):
#                         break
#                     bit = 1 << (nx + ny * w)
#                     if walls & bit:
#                         break
#                     coverage |= bit
#             m ^= lsb

#     return coverage


# def _high_value_targets():
#     """Bitmask of enemy high-value buildings not already covered by my turrets."""
#     my_team_idx = map_info._my_team_idx
#     enemy_idx = 1 - my_team_idx
#     enemy = map_info._bm_team[enemy_idx]

#     high_value = (
#         map_info._bm_et[map_info._IDX_FOUNDRY]
#         | map_info._bm_et[map_info._IDX_GUNNER]
#         | map_info._bm_et[map_info._IDX_SENTINEL]
#         | map_info._bm_et[map_info._IDX_BREACH]
#         | map_info._bm_et[map_info._IDX_CORE]
#         | map_info._bm_et[map_info._IDX_LAUNCHER]
#         | map_info._bm_et[map_info._IDX_HARVESTER]
#     ) & enemy
#     if not high_value:
#         return 0

#     my_coverage = _my_turret_coverage()
#     return high_value & ~my_coverage


# def _placement_candidates():
#     """Bitmask of tiles where a turret could be placed."""
#     my_team_idx = map_info._my_team_idx
#     enemy_idx = 1 - my_team_idx
#     my_team = map_info._bm_team[my_team_idx]
#     enemy_team = map_info._bm_team[enemy_idx]

#     # Location filter: conveyor outputs + cardinal adj to harvesters
#     candidates = map_info._bm_ti_fed | map_info._bm_ax_fed
#     harvesters = (map_info._bm_et[map_info._IDX_HARVESTER]&map_info._bm_env[map_info._IDX_ENV_ORE_TI]) | map_info._bm_et[map_info._IDX_FOUNDRY]  # double for safety margin
#     if harvesters:
#         candidates |= map_info.expand_manhattan(harvesters)

#     # Tile content filter: empty, or clearable
#     empty = ~map_info._bm_any_building

#     my_clearable = (
#         map_info._bm_et[map_info._IDX_BARRIER]
#         | map_info._bm_et[map_info._IDX_ROAD]
#         | map_info._bm_et[map_info._IDX_MARKER]
#     ) & my_team

#     enemy_clearable = (
#         map_info._bm_et[map_info._IDX_MARKER]
#         | map_info._bm_et[map_info._IDX_ROAD]
#     ) & enemy_team

#     candidates &= (empty | my_clearable | enemy_clearable)

#     # Exclusions
#     candidates &= ~map_info._bm_env[map_info._IDX_ENV_WALL]

#     # Exclude tiles with any builder bots (except me)
#     my_bit = 1 << (map_info._my_pos.x + map_info._my_pos.y * map_info._width)
#     all_bots = (map_info._bm_friendly_bots | map_info._bm_enemy_bots) & ~my_bit
#     candidates &= ~all_bots

#     # Avoid enemy builder bots within 6 manhattan — only for enemy road candidates
#     enemy_bots = map_info._bm_enemy_bots
#     if enemy_bots:
#         danger = enemy_bots
#         for _ in range(6):
#             danger = map_info.expand_manhattan(danger)
#         enemy_roads = map_info._bm_et[map_info._IDX_ROAD] & enemy_team
#         candidates &= ~(danger & enemy_roads)

#     return candidates


# def _sentinel_all_offsets():
#     """Union of all sentinel offsets across all 8 directions as (dx, dy) set."""
#     offsets = set()
#     for di in range(8):
#         for dx, dy in map_info._SENTINEL_OFFSETS[di]:
#             offsets.add((dx, dy))
#     return offsets

# _sentinel_all_reach_cache = None

# def _sentinel_all_reach(targets):
#     """Bitmask of positions from which a sentinel (any direction) could hit at least one target.
#     Uses reverse-shift of the union of all direction offsets."""
#     global _sentinel_all_reach_cache
#     if _sentinel_all_reach_cache is None:
#         _sentinel_all_reach_cache = list(_sentinel_all_offsets())
#     w = map_info._width
#     reachable = 0
#     for dx, dy in _sentinel_all_reach_cache:
#         rdx, rdy = -dx, -dy
#         shift_mask = map_info._turret_shift_masks.get((rdx, rdy))
#         if shift_mask is None:
#             continue
#         offset = rdx + rdy * w
#         if offset > 0:
#             reachable |= (targets & shift_mask) << offset
#         else:
#             reachable |= (targets & shift_mask) >> (-offset)
#     return reachable


# def _get_attack_candidates():
#     """Return (non_roaded, roaded) candidate bitmasks."""
#     candidates = _placement_candidates()
#     if not candidates:
#         return 0, 0

#     targets = _high_value_targets()
#     if not targets:
#         return 0, 0

#     # Filter to candidates that can hit at least one target in some direction
#     reachable = _sentinel_all_reach(targets)
#     filtered = candidates & reachable

#     if not filtered:
#         return 0, 0

#     # Split into non-enemy-roaded vs enemy-roaded
#     my_team_idx = map_info._my_team_idx
#     enemy_idx = 1 - my_team_idx
#     enemy_roads = map_info._bm_et[map_info._IDX_ROAD] & map_info._bm_team[enemy_idx]

#     roaded = filtered & enemy_roads
#     non_roaded = filtered & ~enemy_roads

#     return non_roaded, roaded


# def _my_claims():
#     w = map_info._width
#     my_mask = 1 << (map_info._my_pos.x + map_info._my_pos.y * w)
#     non_roaded, roaded = _get_attack_candidates()
#     combined = non_roaded | roaded
#     claimed = pathing.voronoi_claim(my_mask, units.builder.claimed_senders[comm_flag], combined)
#     return claimed & non_roaded, claimed & roaded

# _cached_claims = (0, 0)  # set by score(), reused by run()
# MAX_SCORE = 6
# def score():
#     global _cached_claims
#     if rc.get_global_resources()[0] < rc.get_sentinel_cost()[0]:
#         _cached_claims = (0, 0)
#         return 0
#     _cached_claims = _my_claims()
#     non_roaded, roaded = _cached_claims
#     return 6 if (non_roaded or roaded) else 0


# def run():
#     log("ATTACK")
#     non_roaded, roaded = _cached_claims

#     if not non_roaded and not roaded:
#         return

#     width = map_info._width
#     my_team_idx = map_info._my_team_idx
#     candidates = non_roaded | roaded

#     # Evaluate all adjacent candidate tiles and pick highest scoring
#     my_pos = map_info._my_pos
#     best = None
#     best_score = -1
#     best_direction = Direction.NORTH
#     best_turret_type = EntityType.SENTINEL
#     best_is_enemy_road = False

#     enemy_roads = map_info._bm_et[map_info._IDX_ROAD] & map_info._bm_team[1 - my_team_idx]

#     mask = candidates
#     while mask:
#         lsb = mask & -mask
#         n = lsb.bit_length() - 1
#         px, py = n % width, n // width
#         if max(abs(px - my_pos.x), abs(py - my_pos.y)) <= 1:
#             pos = Position(px, py)
#             direction, turret_type, dir_score = get_best_direction(pos)
#             # Prefer non-roaded tiles
#             is_er = bool(enemy_roads & lsb)
#             adj_score = (0 if is_er else 1, dir_score)
#             if adj_score > (0 if best_is_enemy_road else 1, best_score):
#                 best = pos
#                 best_score = dir_score
#                 best_direction = direction
#                 best_turret_type = turret_type
#                 best_is_enemy_road = is_er
#         mask ^= lsb

#     if best is None:
#         # No adjacent candidates, move toward closest
#         if non_roaded:
#             best, _ = nav.closest(non_roaded)
#         if best is None and roaded:
#             best, _ = nav.closest(roaded)
#         if best is None:
#             return
#         best_direction, best_turret_type, _ = get_best_direction(best)
#         best_n = best.x + best.y * width
#         best_is_enemy_road = bool(enemy_roads & (1 << best_n))

#     best_n = best.x + best.y * width
#     best_bit = 1 << best_n
#     best_id = map_info._building_id[best_n]
#     is_mine = bool(map_info._bm_team[my_team_idx] & best_bit)

#     direction = best_direction
#     turret_type = best_turret_type
#     is_enemy_road = best_is_enemy_road
#     log(f"Attack: best={best}, dir={direction}, type={turret_type}, enemy_road={is_enemy_road}")

#     my_team = map_info._my_team

#     count = 0
#     for uid in rc.get_nearby_units(4):
#         if rc.get_entity_type(uid) != map_info._ET_BUILDER_BOT or rc.get_team(uid) == my_team:
#             continue
#         count += 1

#     if is_enemy_road:
#         # Move onto enemy road, fire it, step off
#         nav.move_to(best)
#         if rc.can_fire(best):
#             if count == 0 or rc.get_hp(best_id) <= 2: # bait them to move away
#                 rc.fire(best)
#         for d in map_info._ALL_DIRECTIONS:
#             if d == Direction.CENTRE:
#                 continue
#             if rc.can_move(d):
#                 rc.move(d)
#                 map_info.update_move()
#                 break
#     else:
#         # Move adjacent and destroy own building if needed
#         nav.move_adjacent(best)
#         if best_id and is_mine:
#             if rc.can_destroy(best) and rc.get_action_cooldown() == 0:
#                 log(f"Attack destroy own building at {best}")
#                 rc.destroy(best)
#                 map_info.update_at(best)

#     # Place turret
#     if turret_type == EntityType.GUNNER:
#         if rc.can_build_gunner(best, direction):
#             rc.build_gunner(best, direction)
#             map_info.update_at(best)
#     elif turret_type == EntityType.BREACH:
#         if rc.can_build_breach(best, direction):
#             rc.build_breach(best, direction)
#             map_info.update_at(best)
#     else:
#         if rc.can_build_sentinel(best, direction):
#             rc.build_sentinel(best, direction)
#             map_info.update_at(best)

#     comms.mark(best.x + best.y * map_info._width, comm_flag)
