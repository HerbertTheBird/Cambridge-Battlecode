import map_info
from pathing import Pathing
import comms
import units.builder
from cambc import *

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
    """Bitmask of tiles within CONV_CHASE_CHEB pathing distance of my conveyors."""
    my_team_idx = map_info._TM_INT[rc.get_team()]
    my_convs = map_info._bm_conveyors & map_info._bm_team[my_team_idx]
    if not my_convs:
        return 0
    w = map_info._width
    board = (1 << (w * map_info._height)) - 1
    avoid = map_info.get_avoid(False, False, False)
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
    mask = units.builder.forget[comm_flag]
    while mask:
        lsb = mask & -mask
        claimed.add(lsb.bit_length() - 1)
        mask ^= lsb
    return claimed


def _find_chase_target():
    """Find an unclaimed enemy builder bot within conv zone. Returns (uid, pos) or None."""
    my_team = rc.get_team()
    zone = _conv_zone()
    if not zone:
        return None

    w = map_info._width
    claimed = _claimed_enemy_ids()
    # Filter enemy bots in zone, unclaimed
    enemy_bots = map_info._bm_enemy_bots & zone
    if not enemy_bots:
        return None

    # Remove claimed enemies
    filtered = 0
    mask = enemy_bots
    while mask:
        lsb = mask & -mask
        n = lsb.bit_length() - 1
        uid = map_info._bot_at.get(n)
        if uid is not None and (uid & ID_MASK) not in claimed:
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
    my_team_idx = map_info._TM_INT[rc.get_team()]
    return map_info._bm_team[my_team_idx]


def _very_damaged_targets():
    """Bitmask of friendly buildings with > 2 damage."""
    return _healable_mask() & map_info._bm_very_damaged


def _heal_targets():
    """Bitmask of friendly damaged buildings."""
    return _healable_mask() & map_info._bm_damaged


def score():
    if _very_damaged_targets():
        return 7
    if _find_chase_target() is not None:
        return 7
    if _heal_targets():
        return 5.5
    return 0


def _try_barrier_dead_ends():
    """Barrier any adjacent tiles that are dead-end conveyor targets."""
    w = map_info._width
    dead_ends = map_info._bm_dead_end
    if not dead_ends:
        return
    # Get target tiles of dead-end conveyors
    targets = 0
    mask = dead_ends
    conv_target = map_info._building_conv_target
    tiles = w * map_info._height
    while mask:
        lsb = mask & -mask
        n = lsb.bit_length() - 1
        tn = conv_target[n]
        if tn and 0 <= tn < tiles:
            targets |= 1 << tn
        mask ^= lsb
    if not targets:
        return
    for d in Direction:
        p = rc.get_position().add(d)
        if not map_info.in_bounds(p):
            continue
        pbit = 1 << (p.x + p.y * w)
        if not (targets & pbit):
            continue
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
    for d in Direction:
        p = rc.get_position().add(d)
        if not map_info.in_bounds(p):
            continue
        pbit = 1 << (p.x + p.y * w)
        if not (healable & pbit):
            continue
        if not rc.can_heal(p):
            continue
        n = p.x + p.y * w
        hp = map_info._building_hp[n]
        for i in range(map_info._NUM_ET):
            if map_info._bm_et[i] & pbit:
                damage = map_info._MAX_HP_BY_IDX[i] - hp
                if damage > best_heal_damage:
                    best_heal_damage = damage
                    best_heal = p
                break
    if best_heal is not None:
        rc.heal(best_heal)


def run():
    print("HEAL")

    # Priority 1: chase an enemy near my conveyors
    target = _find_chase_target()
    if target is not None:
        uid, ep = target
        nav.move_to(ep, avoid_empty=True)
        comms.mark(uid & ID_MASK, comm_flag)
        _try_barrier_dead_ends()
        _do_best_heal()
        return

    # Priority 2: move to most damaged building and heal
    very_damaged = _very_damaged_targets()
    targets = very_damaged if very_damaged else _heal_targets()
    if targets:
        best, dist = nav.closest(targets)
        if best is not None:
            nav.move_adjacent(best, avoid_turret=False, avoid_empty=True)
    
    _try_barrier_dead_ends()
    _do_best_heal()
