from cambc import Controller, Position

from log import log
import vision as vc
from units.sentinel.combat import (
    choose_target, 
    choose_passive_target,
    should_wait_to_sync_shot,
)

def run_sentinel(player, ct: Controller, my_pos: Position) -> None:
    # Initialize last fired round on first turn
    if player.last_fired_round == 0:
        player.last_fired_round = ct.get_current_round()

    # Prioritize enemy units
    target = choose_target(ct, my_pos)
    log("turret target:", target)

    # Fall back to launchers or conveyors, etc
    if target is None:
        target = choose_passive_target(ct, my_pos, player.my_team)
        log("turret passive target:", target)

    # Fire if we have a target
    if target is not None:
        if ct.can_fire(target) and not should_wait_to_sync_shot(ct, target):
            ct.fire(target)
            log(f"turret fired at {target}")
            player.last_fired_round = ct.get_current_round()
            player.skipped_firing_turns = 0
        elif ct.can_fire(target):
            log(f"turret holding fire to sync on {target}")

    # Otherwise increment skipped firing turns
    if ct.get_action_cooldown() == 0:
        player.skipped_firing_turns += 1

    # Self destruct if no nearby enemies and we haven't fired in a while
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
