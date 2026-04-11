import map_info
from pathing import Pathing
import comms
import units.builder
from cambc import *

rc: Controller = None
nav: Pathing = None

comm_flag = 2

def init(c: Controller):
    global rc, nav
    rc = c
    nav = Pathing(rc)

def _disruptable_ore():
    #filter out spots they can shoot, as well as spots with a builder bot within sqrt 20 euclidian distance (check in here using rc.get_nearby_units and filtering for builders)
    
    """Bitmask of ore tiles outside harvest zone that can have a barrier placed.
    Includes tiles with road or marker (either team) since those can be cleared."""
    all_ore = (map_info._bm_env[map_info._IDX_ENV_ORE_TI]
               | map_info._bm_env[map_info._IDX_ENV_ORE_AX])
    clearable = (map_info._bm_et[map_info._IDX_ROAD]
                 | map_info._bm_et[map_info._IDX_MARKER])
    has_building = 0
    for i in range(map_info._NUM_ET):
        has_building |= map_info._bm_et[i]
    return (all_ore
            & (~has_building | clearable)
            & ~units.builder._harvest_zone
            & ~units.builder.forget[comm_flag]
            & ~map_info._bm_enemy_turret_threat
            & ~map_info._bm_enemy_launch_adj)

def score():
    return 2 if _disruptable_ore() else 0

def run():
    print("DISRUPT")
    available = _disruptable_ore()
    if not available:
        return

    best, _ = nav.closest(available)
    if best is None:
        return

    width = map_info._width

    best_n = best.x + best.y * width
    best_id = map_info._building_id[best_n]
    best_bit = 1 << best_n
    my_team_idx = map_info._TM_INT[rc.get_team()]

    if best_id and (map_info._bm_team[my_team_idx] & best_bit):
        # Friendly road/marker — move adjacent and destroy
        nav.move_adjacent(best, avoid_empty=rc.get_global_resources()[0] < rc.get_harvester_cost()[0]*5)
        if rc.can_destroy(best) and rc.get_action_cooldown() == 0:
            rc.destroy(best)
            map_info.update_at(best)
    elif best_id and (map_info._bm_et[map_info._IDX_ROAD]&best_bit):
        # Enemy road/marker — move onto it and fire
        nav.move_to({best}, rc.get_global_resources()[0] < rc.get_harvester_cost()[0]*5)
        if rc.can_fire(best):
            rc.fire(best)
    else:
        # Empty tile — move adjacent and build barrier
        nav.move_adjacent(best, avoid_empty=rc.get_global_resources()[0] < rc.get_harvester_cost()[0]*5)

    if rc.can_build_barrier(best):
        rc.build_barrier(best)
        map_info.update_at(best)

    comms.mark(best, comm_flag)
