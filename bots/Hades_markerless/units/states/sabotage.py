import map_info
import pathing
from pathing import Pathing
import comms
import units.builder
import units.states.attack as attack
from cambc import *
from log import log

rc: Controller = None
nav: Pathing = None

comm_flag = 6

def init(c: Controller):
    global rc, nav
    rc = c
    nav = units.builder.nav

def _sabotage_targets():
    """Bitmask of enemy conveyors/splitters/bridges (not armoured) that are
    not adjacent to a launcher and not in turret line of fire."""
    my_team_idx = map_info._my_team_idx
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

    # Avoid enemy builder bots within 6 pathing distance
    enemy_bots = map_info._bm_enemy_bots
    if enemy_bots:
        w = map_info._width
        board = (1 << (w * map_info._height)) - 1
        avoid = map_info.get_avoid(False, False, False)
        passable = ~avoid & board
        nlc = map_info._not_left_col
        nrc = map_info._not_right_col
        danger_zone = enemy_bots
        frontier = enemy_bots
        for _ in range(6):
            h = frontier | ((frontier & nrc) << 1) | ((frontier & nlc) >> 1)
            expanded = h | (h << w) | (h >> w)
            frontier = expanded & passable & ~danger_zone
            danger_zone |= frontier
        targets &= ~danger_zone

    targets &= ~attack._turret_feed_chains()

    return targets

def _my_claims():
    my_mask = units.builder.my_voronoi_mask(comm_flag)
    targets = units.builder.exclude_crowded_claims(comm_flag, _sabotage_targets())
    return pathing.voronoi_claim(my_mask, units.builder.claimed_senders[comm_flag], targets)


_cached_claims = 0
MAX_SCORE = 1.5
def score():
    global _cached_claims
    _cached_claims = _my_claims()
    # Score 1.5: above explore (1), below disrupt (2). Bots with nothing better
    # to do will pick stealthily-reachable enemy conveyors and chew them down.
    # Sabotage already filters out tiles in turret threat / launcher adjacency /
    # near enemy bots, so this is automatic stealth pressure.
    return 1.5 if _cached_claims else 0

def run():
    log("SABOTAGE")
    targets = _cached_claims

    if not targets:
        return

    best, _ = nav.closest(targets)
    if best is None:
        return
    units.builder.register_active_target(comm_flag, best)

    # Move onto the tile and fire
    nav.move_to({best})
    if rc.can_fire(best):
        rc.fire(best)
        map_info.update_at(best)
