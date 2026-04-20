import map_info
import pathing
from pathing import Pathing
import comms
import units.builder
from cambc import *
from log import log

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
    
    # expensive calculations - nonbitmasked, leave at end. calculates conveyors that go into a turret.
    pruned_targets = 0
    invalid_sabotage_locations = set()
    my_pos = map_info._my_pos
    for p in map_info.iter_mask((map_info._bm_et[map_info._IDX_GUNNER] | map_info._bm_et[map_info._IDX_SENTINEL]) & map_info._bm_team[map_info._my_team_idx]):
        front_positions = []
        
        if p.distance_squared(my_pos) <= 100:
            for conv in map_info.iter_mask(map_info._conv_reverse[p.x + p.y * map_info._width]):
                if conv not in invalid_sabotage_locations:
                    front_positions.append(conv)
                    invalid_sabotage_locations.add(conv)
                    # rc.draw_indicator_dot(conv, 0, 0, 255)
                        
            for _ in range(4):
                new_front = []
                for front_p in front_positions:
                    for conv in map_info.iter_mask(map_info._conv_reverse[front_p.x + front_p.y * map_info._width]):
                        if conv not in invalid_sabotage_locations:
                            new_front.append(conv)
                            invalid_sabotage_locations.add(conv)
                            # rc.draw_indicator_dot(conv, 0, 0, 255)
                front_positions = new_front
    
    for target in map_info.iter_mask(targets):
        if target not in invalid_sabotage_locations:
            pruned_targets |= (1 << (target.x + target.y * map_info._width))

    return pruned_targets

def _my_claims():
    w = map_info._width
    my_mask = 1 << (map_info._my_pos.x + map_info._my_pos.y * w)
    targets = units.builder.exclude_crowded_claims(comm_flag, _sabotage_targets())
    return pathing.voronoi_claim(my_mask, units.builder.claimed_senders[comm_flag], targets)

MAX_SCORE = 5
def score():
    return 5 if _my_claims() else 0

def run():
    log("SABOTAGE")
    targets = _my_claims()

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

    comms.mark(best.x + best.y * map_info._width, comm_flag)
