from cambc import Controller, Direction, EntityType, Position, Team, Environment, GameConstants
import map_info
import pathing
from pathing import Pathing
from log import log
import units.turret_priority as turret_priority

# A gunner one-shots anything with HP ≤ its base damage. We deliberately use
# the base damage (Ti ammo) here even when loaded with refined ax — being
# conservative means we prefer guaranteed-kill targets. Overshooting an ax-
# loaded gunner on a 12-HP target wastes some overkill but doesn't mispick.
ONE_SHOT_HP = GameConstants.GUNNER_DAMAGE  # 10

rc: Controller = None
nav: Pathing = None
my_pos: Position = None
my_team: Team = None
_no_ammo_turns: int = 0
_invalid_upstream_turns: int = 0
_attackable_by_dir: dict = {}

CARDINAL_OFFSETS = [(0, 1), (0, -1), (-1, 0), (1, 0)]

# Sentinel-style weights. Builder bots are intentionally absent from rotation
# scoring per spec — they're only valid as a *current-direction* fire target.
_WEIGHTS = {
    EntityType.CORE: 2,
    EntityType.BREACH: 60,
    EntityType.SENTINEL: 50,
    EntityType.LAUNCHER: 10,
    EntityType.HARVESTER: 0,
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
    global rc, nav, my_pos, my_team, _no_ammo_turns, _invalid_upstream_turns, _attackable_by_dir
    rc = c
    nav = Pathing(c)
    my_pos = rc.get_position()
    _no_ammo_turns = 0
    _invalid_upstream_turns = 0
    my_team = map_info._my_team
    _attackable_by_dir = {
        d: set(rc.get_attackable_tiles_from(my_pos, d, EntityType.GUNNER))
        for d in map_info._DIRECTIONS
    }


def _should_stay():
    pos = rc.get_position()
    for uid in rc.get_nearby_units(8):
        if rc.get_entity_type(uid) != EntityType.BUILDER_BOT:
            continue
        if rc.get_team(uid) == my_team:
            continue
        p = rc.get_position(uid)
        if max(abs(p.x - pos.x), abs(p.y - pos.y)) <= 2:
            return True
    # for dx, dy in CARDINAL_OFFSETS:
    #     p = Position(pos.x + dx, pos.y + dy)
    #     if map_info.in_bounds(p):
    #         bid = rc.get_tile_building_id(p)
    #         if bid and rc.get_entity_type(bid) == EntityType.HARVESTER:
    #             return True
    # Closest builder bot by pathing distance: if a friendly is strictly closer
    # than any enemy, we're in their way — leave. Otherwise stay.
    _, enemy_d = nav.closest_within(map_info._bm_enemy_bots, max_dist=8)
    _, friendly_d = nav.closest_within(map_info._bm_friendly_bots, max_dist=4)
    if enemy_d == -1 and friendly_d == -1:
        return True
    if enemy_d == -1:
        return False
    if friendly_d == -1:
        return True
    return enemy_d <= friendly_d + 1


def _ally_feeder_mask(max_steps: int = 6) -> int:
    """Bitmask of friendly conveyors feeding any of my turrets (gunner/sentinel/breach)."""
    my_team_bm = map_info._bm_team[map_info._my_team_idx]
    my_turrets = (
        map_info._bm_et[map_info._IDX_SENTINEL]
        | map_info._bm_et[map_info._IDX_GUNNER]
        | map_info._bm_et[map_info._IDX_BREACH]
    ) & my_team_bm
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


def _scan_ray(direction, attackable, feeder_mask, allow_builder_bots: bool,
              bot_must_be_on_my_conveyor: bool = False):
    """Walk forward from my_pos in `direction`. Friendly roads and any markers
    are pass-through; everything else is a stopping tile.

    Returns (target_etype, fire_at) where:
      - target_etype: the EntityType of the *enemy* thing motivating the shot
        (used for rotation scoring).
      - fire_at: the Position to pass to rc.fire — the first real game-side
        obstruction on the ray, which may be a friendly road we're sacrificing.
    Returns None if firing is not desired in this direction.

    Rules:
      - Wall: ray blocked, no fire.
      - Friendly non-road non-marker building / friendly builder bot: blocks, no fire.
      - Friendly conveyor that's part of an ally feeder chain: no fire.
      - Enemy building: fire (even past friendly roads).
      - Enemy builder bot: fire only if `allow_builder_bots` AND nothing in
        front of it (no friendly road already passed). If
        `bot_must_be_on_my_conveyor`, additionally require the bot to stand on
        a friendly conveyor tile."""
    w = map_info._width
    cur = map_info.pos_add(my_pos, direction)
    fire_at = None
    passed_road = False
    while map_info.in_bounds(cur) and cur in attackable:
        n = cur.x + cur.y * w
        if map_info.ground_at(cur.x, cur.y) == Environment.WALL:
            return None

        bot_id = rc.get_tile_builder_bot_id(cur)
        bid = rc.get_tile_building_id(cur)

        # Empty
        if bot_id is None and bid is None:
            cur = map_info.pos_add(cur, direction)
            continue

        # Marker (no bot) — pass through
        if bot_id is None and bid is not None and rc.get_entity_type(bid) == EntityType.MARKER:
            cur = map_info.pos_add(cur, direction)
            continue

        # First real obstruction (the engine will resolve fire to this)
        if fire_at is None:
            fire_at = cur

        # Don't shoot tiles feeding our own turrets
        if feeder_mask & (1 << n):
            return None

        if bot_id is not None:
            if rc.get_team(bot_id) == my_team:
                return None
            if not allow_builder_bots:
                return None
            if passed_road:
                return None
            if bot_must_be_on_my_conveyor:
                my_convs = map_info._bm_conveyors & map_info._bm_team[map_info._my_team_idx]
                if not (my_convs & (1 << n)):
                    return None
                # Only commit a rotation to clear an enemy bot off our line if
                # the conveyor is already taking damage AND no friendly builder
                # is within Chebyshev 1 (a friendly bot can heal/repair, so let
                # it handle the trespasser instead of burning a rotation).
                if bid is None:
                    return None
                if rc.get_hp(bid) >= rc.get_max_hp(bid):
                    return None
                cheb = map_info.expand_chebyshev(1 << n)
                if cheb & map_info._bm_friendly_bots:
                    return None
            return EntityType.BUILDER_BOT, fire_at

        # Building only
        bid_etype = rc.get_entity_type(bid)
        if rc.get_team(bid) == my_team:
            if bid_etype == EntityType.ROAD:
                passed_road = True
                cur = map_info.pos_add(cur, direction)
                continue
            return None
        return bid_etype, fire_at

    return None


def _decide_fire():
    direction = rc.get_direction()
    if direction == Direction.CENTRE:
        return None
    attackable = _attackable_by_dir[direction]
    feeder_mask = _ally_feeder_mask()
    res = _scan_ray(direction, attackable, feeder_mask, allow_builder_bots=True)
    return None if res is None else res[1]


def _hp_at(tile: Position) -> int:
    """HP of the entity that would be hit at `tile`. Mirrors `_scan_ray`'s
    resolution: builder-bot wins over building."""
    bot_id = rc.get_tile_builder_bot_id(tile)
    if bot_id is not None:
        return rc.get_hp(bot_id)
    bid = rc.get_tile_building_id(tile)
    if bid is None:
        return 0
    return rc.get_hp(bid)


def _choose_rotate_dir():
    """Pick the best direction to rotate toward by scoring each non-current
    facing's first-obstruction tile through the shared turret priority logic.

    Enemy builder bots are only considered as a rotation target when they
    stand on one of *my* conveyors — that's the legacy "bot trespassing on my
    line" fallback. They're routed through the bot pool in `select_best`,
    which only fires after priorities 1-4 are exhausted."""
    feeder_mask = _ally_feeder_mask()
    current = rc.get_direction()
    w = map_info._width

    candidates = []  # (tile, n, weight, hp, etype, direction)
    for d in map_info._DIRECTIONS:
        if d == current:
            continue
        attackable = _attackable_by_dir[d]
        res = _scan_ray(d, attackable, feeder_mask,
                        allow_builder_bots=True,
                        bot_must_be_on_my_conveyor=True)
        if res is None:
            continue
        etype, fire_at = res
        weight = _WEIGHTS.get(etype, 0)
        n = fire_at.x + fire_at.y * w
        hp = _hp_at(fire_at)
        candidates.append((fire_at, n, weight, hp, etype, d))

    if not candidates:
        return None

    priority_sets = turret_priority.compute_priority_sets(rc)
    chosen = turret_priority.select_best(
        candidates, priority_sets, nav, ONE_SHOT_HP,
        bot_ring_mode='off',
    )
    if chosen is None:
        return None
    return chosen[5]


def run():
    global _no_ammo_turns, _invalid_upstream_turns
    map_info.update()

    if not map_info.turret_could_possibly_be_fed(rc.get_position()):
        _invalid_upstream_turns += 1
        if _invalid_upstream_turns >= 3 and not _should_stay() and rc.get_ammo_amount() == 0:
            rc.self_destruct()
            return
    else:
        _invalid_upstream_turns = 0

    if rc.get_ammo_amount() == 0:
        _no_ammo_turns += 1
        if _no_ammo_turns >= 16 and not _should_stay():
            rc.self_destruct()
            return
    else:
        _no_ammo_turns = 0

    if rc.get_action_cooldown() > 0:
        return
    if rc.get_ammo_amount() < 2:
        return

    fire_target = _decide_fire()
    if fire_target is not None and rc.can_fire(fire_target):
        rc.fire(fire_target)
        log(f"gunner fired at {fire_target}")
        return

    rotate_dir = _choose_rotate_dir()
    if rotate_dir is not None and rc.get_global_resources()[0] >= 60 and rc.can_rotate(rotate_dir):
        rc.rotate(rotate_dir)
        log(f"gunner rotated toward {rotate_dir}")
        return

    if fire_target is None and rotate_dir is None:
        if not _should_stay():
            rc.self_destruct()
