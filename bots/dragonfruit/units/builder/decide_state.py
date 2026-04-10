from cambc import Controller, EntityType, Position, ResourceType

from globals import *
from helpers import *
from units.builder.logic import *
from vision import VisionCache
from log import log, log_time

def decideState(player, ct: Controller, my_pos: Position, vc: VisionCache) -> State:
    # INTERCEPT if enemy threat and good turret build position/direction
    threat_result = get_nearest_enemy_threat_pos(vc, my_pos)
    if threat_result is None:
        threat_result = get_known_core_intercept_threat(player, my_pos, "synthetic threat")
    threat_pos = None
    threat_is_core = False
    if threat_result is not None:
        threat_pos, threat_is_core = threat_result
    
    log_time(ct, "After checking threats")

    # Pre-compute sabotage info so we know which ally tiles feed the enemy
    sabotage_worthy_ally_positions: set[Position] = set()
    sd_result = find_sabotage_target(player, ct, my_pos, vc, sabotage_worthy_ally_positions) if player.global_titanium >= 20 else None
    log_time(ct, "After pre-computing sabotage info")

    if threat_pos is not None:
        log(f"considering intercept against threat at {threat_pos} (core={threat_is_core})")
        if threat_is_core or count_ally_turrets_covering(ct, vc, threat_pos) < 2:
            log("trying to find intercept pos")
            intercept, prio = find_intercept_pos(
                ct,
                my_pos,
                player.my_team,
                vc,
                threat_pos,
                player.map,
                enemy_only=False,
                global_titanium=player.global_titanium,
                enemy_core_pos=player.predicted_enemy_core_pos,
                is_core_threat=threat_is_core,
            )
            log(intercept, prio)
            log_time(ct, "After find intercept pos")
            if intercept is not None:
                if prio == 3 or should_intercept(vc, my_pos, player.core_pos):
                    log(f"intercept target at {intercept}")
                    player.nav.set_destination(intercept, "adjacent")
                    return State.INTERCEPT
        
    # HEAL if an enemy is standing on a damaged ally conveyor
    # Skip ally tiles that feed the enemy (sabotage-worthy)
    heal_pos = find_heal_target(player, ct, my_pos, vc, sabotage_worthy_ally_positions)
    if heal_pos is not None:
        log(f"heal target at {heal_pos}")
        player.nav.set_destination(heal_pos, "adjacent")
        return State.HEAL

    # HEAL if we see a damaged ally core
    # Do not require this bot to be the closest ally since core loss ends the game.
    if player.core_pos is not None and ct.is_in_vision(player.core_pos):
        core_id = ct.get_tile_building_id(player.core_pos)
        if (
            core_id is not None
            and ct.get_hp(core_id) < ct.get_max_hp(core_id) - 40 # Arbitrary threshold to prevent bots getting stuck healing core
        ):
            log(f"heal core at {player.core_pos}")
            player.nav.set_destination(player.core_pos, "adjacent")
            return State.HEAL
            
    # SABOTAGE if we see a good sabotage target and have enough titanium
    # Reuse pre-computed sabotage result from earlier
    if player.global_titanium >= 20:
        log(f"sabotage target: {sd_result}")
        if sd_result is not None:
            sd_target, prio = sd_result
            log(f"sabotage target: {sd_target} with priority {prio}")
            if prio > 0:
                log(f"sabotaging target")
                player.nav.set_destination(sd_target, "exact")
                player.attack_target = sd_target
                player.attack_reason = "sabotage"
                return State.SABOTAGE

    if player.state not in (State.EXPLORE, State.HEAL, State.INTERCEPT, State.SABOTAGE):
        return player.state

    # EXTEND_HARVEST_CHAIN if we can extend a broken harvest chain
    if player.broken_chains:
        broken_chain_target = find_broken_chain_target(player, ct, my_pos, vc)
        if broken_chain_target is not None:
            best_chain_pos, best_chain_resource = broken_chain_target
            player.nav.set_destination(best_chain_pos, "adjacent")
            player.harvest_ore_type = best_chain_resource
            log(f"extending broken chain at {best_chain_pos} on turn {ct.get_current_round()}")
            return State.EXTEND_HARVEST_CHAIN

    # START_HARVEST_CHAIN if there is an unserviced or unharvested ore, we can start a chain, and allies aren't too close
    player.nearest_unserviced = player.map.get_nearest_unserviced_harvester(my_pos, ct)
    if player.nearest_unserviced is None:
        player.nearest_unharvested = player.map.get_nearest_ore_without_harvester(my_pos, ct) if player.nearest_unserviced is None else None

    target = player.nearest_unserviced or player.nearest_unharvested

    if target is not None:
        bbid = ct.get_tile_builder_bot_id(target) if ct.is_in_vision(target) else None
        # Allow one closer ally in case it is busy with something else.
        if (
            count_closer_allies(player, target, my_pos, vc) < 2
            and is_ore_unblocked(player, ct, target)
            and bbid is None
        ):
            log(f"new harvest target at {target}")
            player.timeout_turns = 0
            player.harvest_ore_pos = target
            player.nav.set_destination(target, "adjacent")
            return State.START_HARVEST_CHAIN

    # REROUTE_TITANIUM if we see a foundry with just one input
    if player.core_pos is not None and player.global_titanium >= 1500:
        foundry = player.map.find_single_input_foundry(player.core_pos, player.my_team)
        if foundry is not None:
            player.foundry_pos = foundry
            return State.REROUTE_TITANIUM

    # EXTEND_HARVEST_CHAIN if we see a foundry placeholder
    if player.core_pos is not None and player.global_titanium >= 1500:
        foundry_placeholder = find_upgradeable_axionite_placeholder(player, ct, my_pos, vc)
        if (foundry_placeholder is not None
            and count_closer_allies(player, foundry_placeholder, my_pos, vc) < 2):
            log(f"upgrade foundry placeholder at {foundry_placeholder}")
            player.nav.set_destination(foundry_placeholder, "adjacent")
            player.harvest_ore_type = ResourceType.RAW_AXIONITE
            player.harvest_ore_pos = None
            return State.EXTEND_HARVEST_CHAIN
        
    # DEFEND if we see a harvester with infrastructure or bare titanium ore
    defend_target = find_defend_target(player, ct, my_pos, vc)
    if defend_target is not None and my_pos.distance_squared(defend_target) <= 32 and count_closer_allies(player, defend_target, my_pos, vc) < 2:
        player.harvest_ore_pos = defend_target
        player.nav.set_destination(defend_target, "adjacent")
        log(f"defend target at {defend_target}")
        return State.DEFEND

    # EXPLORE as a last resort
    if player.should_explore_ray:
        spawn_dir = player.core_pos.direction_to(my_pos)
        endpoint = get_ray_endpoint(my_pos, spawn_dir, player.map.width, player.map.height)
        player.nav.set_destination(endpoint, "sensed")
        player.should_explore_ray = False
        log(f"initial explore dest={endpoint} using direction {spawn_dir}")
    else:
        max_iters = 10
        while max_iters > 0 and player.nav.is_destination_reached(ct, player.map):
            player.nav.set_destination(player.map.get_random_tile(), "sensed")
            max_iters -= 1
        log(f"explore target={player.nav.destination} type={player.nav.destination_type}")
    return State.EXPLORE
