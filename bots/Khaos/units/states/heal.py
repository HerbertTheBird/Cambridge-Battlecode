import map_info
import units.builder
from cambc import *
from log import log

rc: Controller = None
nav = None

CONV_CHASE_CHEB = 8
ID_MASK = (1 << 12) - 1


def init(c: Controller):
    global rc, nav
    rc = c
    nav = units.builder.nav


def _conv_zone():
    # return units.builder._harvest_zone
    """Bitmask of tiles within CONV_CHASE_CHEB pathing distance of my conveyors."""
    my_team_idx = map_info._my_team_idx
    my_convs = map_info._bm_conveyors & map_info._bm_team[my_team_idx]
    my_convs |= map_info._bm_my_core_area
    if not my_convs:
        return 0
    w = map_info._width
    board = (1 << (w * map_info._height)) - 1
    avoid = map_info._bm_blocked
    passable = ~avoid & board
    nlc = map_info._not_left_col
    nrc = map_info._not_right_col
    visited = my_convs
    frontier = my_convs
    for _ in range(CONV_CHASE_CHEB):
        h = frontier | ((frontier & nrc) << 1) | ((frontier & nlc) >> 1)
        expanded = h | (h << w) | (h >> w)
        frontier = expanded & passable & ~visited
        visited |= frontier
    return visited


_cached_launcher_t1 = None  # set by _find_chase_target when launcher fallback fires


def _turret_covered_mask():
    """Tiles already covered by friendly turrets — cheb-1 of any friendly
    launcher OR on a friendly gunner's current ray. Enemies on these tiles
    don't need a chase from us."""
    my_team_idx = map_info._my_team_idx
    my_launchers = map_info._bm_et[map_info._IDX_LAUNCHER] & map_info._bm_team[my_team_idx]
    launcher_cover = map_info.expand_chebyshev(my_launchers) if my_launchers else 0
    return launcher_cover | map_info._bm_my_gunner_claims


def _find_chase_target(damaged: bool = True):
    """Wrapper that adds the launcher fallback. If main logic finds no
    normally-reachable target on the damaged=True branch, look for an enemy
    on a damaged friendly conveyor where we can reach 'adjacent to a my-owned
    or empty tile adjacent to the enemy', so a launcher placed at that tile
    can throw the enemy away."""
    global _cached_launcher_t1
    if damaged:
        _cached_launcher_t1 = None
    target = _find_chase_target_main(damaged)
    if target is not None:
        return target
    if not damaged:
        return None
    return _try_launcher_fallback()


def _try_launcher_fallback():
    """Find (enemy, T1) where:
      - enemy stands on a damaged friendly conveyor or bridge,
      - T1 is cheb-1 of enemy and is a marker (any team), my road, empty,
        or my conveyor (any type) — must be no_wall and no bots,
      - some T0 cheb-1 of T1 is reachable, with avoid applied.
    Sets _cached_launcher_t1 = T1 and returns the enemy pos."""
    global _cached_launcher_t1
    w = map_info._width
    my_team_idx = map_info._my_team_idx

    convs = (map_info._bm_et[map_info._IDX_CONVEYOR]
             | map_info._bm_et[map_info._IDX_BRIDGE]) & map_info._bm_team[my_team_idx]
    damaged_convs = convs & map_info._bm_damaged
    enemies = map_info._bm_enemy_bots & damaged_convs & ~_turret_covered_mask()
    if not enemies:
        return None

    # Filter enemies claimed by other friendly bots (matches _find_chase_target_main)
    my_bit = 1 << (map_info._my_pos.x + map_info._my_pos.y * w)
    enemy_zone_4 = map_info.expand_chebyshev(enemies, 4)
    mask = map_info._bm_friendly_bots & ~my_bit & map_info._bm_visible & enemy_zone_4
    while mask:
        lsb = mask & -mask
        friend_zone = map_info.expand_chebyshev(lsb, 4)
        nearby = enemies & friend_zone
        if nearby:
            closest = nav.closest_within(nearby, lsb, 4)
            if closest[0]:
                enemies ^= 1 << (closest[0].x + closest[0].y * w)
        mask ^= lsb
    if not enemies:
        log("launcher fallback: all enemies claimed")
        return None

    no_wall = ~map_info._bm_env[map_info._IDX_ENV_WALL]
    no_bots = ~map_info._bm_friendly_bots & ~map_info._bm_enemy_bots
    markers_any = map_info._bm_et[map_info._IDX_MARKER]
    my_road = map_info._bm_et[map_info._IDX_ROAD] & map_info._bm_team[my_team_idx]
    empty = ~map_info._bm_any_building & no_wall
    my_guard_convs = map_info._bm_guard_conveyor & map_info._bm_team[my_team_idx]
    avoid = map_info.get_avoid(False, False, False)
    t1_candidates = (markers_any | my_road | empty | my_guard_convs) & no_wall & no_bots

    passable = ~avoid

    def _mask_to_positions(mask):
        out = []
        m = mask
        while m:
            lsb = m & -m
            n = lsb.bit_length() - 1
            m ^= lsb
            out.append(Position(n % w, n // w))
        return out

    log("launcher fallback eval enemies:", _mask_to_positions(enemies))

    best_enemy = None
    best_t1 = None
    best_d = None
    em = enemies
    while em:
        elsb = em & -em
        en_n = elsb.bit_length() - 1
        em ^= elsb
        en_pos = Position(en_n % w, en_n // w)
        t1_mask = map_info.expand_chebyshev(elsb) & ~elsb & t1_candidates
        if not t1_mask:
            log("  enemy", en_pos, "no T1 candidates")
            continue
        log("  enemy", en_pos, "T1:", _mask_to_positions(t1_mask))
        t0_mask = map_info.expand_chebyshev(t1_mask) & ~t1_mask & passable
        if not t0_mask:
            log("    no T0 candidates (passable)")
            continue
        t0_pos, d = nav.closest_within(t0_mask, max_dist=8, avoid=avoid)
        if t0_pos is None:
            log("    no reachable T0")
            continue
        log("    closest T0", t0_pos, "dist", d)
        if best_d is not None and d >= best_d:
            log("    skipped (worse than best", best_d, ")")
            continue
        t0_n = t0_pos.x + t0_pos.y * w
        t1_neighbors = map_info.expand_chebyshev(1 << t0_n) & ~(1 << t0_n) & t1_mask
        if not t1_neighbors:
            log("    no T1 neighbor of T0")
            continue
        t1_lsb = t1_neighbors & -t1_neighbors
        t1_n = t1_lsb.bit_length() - 1
        best_enemy = en_pos
        best_t1 = Position(t1_n % w, t1_n // w)
        best_d = d
        log("    new best:", best_enemy, "T1", best_t1, "dist", best_d)

    if best_enemy is None:
        log("launcher fallback: no candidate found")
        return None
    _cached_launcher_t1 = best_t1
    log("launcher fallback selected", best_enemy, "via T1", best_t1, "dist", best_d)
    return best_enemy


def _find_chase_target_main(damaged: bool = True):
    # log("find chase")
    """Find an unclaimed enemy builder bot within conv zone. Returns (uid, pos) or None.

    When `damaged` is True, only consider enemies sitting on one of our
    very-damaged buildings; if none, retry with `damaged=False`."""
    w = map_info._width
    # Filter enemy bots in zone, unclaimed
    raw_enemies = map_info._bm_enemy_bots
    cover = _turret_covered_mask()
    units.builder.draw_mask(cover, 255, 0, 0)
    covered_enemies = raw_enemies & cover
    if covered_enemies:
        positions = []
        m = covered_enemies
        while m:
            lsb = m & -m
            n = lsb.bit_length() - 1
            m ^= lsb
            positions.append(Position(n % w, n // w))
        log("turret-covered enemies filtered:", positions)
    enemy_bots = raw_enemies & ~cover

    if not enemy_bots:
        log("no enemies")
        return None

    if damaged:
        enemy_bots = enemy_bots & _very_damaged_targets()
        if not enemy_bots:
            return _find_chase_target(damaged=False)

    friendly_bots = map_info._bm_friendly_bots
    my_bit = 1 << (map_info._my_pos.x + map_info._my_pos.y * w)
    other_friendly = friendly_bots & ~my_bit

    filtered = enemy_bots
    # Expand the enemy zone once and pre-filter the friendlies we iterate.
    # A friendly outside enemy_zone_4 has no enemy within 4 chebyshev, so
    # the per-friendly expansion below would be a no-op.
    enemy_zone_4 = map_info.expand_chebyshev(enemy_bots, 4)
    mask = friendly_bots & ~my_bit & map_info._bm_visible & enemy_zone_4

    while mask:
        lsb = mask & -mask
        n = lsb.bit_length() - 1
        friend_zone = map_info.expand_chebyshev(lsb, 4)
        nearby = filtered & friend_zone
        if not nearby:
            mask ^= lsb
            continue
        closest = nav.closest_within(nearby, lsb, 4)
        if closest[0]:
            log("filtering", closest[0], "because", n%w, n//2, closest[1])
            filtered ^= (1<<(closest[0].x+closest[0].y*w))
        # uid = map_info._bot_at.get(n)
        # if uid is not None:
        #     # if (uid & ID_MASK) not in claimed:
        #     #     filtered |= lsb
        #     # else:
        #     nearby_friendly = map_info.expand_chebyshev(lsb, 2) & other_friendly
        #     if not nearby_friendly:
        #         filtered |= lsb
        mask ^= lsb
    # log(map_info._bot_pos)
    # units.builder.draw_mask(enemy_bots, 255, 0, 0)
    # units.builder.draw_mask(friendly_bots, 0, 255, 0)

    if not filtered:
        filtered = enemy_bots
        return None
    nearby = filtered & map_info.expand_chebyshev(my_bit, 8)
    if not nearby:
        log("too far")
        return None
    # Enumerate all reachable enemies within max_dist=8 by repeatedly removing
    # the previous closest. Then tiebreak the minimum-BFS-distance set by
    # chebyshev distance to my conveyors (lowest priority — closer wins).
    remaining = nearby
    enumerated = []  # list of (bfs_dist, pos)
    while remaining:
        pos, d = nav.closest_within(remaining, max_dist=8)
        if pos is None:
            break
        enumerated.append((d, pos))
        remaining ^= 1 << (pos.x + pos.y * w)
    if not enumerated:
        log("no closest")
        return None
    min_d = min(d for d, _ in enumerated)
    tied = [p for d, p in enumerated if d == min_d]
    if len(tied) == 1:
        return tied[0]
    my_convs = map_info._bm_conveyors & map_info._bm_team[map_info._my_team_idx]
    best = None
    best_cd = None
    for p in tied:
        cd = _conv_dist(1 << (p.x + p.y * w), my_convs)
        if best is None or cd < best_cd:
            best = p
            best_cd = cd
    return best


def _healable_mask():
    """Bitmask of friendly buildings."""
    my_team_idx = map_info._my_team_idx
    return map_info._bm_team[my_team_idx]


def _mutual_sentinel_threat():
    """Bitmask of MY sentinels that can shoot an enemy sentinel which can also
    shoot them back. Treated as 'very damaged' for heal priority so we rush in
    to keep them alive through the trade. Sentinels already adjacent (cheb 1)
    to a friendly builder bot are excluded — they're already covered."""
    my_idx = map_info._my_team_idx
    enemy_idx = 1 - my_idx
    my_sents = map_info._bm_et[map_info._IDX_SENTINEL] & map_info._bm_team[my_idx]
    my_sents &= ~map_info.expand_chebyshev(map_info._bm_friendly_bots)
    enemy_sents = map_info._bm_et[map_info._IDX_SENTINEL] & map_info._bm_team[enemy_idx]
    if not my_sents or not enemy_sents:
        return 0
    w = map_info._width
    h = map_info._height
    bm_dir = map_info._bm_dir
    OFFSETS = map_info._SENTINEL_OFFSETS

    enemy_dir_at = {}
    m = enemy_sents
    while m:
        lsb = m & -m
        en = lsb.bit_length() - 1
        m ^= lsb
        for di in range(8):
            if bm_dir[di] & lsb:
                enemy_dir_at[en] = di
                break

    result = 0
    m = my_sents
    while m:
        lsb = m & -m
        mn = lsb.bit_length() - 1
        m ^= lsb
        my_x, my_y = mn % w, mn // w
        my_di = None
        for di in range(8):
            if bm_dir[di] & lsb:
                my_di = di
                break
        if my_di is None:
            continue
        attack_mask = 0
        for dx, dy in OFFSETS[my_di]:
            tx, ty = my_x + dx, my_y + dy
            if 0 <= tx < w and 0 <= ty < h:
                attack_mask |= 1 << (tx + ty * w)
        hit_enemies = attack_mask & enemy_sents
        if not hit_enemies:
            continue
        he = hit_enemies
        while he:
            elsb = he & -he
            en = elsb.bit_length() - 1
            he ^= elsb
            edi = enemy_dir_at.get(en)
            if edi is None:
                continue
            ex, ey = en % w, en // w
            for dx, dy in OFFSETS[edi]:
                if ex + dx == my_x and ey + dy == my_y:
                    result |= lsb
                    break
            if result & lsb:
                break
    return result


def _very_damaged_targets():
    """Bitmask of friendly buildings with > 2 damage, plus any friendly sentinel
    locked in a mutual-shot exchange with an enemy sentinel."""
    base = _healable_mask() & map_info._bm_very_damaged & ~map_info._bm_my_core_area & map_info._bm_visible
    return base | (_mutual_sentinel_threat() & map_info._bm_visible)


def _heal_targets():
    """Bitmask of friendly damaged buildings."""
    return _healable_mask() & map_info._bm_damaged & ~_very_damaged_targets()


_cached_chase_target = None  # set by score(), reused by run()

MAX_SCORE = 8
def score():
    # Always refresh chase target so run() uses a current value when it falls
    # through to the chase fallback (case 2 in run()). Previously this was
    # skipped when score=8 returned early, leaving a stale target across turns.
    global _cached_chase_target
    _cached_chase_target = _find_chase_target()

    if _very_damaged_targets():
        # units.builder.draw_mask(_very_damaged_targets(), 255, 0, 0)
        return 7

    target = _cached_chase_target
    # log(target)
    # units.builder.draw_mask(_conv_zone(), 255, 0, 0)
    if target is not None:
        if _conv_zone() & (1<<(target.x + target.y * map_info._width)):
            log("high priority heal", target)
            return 7
        else:
            log("low priority heal", target)
            return 2.5
    return 0


# def _try_barrier_dead_ends():
#     """Barrier any adjacent tiles that are dead-end conveyor targets."""
#     w = map_info._width
#     dead_ends = map_info._bm_dead_end
#     if not dead_ends:
#         return
#     # Only dead-end conveyors whose output is empty / marker / enemy building
#     my_team_idx = map_info._my_team_idx
#     enemy_idx = 1 - my_team_idx
#     enemy_any = map_info._bm_team[enemy_idx]
#     marker = map_info._bm_et[map_info._IDX_MARKER]
#     empty_mask = ~map_info._bm_any_building & ~map_info._bm_env[map_info._IDX_ENV_WALL]

#     targets = 0
#     mask = dead_ends
#     conv_target = map_info._building_conv_target
#     tiles = w * map_info._height
#     while mask:
#         lsb = mask & -mask
#         n = lsb.bit_length() - 1
#         tn = conv_target[n]
#         if tn and 0 <= tn < tiles:
#             tbit = 1 << tn
#             if (empty_mask & tbit) or (marker & tbit) or (enemy_any & tbit):
#                 targets |= lsb
#         mask ^= lsb
#     if not targets:
#         return
#     my_pos = map_info._my_pos
#     for d in map_info._DIRECTIONS:
#         dx, dy = map_info._DIRECTION_DELTAS[d]
#         p = Position(my_pos.x + dx, my_pos.y + dy)
#         if not map_info.in_bounds(p):
#             continue
#         pbit = 1 << (p.x + p.y * w)
#         if not (targets & pbit):
#             continue
#         if rc.get_action_cooldown() == 0:
#             if rc.can_destroy(p):
#                 rc.destroy(p)
#                 map_info.update_at(p)
#         if rc.can_build_barrier(p):
#             rc.build_barrier(p)
#             map_info.update_at(p)
#             return

_HEAL_PRIORITY = [1] * 16  # default low priority for unknown types
_HEAL_PRIORITY[map_info._IDX_ROAD] = 1
_HEAL_PRIORITY[map_info._IDX_BARRIER] = 2
_HEAL_PRIORITY[map_info._IDX_BRIDGE] = 2
_HEAL_PRIORITY[map_info._IDX_SPLITTER] = 2
_HEAL_PRIORITY[map_info._IDX_CONVEYOR] = 3
_HEAL_PRIORITY[map_info._IDX_ARMOURED_CONVEYOR] = 4
_HEAL_PRIORITY[map_info._IDX_HARVESTER] = 4
_HEAL_PRIORITY[map_info._IDX_FOUNDRY] = 4
_HEAL_PRIORITY[map_info._IDX_GUNNER] = 5
_HEAL_PRIORITY[map_info._IDX_SENTINEL] = 5
_HEAL_PRIORITY[map_info._IDX_BREACH] = 5
_HEAL_PRIORITY[map_info._IDX_LAUNCHER] = 5
_HEAL_PRIORITY[map_info._IDX_CORE] = 6


def _conv_dist(pbit: int, source: int, cap: int = 12) -> int:
    """Chebyshev distance from `source` to the tile bit `pbit` via slow
    iterated bitwise expansion. Returns `cap + 1` if not reached within cap."""
    if not source:
        return cap + 1
    if pbit & source:
        return 0
    cur = source
    for d in range(1, cap + 1):
        cur = map_info.expand_chebyshev(cur)
        if cur & pbit:
            return d
    return cap + 1


def _do_best_heal():
    """Heal the most-damaged adjacent friendly building. Mirrors the run-time
    pool ordering: tier 0 = _very_damaged_targets(), tier 1 = _heal_targets()
    (any other damaged friendly). Within a tier, tiebreak by
    damage * _HEAL_PRIORITY[et_idx]."""
    w = map_info._width
    h = map_info._height
    healable = _healable_mask() & map_info._bm_damaged
    very_damaged = _very_damaged_targets()
    best_heal = None
    best_tier = 99
    best_score = -1
    my_pos = map_info._my_pos
    my_x = my_pos.x
    my_y = my_pos.y
    for dx, dy in map_info._DIRECTION_DELTAS_I:
        x = my_x + dx
        y = my_y + dy
        if not (0 <= x < w and 0 <= y < h):
            continue
        n = x + y * w
        pbit = 1 << n
        if not (healable & pbit):
            continue
        p = Position(x, y)
        if not rc.can_heal(p):
            continue
        hp = map_info._building_hp[n]
        et_idx = map_info._building_et_idx[n]
        if et_idx < 0:
            continue
        damage = map_info._MAX_HP_BY_IDX[et_idx] - hp
        score = damage * _HEAL_PRIORITY[et_idx]
        tier = 0 if (pbit & very_damaged) else 1
        if tier < best_tier or (tier == best_tier and score > best_score):
            best_tier = tier
            best_score = score
            best_heal = p
    if best_heal is not None:
        log("heal: do_best_heal", best_heal, "tier", best_tier, "score", best_score)
        rc.heal(best_heal)


def _try_chase(target):
    """Run chase logic for `target`. Returns True if it took an action and
    run() should return."""
    w = map_info._width
    avoid = map_info.get_avoid(False, False, False)
    en_n = target.x + target.y * w
    adj = map_info.expand_chebyshev(1 << en_n) & ~(1 << en_n) & ~avoid
    reach_pos, _ = nav.closest_within(adj, max_dist=8, avoid=avoid)
    if reach_pos is not None:
        log("heal: chase target", target, "reachable via", reach_pos)
        nav.move_to(target)
        _do_best_heal()
        return True
    if _cached_launcher_t1 is not None:
        t1 = _cached_launcher_t1
        t1_n = t1.x + t1.y * w
        t0_zone = map_info.expand_chebyshev(1 << t1_n) & ~(1 << t1_n) & ~avoid
        t0_set = set()
        m = t0_zone
        while m:
            lsb = m & -m
            n = lsb.bit_length() - 1
            m ^= lsb
            t0_set.add(Position(n % w, n // w))
        log("heal: launcher fallback for", target, "via T1", t1, "T0 set", t0_set)
        if t0_set:
            nav.move_to(t0_set)
        my_pos = map_info._my_pos
        if my_pos.distance_squared(t1) <= 2 and my_pos != t1:
            # If T1 is a guard conveyor adjacent to a friendly harvester,
            # build a gunner facing the enemy instead of a launcher.
            my_team_idx = map_info._my_team_idx
            t1_bit = 1 << t1_n
            my_guard = map_info._bm_guard_conveyor & map_info._bm_team[my_team_idx]
            my_harvesters = (map_info._bm_et[map_info._IDX_HARVESTER]
                             & map_info._bm_team[my_team_idx])
            use_gunner = bool(t1_bit & my_guard) and bool(
                map_info.expand_chebyshev(t1_bit) & my_harvesters
            )
            if rc.can_destroy(t1):
                log("heal: destroying T1", t1)
                rc.destroy(t1)
                map_info.update_at(t1)
            if use_gunner:
                gunner_dir = t1.direction_to(target)
                if rc.can_build_gunner(t1, gunner_dir) and rc.get_global_resources()[0] >= rc.get_gunner_cost()[0] + map_info.builder_ti_reserve():
                    log("heal: building gunner at", t1, "facing", gunner_dir)
                    rc.build_gunner(t1, gunner_dir)
                    map_info.update_at(t1)
                else:
                    log("heal: gunner build blocked at", t1, "(can_build=", rc.can_build_gunner(t1, gunner_dir), "ti=", rc.get_global_resources()[0], "need=", rc.get_gunner_cost()[0] + map_info.builder_ti_reserve(), ")")
            else:
                if rc.can_build_launcher(t1) and rc.get_global_resources()[0] >= rc.get_launcher_cost()[0] + map_info.builder_ti_reserve():
                    log("heal: building launcher at", t1)
                    rc.build_launcher(t1)
                    map_info.update_at(t1)
                else:
                    log("heal: launcher build blocked at", t1, "(can_build=", rc.can_build_launcher(t1), "ti=", rc.get_global_resources()[0], "need=", rc.get_launcher_cost()[0] + map_info.builder_ti_reserve(), ")")
        _do_best_heal()
        return True
    log("heal: chase target", target, "unreachable, no launcher T1 cached")
    return False


def _chase_on_damaged_conv(target) -> bool:
    if target is None:
        return False
    w = map_info._width
    my_team_idx = map_info._my_team_idx
    en_bit = 1 << (target.x + target.y * w)
    my_dam_convs = (map_info._bm_conveyors
                    & map_info._bm_team[my_team_idx]
                    & map_info._bm_damaged)
    return bool(en_bit & my_dam_convs)


def run():
    log("HEAL")
    target = _cached_chase_target
    on_dam_conv = _chase_on_damaged_conv(target)

    # Priority 1: chase target sitting on a damaged friendly conveyor.
    if target is not None and on_dam_conv:
        log("heal: priority-1 chase (target on damaged conveyor)", target)
        if _try_chase(target):
            return

    # Priority 2: very-damaged friendly building with no enemy bot on it.
    very_damaged = _very_damaged_targets() & ~map_info._bm_enemy_bots
    if very_damaged:
        best, dist = nav.closest(very_damaged)
        if best is not None and dist <= 4:
            log("heal: priority-2 very_damaged-no-bot target", best, "dist", dist)
            nav.move_adjacent(best, avoid_turret=False)
            _do_best_heal()
            return
        else:
            log("heal: priority-2 very_damaged-no-bot unreachable within 4 (best=", best, "dist=", dist, ")")

    # Priority 3: chase target NOT on damaged conveyor.
    if target is not None and not on_dam_conv:
        log("heal: priority-3 chase (target not on damaged conveyor)", target)
        if _try_chase(target):
            return
    elif target is None:
        log("heal: no chase target")

    very_damaged = _very_damaged_targets()
    targets = very_damaged if very_damaged else _heal_targets()
    pool_kind = "very_damaged" if very_damaged else "heal_targets"
    if targets:
        best, dist = nav.closest(targets)
        if best is not None:
            if dist <= 4:
                log("heal: bottom-block", pool_kind, "target", best, "dist", dist, "(adjacent move)")
                nav.move_adjacent(best, avoid_turret=False)
            else:
                # No immediate heal but reachable damage exists — close the
                # gap so we can heal next turn (otherwise heal score 8 made
                # us pivot here for nothing).
                log("heal: bottom-block", pool_kind, "target", best, "dist", dist, "(close gap)")
                nav.move_to(best)
        else:
            log("heal: bottom-block", pool_kind, "exists but unreachable")
    else:
        log("heal: no heal targets at all")
    _do_best_heal()
