"""Shared turret target-priority scoring used by sentinel firing and gunner
rotation decisions.

Priority buckets (lowest number = best):
  1. Enemy foundries that feed ≥1 enemy turret AND 0 of mine.
  2. Enemy harvesters that feed >1 enemy turrets AND 0 of mine.
  3. Enemy turrets that can hit one of my turrets, the enemy conveyors that
     feed them, and harvesters whose chain reaches them (and don't feed mine).
  4. Same as 3 but without the "threatens my turret" guard.
  5. Enemy roads / conveyor types / barriers cardinally adjacent to any harvester.
  6. Anything else with positive weight.

Anything in `protected` (conveyors / harvesters / foundries that feed one of
my turrets) is dropped before bucketing.

Tiebreaks within a bucket: one-shot first, then furthest from nearest enemy
builder bot, then weight.
"""

from cambc import EntityType, Position
import map_info
from log import log, DRAW_DEBUG


_TURRET_IDX_TO_ET = None


def _turret_et_for_idx(idx: int):
    global _TURRET_IDX_TO_ET
    if _TURRET_IDX_TO_ET is None:
        _TURRET_IDX_TO_ET = {
            map_info._IDX_GUNNER: EntityType.GUNNER,
            map_info._IDX_SENTINEL: EntityType.SENTINEL,
            map_info._IDX_BREACH: EntityType.BREACH,
        }
    return _TURRET_IDX_TO_ET.get(idx)


def _enemy_turrets_mask() -> int:
    enemy_team = map_info._bm_team[1 - map_info._my_team_idx]
    return ((map_info._bm_et[map_info._IDX_GUNNER]
             | map_info._bm_et[map_info._IDX_SENTINEL]
             | map_info._bm_et[map_info._IDX_BREACH]) & enemy_team)


def _my_turrets_mask() -> int:
    my_team = map_info._bm_team[map_info._my_team_idx]
    return ((map_info._bm_et[map_info._IDX_GUNNER]
             | map_info._bm_et[map_info._IDX_SENTINEL]
             | map_info._bm_et[map_info._IDX_BREACH]) & my_team)


def _threatening_enemy_turrets(rc, enemy_turrets: int, my_turrets: int) -> int:
    """Bitmask of enemy turrets that would have a legal shot at any of my
    turrets on the current map. Uses `rc.can_fire_from`, which for gunners
    enforces first-obstruction LOS through current occupancy and walls — so
    a gunner whose ray is blocked by a wall or another building before
    reaching my turret is correctly NOT marked threatening. Sentinels and
    breaches still use a pure geometric range/shape check."""
    if not enemy_turrets or not my_turrets:
        return 0
    w = map_info._width

    my_positions = []
    m = my_turrets
    while m:
        lsb = m & -m
        n = lsb.bit_length() - 1
        m ^= lsb
        my_positions.append(Position(n % w, n // w))

    result = 0
    m = enemy_turrets
    while m:
        lsb = m & -m
        n = lsb.bit_length() - 1
        m ^= lsb
        et = _turret_et_for_idx(map_info._building_et_idx[n])
        if et is None:
            continue
        bid = map_info._building_id[n]
        if bid is None:
            continue
        try:
            d = rc.get_direction(bid)
        except Exception:
            continue
        pos = Position(n % w, n // w)
        for mp in my_positions:
            if rc.can_fire_from(pos, d, et, mp):
                result |= lsb
                break
    return result


def _reverse_conveyor_feeders(seeds: int, conv_filter: int, max_steps: int = 16) -> int:
    if not seeds or not conv_filter:
        return 0
    reverse = map_info._conv_reverse
    visited = 0
    frontier = seeds
    for _ in range(max_steps):
        next_frontier = 0
        m = frontier
        while m:
            lsb = m & -m
            n = lsb.bit_length() - 1
            next_frontier |= reverse[n]
            m ^= lsb
        next_frontier = next_frontier & conv_filter & ~visited
        if not next_frontier:
            break
        visited |= next_frontier
        frontier = next_frontier
    return visited


def _adj_seed_conveyors(src: int) -> int:
    """Conveyors cardinally adjacent to src whose direction is not back toward
    src. Mirrors map_info._compute_fed.adj_seed."""
    if not src:
        return 0
    bm_conv = map_info._bm_conveyors
    nlc = map_info._not_left_col
    nrc = map_info._not_right_col
    ntr = map_info._not_top_row
    nbr = map_info._not_bottom_row
    w = map_info._width
    bm_et = map_info._bm_et
    dir_mask = map_info._bm_dir
    cardinal = (
        bm_et[map_info._IDX_CONVEYOR]
        | bm_et[map_info._IDX_ARMOURED_CONVEYOR]
        | bm_et[map_info._IDX_SPLITTER]
    )
    convs_e = cardinal & dir_mask[map_info._DIR_E]
    convs_w = cardinal & dir_mask[map_info._DIR_W]
    convs_s = cardinal & dir_mask[map_info._DIR_S]
    convs_n = cardinal & dir_mask[map_info._DIR_N]
    adj = map_info.expand_manhattan(src) & bm_conv
    pointing_back = (
        (((src & nlc) >> 1) & convs_e)
        | (((src & nrc) << 1) & convs_w)
        | (((src & ntr) >> w) & convs_s)
        | (((src & nbr) << w) & convs_n)
    )
    return adj & ~pointing_back


def _forward_reach(seed_conv: int, max_steps: int = 16) -> int:
    if not seed_conv:
        return 0
    w = map_info._width
    h = map_info._height
    nlc = map_info._not_left_col
    nrc = map_info._not_right_col
    ntr = map_info._not_top_row
    nbr = map_info._not_bottom_row
    board = map_info._board_mask
    bm_conv = map_info._bm_conveyors
    bm_et = map_info._bm_et
    dir_mask = map_info._bm_dir
    cardinal = (
        bm_et[map_info._IDX_CONVEYOR]
        | bm_et[map_info._IDX_ARMOURED_CONVEYOR]
        | bm_et[map_info._IDX_SPLITTER]
    )
    convs_e = cardinal & dir_mask[map_info._DIR_E]
    convs_w = cardinal & dir_mask[map_info._DIR_W]
    convs_s = cardinal & dir_mask[map_info._DIR_S]
    convs_n = cardinal & dir_mask[map_info._DIR_N]
    bridges = bm_et[map_info._IDX_BRIDGE]
    conv_target = map_info._building_conv_target
    tiles = w * h
    expanded = seed_conv
    cur = seed_conv
    for _ in range(max_steps):
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
        cur = new_targets & bm_conv
        if not cur:
            break
    return expanded


def compute_priority_sets(rc) -> dict:
    """Return {1: mask, 2: mask, 3: mask, 4: mask, 5: mask} — see module docstring."""
    enemy_team = map_info._bm_team[1 - map_info._my_team_idx]
    bm_conv = map_info._bm_conveyors
    bm_et = map_info._bm_et

    enemy_turrets = _enemy_turrets_mask()
    my_turrets = _my_turrets_mask()

    convs_to_enemy_turret = _reverse_conveyor_feeders(enemy_turrets, bm_conv)
    convs_to_my_turret = _reverse_conveyor_feeders(my_turrets, bm_conv)

    threatening = _threatening_enemy_turrets(rc, enemy_turrets, my_turrets)
    enemy_convs = bm_conv & enemy_team
    convs_to_threatening_enemy_side = _reverse_conveyor_feeders(threatening, enemy_convs)
    convs_to_any_enemy_turret_enemy_side = _reverse_conveyor_feeders(enemy_turrets, enemy_convs)

    enemy_foundries = bm_et[map_info._IDX_FOUNDRY] & enemy_team
    enemy_harvesters = bm_et[map_info._IDX_HARVESTER] & enemy_team

    p1 = 0
    m = enemy_foundries
    while m:
        lsb = m & -m
        m ^= lsb
        adj = _adj_seed_conveyors(lsb)
        direct = map_info.expand_manhattan(lsb)
        feeds_enemy = bool((adj & convs_to_enemy_turret) or (direct & enemy_turrets))
        feeds_mine = bool((adj & convs_to_my_turret) or (direct & my_turrets))
        if feeds_enemy and not feeds_mine:
            p1 |= lsb

    p2 = 0
    p3_harvesters = 0
    p4_harvesters = 0
    m = enemy_harvesters
    while m:
        lsb = m & -m
        m ^= lsb
        adj = _adj_seed_conveyors(lsb)
        direct = map_info.expand_manhattan(lsb)
        feeds_mine = bool((adj & convs_to_my_turret) or (direct & my_turrets))
        if feeds_mine:
            continue
        if (adj & convs_to_threatening_enemy_side) or (direct & threatening):
            p3_harvesters |= lsb
        if (adj & convs_to_any_enemy_turret_enemy_side) or (direct & enemy_turrets):
            p4_harvesters |= lsb
        reach = _forward_reach(adj)
        fed_turrets = (reach & enemy_turrets) | (direct & enemy_turrets)
        if bin(fed_turrets).count("1") > 1:
            p2 |= lsb

    p3 = threatening | convs_to_threatening_enemy_side | p3_harvesters
    p4 = enemy_turrets | convs_to_any_enemy_turret_enemy_side | p4_harvesters

    bm_road = bm_et[map_info._IDX_ROAD]
    bm_barrier = bm_et[map_info._IDX_BARRIER]
    bm_conv_types = (
        bm_et[map_info._IDX_CONVEYOR]
        | bm_et[map_info._IDX_ARMOURED_CONVEYOR]
        | bm_et[map_info._IDX_SPLITTER]
        | bm_et[map_info._IDX_BRIDGE]
    )
    all_harvesters = bm_et[map_info._IDX_HARVESTER]
    harvester_cardinal = map_info.expand_manhattan(all_harvesters) & ~all_harvesters
    p5 = (bm_road | bm_barrier | bm_conv_types) & enemy_team & harvester_cardinal

    # Never shoot anything that feeds one of my turrets — even when it
    # belongs to the enemy. Includes the upstream conveyor chain plus any
    # harvester / foundry that sits directly cardinally adjacent to my
    # turret or feeds a conveyor in that chain.
    protected = convs_to_my_turret
    sources = bm_et[map_info._IDX_HARVESTER] | bm_et[map_info._IDX_FOUNDRY]
    m = sources
    while m:
        lsb = m & -m
        m ^= lsb
        adj = _adj_seed_conveyors(lsb)
        direct = map_info.expand_manhattan(lsb)
        if (adj & convs_to_my_turret) or (direct & my_turrets):
            protected |= lsb

    if DRAW_DEBUG and protected:
        for p in map_info.iter_mask(protected):
            rc.draw_indicator_dot(p, 0, 200, 200)

    return {1: p1, 2: p2, 3: p3, 4: p4, 5: p5, 'protected': protected}


def _apply_tiebreaks(pool, nav, one_shot_hp: int, enemy_bots: int, label: str):
    """Tiebreak within a single pool: one-shot, furthest-from-nearest-enemy-bot,
    then weight (HP as sub-tiebreak). Logs every step."""
    if not pool:
        return None

    def _dist(c):
        if not enemy_bots:
            return None
        _, d = nav.closest(enemy_bots, pos=c[0])
        return d  # -1 means unreachable

    def _fmt_dist(d):
        if d is None:
            return "n/a"
        if d == -1:
            return "inf"
        return str(d)

    log(f"  [{label}] pool size={len(pool)}: " + ", ".join(
        f"({c[0].x},{c[0].y}) et={c[4].value} w={c[2]} hp={c[3]} d={_fmt_dist(_dist(c))}"
        for c in pool
    ))
    one_shots = [c for c in pool if c[3] <= one_shot_hp]
    if one_shots:
        log(f"  [{label}] one-shot filter ({one_shot_hp}): {len(one_shots)}/{len(pool)} kept")
        pool = one_shots
    if len(pool) == 1:
        c = pool[0]
        log(f"  [{label}] sole survivor → ({c[0].x},{c[0].y}) et={c[4].value}")
        return pool[0]
    if enemy_bots:
        scored = []
        for c in pool:
            _, dist = nav.closest(enemy_bots, pos=c[0])
            if dist == -1:
                dist = 1 << 30
            scored.append((dist, c))
        log(f"  [{label}] dist-to-enemy-bot: " + ", ".join(
            f"({c[0].x},{c[0].y})={d if d < (1<<29) else 'inf'}" for d, c in scored
        ))
        max_dist = max(s[0] for s in scored)
        pool = [c for d, c in scored if d == max_dist]
        log(f"  [{label}] furthest-from-bot ({max_dist if max_dist < (1<<29) else 'inf'}): {len(pool)} kept")
        if len(pool) == 1:
            c = pool[0]
            log(f"  [{label}] sole survivor → ({c[0].x},{c[0].y}) et={c[4].value}")
            return pool[0]
    if any(c[3] <= one_shot_hp for c in pool):
        pool.sort(key=lambda c: (-c[2], -c[3]))
        log(f"  [{label}] sort by (-weight, -hp) for one-shot")
    else:
        pool.sort(key=lambda c: (-c[2], c[3]))
        log(f"  [{label}] sort by (-weight, hp)")
    c = pool[0]
    log(f"  [{label}] picked → ({c[0].x},{c[0].y}) et={c[4].value} w={c[2]} hp={c[3]}")
    return pool[0]


def select_best(candidates, priority_sets, nav, one_shot_hp: int,
                bot_ring_mode: str = 'strict',
                ring_override_mask: int = 0):
    """Pick the best candidate from `candidates`. Each candidate is a tuple
    whose first five elements are (tile, n, weight, hp, etype); extra fields
    (e.g. direction for the gunner) are passed through unchanged.

    Drops anything in `priority_sets['protected']` (conveyors / harvesters /
    foundries that feed my turrets) up front — we never shoot our own
    pipeline.

    `bot_ring_mode` controls how a non-bot candidate adjacent to ≥2 enemy
    builder bots (bots heal what we shoot, so two healers usually out-tempo
    a single shot — one healer alone we still try to outpace) is treated.
    Candidates are NEVER dropped here — they're only demoted to a last-resort
    fallback tier that fires after every normal pool is empty:
      - `'strict'`           — demote unconditionally.
      - `'one_shot_override'` — demote unless the candidate is a one-shot
        (`hp <= one_shot_hp`) or its tile is in `ring_override_mask`.
        Used by the sentinel.
      - `'off'`               — never demote. Used by the gunner: gunners
        already pay a fire cooldown and rotate cost, and we'd rather keep
        their candidate pool wide.

    `ring_override_mask` is an additional bypass for the sentinel: any
    candidate tile whose bit is set is treated as if it were one-shot
    (no demotion). Sentinel populates it from `_kill_assist_mask` —
    coordinated kills with allied turrets.

    Pool order tried: priorities 1, 2, 3, 4 (non-bot, normal), then enemy
    builder bots, then priorities 5, 6 (non-bot, normal), then fallback
    priorities 1–6 (the bot-adjacent demoted candidates). Within each pool
    the same tiebreak chain is applied: one-shot, furthest-from-nearest-bot,
    weight.
    """
    log(f"select_best: {len(candidates)} raw candidates, one_shot_hp={one_shot_hp}")
    if not candidates:
        log("  empty candidate list")
        return None

    enemy_bots = map_info._bm_enemy_bots
    friendly_bots = map_info._bm_friendly_bots
    if enemy_bots:
        # Demote only when ≥2 enemy builder bots can heal the same tile —
        # one healer is a kill we should still take.
        w = map_info._width
        nlc = map_info._not_left_col
        nrc = map_info._not_right_col
        ntr = map_info._not_top_row
        nbr = map_info._not_bottom_row
        b = enemy_bots
        shifts = (
            (b & nlc) >> 1,                  # bot E of tile
            (b & nrc) << 1,                  # bot W of tile
            (b & nbr) << w,                  # bot N of tile
            (b & ntr) >> w,                  # bot S of tile
            (b & nlc & nbr) << (w - 1),      # bot NE
            (b & nrc & nbr) << (w + 1),      # bot NW
            (b & nlc & ntr) >> (w + 1),      # bot SE
            (b & nrc & ntr) >> (w - 1),      # bot SW
        )
        seen_one = 0
        seen_two = 0
        for s in shifts:
            seen_two |= seen_one & s
            seen_one |= s
        bot_ring = seen_two & ~enemy_bots & map_info._board_mask
    else:
        bot_ring = 0
    protected = priority_sets.get('protected', 0)
    log(f"  enemy_bots={'yes' if enemy_bots else 'no'} bot_ring={'set' if bot_ring else '0'} "
        f"protected={'set' if protected else '0'} friendly_bots={'set' if friendly_bots else '0'} "
        f"ring_mode={bot_ring_mode} ring_override={'set' if ring_override_mask else '0'}")

    bot_pool = []
    non_bots = []
    fallback = []
    drop_protected = 0
    drop_friendly = 0
    for cand in candidates:
        n = cand[1]
        etype = cand[4]
        bit = 1 << n
        # Hard safety: never fire on a tile occupied by a friendly bot — applies
        # to bot-pool candidates too (a bot tile that is somehow our team
        # shouldn't slip through).
        if bit & friendly_bots:
            drop_friendly += 1
            continue
        if etype == EntityType.BUILDER_BOT:
            bot_pool.append(cand)
            continue
        if bit & protected:
            drop_protected += 1
            continue
        if bit & bot_ring:
            if bot_ring_mode == 'off':
                non_bots.append(cand)
            elif bit & ring_override_mask:
                log(f"    ring override (kill-assist) at ({cand[0].x},{cand[0].y})")
                non_bots.append(cand)
            elif bot_ring_mode == 'one_shot_override' and cand[3] <= one_shot_hp:
                non_bots.append(cand)
            else:
                fallback.append(cand)
        else:
            non_bots.append(cand)
    log(f"  prefilter: {len(non_bots)} non-bots, {len(bot_pool)} bots, "
        f"{len(fallback)} fallback (bot-adjacent), "
        f"dropped {drop_protected} protected + {drop_friendly} friendly-bot")

    def _bucket_for(cand):
        n = cand[1]
        weight = cand[2]
        bit = 1 << n
        for p in (1, 2, 3, 4, 5):
            if priority_sets[p] & bit:
                return p
        if weight <= 0:
            return None
        return 6

    buckets = {1: [], 2: [], 3: [], 4: [], 5: [], 6: []}
    for cand in non_bots:
        b = _bucket_for(cand)
        if b is not None:
            buckets[b].append(cand)
    fb_buckets = {1: [], 2: [], 3: [], 4: [], 5: [], 6: []}
    for cand in fallback:
        b = _bucket_for(cand)
        if b is not None:
            fb_buckets[b].append(cand)
    log(f"  buckets: p1={len(buckets[1])} p2={len(buckets[2])} p3={len(buckets[3])} "
        f"p4={len(buckets[4])} bots={len(bot_pool)} p5={len(buckets[5])} p6={len(buckets[6])} "
        f"| fb_p1={len(fb_buckets[1])} fb_p2={len(fb_buckets[2])} fb_p3={len(fb_buckets[3])} "
        f"fb_p4={len(fb_buckets[4])} fb_p5={len(fb_buckets[5])} fb_p6={len(fb_buckets[6])}")

    pools_in_order = (
        ('p1', buckets[1]),
        ('p2', buckets[2]),
        ('p3', buckets[3]),
        ('p4', buckets[4]),
        ('bots', bot_pool),
        ('p5', buckets[5]),
        ('p6', buckets[6]),
        ('fb_p1', fb_buckets[1]),
        ('fb_p2', fb_buckets[2]),
        ('fb_p3', fb_buckets[3]),
        ('fb_p4', fb_buckets[4]),
        ('fb_p5', fb_buckets[5]),
        ('fb_p6', fb_buckets[6]),
    )
    for label, pool in pools_in_order:
        if not pool:
            continue
        log(f"trying case {label}")
        chosen = _apply_tiebreaks(pool, nav, one_shot_hp, enemy_bots, label)
        if chosen is not None:
            log(f"select_best: case={label} winner=({chosen[0].x},{chosen[0].y}) "
                f"et={chosen[4].value} w={chosen[2]} hp={chosen[3]}")
            return chosen
    log("select_best: no winner")
    return None
