from cambc import Controller, Position, EntityType, Direction, GameConstants
import map_info
import pathing
from pathing import Pathing
from log import log
import units.turret_priority as turret_priority

rc: Controller = None
nav: Pathing = None
_no_ammo_turns = 0
_invalid_upstream_turns = 0

CARDINAL_OFFSETS = [(0, 1), (0, -1), (-1, 0), (1, 0)]
ONE_SHOT_HP = GameConstants.SENTINEL_DAMAGE  # 18

# "Highest-id sentinel coordinates a one-shot finish" feature.
LOCK_TURNS = 8
CANT_ONE_SHOT_TTL = 50

_prev_visible_hp: dict = {}     # tile_n -> hp at start of last turn, with my own
                                # damage already subtracted. Any further drop on
                                # the next turn is external damage.
_lock_target_n: int = -1        # tile_n of currently locked target, or -1
_lock_target_id: int = -1       # entity id of locked building (so a rebuilt tile doesn't fool us)
_lock_turns_left: int = 0
_no_lock_until: dict = {}       # tile_n -> earliest round to lock again

_WEIGHTS = {
    EntityType.CORE: 2,
    EntityType.BREACH: 60,
    EntityType.SENTINEL: 50,
    EntityType.LAUNCHER: 10,
    EntityType.HARVESTER: 0,
    EntityType.BUILDER_BOT: 15,
    EntityType.GUNNER: 40,
    EntityType.FOUNDRY: 55,
    EntityType.BRIDGE: 5,
    EntityType.ARMOURED_CONVEYOR: 1,
    EntityType.BARRIER: 5,
    EntityType.SPLITTER: 3,
    EntityType.CONVEYOR: 4,
    EntityType.ROAD: 0,
    EntityType.MARKER: 0,
}


def init(c: Controller):
    global rc, nav, _no_ammo_turns, _invalid_upstream_turns
    global _prev_visible_hp
    global _lock_target_n, _lock_target_id, _lock_turns_left, _no_lock_until
    rc = c
    nav = Pathing(c)
    _no_ammo_turns = 0
    _invalid_upstream_turns = 0
    _prev_visible_hp = {}
    _lock_target_n = -1
    _lock_target_id = -1
    _lock_turns_left = 0
    _no_lock_until = {}


def _snapshot_visible_enemy_hp() -> dict:
    """{tile_n: (entity_id, hp)} for every visible enemy building. Tracking the
    id alongside HP lets us tell apart "this building lost HP" (external damage)
    from "the original was destroyed and a new building was placed here"."""
    out = {}
    enemy = map_info._bm_team[1 - map_info._my_team_idx]
    enemy_buildings = map_info._bm_any_building & enemy & map_info._bm_visible
    m = enemy_buildings
    while m:
        lsb = m & -m
        n = lsb.bit_length() - 1
        m ^= lsb
        bid = map_info._building_id[n]
        hp = map_info._building_hp[n]
        if bid is not None and hp >= 0:
            out[n] = (bid, hp)
    return out


def _am_highest_sentinel() -> bool:
    """True iff no other visible friendly sentinel has a strictly higher entity id."""
    my_id = rc.get_id()
    w = map_info._width
    my_pos = rc.get_position()
    my_n = my_pos.x + my_pos.y * w
    others = (
        map_info._bm_et[map_info._IDX_SENTINEL]
        & map_info._bm_team[map_info._my_team_idx]
        & map_info._bm_visible
    ) & ~(1 << my_n)
    m = others
    while m:
        lsb = m & -m
        n = lsb.bit_length() - 1
        m ^= lsb
        bid = map_info._building_id[n]
        if bid is not None and bid > my_id:
            return False
    return True


def _should_stay():
    my_pos = rc.get_position()
    my_team = map_info._my_team
    for uid in rc.get_nearby_units(8):
        if rc.get_entity_type(uid) != EntityType.BUILDER_BOT:
            continue
        if rc.get_team(uid) == my_team:
            continue
        p = rc.get_position(uid)
        if max(abs(p.x - my_pos.x), abs(p.y - my_pos.y)) <= 2:
            return True
    # for dx, dy in CARDINAL_OFFSETS:
    #     p = Position(my_pos.x + dx, my_pos.y + dy)
    #     if map_info.in_bounds(p):
    #         bid = rc.get_tile_building_id(p)
    #         if bid and rc.get_entity_type(bid) == EntityType.HARVESTER:
    #             return True
    # Closest builder bot by pathing distance: if a friendly is strictly closer
    # than any enemy, we're in their way — leave. Otherwise stay.
    _, enemy_d = nav.closest_within(map_info._bm_enemy_bots&map_info._bm_visible, max_dist=8)
    _, friendly_d = nav.closest_within(map_info._bm_friendly_bots&map_info._bm_visible, max_dist=2)
    if enemy_d == -1 and friendly_d == -1:
        return True
    if enemy_d == -1:
        return False
    if friendly_d == -1:
        return True
    return enemy_d <= friendly_d+1


def _ally_feeder_mask(max_steps: int = 6) -> int:
    """Bitmask of friendly conveyors feeding any of my turrets (gunner/sentinel/breach).
    Walks upstream via map_info._conv_reverse from each turret tile."""
    my_team = map_info._bm_team[map_info._my_team_idx]
    my_turrets = (
        map_info._bm_et[map_info._IDX_SENTINEL]
        | map_info._bm_et[map_info._IDX_GUNNER]
        | map_info._bm_et[map_info._IDX_BREACH]
    ) & my_team
    if not my_turrets:
        return 0
    reverse = map_info._conv_reverse
    visited = 0
    frontier = my_turrets
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
    return visited


def _other_sentinel_attack_mask() -> int:
    """Union of geometric attack patterns of every OTHER friendly sentinel."""
    w = map_info._width
    my_pos = rc.get_position()
    my_n = my_pos.x + my_pos.y * w
    sentinels = (
        map_info._bm_et[map_info._IDX_SENTINEL]
        & map_info._bm_team[map_info._my_team_idx]
        & ~(1 << my_n)
    )
    if not sentinels:
        return 0
    union = 0
    m = sentinels
    while m:
        lsb = m & -m
        n = lsb.bit_length() - 1
        m ^= lsb
        sid = map_info._building_id[n]
        if sid is None:
            continue
        try:
            sdir = rc.get_direction(sid)
        except Exception:
            continue
        spos = Position(n % w, n // w)
        for t in rc.get_attackable_tiles_from(spos, sdir, EntityType.SENTINEL):
            union |= 1 << (t.x + t.y * w)
    return union


def _kill_assist_mask(candidates) -> int:
    """Return a bitmask of candidate tiles where this sentinel + every other
    allied turret with a legal shot can collectively deal at least `hp + 4`
    damage (the user's "−4 HP grace"). These tiles bypass the bot-adjacency
    filter in `select_best`.

    Damage values are conservative bases (no axionite buffs):
      sentinel = 18, gunner = 10, breach = 40 (direct hit only — splash
      isn't counted because it depends on adjacency to the actual hit tile).
    `rc.can_fire_from` ignores ammo / cooldown but enforces geometry and
    (for gunners) first-obstruction LOS — the same proxy used elsewhere in
    the codebase."""
    if not candidates:
        return 0

    DMG = {
        EntityType.SENTINEL: GameConstants.SENTINEL_DAMAGE,  # 18
        EntityType.GUNNER:   GameConstants.GUNNER_DAMAGE,    # 10
        EntityType.BREACH:   GameConstants.BREACH_DAMAGE,    # 40
    }
    et_idx_to_et = {
        map_info._IDX_SENTINEL: EntityType.SENTINEL,
        map_info._IDX_GUNNER:   EntityType.GUNNER,
        map_info._IDX_BREACH:   EntityType.BREACH,
    }

    my_team_bm = map_info._bm_team[map_info._my_team_idx]
    w = map_info._width
    my_pos = rc.get_position()
    my_n = my_pos.x + my_pos.y * w

    ally_turrets = (
        (map_info._bm_et[map_info._IDX_SENTINEL]
         | map_info._bm_et[map_info._IDX_GUNNER]
         | map_info._bm_et[map_info._IDX_BREACH])
        & my_team_bm
        & ~(1 << my_n)
    )

    turret_info = []  # (pos, dir, etype, dmg)
    m = ally_turrets
    while m:
        lsb = m & -m
        n = lsb.bit_length() - 1
        m ^= lsb
        bid = map_info._building_id[n]
        if bid is None:
            continue
        et = et_idx_to_et.get(map_info._building_et_idx[n])
        if et is None:
            continue
        try:
            d = rc.get_direction(bid)
        except Exception:
            continue
        turret_info.append((Position(n % w, n // w), d, et, DMG[et]))

    my_dmg = GameConstants.SENTINEL_DAMAGE
    result = 0
    for cand in candidates:
        tile, n, _, hp, _ = cand[:5]
        needed = hp + 4
        total = my_dmg
        if total >= needed:
            result |= 1 << n
            continue
        for pos, d, et, dmg in turret_info:
            if rc.can_fire_from(pos, d, et, tile):
                total += dmg
                if total >= needed:
                    result |= 1 << n
                    break
    return result


def _resolve_target_on_tile(tile: Position):
    """Return (etype, hp) of what a sentinel shot at `tile` would actually hit,
    or None if the tile is empty / friendly / a marker. Sentinels (like all
    turrets) hit a builder bot before any building on the same tile."""
    my_team = map_info._my_team
    bot_id = rc.get_tile_builder_bot_id(tile)
    if bot_id is not None:
        if rc.get_team(bot_id) == my_team:
            return None
        return EntityType.BUILDER_BOT, rc.get_hp(bot_id)
    bid = rc.get_tile_building_id(tile)
    if bid is None:
        return None
    if rc.get_team(bid) == my_team:
        return None
    etype = rc.get_entity_type(bid)
    if etype == EntityType.MARKER:
        return None
    return etype, rc.get_hp(bid)


def _fire_and_track(cand):
    """Fire at the candidate, then subtract my damage from `_prev_visible_hp`
    so next turn's delta check counts only external damage. Builder-bot tiles
    aren't tracked in the snapshot — skipping them here is fine."""
    n = cand[1]
    etype = cand[4]
    if etype != EntityType.BUILDER_BOT and n in _prev_visible_hp:
        bid, prev_hp_val = _prev_visible_hp[n]
        new_hp = prev_hp_val - GameConstants.SENTINEL_DAMAGE
        if new_hp <= 0:
            del _prev_visible_hp[n]
        else:
            _prev_visible_hp[n] = (bid, new_hp)
    rc.fire(cand[0])


def run():
    global _no_ammo_turns, _invalid_upstream_turns
    global _prev_visible_hp
    global _lock_target_n, _lock_target_id, _lock_turns_left, _no_lock_until
    map_info.update()

    cur_round = rc.get_current_round()
    if _no_lock_until:
        for n in [k for k, v in _no_lock_until.items() if v <= cur_round]:
            del _no_lock_until[n]

    # Use last turn's snapshot (post-my-damage) for delta detection, then
    # overwrite the global with this turn's snapshot up front so early returns
    # leave the global pointing at the right dict. _fire_and_track will then
    # subtract my damage from this same dict before the turn ends.
    prev_hp = _prev_visible_hp
    _prev_visible_hp = _snapshot_visible_enemy_hp()

    if not map_info.turret_could_possibly_be_fed(rc.get_position()):
        _invalid_upstream_turns += 1
        if _invalid_upstream_turns >= 4 and not _should_stay() and rc.get_ammo_amount() == 0:
            rc.self_destruct()
            return
    else:
        _invalid_upstream_turns = 0

    if rc.get_ammo_amount() < 10:
        _no_ammo_turns += 1
        if _no_ammo_turns >= 16 and not _should_stay():
            rc.self_destruct()
            return
    else:
        _no_ammo_turns = 0

    # ----- Always-run damage detection + lock state evolution ----------------
    # Runs every turn so the lock countdown ticks and we record [SENTINEL_DAMAGED]
    # even when we're on cooldown or low on ammo. No firing happens here — only
    # state updates and prints.
    w = map_info._width
    feeder_mask = _ally_feeder_mask()
    in_range = []
    for tile in rc.get_attackable_tiles():
        n = tile.x + tile.y * w
        if feeder_mask & (1 << n):
            continue
        resolved = _resolve_target_on_tile(tile)
        if resolved is None:
            continue
        etype, hp = resolved
        weight = _WEIGHTS.get(etype, 0)
        in_range.append((tile, n, weight, hp, etype))
    in_range_by_n = {c[1]: c for c in in_range}

    # Maintain existing lock — release on death/replace always, but only tick
    # the countdown on turns where we're actually loaded (ammo>=5). Otherwise
    # an ammo drought would burn the lock without giving us any real chances.
    if _lock_target_n != -1:
        lock_pos = (_lock_target_n % w, _lock_target_n // w)
        cur_bid = map_info._building_id[_lock_target_n]
        if cur_bid is None or cur_bid != _lock_target_id:
            print(
                f"[SENTINEL_LOCK] round={cur_round} unit={rc.get_id()} "
                f"released tile={lock_pos} reason=target_gone"
            )
            _lock_target_n = -1
            _lock_target_id = -1
            _lock_turns_left = 0
        elif rc.get_ammo_amount() >= 5:
            _lock_turns_left -= 1
            if _lock_turns_left <= 0:
                print(
                    f"[SENTINEL_LOCK] round={cur_round} unit={rc.get_id()} "
                    f"timed_out tile={lock_pos} blacklist_until={cur_round + CANT_ONE_SHOT_TTL}"
                )
                _no_lock_until[_lock_target_n] = cur_round + CANT_ONE_SHOT_TTL
                _lock_target_n = -1
                _lock_target_id = -1
                _lock_turns_left = 0

    # Acquire a new lock if eligible and not currently locked.
    if _lock_target_n == -1 and prev_hp and _am_highest_sentinel():
        damaged = []
        for cand in in_range:
            tile, n, _w, hp, etype = cand
            if etype == EntityType.BUILDER_BOT:
                continue
            if n in _no_lock_until:
                continue
            prev_entry = prev_hp.get(n)
            if prev_entry is None:
                continue
            prev_bid, prev_hp_val = prev_entry
            cur_bid = map_info._building_id[n]
            # Same tile, different building → destroyed + replaced, not damaged.
            if cur_bid is None or cur_bid != prev_bid:
                continue
            # Significant damage only — small chip (e.g. heal contention) doesn't
            # count.
            if prev_hp_val - hp <= 2:
                continue
            damaged.append(cand)
            print(
                f"[SENTINEL_DAMAGED] round={cur_round} unit={rc.get_id()} "
                f"tile=({tile.x},{tile.y}) et={etype.value} "
                f"prev_hp={prev_hp_val} cur_hp={hp} delta={prev_hp_val - hp} "
                f"bid={cur_bid}"
            )

        if damaged:
            damaged.sort(key=lambda c: (c[3], -c[2]))
            target = damaged[0]
            target_n = target[1]
            target_bid = map_info._building_id[target_n]
            if target_bid is not None:
                _lock_target_n = target_n
                _lock_target_id = target_bid
                _lock_turns_left = LOCK_TURNS
                print(
                    f"[SENTINEL_LOCK] round={cur_round} unit={rc.get_id()} "
                    f"acquired tile=({target[0].x},{target[0].y}) "
                    f"et={target[4].value} hp={target[3]} turns={LOCK_TURNS}"
                )

    # ----- Original cooldown / ammo gates (preserved) -----------------------
    if rc.get_action_cooldown() > 0:
        return
    if rc.get_ammo_amount() < 5:
        return

    # Build raw (fire-eligible). Recompute via can_fire over in_range.
    raw = [c for c in in_range if rc.can_fire(c[0])]

    if not raw:
        if not _should_stay():
            rc.self_destruct()
        return

    raw_by_n = {c[1]: c for c in raw}

    priority_sets = turret_priority.compute_priority_sets(rc)
    kill_assist = _kill_assist_mask(raw)
    chosen = turret_priority.select_best(
        raw, priority_sets, nav, ONE_SHOT_HP,
        bot_ring_mode='one_shot_override',
        ring_override_mask=kill_assist,
    )

    # 1. Existing one-shot wins over the lock — if normal logic picked a tile
    #    we can one-shot kill, fire it now.
    if chosen is not None and chosen[3] <= ONE_SHOT_HP:
        if _lock_target_n == chosen[1]:
            _lock_target_n = -1
            _lock_target_id = -1
            _lock_turns_left = 0
        _fire_and_track(chosen)
        return

    # 2. If locked: fire only if we can one-shot the locked tile, else wait.
    if _lock_target_n != -1:
        lock_pos = (_lock_target_n % w, _lock_target_n // w)
        fire_cand = raw_by_n.get(_lock_target_n)
        if fire_cand is not None and fire_cand[3] <= ONE_SHOT_HP:
            print(
                f"[SENTINEL_LOCK] round={cur_round} unit={rc.get_id()} "
                f"firing tile={lock_pos} hp={fire_cand[3]} et={fire_cand[4].value}"
            )
            _lock_target_n = -1
            _lock_target_id = -1
            _lock_turns_left = 0
            _fire_and_track(fire_cand)
            return
        cur_view = in_range_by_n.get(_lock_target_n)
        cur_hp = cur_view[3] if cur_view is not None else "?"
        print(
            f"[SENTINEL_LOCK] round={cur_round} unit={rc.get_id()} "
            f"waiting tile={lock_pos} turns_left={_lock_turns_left} "
            f"cur_hp={cur_hp} fire_eligible={fire_cand is not None}"
        )
        return

    # 3. No lock — normal pick.
    if chosen is None:
        if not _should_stay():
            rc.self_destruct()
        return

    _fire_and_track(chosen)
