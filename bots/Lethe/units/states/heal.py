import map_info
from pathing import Pathing
import comms
import units.builder
from cambc import *

rc: Controller = None
nav: Pathing = None

comm_flag = 5

# Max HP lookup per entity type index
_MAX_HP = {
    map_info._IDX_CONVEYOR: 20,
    map_info._IDX_ARMOURED_CONVEYOR: 50,
    map_info._IDX_BRIDGE: 20,
    map_info._IDX_SPLITTER: 20,
    map_info._IDX_HARVESTER: 30,
    map_info._IDX_FOUNDRY: 50,
    map_info._IDX_ROAD: 5,
    map_info._IDX_BARRIER: 30,
    map_info._IDX_GUNNER: 40,
    map_info._IDX_SENTINEL: 30,
    map_info._IDX_BREACH: 60,
    map_info._IDX_LAUNCHER: 30,
    map_info._IDX_CORE: 500,
}

def init(c: Controller):
    global rc, nav
    rc = c
    nav = Pathing(rc)

def _heal_targets():
    """Bitmask of friendly buildings not at full HP (conveyors + turrets + core)."""
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

    candidates = my_buildings & healable_types & ~units.builder.forget[comm_flag]
    if not candidates:
        return 0

    # Filter to damaged only
    building_hp = map_info._building_hp
    bm_et = map_info._bm_et
    width = map_info._width
    damaged = 0
    mask = candidates
    while mask:
        lsb = mask & -mask
        n = lsb.bit_length() - 1
        hp = building_hp[n]
        if hp > 0:
            for idx, max_hp in _MAX_HP.items():
                if bm_et[idx] & lsb:
                    if hp < max_hp-2:
                        damaged |= lsb
                    break
        mask ^= lsb
    return damaged

def score():
    return 8 if _heal_targets() else 0

def run():
    print("HEAL")
    targets = _heal_targets()
    if not targets:
        return

    core = map_info._my_core
    if core is None:
        return

    width = map_info._width
    reached = 1 << (core.x + core.y * width)
    best = None

    for _ in range(width + map_info._height):
        found = targets & reached
        if found:
            lsb = found & -found
            n = lsb.bit_length() - 1
            best = Position(n % width, n // width)
            break
        reached = map_info.expand_manhattan(reached)

    if best is None:
        return

    danger = map_info._bm_enemy_turret_threat | map_info._bm_enemy_launch_adj
    adj = set()
    for d in Direction:
        if d == Direction.CENTRE:
            continue
        p = best.add(d)
        if map_info.in_bounds(p) and map_info.is_passable(p) and not (danger & (1 << (p.x + p.y * width))):
            adj.add(p)
    if not adj:
        adj.add(best)
    nav.move_to(adj)
    print("want to heal", best, rc.get_action_cooldown(), adj)
    if rc.can_heal(best):
        rc.heal(best)

    comms.mark(best, comm_flag)
