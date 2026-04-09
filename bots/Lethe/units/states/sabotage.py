import map_info
from pathing import Pathing
import comms
import units.builder
from cambc import *

rc: Controller = None
nav: Pathing = None

comm_flag = 6

def init(c: Controller):
    global rc, nav
    rc = c
    nav = Pathing(rc)

def _sabotage_targets():
    """Bitmask of enemy conveyors/splitters/bridges (not armoured) that are
    not adjacent to a launcher and not in turret line of fire."""
    my_team_idx = map_info._TM_INT[rc.get_team()]
    enemy_idx = 1 - my_team_idx
    enemy = map_info._bm_team[enemy_idx]

    targets = (
        map_info._bm_et[map_info._IDX_CONVEYOR]
        | map_info._bm_et[map_info._IDX_SPLITTER]
        | map_info._bm_et[map_info._IDX_BRIDGE]
    ) & enemy

    if not targets:
        return 0

    # Exclude tiles in turret threat or adjacent to enemy launcher
    danger = map_info._bm_enemy_turret_threat | map_info._bm_enemy_launch_adj
    targets &= ~danger
    targets &= ~units.builder.forget[comm_flag]
    return targets

def score():
    return 6 if _sabotage_targets() else 0

def run():
    print("SABOTAGE")
    targets = _sabotage_targets()
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

    # Move onto the tile and fire
    nav.move_to({best})
    if rc.can_fire(best):
        rc.fire(best)

    comms.mark(best, comm_flag)
