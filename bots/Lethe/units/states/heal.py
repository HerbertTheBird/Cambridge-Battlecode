import map_info
from pathing import Pathing
import comms
import units.builder
from cambc import *

rc: Controller = None
nav: Pathing = None

comm_flag = 7


def init(c: Controller):
    global rc, nav
    rc = c
    nav = Pathing(rc)

def _healable_mask():
    """Bitmask of friendly healable building types."""
    my_team_idx = map_info._TM_INT[rc.get_team()]
    my_buildings = map_info._bm_team[my_team_idx]

    healable_types = (
        map_info._bm_et[map_info._IDX_CONVEYOR]
        | map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR]
        | map_info._bm_et[map_info._IDX_BRIDGE]
        | map_info._bm_et[map_info._IDX_SPLITTER]
        | map_info._bm_et[map_info._IDX_GUNNER]
        | map_info._bm_et[map_info._IDX_SENTINEL]
        | map_info._bm_et[map_info._IDX_BREACH]
        | map_info._bm_et[map_info._IDX_LAUNCHER]
        | map_info._bm_et[map_info._IDX_HARVESTER]
        | map_info._bm_et[map_info._IDX_CORE]
    )

    return my_buildings & healable_types

def _heal_targets():
    """Bitmask of friendly damaged buildings."""
    return _healable_mask() & map_info._bm_damaged & ~units.builder.forget[comm_flag]

def _very_damaged_targets():
    """Bitmask of friendly buildings with > 2 damage."""
    return _healable_mask() & map_info._bm_very_damaged

def score():
    if _very_damaged_targets():
        return 7
    if _enemy_near_conveyors() is not None:
        return 7
    if _heal_targets():
        return 5.5
    return 0

def _enemy_near_conveyors():
    """Find an enemy builder bot within 4 Chebyshev of my conveyors, only if no ally is already adjacent."""
    my_team = rc.get_team()
    my_team_idx = map_info._TM_INT[my_team]
    my_convs = map_info._bm_conveyors & map_info._bm_team[my_team_idx]
    if not my_convs:
        return None
    conv_adj = my_convs
    for _ in range(4):
        conv_adj = map_info.expand_chebyshev(conv_adj)
    my_pos = rc.get_position()
    # Collect allied builder bot positions for adjacency check
    allies = []
    for uid in rc.get_nearby_units():
        if rc.get_team(uid) == my_team and rc.get_entity_type(uid) == EntityType.BUILDER_BOT:
            ap = rc.get_position(uid)
            if ap != my_pos:
                allies.append(ap)
    # Check all enemies, skip those already covered by an ally
    for uid in rc.get_nearby_units():
        if rc.get_team(uid) != my_team and rc.get_entity_type(uid) == EntityType.BUILDER_BOT:
            ep = rc.get_position(uid)
            if not (conv_adj & (1 << (ep.x + ep.y * map_info._width))):
                continue
            ally_adjacent = False
            for ap in allies:
                if max(abs(ap.x - ep.x), abs(ap.y - ep.y)) <= 1:
                    ally_adjacent = True
                    break
            if not ally_adjacent:
                return ep
    return None

def run():
    print("HEAL")
    very_damaged = _very_damaged_targets()
    if very_damaged:
        # Find most damaged among very damaged
        worst_damage = 0
        mask = very_damaged
        while mask:
            lsb = mask & -mask
            n = lsb.bit_length() - 1
            hp = map_info._building_hp[n]
            for i in range(map_info._NUM_ET):
                if map_info._bm_et[i] & lsb:
                    damage = map_info._MAX_HP_BY_IDX[i] - hp
                    if damage > worst_damage:
                        worst_damage = damage
                    break
            mask ^= lsb
        # Target all very damaged within 2 damage of the worst
        threshold = worst_damage - 2
        targets = 0
        mask = very_damaged
        while mask:
            lsb = mask & -mask
            n = lsb.bit_length() - 1
            hp = map_info._building_hp[n]
            for i in range(map_info._NUM_ET):
                if map_info._bm_et[i] & lsb:
                    damage = map_info._MAX_HP_BY_IDX[i] - hp
                    if damage >= threshold:
                        targets |= lsb
                    break
            mask ^= lsb
    else:
        targets = _heal_targets()
    if not targets:
        enemy_pos = _enemy_near_conveyors()
        if enemy_pos is not None:
            nav.move_to(enemy_pos, avoid_empty=True)
        return

    # Follow enemy bots threatening my conveyors
    units.builder.draw_mask(targets, 255, 0, 0)
    best, dist = nav.closest(targets)
    if best is None:
        enemy_pos = _enemy_near_conveyors()
        if enemy_pos is not None:
            nav.move_to(enemy_pos, avoid_empty=True)
        return

    width = map_info._width

    nav.move_adjacent(best, avoid_turret=False, avoid_empty=True)
    print("want to heal", best, dist, rc.get_action_cooldown())

    # Heal the most damaged adjacent building we can
    healable = _healable_mask() & map_info._bm_damaged
    best_heal = None
    best_heal_damage = -1
    for d in Direction:
        p = rc.get_position().add(d)
        if not map_info.in_bounds(p):
            continue
        pbit = 1 << (p.x + p.y * width)
        if not (healable & pbit):
            continue
        if not rc.can_heal(p):
            continue
        n = p.x + p.y * width
        hp = map_info._building_hp[n]
        for i in range(map_info._NUM_ET):
            if map_info._bm_et[i] & pbit:
                damage = map_info._MAX_HP_BY_IDX[i] - hp
                print(p, damage)
                if damage > best_heal_damage:
                    best_heal_damage = damage
                    best_heal = p
                break
    if best_heal is not None:
        rc.heal(best_heal)

    # comms.mark(best, comm_flag)
