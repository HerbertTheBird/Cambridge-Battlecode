from cambc import Controller, Position

from globals import *
from units.breach.combat import choose_target
from log import log

def run_breach(player, ct: Controller, my_pos: Position) -> None:
    # Initialize last fired round on first turn
    if player.last_fired_round == 0:
        player.last_fired_round = ct.get_current_round()

    target = choose_target(ct, my_pos, player.enemy_core_pos)
    log("breach target:", target)

    if target is not None and ct.can_fire(target):
        ct.fire(target)
        log(f"breach fired at {target}")
        player.last_fired_round = ct.get_current_round()
        player.skipped_firing_turns = 0

    if ct.get_action_cooldown() == 0:
        player.skipped_firing_turns += 1
