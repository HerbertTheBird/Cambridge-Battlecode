import map_info
import units.builder
from cambc import *
from log import log

rc: Controller = None
nav = None

comm_flag = 8

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


def _find_chase_target(damaged=True):
    # log("find chase")
    """Find an unclaimed enemy builder bot within conv zone. Returns (uid, pos) or None."""
    w = map_info._width
    # Filter enemy bots in zone, unclaimed
    enemy_bots = map_info._bm_enemy_bots
    if damaged:
        enemy_bots = enemy_bots & _very_damaged_targets()
    units.builder.draw_mask(enemy_bots, 255, 0, 0)
    if not enemy_bots:
        # log("no enemies")
        if damaged:
            return _find_chase_target(False)
        return None

    friendly_bots = map_info._bm_friendly_bots
    my_bit = 1 << (map_info._my_pos.x + map_info._my_pos.y * w)
    other_friendly = friendly_bots & ~my_bit

    filtered = enemy_bots
    # Expand the enemy zone once and pre-filter the friendlies we iterate.
    # A friendly outside enemy_zone_4 has no enemy within 4 chebyshev, so
    # the per-friendly expansion below would be a no-op.
    enemy_zone_4 = map_info.expand_chebyshev(enemy_bots, 4)
    mask = friendly_bots & ~my_bit & enemy_zone_4

    while mask:
        lsb = mask & -mask
        n = lsb.bit_length() - 1
        friend_zone = map_info.expand_chebyshev(lsb, 4)
        nearby = filtered & friend_zone
        if not nearby:
            mask ^= lsb
            continue
        closest = nav.closest_within(nearby, Position(n % w, n // w), 4)
        if closest[0]:
            # log("filtering", closest[0], "because", n % w, n // w, closest[1])
            filtered ^= (1 << (closest[0].x + closest[0].y * w))
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
        # log("no filtered")
        if damaged:
            return _find_chase_target(False)
        return None
    nearby = filtered & map_info.expand_chebyshev(my_bit, 8)
    if not nearby:
        # log("too far")
        if damaged:
            return _find_chase_target(False)
        return None
    closest_pos, dist = nav.closest_within(nearby, max_dist=8)
    if closest_pos is None:
        # log("no closest")
        if damaged:
            return _find_chase_target(False)
        return None
    # if dist < 6:
    #     return None
    n = closest_pos.x + closest_pos.y * w
    # if closest_pos.distance_squared(map_info._my_pos) < 5:
    #     log("too close")
    #     return None
    # log("found chase target", closest_pos)
    return closest_pos


def _healable_mask():
    """Bitmask of friendly buildings."""
    my_team_idx = map_info._my_team_idx
    return map_info._bm_team[my_team_idx]


def _very_damaged_targets():
    """Bitmask of friendly buildings with > 2 damage."""
    return _healable_mask() & map_info._bm_very_damaged & ~map_info._bm_my_core_area & map_info._bm_visible


def _heal_targets():
    """Bitmask of friendly damaged buildings."""
    return _healable_mask() & (map_info._bm_damaged | (map_info._bm_et[map_info._IDX_SENTINEL] | map_info._bm_et[map_info._IDX_GUNNER]) & map_info._bm_enemy_turret_threat) & ~_very_damaged_targets()


_cached_chase_target = None  # set by score(), reused by run()

MAX_SCORE = 8


def score():
    global _cached_chase_target
    _cached_chase_target = _find_chase_target()

    if _very_damaged_targets():
        # units.builder.draw_mask(_very_damaged_targets(), 255, 0, 0)
        return 8

    target = _cached_chase_target
    # log(target)
    # units.builder.draw_mask(_conv_zone(), 255, 0, 0)
    if target is not None:
        if _conv_zone() & (1 << (target.x + target.y * map_info._width)):
            log("high priority heal", target)
            return 7
        else:
            log("low priority heal", target)
            return 2.5
    if _heal_targets():
        return 1.5
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

def _do_best_heal():
    """Heal the most damaged adjacent friendly building."""
    w = map_info._width
    h = map_info._height
    healable = _healable_mask() & map_info._bm_damaged
    best_heal = None
    best_heal_damage = -1
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
        if et_idx >= 0:
            damage = map_info._MAX_HP_BY_IDX[et_idx] - hp
            if damage > best_heal_damage:
                best_heal_damage = damage
                best_heal = p
    if best_heal is not None:
        rc.heal(best_heal)


def run():
    log("HEAL")
    target = _cached_chase_target
    if target is not None and _very_damaged_targets() & (1<<(target.x+target.y*map_info._width)):
        ep = target
        # _try_barrier_dead_ends()
        log("best chase", target)
        nav.move_to(ep)
        _do_best_heal()
        return
    very_damaged = _very_damaged_targets() & ~(map_info._bm_enemy_bots & map_info.expand_chebyshev(map_info._bm_friendly_bots))
    targets = very_damaged
    if targets:
        best, dist = nav.closest(targets)
        if best is not None and dist <= map_info._building_hp[best.x+best.y*map_info._width]//2 + 1:
            nav.move_adjacent(best, avoid_turret=False)
            _do_best_heal()
            return
    # Priority 1: chase an enemy near my conveyors
    target = _cached_chase_target
    if target is not None:
        ep = target
        # _try_barrier_dead_ends()
        log("best chase", target)
        nav.move_to(ep)
        _do_best_heal()
        return
    very_damaged = _very_damaged_targets()
    targets = very_damaged if very_damaged else _heal_targets()
    if targets:
        best, dist = nav.closest(targets)
        if best is not None:
            nav.move_adjacent(best, avoid_turret=False)
    _do_best_heal()
