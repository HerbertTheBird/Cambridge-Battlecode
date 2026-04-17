from cambc import *

import map_info
import pathing
from pathing import Pathing
import comms
import units.builder
from log import log

rc: Controller = None
nav: Pathing = None

comm_flag = 2

def init(c: Controller):
    global rc, nav
    rc = c
    nav = Pathing(rc)

def _disruptable_ore():
    all_ore = (map_info._bm_env[map_info._IDX_ENV_ORE_TI]
               | map_info._bm_env[map_info._IDX_ENV_ORE_AX])
    clearable = (map_info._bm_et[map_info._IDX_ROAD]
                 | map_info._bm_et[map_info._IDX_MARKER])
    result = (all_ore
              & (~map_info._bm_any_building | clearable)
              & ~units.builder._harvest_zone
              & ~map_info._bm_enemy_turret_threat
              & ~map_info._bm_enemy_launch_adj)
    if rc.get_current_round() < 200:
        w = map_info._width
        my_zone = 1 << (map_info._my_pos.x + map_info._my_pos.y * w)
        for _ in range(5):
            my_zone = map_info.expand_chebyshev(my_zone)
        result &= my_zone
    return result

def _my_claims():
    w = map_info._width
    my_mask = 1 << (map_info._my_pos.x + map_info._my_pos.y * w)
    return pathing.voronoi_claim(my_mask, units.builder.claimed_senders[comm_flag], _disruptable_ore())

def score():
    if rc.get_global_resources()[0] < rc.get_harvester_cost()[0]*5:
        return 0
    return 2 if _my_claims() else 0

def run():
    log("DISRUPT")
    available = _my_claims()
    if not available:
        return

    best, _ = nav.closest(available)
    if best is None:
        return

    width = map_info._width

    best_n = best.x + best.y * width
    best_id = map_info._building_id[best_n]
    best_bit = 1 << best_n
    my_team_idx = map_info._my_team_idx

    if best_id and (map_info._bm_team[my_team_idx] & best_bit):
        # Friendly road/marker — move adjacent and destroy
        nav.move_adjacent(best)
        if not map_info.has_builder_bot(best) and rc.can_destroy(best) and rc.get_action_cooldown() == 0:
            rc.destroy(best)
            map_info.update_at(best)
    elif best_id and (map_info._bm_et[map_info._IDX_ROAD]&best_bit):
        # Enemy road/marker — move onto it and fire
        nav.move_to({best})
        if rc.can_fire(best):
            rc.fire(best)
    else:
        # Empty tile — move adjacent and build barrier
        nav.move_adjacent(best)

    if rc.can_build_barrier(best):
        rc.build_barrier(best)
        map_info.update_at(best)

    comms.mark(best.x + best.y * map_info._width, comm_flag)
