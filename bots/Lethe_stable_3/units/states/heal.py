import map_info
from pathing import Pathing
import comms
import units.builder
from cambc import *
from log import log

rc: Controller = None
nav: Pathing = None

comm_flag = 7

CONV_CHASE_CHEB = 8
ID_MASK = (1 << 12) - 1


def init(c: Controller):
    global rc, nav
    rc = c
    nav = Pathing(rc)


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


def _claimed_enemy_ids():
    """Set of enemy ID hashes (mod 2^12) already claimed by other builders."""
    claimed = set()
    mask = units.builder.claimed_targets[comm_flag]
    while mask:
        lsb = mask & -mask
        claimed.add(lsb.bit_length() - 1)
        mask ^= lsb
    return claimed


def _find_chase_target():
    """Find an unclaimed enemy builder bot within conv zone. Returns (uid, pos) or None."""
    w = map_info._width
    claimed = _claimed_enemy_ids()
    # Filter enemy bots in zone, unclaimed
    enemy_bots = map_info._bm_enemy_bots
    if not enemy_bots:
        return None

    friendly_bots = map_info._bm_friendly_bots
    my_bit = 1 << (map_info._my_pos.x + map_info._my_pos.y * w)
    other_friendly = friendly_bots & ~my_bit

    filtered = 0
    mask = enemy_bots
    while mask:
        lsb = mask & -mask
        n = lsb.bit_length() - 1
        uid = map_info._bot_at.get(n)
        if uid is not None:
            if (uid & ID_MASK) not in claimed:
                filtered |= lsb
            else:
                nearby_friendly = map_info.expand_chebyshev(lsb) & other_friendly
                if not nearby_friendly:
                    filtered |= lsb
        mask ^= lsb

    if not filtered:
        return None

    closest_pos, dist = nav.closest(filtered)
    if closest_pos is None or dist > 6:
        return None
    n = closest_pos.x + closest_pos.y * w
    uid = map_info._bot_at.get(n)
    if uid is None:
        return None
    return (uid, closest_pos)


def _healable_mask():
    """Bitmask of friendly buildings."""
    my_team_idx = map_info._my_team_idx
    return map_info._bm_team[my_team_idx]


def _very_damaged_targets():
    """Bitmask of friendly buildings with > 2 damage."""
    return _healable_mask() & map_info._bm_very_damaged


def _heal_targets():
    """Bitmask of friendly damaged buildings."""
    return _healable_mask() & map_info._bm_damaged & ~map_info._bm_enemy_bots


_cached_chase_target = None  # set by score(), reused by run()

MAX_SCORE = 7
def score():
    global _cached_chase_target
    if _very_damaged_targets():
        _cached_chase_target = None
        return 7
    _cached_chase_target = _find_chase_target()
    target = _cached_chase_target
    if target is not None:
        if _conv_zone() & (1<<(target[1].x + target[1].y * map_info._width)):
            log("high priority heal", target[0])
            return 7
        else:
            log("low priority heal", target[0])
            return 2.5
    return 0


def _try_barrier_dead_ends():
    """Barrier any adjacent tiles that are dead-end conveyor targets."""
    w = map_info._width
    dead_ends = map_info._bm_dead_end
    if not dead_ends:
        return
    # Only dead-end conveyors whose output is empty / marker / enemy building
    my_team_idx = map_info._my_team_idx
    enemy_idx = 1 - my_team_idx
    enemy_any = map_info._bm_team[enemy_idx]
    marker = map_info._bm_et[map_info._IDX_MARKER]
    empty_mask = ~map_info._bm_any_building & ~map_info._bm_env[map_info._IDX_ENV_WALL]

    targets = 0
    mask = dead_ends
    conv_target = map_info._building_conv_target
    tiles = w * map_info._height
    while mask:
        lsb = mask & -mask
        n = lsb.bit_length() - 1
        tn = conv_target[n]
        if tn and 0 <= tn < tiles:
            tbit = 1 << tn
            if (empty_mask & tbit) or (marker & tbit) or (enemy_any & tbit):
                targets |= lsb
        mask ^= lsb
    if not targets:
        return
    my_pos = map_info._my_pos
    for d in map_info._ALL_DIRECTIONS:
        dx, dy = map_info._DIRECTION_DELTAS[d]
        p = Position(my_pos.x + dx, my_pos.y + dy)
        if not map_info.in_bounds(p):
            continue
        pbit = 1 << (p.x + p.y * w)
        if not (targets & pbit):
            continue
        if rc.get_action_cooldown() == 0:
            if rc.can_destroy(p):
                rc.destroy(p)
                map_info.update_at(p)
        if rc.can_build_barrier(p):
            rc.build_barrier(p)
            map_info.update_at(p)
            return

def _do_best_heal():
    """Heal the most damaged adjacent friendly building."""
    w = map_info._width
    healable = _healable_mask() & map_info._bm_damaged
    best_heal = None
    best_heal_damage = -1
    my_pos = map_info._my_pos
    for d in map_info._ALL_DIRECTIONS:
        dx, dy = map_info._DIRECTION_DELTAS[d]
        p = Position(my_pos.x + dx, my_pos.y + dy)
        if not map_info.in_bounds(p):
            continue
        pbit = 1 << (p.x + p.y * w)
        if not (healable & pbit):
            continue
        if not rc.can_heal(p):
            continue
        n = p.x + p.y * w
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

    # Priority 1: chase an enemy near my conveyors
    target = _cached_chase_target
    if target is not None:
        log("target is",target)
        uid, ep = target
        _try_barrier_dead_ends()
        nav.move_to(ep)
        comms.mark(uid & ID_MASK, comm_flag)
        _do_best_heal()
        return

    # Priority 2: move to most damaged building and heal
    very_damaged = _very_damaged_targets()
    targets = very_damaged if very_damaged else _heal_targets()
    if targets:
        best, dist = nav.closest(targets)
        if best is not None:
            nav.move_adjacent(best, avoid_turret=False)
    _do_best_heal()
