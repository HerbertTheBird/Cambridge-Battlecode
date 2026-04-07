from cambc import Controller, Position

from globals import *
from combat import choose_target, choose_passive_target
from log import log

def run_turret(player, ct: Controller, my_pos: Position, vc) -> None:
    if player.last_fired_round == 0:
        player.last_fired_round = ct.get_current_round()

    target = choose_target(ct, my_pos, vc)
    log("turret target:", target)
    
    if target is None:
        target = choose_passive_target(ct, my_pos, player.my_team, vc, map_obj=player.map)
        log("turret passive target:", target)
    if target is not None:
        if ct.can_fire(target):
            ct.fire(target)
            log(f"turret fired at {target}")
            player.last_fired_round = ct.get_current_round()
            player.skipped_firing_turns = 0
    elif ct.get_action_cooldown() == 0:
        player.skipped_firing_turns += 1
    
    if player.skipped_firing_turns >= 8:
        if len(vc.enemy_units) > 0:
            player.last_fired_round = ct.get_current_round()

        if (ct.get_scale_percent() > 500 or player.skipped_firing_turns >= 20) and len(vc.ally_builder_bots) > 0:
            adjacent_to_harvester = any(
                my_pos.distance_squared(hpos) == 1
                for (_bid, hpos, _team) in vc.harvesters
            )

            if not adjacent_to_harvester:
                ct.self_destruct()
            else:
                log(f"skip self-destruct: adjacent to harvester at {my_pos}")