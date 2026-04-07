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
    
    if ct.get_current_round() - player.last_fired_round >= 20:
        if len(vc.enemy_units) > 0:
            player.last_fired_round = ct.get_current_round()
            return
        if ct.get_scale_percent() > 500:
            ct.self_destruct()
