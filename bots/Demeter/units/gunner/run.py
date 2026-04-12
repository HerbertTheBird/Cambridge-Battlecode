from cambc import Controller, Position, GameConstants

from log import log
import vision as vc
from units.gunner.combat import (
    choose_gunner_target, 
    choose_rotate_dir
)

def run_gunner(player, ct: Controller, my_pos: Position) -> None:
    # Initialize last fired round on first turn
    if player.last_fired_round == 0:
        player.last_fired_round = ct.get_current_round()

    # Find target to shoot at
    target = choose_gunner_target(ct, my_pos, player.my_team)
    log("gunner target:", target)

    # Fire if we have a target
    if target is not None and ct.can_fire(target):
        ct.fire(target)
        log(f"gunner fired at {target}")
        player.last_fired_round = ct.get_current_round()
        player.skipped_firing_turns = 0

    # Otherwise try to rotate toward enemy
    elif (target is None or ct.get_ammo_amount() > 0) and player.global_titanium >= GameConstants.GUNNER_ROTATE_COST[0] + 50:
        rotate_dir = choose_rotate_dir(ct, my_pos, vc.enemy_units, player.my_team)

        if rotate_dir is not None and ct.can_rotate(rotate_dir):
            ct.rotate(rotate_dir)
            player.skipped_firing_turns = 0
            log(f"gunner rotated toward adjacent enemy turret: {rotate_dir}")

    # Otherwise increment skipped firing turns
    if ct.get_action_cooldown() == 0:
        player.skipped_firing_turns += 1

    # Self destruct if no nearby enemies and we haven't fired in a while
    if player.skipped_firing_turns >= 8:
        if len(vc.enemy_units) > 0:
            player.last_fired_round = ct.get_current_round()
            player.skipped_firing_turns -= 1
        if (ct.get_scale_percent() > 500 or player.skipped_firing_turns >= 16) and len(vc.ally_builder_bots) > 0:
            ct.self_destruct()
