from cambc import Controller, Position, EntityType, Direction
import map_info
from log import log, DRAW_DEBUG


rc: Controller = None
_no_ammo_turns = 0

CARDINAL_OFFSETS = [(0, 1), (0, -1), (-1, 0), (1, 0)]

_WEIGHTS = {
    EntityType.CORE: 35,
    EntityType.BREACH: 60,
    EntityType.SENTINEL: 50,
    EntityType.LAUNCHER: 10,
    EntityType.HARVESTER: 0,
    EntityType.BUILDER_BOT: 15,
    EntityType.GUNNER: 40,
    EntityType.FOUNDRY: 55,
    EntityType.BRIDGE: 4,
    EntityType.ARMOURED_CONVEYOR: 4,
    EntityType.BARRIER: 4,
    EntityType.SPLITTER: 3,
    EntityType.CONVEYOR: 2,
    EntityType.ROAD: 1,
    EntityType.MARKER: 0,
}


def init(c: Controller):
    global rc
    rc = c


def _should_stay():
    if rc.get_global_resources()[0] < rc.get_bridge_cost()[0]:
        return True
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
    best_d = 8
    closest_is_friendly = False
    for uid in rc.get_nearby_units():
        if rc.get_entity_type(uid) != EntityType.BUILDER_BOT:
            continue
        p = rc.get_position(uid)
        d = my_pos.distance_squared(p)
        if best_d is None or d < best_d:
            best_d = d
            closest_is_friendly = (rc.get_team(uid) == my_team)
    if best_d is None:
        return True
    return not closest_is_friendly


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


def run():
    global _no_ammo_turns
    map_info.update()

    if rc.get_ammo_amount() < 10:
        _no_ammo_turns += 1
        if _no_ammo_turns >= 16 and not _should_stay():
            rc.self_destruct()
            return
    else:
        _no_ammo_turns = 0

    if rc.get_action_cooldown() > 0:
        return
    if rc.get_ammo_amount() < 5:
        return

    w = map_info._width
    feeder_mask = _ally_feeder_mask()
    harv_adj = map_info.expand_manhattan(map_info._bm_et[map_info._IDX_HARVESTER])

    candidates = []  # (pos, weight, hp)
    for tile in rc.get_attackable_tiles():
        n = tile.x + tile.y * w
        if feeder_mask & (1 << n):
            continue
        if not rc.can_fire(tile):
            continue
        resolved = _resolve_target_on_tile(tile)
        if resolved is None:
            continue
        etype, hp = resolved
        weight = _WEIGHTS.get(etype, 0)
        if weight <= 0:
            continue
        if etype in (EntityType.BARRIER, EntityType.CONVEYOR, EntityType.ARMOURED_CONVEYOR) and (harv_adj >> n) & 1:
            weight += 1
        candidates.append((tile, weight, hp))

    if not candidates:
        if not _should_stay():
            rc.self_destruct()
        return

    other_mask = _other_sentinel_attack_mask()
    def _key(c):
        tile, weight, hp = c
        one_shot = hp <= 18
        # +0.5 keeps one-shot priority *within* a weight tier without letting a
        # one-shot road outvalue a non-one-shot core.
        score = weight + (0.5 if one_shot else 0)
        in_focus = bool(other_mask & (1 << (tile.x + tile.y * w)))
        # secondary: prefer focused tiles (other sentinel can also hit).
        # tertiary: one-shots use full damage on the chunkiest kill; otherwise
        # finish soft targets first.
        return (-score, 0 if in_focus else 1, -hp if one_shot else hp)
    candidates.sort(key=_key)
    best = candidates[0][0]

    rc.fire(best)
