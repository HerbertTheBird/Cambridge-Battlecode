from cambc import Controller, Direction, EntityType, Position, Team, Environment
import map_info
from log import log

rc: Controller = None
my_pos: Position = None
my_team: Team = None
_no_ammo_turns: int = 0

CARDINAL_OFFSETS = [(0, 1), (0, -1), (-1, 0), (1, 0)]

# Sentinel-style weights. Builder bots are intentionally absent from rotation
# scoring per spec — they're only valid as a *current-direction* fire target.
_WEIGHTS = {
    EntityType.CORE: 35,
    EntityType.BREACH: 60,
    EntityType.SENTINEL: 50,
    EntityType.LAUNCHER: 10,
    EntityType.HARVESTER: 0,
    EntityType.GUNNER: 40,
    EntityType.FOUNDRY: 55,
    EntityType.BRIDGE: 4,
    EntityType.ARMOURED_CONVEYOR: 4,
    EntityType.BARRIER: 4,
    EntityType.SPLITTER: 2,
    EntityType.CONVEYOR: 1,
    EntityType.ROAD: 0,
    EntityType.MARKER: 0,
}


def init(c: Controller):
    global rc, my_pos, my_team, _no_ammo_turns
    rc = c
    my_pos = rc.get_position()
    _no_ammo_turns = 0
    my_team = map_info._my_team


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
    for dx, dy in CARDINAL_OFFSETS:
        p = Position(pos.x + dx, pos.y + dy)
        if map_info.in_bounds(p):
            bid = rc.get_tile_building_id(p)
            if bid and rc.get_entity_type(bid) == EntityType.HARVESTER:
                return True
    best_d = 8
    closest_is_friendly = False
    for uid in rc.get_nearby_units():
        if rc.get_entity_type(uid) != EntityType.BUILDER_BOT:
            continue
        p = rc.get_position(uid)
        d = pos.distance_squared(p)
        if best_d is None or d < best_d:
            best_d = d
            closest_is_friendly = (rc.get_team(uid) == my_team)
    if best_d is None:
        return True
    return not closest_is_friendly


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


def _scan_ray(direction, attackable, feeder_mask, allow_builder_bots: bool):
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
        front of it (no friendly road already passed)."""
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
    attackable = set(rc.get_attackable_tiles())
    feeder_mask = _ally_feeder_mask()
    res = _scan_ray(direction, attackable, feeder_mask, allow_builder_bots=True)
    return None if res is None else res[1]


def _choose_rotate_dir():
    feeder_mask = _ally_feeder_mask()
    current = rc.get_direction()
    best_dir = None
    best_score = 0
    for d in map_info._DIRECTIONS:
        if d == current:
            continue
        attackable = set(rc.get_attackable_tiles_from(my_pos, d, EntityType.GUNNER))
        res = _scan_ray(d, attackable, feeder_mask, allow_builder_bots=False)
        if res is None:
            continue
        etype, _ = res
        score = _WEIGHTS.get(etype, 0)
        if score > best_score:
            best_score = score
            best_dir = d
    return best_dir


def run():
    global _no_ammo_turns
    map_info.update()

    if rc.get_ammo_amount() < 2:
        _no_ammo_turns += 1
        if _no_ammo_turns >= 10 and not _should_stay():
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
