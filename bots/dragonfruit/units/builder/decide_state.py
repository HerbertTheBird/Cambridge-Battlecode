from cambc import Controller, EntityType, Position, ResourceType

from globals import *
from helpers import *
from units.builder.logic import *
from vision import VisionCache
from log import log, log_time

def decideState(player, ct: Controller, my_pos: Position, vc: VisionCache) -> State:
    # Scan for broken ally chains (conveyors with input but no ally output)
    if player.map is not None:
        for (bid, etype, pos) in vc.ally_conveyors:
            if etype == EntityType.BRIDGE:
                output_pos = ct.get_bridge_target(bid)
            else:
                output_pos = pos.add(ct.get_direction(bid))
            if not on_map(output_pos, player.map.width, player.map.height) or not ct.is_in_vision(output_pos):
                continue
            if not player.map.has_conveyor_inputs(pos):
                has_adj_harvester = False
                for d in CARDINAL_DIRECTIONS:
                    adj = pos.add(d)
                    if not on_map(adj, player.map.width, player.map.height):
                        continue
                    adj_entity = player.map.get_tile_entity(adj)
                    if adj_entity is not None and adj_entity[1] == EntityType.HARVESTER:
                        has_adj_harvester = True
                        break
                if not has_adj_harvester:
                    player.broken_chains.pop(output_pos, None)
                    continue
            if is_core_tile(player.core_pos, output_pos):
                player.broken_chains.pop(output_pos, None)
                continue
            out_bid = ct.get_tile_building_id(output_pos)
            if out_bid is not None:
                out_etype = ct.get_entity_type(out_bid)
                out_team = ct.get_team(out_bid)
                if out_team == player.my_team and (out_etype in CONVEYOR_TYPES or out_etype == EntityType.FOUNDRY):
                    player.broken_chains.pop(output_pos, None)
                    continue
                if out_team != player.my_team and out_etype in CONVEYOR_TYPES:
                    if player.map.feeds_ally_building_in_vision(output_pos, player.my_team, ct, core_pos=player.core_pos):
                        player.broken_chains.pop(output_pos, None)
                        continue
            resource = ct.get_stored_resource(bid)
            if resource is None:
                has_axionite_input = player.map.input_chain_reaches_resource(pos, ResourceType.RAW_AXIONITE)
                has_titanium_input = player.map.input_chain_reaches_resource(pos, ResourceType.TITANIUM)
                if has_axionite_input and not has_titanium_input:
                    resource = ResourceType.RAW_AXIONITE
                elif has_titanium_input and not has_axionite_input:
                    resource = ResourceType.TITANIUM
                else:
                    resources = player.map.get_recent_conveyor_resources(pos)
                    if ResourceType.RAW_AXIONITE in resources and ResourceType.TITANIUM not in resources:
                        resource = ResourceType.RAW_AXIONITE
                    elif ResourceType.TITANIUM in resources and ResourceType.RAW_AXIONITE not in resources:
                        resource = ResourceType.TITANIUM
                    else:
                        resource = ResourceType.TITANIUM
            player.broken_chains[output_pos] = resource
    for broken_chain in list(player.broken_chains):
        if not ct.is_in_vision(broken_chain):
            continue
        entity = player.map.get_tile_entity(broken_chain)
        if entity is not None and entity[2] == player.my_team and (entity[1] in CONVEYOR_TYPES or entity[1] == EntityType.FOUNDRY):
            player.broken_chains.pop(broken_chain, None)
    log(f"broken chains: {player.broken_chains}")
    
    log_time(ct, "After broken chain scan")

    enemy_core_anchor = get_enemy_core_anchor(player)

    if player.rush_enemy_core:
        if player.state in (State.INTERCEPT, State.SABOTAGE):
            return player.state

        threat_result = get_nearest_enemy_threat_pos(vc, my_pos) if vc.enemy_units else None
        if threat_result is None:
            threat_result = get_known_core_intercept_threat(player, my_pos, "rush synthetic threat")
        min_turret_cost = min(ct.get_gunner_cost()[0], ct.get_sentinel_cost()[0])
        if threat_result is not None and player.global_titanium >= min_turret_cost * 2:
            threat_pos, _ = threat_result
            if enemy_core_anchor is None or threat_pos.distance_squared(enemy_core_anchor) <= 100:
                intercept = find_intercept_pos(
                    ct, my_pos, player.my_team, vc, threat_pos, player.map,
                    enemy_only=True,
                    global_titanium=player.global_titanium,
                    enemy_core_pos=enemy_core_anchor,
                )
                log_time(ct, "After rush find intercept pos")
                if intercept is not None:
                    log(f"rush intercept target at {intercept}")
                    player.nav.set_destination(intercept, "adjacent")
                    return State.INTERCEPT

        if player.global_titanium >= 20:
            sd_result = find_sabotage_target(player, ct, my_pos, vc)
            log(f"rush sabotage target: {sd_result}")
            log_time(ct, "After rush finding sabotage target")
            if sd_result is not None:
                sd_target, prio = sd_result
                if enemy_core_anchor is None or sd_target.distance_squared(enemy_core_anchor) <= 128:
                    if prio >= 2 or (prio > 0 and player.global_titanium >= 100):
                        player.nav.set_destination(sd_target, "exact")
                        player.attack_target = sd_target
                        player.attack_reason = "sabotage"
                        return State.SABOTAGE

        if enemy_core_anchor is not None:
            player.nav.set_destination(enemy_core_anchor, "exact")
        return State.EXPLORE
    
    if player.state == State.INTERCEPT:
        return player.state

    # Priority 0: intercept enemy conveyors if threat detected
    threat_result = get_nearest_enemy_threat_pos(vc, my_pos) if should_intercept(vc, my_pos, player.core_pos) else None
    if threat_result is None:
        threat_result = get_known_core_intercept_threat(player, my_pos, "synthetic threat")
    threat_pos = None
    threat_is_core = False
    cost_mult = 3
    if threat_result is not None:
        threat_pos, threat_is_core = threat_result
        cost_mult = 2 if threat_is_core else 3
    min_turret_cost = min(ct.get_gunner_cost()[0], ct.get_sentinel_cost()[0])
    
    log_time(ct, "After checking threats")
    
    if threat_pos is not None and player.global_titanium >= min_turret_cost * cost_mult:
        if threat_is_core or count_ally_turrets_covering(ct, vc, threat_pos) < 2:
            log("trying to find intercept pos")
            intercept = find_intercept_pos(ct, my_pos, player.my_team, vc, threat_pos, player.map, enemy_only=False, global_titanium=player.global_titanium, enemy_core_pos=enemy_core_anchor)
            log_time(ct, "After find intercept pos")
            if intercept is not None:
                log(f"intercept target at {intercept}")
                player.nav.set_destination(intercept, "adjacent")
                return State.INTERCEPT
        
    # Priority 0.5: heal damaged ally conveyors that enemy bots are standing on
    best_heal_pos = None
    best_heal_dist = INF
    for (_eid, etype, epos) in vc.enemy_units:
        if etype != EntityType.BUILDER_BOT:
            continue
        bid = ct.get_tile_building_id(epos)
        if bid is None:
            continue
        if ct.get_team(bid) != player.my_team:
            continue
        btype = ct.get_entity_type(bid)
        if btype not in CONVEYOR_TYPES:
            continue
        if ct.get_hp(bid) >= ct.get_max_hp(bid):
            continue
        dist = my_pos.distance_squared(epos)
        if dist < best_heal_dist:
            best_heal_dist = dist
            best_heal_pos = epos
    if best_heal_pos is not None:
        # Only go if we are the closest ally bot
        closer_ally = False
        for (_eid, apos) in vc.ally_builder_bots:
            if apos.distance_squared(best_heal_pos) < best_heal_dist:
                closer_ally = True
                break
        if not closer_ally:
            log(f"heal target at {best_heal_pos}")
            player.nav.set_destination(best_heal_pos, "adjacent")
            return State.HEAL

    # Priority 0.6: if we can see a damaged ally core, go heal it.
    # Do not require this bot to be the closest ally since core loss ends the game.
    if vc.core_pos is not None and ct.is_in_vision(vc.core_pos):
        core_id = ct.get_tile_building_id(vc.core_pos)
        if (
            core_id is not None
            and ct.get_team(core_id) == player.my_team
            and ct.get_entity_type(core_id) == EntityType.CORE
            and ct.get_hp(core_id) < ct.get_max_hp(core_id) - 40 # Arbitrary threshold to prevent bots getting stuck healing core
        ):
            log(f"heal core at {vc.core_pos}")
            player.nav.set_destination(vc.core_pos, "adjacent")
            return State.HEAL
            
    log_time(ct, "After checking heals")

    if player.state != State.EXPLORE and player.state != State.HEAL:
        return player.state

    # Priority 1: unserviced harvester; Priority 2: unharvested ore
    target = player.map.get_nearest_unserviced_harvester(my_pos, ct)
    if target is None:
        target = player.map.get_nearest_ore_without_harvester(my_pos, ct)

    log_time(ct, "After finding ore/harvest target")

    if target is not None:
        # Allow one closer ally in case it is busy with something else.
        if (
            count_closer_allies(player, target, my_pos, vc) < 2
            and can_start_harvest_chain_now(player, ct, my_pos, target, vc)
        ):
            log(f"new harvest target at {target}")
            player.timeout_turns = 0
            player.harvest_ore_pos = target
            player.nav.set_destination(target, "adjacent")
            return State.START_HARVEST_CHAIN

    # Priority 3: reroute titanium to foundry with single input
    if player.core_pos is not None and player.global_titanium >= 1500:
        foundry = player.map.find_single_input_foundry(player.core_pos, player.my_team)
        if foundry is not None:
            player.foundry_pos = foundry
            return State.REROUTE_TITANIUM

    # Priority 3.5: upgrade visible axionite placeholder conveyors to foundries
    if player.core_pos is not None and player.global_titanium >= 1500:
        foundry_placeholder = find_upgradeable_axionite_placeholder(player, ct, my_pos, vc)
        if (foundry_placeholder is not None
            and count_closer_allies(player, foundry_placeholder, my_pos, vc) < 2):
            log(f"upgrade foundry placeholder at {foundry_placeholder}")
            player.nav.set_destination(foundry_placeholder, "adjacent")
            player.harvest_ore_type = ResourceType.RAW_AXIONITE
            player.harvest_ore_pos = None
            return State.EXTEND_HARVEST_CHAIN

    # Sabotage: spot a core-feeding conveyor/bridge nearby with 500 resources
    if player.global_titanium >= 20:
        sd_result = find_sabotage_target(player, ct, my_pos, vc)
        log(f"sabotage target: {sd_result}")
        log_time(ct, "After finding sabotage target")
        if sd_result is not None:
            sd_target, prio = sd_result
            log(f"sabotage target priority: {prio}")
            if (prio >= 2 or prio > 0 and player.global_titanium >= 100):
                log(f"sabotage: spotted core or turret feeding building at {sd_target}")
                player.nav.set_destination(sd_target, "exact")
                player.attack_target = sd_target
                player.attack_reason = "sabotage"
                return State.SABOTAGE
        
    # Defend: protect harvesters with infrastructure, or block bare titanium ore
    defend_target = find_defend_target(player, ct, my_pos, vc)
    if defend_target is not None:
        log(f"defend target at {defend_target}")
        player.harvest_ore_pos = defend_target
        player.nav.set_destination(defend_target, "adjacent")
        return State.DEFEND

    # Extend broken ally chains: pick nearest from pre-scanned broken_chains
    if player.broken_chains:
        best_chain_pos = None
        best_chain_dist = INF
        best_chain_resource = None
        for output_pos, resource in player.broken_chains.items():
            dist = my_pos.distance_squared(output_pos)
            if dist >= best_chain_dist:
                continue
            if not can_repair_broken_chain_now(player, ct, output_pos, vc):
                continue
            if count_closer_allies(player, output_pos, my_pos, vc) >= 2:
                continue
            best_chain_dist = dist
            best_chain_pos = output_pos
            best_chain_resource = resource
        if best_chain_pos is not None:
            log(f"extending broken chain at {best_chain_pos} on turn {ct.get_current_round()}")
            player.nav.set_destination(best_chain_pos, "adjacent")
            player.harvest_ore_type = best_chain_resource
            return State.EXTEND_HARVEST_CHAIN

    # Explore
    if (not player.has_explored_first_destination
        and player.core_pos is not None
        and ct.get_current_round() < 100):
        player.has_explored_first_destination = True
        spawn_dir = player.core_pos.direction_to(my_pos)
        dx, dy = spawn_dir.delta()

        ex, ey = my_pos.x, my_pos.y

        while True:
            nx, ny = ex + dx, ey + dy
            if nx < 0 or nx >= player.map.width or ny < 0 or ny >= player.map.height:
                break
            ex, ey = nx, ny

        player.nav.set_destination(Position(ex, ey), "visited")
        log(f"initial explore dest=({ex},{ey}) using direction {spawn_dir}")
        log(f"initial explore dest=({ex},{ey}) from core offset=({dx},{dy})")
    else:
        max_iters = 10
        while max_iters > 0 and player.nav.is_destination_reached(ct, player.map):
            player.nav.set_destination(player.map.get_random_tile(), "sensed")
            max_iters -= 1
        log(f"explore target={player.nav.destination} type={player.nav.destination_type}")
    return State.EXPLORE
