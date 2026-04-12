from cambc import Controller, Position, ResourceType

from globals import State
from helpers import is_in_vision
from units.builder.logic import (
    get_nearest_enemy_threat_pos,
    get_known_core_intercept_threat,
    find_intercept_pos,
    count_ally_turrets_covering,
    should_intercept,
    find_sabotage_target,
    find_heal_target,
    find_broken_chain_target,
    find_upgradeable_axionite_placeholder,
    find_defend_target,
    count_closer_allies,
    is_ore_unblocked,
    get_ray_endpoint,
)
import map as map_mod
import nav
import vision as vc
from log import log, log_time

def decide_state(player, ct: Controller, my_pos: Position) -> State:
    # INTERCEPT if enemy threat and good turret build position/direction
    threat_result = get_nearest_enemy_threat_pos(my_pos)
    if threat_result is None:
        threat_result = get_known_core_intercept_threat(player, my_pos, "synthetic threat")
    threat_pos = None
    threat_is_core = False
    if threat_result is not None:
        threat_pos, threat_is_core = threat_result
    
    log_time(ct, "After checking threats")

    if threat_pos is not None:
        log(f"considering intercept against threat at {threat_pos} (core={threat_is_core})")
        if threat_is_core or count_ally_turrets_covering(ct, threat_pos) < 2:
            log("trying to find intercept pos")
            intercept, prio = find_intercept_pos(
                ct,
                my_pos,
                player.my_team,
                threat_pos,
                enemy_only=False,
                global_titanium=player.global_titanium,
                enemy_core_pos=player.predicted_enemy_core_pos,
                is_core_threat=threat_is_core,
            )
            log(intercept, prio)
            log_time(ct, "After find intercept pos")
            if intercept is not None:
                if prio == 3 or should_intercept(my_pos, player.core_pos):
                    log(f"intercept target at {intercept}")
                    nav.set_destination(intercept, "adjacent")
                    return State.INTERCEPT
            
    # Pre-compute sabotage info so we know which ally tiles feed the enemy
    sabotage_worthy_ally_mask = 0
    sd_result = find_sabotage_target(player, ct, my_pos) if player.global_titanium >= 20 else None
    if sd_result is not None:
        _sd_target, _sd_prio, sabotage_worthy_ally_mask = sd_result
    log_time(ct, "After pre-computing sabotage info")
        
    # HEAL if an enemy is standing on a damaged ally conveyor
    # Skip ally tiles that feed the enemy (i.e. tiles we would want to sabotage anyways)
    heal_pos = find_heal_target(player, ct, my_pos, sabotage_worthy_ally_mask)
    if heal_pos is not None:
        log(f"heal target at {heal_pos}")
        nav.set_destination(heal_pos, "adjacent")
        return State.HEAL

    # HEAL if we see a damaged ally core
    # Do not require this bot to be the closest ally since core loss ends the game.
    if player.core_pos is not None and is_in_vision(my_pos, player.core_pos):
        core_id = map_mod.get_tile_entity_id(player.core_pos)
        if (
            core_id is not None
            and ct.get_hp(core_id) < ct.get_max_hp(core_id) - 40 # Arbitrary threshold to prevent bots getting stuck healing core
        ):
            log(f"heal core at {player.core_pos}")
            nav.set_destination(player.core_pos, "adjacent")
            return State.HEAL
        
    log_time(ct, "After checking heal targets")
            
    # SABOTAGE if we see a good sabotage target and have enough titanium
    log(f"sabotage target: {sd_result}")
    if sd_result is not None:
        sd_target, prio, _sabotage_worthy_ally_mask = sd_result
        log(f"sabotage target: {sd_target} with priority {prio}")
        if prio > 0:
            log(f"sabotaging target")
            nav.set_destination(sd_target, "exact")
            player.attack_target = sd_target
            player.attack_reason = "sabotage"
            return State.SABOTAGE
        
    log_time(ct, "After checking sabotage targets")
            
    # Set destination to predicted enemy core if rushing (don't bother trying to harvest/chain/etc)
    if player.rushing_enemy and player.predicted_enemy_core_pos is not None:
        if my_pos.distance_squared(player.predicted_enemy_core_pos) <= 13:
            player.rushing_enemy = False
        else:
            log(f"rushing enemy core at {player.predicted_enemy_core_pos}")
            nav.set_destination(player.predicted_enemy_core_pos, "adjacent")
            return State.EXPLORE
        
    log_time(ct, "After checking rush conditions")

    # Sticky state so we don't oscillate between tasks
    if player.state not in (State.EXPLORE, State.HEAL, State.INTERCEPT, State.SABOTAGE, State.DEFEND):
        return player.state

    # EXTEND_HARVEST_CHAIN if we can extend a broken harvest chain
    if player.broken_chains:
        broken_chain_target = find_broken_chain_target(player, ct, my_pos)
        if broken_chain_target is not None:
            best_chain_pos, best_chain_resource = broken_chain_target
            nav.set_destination(best_chain_pos, "adjacent")
            player.harvest_ore_type = best_chain_resource
            log(f"extending broken chain at {best_chain_pos} on turn {ct.get_current_round()}")
            return State.EXTEND_HARVEST_CHAIN
    
    log_time(ct, "After checking broken chains")

    # START_HARVEST_CHAIN if there is an unserviced or unharvested ore, we can start a chain, and allies aren't too close
    player.nearest_unserviced = map_mod.get_nearest_unserviced_harvester(my_pos, ct, player.core_pos)
    if player.nearest_unserviced is None:
        player.nearest_unharvested = map_mod.get_nearest_ore_without_harvester(my_pos, ct, player.core_pos) if player.nearest_unserviced is None else None

    target = player.nearest_unserviced or player.nearest_unharvested
    
    log_time(ct, "After finding nearest unserviced/unharvested")

    if target is not None:
        bbid = ct.get_tile_builder_bot_id(target) if is_in_vision(my_pos, target) else None
        # Allow one closer ally in case it is busy with something else.
        if (
            count_closer_allies(player, target, my_pos) < 2
            and is_ore_unblocked(player, ct, target, my_pos)
            and bbid is None
        ):
            log(f"new harvest target at {target}")
            player.timeout_turns = 0
            player.harvest_ore_pos = target
            nav.set_destination(target, "adjacent")
            return State.START_HARVEST_CHAIN
        
    log_time(ct, "After checking harvest chain start conditions")

    # REROUTE_TITANIUM if we see a foundry with just one input
    if player.core_pos is not None and player.global_titanium >= 1500:
        foundry = map_mod.find_single_input_foundry(player.core_pos, player.my_team)
        if foundry is not None:
            player.foundry_pos = foundry
            return State.REROUTE_TITANIUM
    
    log_time(ct, "After checking foundry reroute conditions")

    # EXTEND_HARVEST_CHAIN if we see a foundry placeholder
    if player.core_pos is not None and player.global_titanium >= 1500:
        foundry_placeholder = find_upgradeable_axionite_placeholder(player, ct, my_pos)
        if (foundry_placeholder is not None
            and count_closer_allies(player, foundry_placeholder, my_pos) < 2):
            log(f"upgrade foundry placeholder at {foundry_placeholder}")
            nav.set_destination(foundry_placeholder, "adjacent")
            player.harvest_ore_type = ResourceType.RAW_AXIONITE
            player.harvest_ore_pos = None
            return State.EXTEND_HARVEST_CHAIN
        
    log_time(ct, "After checking foundry placeholder conditions")
        
    # DEFEND if we see a harvester with infrastructure or bare titanium ore
    defend_target = find_defend_target(player, ct, my_pos)
    if defend_target is not None and my_pos.distance_squared(defend_target) <= 32 and count_closer_allies(player, defend_target, my_pos) < 2:
        player.harvest_ore_pos = defend_target
        nav.set_destination(defend_target, "adjacent")
        log(f"defend target at {defend_target}")
        return State.DEFEND
    
    log_time(ct, "After checking defend targets")

    # EXPLORE as a last resort
    if player.should_explore_ray:
        spawn_dir = player.core_pos.direction_to(my_pos)
        endpoint = get_ray_endpoint(my_pos, spawn_dir, map_mod.width, map_mod.height)
        nav.set_destination(endpoint, "sensed")
        player.should_explore_ray = False
        log(f"initial explore dest={endpoint} using direction {spawn_dir}")
    else:
        max_iters = 10
        while max_iters > 0 and nav.is_destination_reached(my_pos):
            nav.set_destination(map_mod.get_random_tile(), "sensed")
            max_iters -= 1
        log(f"explore target={nav.destination} type={nav.destination_type}")
    return State.EXPLORE
