import map_info
from pathing import Pathing
import comms
import units.builder
from cambc import *

rc: Controller = None
nav: Pathing = None

comm_flag = 5

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

    # Avoid enemy builder bots within 6 manhattan
    enemy_bots = map_info._bm_enemy_bots
    if enemy_bots:
        danger_zone = enemy_bots
        for _ in range(6):
            danger_zone = map_info.expand_manhattan(danger_zone)
        targets &= ~danger_zone

    return targets

def score():
    return 0 if _sabotage_targets() else 0

def run():
    print("SABOTAGE")
    targets = _sabotage_targets()
    units.builder.draw_mask(targets, 255, 0, 255)

    if not targets:
        return

    best, _ = nav.closest(targets)
    if best is None:
        return

    # Move onto the tile and fire
    nav.move_to({best})
    if rc.can_fire(best):
        rc.fire(best)

    comms.mark(best.x + best.y * map_info._width, comm_flag)
