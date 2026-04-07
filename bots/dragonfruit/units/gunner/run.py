from cambc import Controller, Position, GameConstants

from globals import *
from combat import choose_gunner_target
from helpers import *
from log import log

def run_gunner(player, ct: Controller, my_pos: Position, vc) -> None:
    if player.last_fired_round == 0:
            player.last_fired_round = ct.get_current_round()

    target = choose_gunner_target(ct, my_pos, player.my_team)
    log("gunner target:", target)

    if target is not None and ct.can_fire(target):
        ct.fire(target)
        log(f"gunner fired at {target}")
        player.last_fired_round = ct.get_current_round()
        player.skipped_firing_turns = 0
    else:
        if player.global_titanium <= GameConstants.GUNNER_ROTATE_COST[0] + 50:
            current_dir = ct.get_direction()
            rotate_dir = None
            rotate_dist = INF
            for (_eid, etype, pos) in vc.enemy_units:
                if etype not in TURRET_TYPES:
                    continue
                dist = my_pos.distance_squared(pos)
                if dist > 2:
                    continue
                desired_dir = my_pos.direction_to(pos)
                if desired_dir == current_dir:
                    continue
                if dist < rotate_dist:
                    rotate_dist = dist
                    rotate_dir = desired_dir
            if rotate_dir is not None and ct.can_rotate(rotate_dir):
                ct.rotate(rotate_dir)
                player.skipped_firing_turns = 0
                log(f"gunner rotated toward adjacent enemy turret: {rotate_dir}")
    if ct.get_action_cooldown() == 0:
        player.skipped_firing_turns += 1
                

    if player.skipped_firing_turns >= 8:
        if len(vc.enemy_units) > 0:
            player.last_fired_round = ct.get_current_round()
            player.skipped_firing_turns -= 1
        if (ct.get_scale_percent() > 500 or player.skipped_firing_turns >= 16) and len(vc.ally_builder_bots) > 0:
            ct.self_destruct()