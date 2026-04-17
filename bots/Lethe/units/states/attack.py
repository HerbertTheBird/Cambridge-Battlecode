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
BUILDING_SCORE[map_info._IDX_CORE] = 100
BUILDING_SCORE[map_info._IDX_HARVESTER] = 10
BUILDING_SCORE[map_info._IDX_FOUNDRY] = 15
BUILDING_SCORE[map_info._IDX_GUNNER] = 20
BUILDING_SCORE[map_info._IDX_SENTINEL] = 20
BUILDING_SCORE[map_info._IDX_BREACH] = 25
BUILDING_SCORE[map_info._IDX_LAUNCHER] = 15
BUILDING_SCORE[map_info._IDX_CONVEYOR] = 2
BUILDING_SCORE[map_info._IDX_ARMOURED_CONVEYOR] = 3
BUILDING_SCORE[map_info._IDX_BARRIER] = 1
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
SCORE_THRESHOLD_FACTOR = 0.2

# Attack state refuses to enter at all if the global best sentinel/breach score
# across candidates is below this. Tune up to make attack pickier.
MIN_ATTACK_SCORE = 10


def _add_const_to_planes(planes, c, mask):
    """Bit-sliced: add constant `c` to counters at every set bit of `mask`."""
    if not mask or not c:
        return
    i = 0
    while c and i < _NUM_PLANES:
        if c & 1:
            # Add 2^i to counters at `mask` — XOR, carry propagates up.
            carry = planes[i] & mask
            planes[i] ^= mask
            j = i + 1
            while carry and j < _NUM_PLANES:
                new_carry = planes[j] & carry
                planes[j] ^= carry
                carry = new_carry
                j += 1
        c >>= 1
        i += 1


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


def _compute_dir_scores(offsets_table, enemy_team_bm):
    """For each of 8 facing directions, compute per-tile turret score planes.
    Uses `_turret_shift_masks` to move enemy-building masks by (-dx, -dy)
    so each tile's planes sum its attackable enemies' BUILDING_SCOREs.
    Core reach is OR'd (counted once per position) to match `core_counted`."""
    w = map_info._width
    shift_masks = map_info._turret_shift_masks
    bm_et = map_info._bm_et
    core_mask = bm_et[map_info._IDX_CORE] & enemy_team_bm
    core_score = BUILDING_SCORE[map_info._IDX_CORE]

    # Pre-mask type bitmasks once; skip types with no enemy presence.
    type_masks = []
    for t_idx, s in _SCORED_NON_CORE_TYPES:
        bm_t = bm_et[t_idx] & enemy_team_bm
        if bm_t:
            type_masks.append((bm_t, s))

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
            for bm_t, s in type_masks:
                masked = bm_t & sm
                if not masked:
                    continue
                if rev_off >= 0:
                    contrib = masked << rev_off
                else:
                    contrib = masked >> (-rev_off)
                _add_const_to_planes(planes, s, contrib)
        if core_reach:
            _add_const_to_planes(planes, core_score, core_reach)
        all_planes.append(planes)
    return all_planes


def _compute_loader_blockers():
    """Per-direction bitmask of tiles where a loader occupies that direction,
    so a turret at that tile can't face that direction (sentinel/breach rules)."""
    w = map_info._width
    bm_et = map_info._bm_et
    shift_masks = map_info._turret_shift_masks
    dir_vecs = map_info._DIR_VECS

    harvesters = bm_et[map_info._IDX_HARVESTER]
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
    enemy_buildings = map_info._bm_team[1 - my_team_idx]
    my_buildings = map_info._bm_team[my_team_idx]
    walls = map_info._bm_env[map_info._IDX_ENV_WALL]

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
    blockers = _round_cache_loader_blockers
    # Per-tile loader info from cached blocker masks — replaces _get_loaders.
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
    building_et_idx = map_info._building_et_idx
    gunner_rays = map_info._GUNNER_RAYS
    road_mask = map_info._bm_et[map_info._IDX_ROAD]

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

        # Gunner: single ray, wall/friendly-blocked — still per-tile.
        if gunner_allows_all or not direction_blocked:
            g_score = 0
            for dx, dy in gunner_rays[di]:
                sx, sy = px + dx, py + dy
                if not (0 <= sx < w and 0 <= sy < h):
                    break
                tile_n = sx + sy * w
                sbit = 1 << tile_n
                if walls & sbit:
                    break
                if my_buildings & sbit:
                    if not road_mask & sbit:
                        break
                if enemy_buildings & sbit:
                    et_idx = building_et_idx[tile_n]
                    if et_idx >= 0:
                        g_score += BUILDING_SCORE[et_idx]
            g_score *= 5
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


def _my_turret_coverage():
    """Bitmask of all tiles my turrets can attack (regardless of ammo)."""
    my_team_idx = map_info._my_team_idx
    my_team_bm = map_info._bm_team[my_team_idx]
    w = map_info._width
    h = map_info._height
    coverage = 0

    for turret_idx, offsets_table in ((map_info._IDX_BREACH, map_info._BREACH_OFFSETS),
                                      (map_info._IDX_SENTINEL, map_info._SENTINEL_OFFSETS)):
        turrets = map_info._bm_et[turret_idx] & my_team_bm
        if not turrets:
            continue
        dir_masks = [0] * 8
        m = turrets
        while m:
            lsb = m & -m
            n = lsb.bit_length() - 1
            di = map_info._building_dir[n]
            dir_masks[di] |= lsb
            m ^= lsb
        for di in range(8):
            dm = dir_masks[di]
            if not dm:
                continue
            for dx, dy in offsets_table[di]:
                shift_mask = map_info._turret_shift_masks.get((dx, dy))
                if shift_mask is None:
                    continue
                offset = dx + dy * w
                if offset > 0:
                    coverage |= (dm & shift_mask) << offset
                else:
                    coverage |= (dm & shift_mask) >> (-offset)

    gunners = map_info._bm_et[map_info._IDX_GUNNER] & my_team_bm
    if gunners:
        walls = map_info._bm_env[map_info._IDX_ENV_WALL]
        m = gunners
        while m:
            lsb = m & -m
            n = lsb.bit_length() - 1
            px = n % w
            py = n // w
            for ray_di in range(8):
                for dx, dy in map_info._GUNNER_RAYS[ray_di]:
                    nx, ny = px + dx, py + dy
                    if not (0 <= nx < w and 0 <= ny < h):
                        break
                    bit = 1 << (nx + ny * w)
                    if walls & bit:
                        break
                    coverage |= bit
            m ^= lsb

    return coverage


def _high_value_targets():
    """Bitmask of enemy high-value buildings not already covered by my turrets."""
    my_team_idx = map_info._my_team_idx
    enemy_idx = 1 - my_team_idx
    enemy = map_info._bm_team[enemy_idx]

    high_value = (
        map_info._bm_et[map_info._IDX_FOUNDRY]
        | map_info._bm_et[map_info._IDX_GUNNER]
        | map_info._bm_et[map_info._IDX_SENTINEL]
        | map_info._bm_et[map_info._IDX_BREACH]
        | map_info._bm_et[map_info._IDX_CORE]
        | map_info._bm_et[map_info._IDX_LAUNCHER]
        | map_info._bm_et[map_info._IDX_HARVESTER]
    ) & enemy
    if not high_value:
        return 0

    my_coverage = _my_turret_coverage()
    return high_value & ~my_coverage


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

    # Avoid all tiles within 4 Chebyshev of any enemy builder bot.
    enemy_bots = map_info._bm_enemy_bots
    if enemy_bots:
        danger = enemy_bots
        for _ in range(4):
            danger = map_info.expand_chebyshev(danger)
        candidates &= ~danger

    return candidates


_sentinel_reverse_ops = None

def _init_sentinel_reverse_ops():
    """Precompute (shift_mask, offset) pairs for the reverse-shift over all unique sentinel offsets."""
    global _sentinel_reverse_ops
    w = map_info._width
    seen = set()
    ops = []
    for di in range(8):
        for dx, dy in map_info._SENTINEL_OFFSETS[di]:
            rdx, rdy = -dx, -dy
            if (rdx, rdy) in seen:
                continue
            seen.add((rdx, rdy))
            sm = map_info._turret_shift_masks.get((rdx, rdy))
            if sm is None:
                continue
            ops.append((sm, rdx + rdy * w))
    _sentinel_reverse_ops = ops


def _sentinel_all_reach(targets):
    """Bitmask of positions from which a sentinel (any direction) could hit at least one target."""
    if _sentinel_reverse_ops is None:
        _init_sentinel_reverse_ops()
    reachable = 0
    for sm, offset in _sentinel_reverse_ops:
        masked = targets & sm
        if offset > 0:
            reachable |= masked << offset
        else:
            reachable |= masked >> (-offset)
    return reachable


def _get_attack_candidates():
    """Return (non_roaded, roaded) candidate bitmasks."""
    candidates = _placement_candidates()
    if not candidates:
        return 0, 0

    targets = _high_value_targets()
    if not targets:
        return 0, 0

    # Filter to candidates that can hit at least one target in some direction
    reachable = _sentinel_all_reach(targets)
    filtered = candidates & reachable

    if not filtered:
        return 0, 0

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
    _ensure_score_planes()
    sent_planes = _round_cache_sentinel_planes
    brch_planes = _round_cache_breach_planes  # may be None
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
    if max_score < MIN_ATTACK_SCORE:
        return 0, 0
    if max_score > 0:
        threshold = int(max_score * SCORE_THRESHOLD_FACTOR)
        keep = 0
        for d in range(8):
            allowed = filtered & ~blockers[d]
            if not allowed:
                continue
            keep |= _ge_threshold_mask(sent_planes[d], threshold, allowed)
            if brch_planes is not None:
                keep |= _ge_threshold_mask(brch_planes[d], threshold, allowed)
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
_round_cache_loader_blockers = None
_round_cache_need_breach = False

def _ensure_round_cache():
    global _round_cache_round, _round_cache_attack_candidates
    global _round_cache_sentinel_planes, _round_cache_breach_planes, _round_cache_loader_blockers
    global _round_cache_need_breach
    r = rc.get_current_round()
    if _round_cache_round == r:
        return
    _round_cache_round = r
    # Invalidate planes first; _get_attack_candidates may re-populate them
    # as part of the threshold filter.
    _round_cache_sentinel_planes = None
    _round_cache_breach_planes = None
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
    """Debug: draw a dot on each candidate tile and a length-1 line
    toward its best non-blocked sentinel/breach direction."""
    w = map_info._width
    dir_vecs = map_info._DIR_VECS
    blockers = _round_cache_loader_blockers
    m = mask
    while m:
        lsb = m & -m
        n = lsb.bit_length() - 1
        x, y = n % w, n // w
        best_d = -1
        best_score = -1
        for d in range(8):
            if blockers[d] & lsb:
                continue
            s = _read_score(sent_planes[d], n)
            if s > best_score:
                best_score = s
                best_d = d
            if brch_planes is not None:
                b = _read_score(brch_planes[d], n)
                if b > best_score:
                    best_score = b
                    best_d = d
        pos = Position(x, y)
        rc.draw_indicator_dot(pos, 255, 0, 0)
        if best_d >= 0:
            dx, dy = dir_vecs[best_d]
            end = Position(x + dx, y + dy)
            if map_info.in_bounds(end):
                rc.draw_indicator_line(pos, end, 255, 0, 0)
        m ^= lsb


def _ensure_score_planes():
    """Lazily build the per-direction sentinel & breach score planes once per round.
    Breach planes only built if at least one candidate is adjacent to a friendly
    foundry (otherwise breach never wins in `get_best_direction`)."""
    global _round_cache_sentinel_planes, _round_cache_breach_planes
    if _round_cache_sentinel_planes is not None:
        return
    enemy_team_bm = map_info._bm_team[1 - map_info._my_team_idx]
    _round_cache_sentinel_planes = _compute_dir_scores(map_info._SENTINEL_OFFSETS, enemy_team_bm)
    if _round_cache_need_breach:
        _round_cache_breach_planes = _compute_dir_scores(map_info._BREACH_OFFSETS, enemy_team_bm)
    else:
        _round_cache_breach_planes = None


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
    return 6 if (non_roaded or roaded) else 0


def run():
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
        # No adjacent candidates, move toward closest
        best, _ = nav.closest(non_roaded | roaded)
        if best is None:
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
