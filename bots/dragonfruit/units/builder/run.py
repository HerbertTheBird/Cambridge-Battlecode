from cambc import Controller, Direction, EntityType, Environment, Position, ResourceType

from globals import *
from helpers import (get_cardinal_direction_into_core, get_foundry_positions)
from units.builder.build import *
from units.builder.logic import *
from log import log, log_time
from units.builder.decide_state import decideState

TURN_CPU_BUDGET_US = 2000
BUGNAV_RESERVE_US = 200
END_TURN_RESERVE_US = 50


def run_builder(player, ct: Controller, my_pos: Position, vc) -> None:
    player.attack_target = None  # set by state logic, attacked at the end
    player.attack_reason = ""
    player.comms.reset_turn(ct.get_current_round())
    player.map.update_vision(ct, player.comms)
    
    log_time(ct, "After vision update")
    
    if player.foundry_positions is None and player.core_pos is not None and player.map is not None:
        player.foundry_positions = {p for p in get_foundry_positions(player.core_pos, player.map.width, player.map.height)
                                    if player.map.get_tile_env(p) != Environment.WALL}

    if player.comms.symmetry is not None and player.map.symmetry == Symmetry.UNKNOWN:
        player.map.symmetry = player.comms.symmetry
        log(f"symmetry from marker: {player.map.symmetry.name}")

    predicted_enemy_core = get_predicted_enemy_core_pos(player)
    if predicted_enemy_core != player.predicted_enemy_core_pos:
        player.predicted_enemy_core_pos = predicted_enemy_core
        if player.predicted_enemy_core_pos is not None:
            log(f"predicted enemy core position at {player.predicted_enemy_core_pos}")
        
    log_time(ct, "After map checks")

    if RUSH_CORE and ct.get_current_round() == 1:
        player.rush_enemy_core = True
        
    # Check for axionite conveyors at foundry-eligible positions to upgrade to foundry
    if player.global_titanium >= 1500 and player.core_pos is not None:
        for d in DIRECTIONS:
            adj = my_pos.add(d)
            if not on_map(adj, player.map.width, player.map.height) or not ct.is_in_vision(adj) or not is_foundry_position(player.core_pos, adj):
                continue
            bid = ct.get_tile_building_id(adj)
            if bid is None:
                continue
            if ct.get_team(bid) != player.my_team:
                continue
            etype = ct.get_entity_type(bid)
            if etype != EntityType.CONVEYOR:
                continue
            is_axionite = (ct.get_stored_resource(bid) == ResourceType.RAW_AXIONITE or
                            player.map.has_recent_conveyor_resource(adj, ResourceType.RAW_AXIONITE) or
                            player.map.input_chain_reaches_resource(adj, ResourceType.RAW_AXIONITE))
            is_titanium = (ct.get_stored_resource(bid) == ResourceType.TITANIUM or
                            player.map.has_recent_conveyor_resource(adj, ResourceType.TITANIUM) or
                            player.map.input_chain_reaches_resource(adj, ResourceType.TITANIUM))
            if (is_axionite and not is_titanium) and ct.can_destroy(adj):
                if ct.get_tile_builder_bot_id(adj) is not None:
                    continue
                # Check if there's already an adjacent foundry — if so, build splitter toward it
                adjacent_foundry_dir = None
                for fd in CARDINAL_DIRECTIONS:
                    fpos = adj.add(fd)
                    if not on_map(fpos, player.map.width, player.map.height) or not ct.is_in_vision(fpos):
                        continue
                    fbid = ct.get_tile_building_id(fpos)
                    if fbid is not None and ct.get_entity_type(fbid) == EntityType.FOUNDRY and ct.get_team(fbid) == player.my_team:
                        adjacent_foundry_dir = fd
                        break
                safe_destroy(player, ct, adj, vc)
                log(f"destroyed to build foundry")
                if adjacent_foundry_dir is not None and can_build_conveyor_here(adj, adjacent_foundry_dir, ct, my_pos, player.my_team, player.map, vc=vc):
                    safe_build_conveyor(player, ct, adj, adjacent_foundry_dir)
                    log(f"upgraded axionite conveyor to splitter at {adj} -> foundry")
                else:
                    safe_build_foundry(player, ct, adj)
                    log(f"upgraded axionite conveyor to foundry at {adj}")
                break
            
    log_time(ct, "After checking foundry upgrades")

    prev_state = player.state
    player.state = decideState(player, ct, my_pos, vc)
    log(f"state={player.state}")
    
    log_time(ct, "After decideState")

    if player.state == State.START_HARVEST_CHAIN:
        nearest_unserviced = player.map.get_nearest_unserviced_harvester(my_pos, ct)
        if nearest_unserviced is not None:
            player.harvest_ore_pos = nearest_unserviced
        else:
            nearest_without_harvester = player.map.get_nearest_ore_without_harvester(my_pos, ct)
            if player.harvest_ore_pos is not None and my_pos.distance_squared(player.harvest_ore_pos) <= 2:
                pass  # keep current target if we are adjacent
            else:
                player.harvest_ore_pos = nearest_without_harvester
        ore_pos = player.harvest_ore_pos
        
        if ore_pos is None:
            log("no harvest target found on START_HARVEST_CHAIN")
            clear_state(player, )
        
        if ore_pos is not None and count_closer_allies(player, ore_pos, my_pos, vc) >= 2:
            log(f"2+ closer allies to {ore_pos} -> abandoning harvest")
            clear_state(player, )
            
        if ore_pos is not None and ct.is_in_vision(ore_pos) and player.state == State.START_HARVEST_CHAIN:
            ore_entity = player.map.get_tile_entity(ore_pos)

            # If we see a harvester on the target ore...
            if ore_entity is not None and ore_entity[1] == EntityType.HARVESTER:

                # Abandon if there is already an adjacent ally bridge
                if not player.map.is_unserviced_harvester(ore_pos, player.my_team):
                    log(f"ore {ore_pos} already serviced -> done")
                    clear_state(player, )

                # Otherwise, start bridge chain from this harvester
                else:
                    opposite_ore = player.map.ore_ti if ore_pos in player.map.ore_ax else player.map.ore_ax
                    best_build_pos = get_best_bridge_build_pos(ore_pos, player.core_pos, ct, player.my_team, player.map, vc, opposite_ore=opposite_ore)
                    if best_build_pos is None:
                        player.timeout_turns += 1
                        if player.timeout_turns >= TIMEOUT_TURNS:
                            log(f"timeout trying to build bridge from {ore_pos} -> abandoning")
                            clear_state(player, )
                            player.timeout_turns = 0
                            player.map.unreachable_harvesters.add(ore_pos)
                    else:
                        player.nav.set_destination(best_build_pos, "adjacent")
                        player.state = State.EXTEND_HARVEST_CHAIN
                        player.harvest_ore_type = ResourceType.RAW_AXIONITE if ore_pos in player.map.ore_ax else ResourceType.TITANIUM
                        player.harvest_ore_pos = ore_pos

            # If we don't see a harvester — barrier first, then build harvester
            else:
                is_titanium_ore = player.map.get_tile_env(ore_pos) == Environment.ORE_TITANIUM
                
                # TODO: Add better reachability check
                barrier_count = 0
                for d in CARDINAL_DIRECTIONS:
                    adj = ore_pos.add(d)
                    if not on_map(adj, player.map.width, player.map.height) or not ct.is_in_vision(adj):
                        continue
                    env = player.map.get_tile_env(adj)
                    adj_bid = ct.get_tile_building_id(adj)
                    if env == Environment.WALL or adj_bid is not None and ct.get_team(adj_bid) != player.my_team and ct.get_entity_type(adj_bid) == EntityType.BARRIER:
                        barrier_count += 1
                        
                if barrier_count == 4:                            
                    clear_state(player, )
                    player.map.unreachable_harvesters.add(ore_pos)
                    log(f"marked {ore_pos} as unreachable due to barriers")
                
                # Mark enemy building covering ore for destruction
                if (ore_entity is not None and ore_entity[2] != player.my_team
                    and ore_entity[1] != EntityType.MARKER
                    and player.global_titanium >= 100
                    and (ct.is_tile_passable(ore_pos) or my_pos == ore_pos)):
                    player.attack_target = ore_pos
                    player.attack_reason = "ore covered"

                # Compute bridge build direction to leave open
                opposite_ore = player.map.ore_ti if ore_pos in player.map.ore_ax else player.map.ore_ax
                bridge_pos = get_best_bridge_build_pos(ore_pos, player.core_pos, ct, player.my_team, player.map, vc, opposite_ore=opposite_ore)

                bbid = ct.get_tile_builder_bot_id(ore_pos)
                if (bbid is None or bbid == ct.get_id()) and ct.can_destroy(ore_pos):
                    safe_destroy(player, ct, ore_pos, vc)

                # Find barrier targets (cardinal sides minus bridge direction) once
                # we are close enough to act on the ore.
                barrier_targets = []
                if is_titanium_ore and bridge_pos is not None and my_pos.distance_squared(ore_pos) <= 2:
                    for d in CARDINAL_DIRECTIONS:
                        adj = ore_pos.add(d)
                        if adj == bridge_pos:
                            continue
                        if not on_map(adj, player.map.width, player.map.height) or not ct.is_in_vision(adj):
                            continue
                        if player.map.get_tile_env(adj) == Environment.WALL:
                            continue
                        bbid_adj = ct.get_tile_builder_bot_id(adj)
                        if bbid_adj is not None and bbid_adj != ct.get_id():
                            continue
                        bid_adj = ct.get_tile_building_id(adj)
                        if bid_adj is not None:
                            etype = ct.get_entity_type(bid_adj)
                            team = ct.get_team(bid_adj)
                            if not (etype == EntityType.MARKER or (etype == EntityType.ROAD and team == player.my_team)):
                                continue
                        barrier_targets.append(adj)

                # Skip barriers if we can't afford them all plus the harvester
                barrier_cost = len(barrier_targets) * ct.get_barrier_cost()[0]
                harvester_cost = ct.get_harvester_cost()[0]
                if player.global_titanium < barrier_cost + harvester_cost:
                    barrier_targets = []

                # If barriers are already settled and we can reach the bridge side,
                # do that directly instead of staging on the ore first.
                if (
                    my_pos.distance_squared(ore_pos) <= 2
                    and my_pos != ore_pos
                    and bridge_pos is not None
                    and not barrier_targets
                ):
                    player.nav.set_destination(bridge_pos, "exact")

                # Stand on ore and place barriers one per turn
                if my_pos == ore_pos and barrier_targets:
                    target = barrier_targets[0]
                    bid_t = ct.get_tile_building_id(target)
                    if bid_t is not None and not is_marker_building(ct, bid_t) and ct.can_destroy(target):
                        safe_destroy(player, ct, target, vc)
                        log(f"START_CHAIN: destroyed at {target} for barrier")
                    elif safe_build_barrier(player, ct, target):
                        log(f"START_CHAIN: barrier at {target} (protecting {ore_pos})")

                # All barriers placed (or none needed) — move to bridge side
                elif my_pos == ore_pos and not barrier_targets:
                    if bridge_pos is not None:
                        # Destroy ally barrier at bridge_pos if present (free destruct)
                        bp_bid = ct.get_tile_building_id(bridge_pos)
                        bbid = ct.get_tile_builder_bot_id(bridge_pos)
                        if bp_bid is not None and ct.get_team(bp_bid) == player.my_team and ct.get_entity_type(bp_bid) == EntityType.BARRIER:
                            if ct.can_destroy(bridge_pos) and (bbid is None or bbid == ct.get_id()):
                                safe_destroy(player, ct, bridge_pos, vc)
                                log(f"START_CHAIN: destroyed ally barrier at {bridge_pos} to reach bridge side")
                        player.nav.set_destination(bridge_pos, "exact")
                    else:
                        # No bridge pos, just build harvester from here
                        if safe_build_harvester(player, ct, ore_pos):
                            log(f"built harvester at {ore_pos}")

                # On the bridge side, destroy blocker and build harvester
                elif my_pos == bridge_pos:
                    bbid = ct.get_tile_builder_bot_id(ore_pos)
                    if (ore_entity is not None
                        and ore_entity[1] not in (EntityType.HARVESTER, EntityType.MARKER)
                        and player.global_titanium >= ct.get_harvester_cost()[0]
                        and ct.can_destroy(ore_pos)
                        and (bbid is None or bbid == ct.get_id())):
                        log(f"destroyed {ore_pos} to build harvester")
                        safe_destroy(player, ct, ore_pos, vc)
                    if safe_build_harvester(player, ct, ore_pos):
                        log(f"built harvester at {ore_pos}")

                # Navigate onto the ore tile first
                elif player.nav.original_destination != ore_pos or player.nav.destination_type != "exact":
                    bid = ct.get_tile_building_id(ore_pos)
                    etype = ct.get_entity_type(bid) if bid is not None else None
                    if bid is None or etype in CONVEYOR_TYPES or etype == EntityType.ROAD or etype == EntityType.MARKER:
                        player.nav.set_destination(ore_pos, "exact")
                    else:
                        player.nav.set_destination(ore_pos, "adjacent")

    if player.state == State.EXTEND_HARVEST_CHAIN:
        dest = player.nav.original_destination

        # Recalculate first bridge position as we get closer and see more tiles
        if dest is not None and player.harvest_ore_pos is not None and ct.is_in_vision(player.harvest_ore_pos):
            opposite_ore = player.map.ore_ti if player.harvest_ore_type == ResourceType.RAW_AXIONITE else player.map.ore_ax
            new_pos = get_best_bridge_build_pos(player.harvest_ore_pos, player.core_pos, ct, player.my_team, player.map, vc, opposite_ore=opposite_ore)
            if new_pos is not None and new_pos != dest:
                log(f"recalculated first bridge pos: {dest} -> {new_pos}")
                dest = new_pos
                player.nav.set_destination(new_pos, "adjacent")

        dest_entity = player.map.get_tile_entity(dest) if dest is not None else None

        # If we are chaining but don't have valid target, abandon
        if dest is None:
            log(f"error: no destination for harvest chain")
            clear_state(player, )

        # If we are chaining and reach the core, done
        elif is_core_tile(player.core_pos, dest):
            log(f"chain reaches core -> done")
            clear_state(player, )

        # If we reach an ally foundry, done
        elif dest_entity is not None and dest_entity[1] == EntityType.FOUNDRY and dest_entity[2] == player.my_team:
            log(f"chain reaches foundry -> done")
            clear_state(player, )

        # If axionite chain reaches a foundry-eligible position and we're adjacent, build foundry or conveyor
        elif (player.harvest_ore_type == ResourceType.RAW_AXIONITE and is_foundry_position(player.core_pos, dest)
                and ct.is_in_vision(dest) and my_pos.distance_squared(dest) <= 2):
            builder_on_dest = ct.get_tile_builder_bot_id(dest) is not None
            if (player.global_titanium >= 1500
                and not builder_on_dest
                and can_build_foundry_here(dest, ct, my_pos, player.my_team, player.map, vc=vc)):
                bid = ct.get_tile_building_id(dest)
                bbid = ct.get_tile_builder_bot_id(dest)
                if bid is not None and (bbid is None or bbid == ct.get_id()) and safe_destroy(player, ct, dest, vc):
                    log(f"destroyed to build foundry")
                if safe_build_foundry(player, ct, dest):
                    log(f"BUILT foundry at {dest}")
                clear_state(player, )
            else:
                # Place a conveyor toward core as placeholder until we can afford foundry
                existing_bid = ct.get_tile_building_id(dest)
                existing_etype = ct.get_entity_type(existing_bid) if existing_bid is not None else None
                core_dir = get_cardinal_direction_into_core(player.core_pos, dest)
                if (existing_etype not in CONVEYOR_TYPES
                    and core_dir is not None
                    and player.global_titanium >= ct.get_conveyor_cost()[0]
                    and can_build_conveyor_here(dest, core_dir, ct, my_pos, player.my_team, player.map, vc=vc)):
                    if existing_bid is not None and safe_destroy(player, ct, dest, vc):
                        log(f"destroyed to build conveyor")
                    if safe_build_conveyor(player, ct, dest, core_dir):
                        if player.harvest_ore_type is not None:
                            player.map.tag_conveyor_resource(dest, player.harvest_ore_type)
                        log(f"placed axionite conveyor at {dest} as foundry placeholder")
                    else:
                        log(f"failed to place axionite conveyor at {dest} as foundry placeholder")
                clear_state(player, )

        # Otherwise, keep chaining towards the core/foundry
        elif ct.is_in_vision(dest):
            build_pos = dest
            harvest_anchor = player.harvest_ore_pos
            player.harvest_ore_pos = None  # first bridge committed, stop recalculating

            built_support_launcher = False
            if USE_LAUNCHERS:
                launcher_anchors = [build_pos]
                if harvest_anchor is not None:
                    launcher_anchors.append(harvest_anchor)
                built_support_launcher = try_build_support_launcher(player,
                    ct, my_pos, vc, launcher_anchors, player.core_pos, min_spacing_sq=8
                )

            if not built_support_launcher:
                inferred_resource = player.map.infer_chain_resource_at_output(build_pos, ct)
                if inferred_resource is not None and inferred_resource != player.harvest_ore_type:
                    log(f"updated chain resource at {build_pos}: {player.harvest_ore_type} -> {inferred_resource}")
                    player.harvest_ore_type = inferred_resource

                # Compute end positions based on ore type
                end_positions = None
                if player.harvest_ore_type == ResourceType.RAW_AXIONITE:
                    end_positions = set()
                    if player.foundry_positions is not None:
                        for p in player.foundry_positions:
                            has_titanium = (
                                player.map.has_recent_conveyor_resource(p, ResourceType.TITANIUM)
                                or player.map.input_chain_reaches_resource(p, ResourceType.TITANIUM)
                            )
                            if not has_titanium and ct.is_in_vision(p):
                                p_bid = ct.get_tile_building_id(p)
                                if p_bid is not None and ct.get_entity_type(p_bid) in CONVEYOR_TYPES:
                                    has_titanium = ct.get_stored_resource(p_bid) == ResourceType.TITANIUM
                            if not has_titanium:
                                end_positions.add(p)
                elif player.harvest_ore_type == ResourceType.TITANIUM and player.core_pos is not None:
                    if player.foundry_pos is not None:
                        # Rerouting titanium to a specific foundry — re-check it still needs input
                        if not player.map.is_single_input_foundry(player.foundry_pos, player.my_team):
                            log(f"foundry at {player.foundry_pos} no longer needs titanium reroute -> redirecting to core")
                            player.foundry_pos = None
                        else:
                            end_positions = {player.foundry_pos}
                    else:
                        # Normal titanium chain - check for single-input foundry
                        foundry_target = player.map.find_single_input_foundry(player.core_pos, player.my_team)
                        if foundry_target is not None:
                            end_positions = set()
                            for dx in range(-1, 2):
                                for dy in range(-1, 2):
                                    end_positions.add(Position(player.core_pos.x + dx, player.core_pos.y + dy))
                            end_positions.add(foundry_target)

                # Check for existing conveyor/bridge we can follow
                existing_bid = ct.get_tile_building_id(build_pos)
                existing_etype = ct.get_entity_type(existing_bid) if existing_bid is not None else None
                existing_team = ct.get_team(existing_bid) if existing_bid is not None else None
                if existing_bid is not None and existing_etype in CONVEYOR_TYPES:
                    if existing_etype == EntityType.BRIDGE:
                        next_pos = ct.get_bridge_target(existing_bid)
                    else:
                        next_pos = build_pos.add(ct.get_direction(existing_bid))
                    if existing_team == player.my_team:
                        # Check if following leads to a terminal — done immediately
                        if is_core_tile(player.core_pos, next_pos):
                            log(f"ally {existing_etype.name} at {build_pos} feeds core -> done")
                            clear_state(player, )
                        else:
                            next_entity = player.map.get_tile_entity(next_pos)
                            if next_entity is not None and next_entity[1] == EntityType.FOUNDRY and next_entity[2] == player.my_team:
                                log(f"ally {existing_etype.name} at {build_pos} feeds foundry -> done")
                                clear_state(player, )
                            else:
                                player.nav.set_destination(next_pos, "adjacent")
                                log(f"ally {existing_etype.name} at {build_pos} -> following to {next_pos}")
                    elif player.core_pos is not None and next_pos.distance_squared(player.core_pos) < build_pos.distance_squared(player.core_pos):
                        # Enemy conveyor/bridge outputting closer to our core - follow it
                        player.nav.set_destination(next_pos, "adjacent")
                        log(f"enemy {existing_etype.name} at {build_pos} outputs toward core -> following to {next_pos}")
                    elif (ct.is_tile_passable(build_pos) or my_pos == build_pos) and (len(vc.enemy_units) == 0 or not player.map.feeds_ally_turret(build_pos, player.my_team)):
                        # Enemy conveyor/bridge going away from core - fire on it to build over
                        player.attack_target = build_pos
                        player.attack_reason = "enemy conveyor blocking chain"
                        log(f"enemy {existing_etype.name} at {build_pos} blocks chain -> firing to destroy")
                    else:
                        log(f"enemy {existing_etype.name} at {build_pos} blocks chain -> abandoning")
                        clear_state(player, )

                # Build ourselves if tile is empty or has a destroyable building
                elif existing_bid is None or existing_etype in (EntityType.ROAD, EntityType.MARKER) or (existing_etype == EntityType.BARRIER and existing_team == player.my_team) or (existing_etype in TURRET_TYPES and existing_team == player.my_team and len(vc.enemy_units) == 0) or (existing_etype == EntityType.LAUNCHER and existing_team == player.my_team and len(vc.enemy_units) == 0):
                    chain_resource = player.harvest_ore_type
                    allow_launcher_replacement = existing_etype == EntityType.LAUNCHER and existing_team == player.my_team and len(vc.enemy_units) == 0
                    conveyor_info = player.map.get_best_conveyor_output(build_pos, player.core_pos, ct, player.my_team, end_positions=end_positions, resource=chain_resource)

                    # Prefer conveyor; only consider bridge when conveyor can't get us closer
                    if conveyor_info is None:
                        bridge_output_pos = player.map.get_best_bridge_output(build_pos, player.core_pos, ct, player.my_team, end_positions=end_positions, resource=chain_resource)
                    else:
                        bridge_output_pos = None

                    # Determine what to build and where to target
                    built = False
                    if conveyor_info is not None:
                        conv_dir, conv_target = conveyor_info
                        # Use splitter for titanium feeding into a foundry
                        target_entity = player.map.get_tile_entity(conv_target)
                        feeds_foundry = (
                            (target_entity is not None and target_entity[1] == EntityType.FOUNDRY) or
                            (end_positions is not None and conv_target in end_positions and is_foundry_position(player.core_pos, conv_target))
                        )
                        # Determine splitter facing: face away from non-bridge feeder,
                        # but the 3 non-back sides must not have conveyors/bridges of opposite resource
                        splitter_dir = None
                        if feeds_foundry:
                            chain_resource = player.harvest_ore_type
                            foundry_dir = build_pos.direction_to(conv_target)

                            def _splitter_sides_clear(candidate_dir):
                                """Check that the 3 non-back sides have no opposite-resource conveyors/bridges."""
                                back = candidate_dir.opposite()
                                for sd in CARDINAL_DIRECTIONS:
                                    if sd == back:
                                        continue
                                    side_pos = build_pos.add(sd)
                                    if player.map.has_conflict(chain_resource, side_pos, ct):
                                        return False
                                return True

                            # Try facing away from each non-bridge feeder on a non-foundry side
                            for check_d in CARDINAL_DIRECTIONS:
                                if check_d == foundry_dir:
                                    continue
                                adj_entity = player.map.get_tile_entity(build_pos.add(check_d))
                                if (adj_entity is not None
                                        and adj_entity[1] in CONVEYOR_TYPES
                                        and adj_entity[1] != EntityType.BRIDGE
                                        and adj_entity[2] == player.my_team):
                                    feeder_output = player.map.get_conveyor_output(build_pos.add(check_d))
                                    if feeder_output == build_pos:
                                        candidate = check_d.opposite()
                                        if _splitter_sides_clear(candidate):
                                            splitter_dir = candidate
                                        break
                            # Fallback: use conv_dir if no feeder found, but still check sides
                            if splitter_dir is None and _splitter_sides_clear(conv_dir):
                                splitter_dir = conv_dir
                        use_splitter = (splitter_dir is not None
                                        and player.harvest_ore_type == ResourceType.TITANIUM
                                        and can_build_splitter_here(build_pos, splitter_dir, ct, my_pos, player.my_team, player.map, vc=vc, allow_launchers=allow_launcher_replacement))
                        can_build = use_splitter or can_build_conveyor_here(build_pos, conv_dir, ct, my_pos, player.my_team, player.map, vc=vc, allow_launchers=allow_launcher_replacement)
                        if can_build:
                            if ct.get_tile_building_id(build_pos) is not None:
                                safe_destroy(player, ct, build_pos, vc)
                            if use_splitter:
                                if safe_build_splitter(player, ct, build_pos, splitter_dir):
                                    log(f"BUILT splitter at {build_pos} facing {splitter_dir}")
                                    built = True
                                    player.nav.set_destination(conv_target, "adjacent")
                            else:
                                if safe_build_conveyor(player, ct, build_pos, conv_dir):
                                    log(f"BUILT conveyor at {build_pos} -> {conv_target}")
                                    built = True
                                    player.nav.set_destination(conv_target, "adjacent")

                    elif bridge_output_pos and can_build_bridge_here(build_pos, bridge_output_pos, ct, my_pos, player.my_team, player.map, vc=vc, allow_launchers=allow_launcher_replacement):
                        if ct.get_tile_building_id(build_pos) is not None:
                            safe_destroy(player, ct, build_pos, vc)
                        if safe_build_bridge(player, ct, build_pos, bridge_output_pos):
                            log(f"BUILT bridge at {build_pos} -> {bridge_output_pos}")
                            built = True
                            player.nav.set_destination(bridge_output_pos, "adjacent")

                    if built and player.harvest_ore_type is not None:
                        player.map.tag_conveyor_resource(build_pos, player.harvest_ore_type)

                    # If we didn't build but are adjacent, stand on the tile to block enemies
                    if not built and my_pos.distance_squared(build_pos) <= 2 and ct.is_tile_empty(build_pos):
                        move_dir = my_pos.direction_to(build_pos)
                        if ct.can_move(move_dir):
                            ct.move(move_dir)
                            my_pos = ct.get_position()
                            log(f"moved onto {build_pos} to block enemies")

                # Unhandled building type blocks chain - abandon
                else:
                    log(f"{existing_etype.name if existing_etype else 'unknown'} at {build_pos} blocks chain -> abandoning")
                    clear_state(player, )

        # If we updated destination but it turns out to be invalid, abandon
        if player.nav.original_destination is None:
            log(f"error after updating: no destination for harvest chain")
            clear_state(player, )

    if player.state == State.REROUTE_TITANIUM:
        # Verify foundry still needs titanium input
        foundry_inputs = player.map.get_conveyor_input_count(player.foundry_pos) if player.foundry_pos and player.map else 0
        if player.foundry_pos is None or foundry_inputs >= 2:
            log(f"foundry reroute no longer needed -> done")
            clear_state(player, )
        else:
            # First try the simple local reroute case: break an adjacent titanium conveyor/bridge
            # and rebuild it to face the foundry.
            ti_source = find_adjacent_foundry_reroute_source(player, ct, my_pos, player.foundry_pos)
            # Otherwise fall back to the broader "pick a titanium source and extend from there" logic.
            if ti_source is None:
                ti_source = find_nearest_titanium_conveyor(ct, my_pos, vc, map_obj=player.map, my_team=player.my_team, target_foundry=player.foundry_pos)
            if ti_source is None:
                ti_source = player.map.find_nearest_conveyor_with_resource(my_pos, ResourceType.TITANIUM, my_team=player.my_team, target_foundry=player.foundry_pos)
            if ti_source is not None:
                ti_pos = ti_source
                if my_pos.distance_squared(ti_pos) <= 2:
                    # Adjacent - destroy and start chain from here to foundry
                    if safe_destroy(player, ct, ti_pos, vc):
                        log(f"destroyed titanium conveyor at {ti_pos} for foundry reroute")
                        player.nav.set_destination(ti_pos, "adjacent")
                        player.state = State.EXTEND_HARVEST_CHAIN
                        player.harvest_ore_type = ResourceType.TITANIUM
                else:
                    player.nav.set_destination(ti_pos, "adjacent")
            else:
                # Walk toward nearest titanium ore to find conveyors
                nearest_ti = player.map.get_nearest_titanium_ore(my_pos)
                if nearest_ti is not None:
                    player.nav.set_destination(nearest_ti, "adjacent")
                if player.nav.is_destination_reached(ct, player.map):
                    clear_state(player, )

    if player.state == State.INTERCEPT:
        enemy_core_anchor = get_enemy_core_anchor(player)
        enemy_result = get_nearest_enemy_threat_pos(vc, my_pos)
        if enemy_result is None:
            enemy_result = get_known_core_intercept_threat(player, my_pos, "intercept synthetic threat")
        if enemy_result is None:
            log("no visible or synthetic enemies to intercept -> abandoning")
            clear_state(player, )
        elif not enemy_result[1] and count_ally_turrets_covering(ct, vc, enemy_result[0]) >= 2:
            log("enough ally turrets covering threat -> abandoning intercept")
            clear_state(player, )
        else:
            # Revalidate intercept pos once per turn (skip if we just entered this state)
            if prev_state == State.INTERCEPT:
                if player.rush_enemy_core:
                    threat_result = get_nearest_enemy_threat_pos(vc, my_pos) if vc.enemy_units else None
                else:
                    threat_result = get_nearest_enemy_threat_pos(vc, my_pos) if should_intercept(vc, my_pos, player.core_pos) else None
                if threat_result is None:
                    threat_result = get_known_core_intercept_threat(player,
                        player.nav.original_destination if player.nav.original_destination is not None else my_pos,
                        "recalculated intercept synthetic threat"
                    )
                threat_pos = threat_result[0] if threat_result is not None else None
                if threat_pos is None:
                    log("recalculated intercept: no threat -> abandoning")
                    clear_state(player, )
                elif player.nav.original_destination is not None and is_valid_intercept_pos(
                        player.nav.original_destination, ct, player.my_team, threat_pos, my_pos,
                        map_obj=player.map, global_titanium=player.global_titanium, enemy_core_pos=enemy_core_anchor):
                    pass  # existing intercept pos still valid, keep it
                else:
                    # Existing pos invalid, do full recalculation
                    player.state = State.EXPLORE # Clear state in the meantime to prevent getting stuck if find_intercept_pos TLEs
                    new_intercept = find_intercept_pos(ct, my_pos, player.my_team, vc, threat_pos, player.map, enemy_only=player.rush_enemy_core, global_titanium=player.global_titanium, enemy_core_pos=enemy_core_anchor)
                    if new_intercept is not None:
                        player.state = State.INTERCEPT
                        log(f"recalculated intercept: {player.nav.original_destination} -> {new_intercept}")
                        player.nav.set_destination(new_intercept, "adjacent")
                    else:
                        log("recalculated intercept: no valid pos -> abandoning")
                        clear_state(player, )
            intercept_pos = player.nav.original_destination
            log(f"intercepting at {intercept_pos}")
            # Revalidate: check input chain is still intact
            if intercept_pos is not None and ct.is_in_vision(intercept_pos):
                # Check if position is still fed by an adjacent harvester on ore
                # or by a valid input chain terminating at intercept_pos.
                still_valid = (
                    player.map is not None
                    and (
                        player.map.has_adjacent_ore_harvester(intercept_pos)
                        or player.map.has_valid_input_chain(intercept_pos)
                    )
                )
                if not still_valid:
                    log(f"intercept at {intercept_pos}: input chain broken, abandoning")
                    clear_state(player, )
            if intercept_pos is not None and my_pos.distance_squared(intercept_pos) <= 2:
                enemy_result = get_nearest_enemy_threat_pos(vc, my_pos)
                if enemy_result is None:
                    enemy_result = get_known_core_intercept_threat(player, intercept_pos)
                if enemy_result is not None:
                    log(f"threat at {enemy_result[0]} -> trying to intercept")
                    enemy_pos = enemy_result[0]
                    direction = get_sentinel_direction(intercept_pos, enemy_pos, ct, player.map)
                    if direction is not None:
                        bid = ct.get_tile_building_id(intercept_pos)
                        if bid is not None:
                            bid_team = ct.get_team(bid)
                            bid_etype = ct.get_entity_type(bid)
                            # Abort if the same allied turret we would build is already here.
                            if (bid_team == player.my_team
                                and bid_etype in TURRET_TYPES
                                and bid_etype == get_best_turret_type(intercept_pos, enemy_core_anchor, ct, None, player.map)
                                and ct.get_direction(bid) == direction):
                                clear_state(player, )
                            # Skip if the building feeds one of our turrets
                            elif (player.map is not None
                                    and bid_team != player.my_team
                                    and player.map.feeds_ally_turret(intercept_pos, player.my_team)):
                                log(f"intercept at {intercept_pos}: feeds ally turret, abandoning")
                                clear_state(player, )
                            # Destroy enemy building if present and we can afford to kill it
                            elif (bid_team != player.my_team
                                    and bid_etype != EntityType.MARKER
                                    and (ct.is_tile_passable(intercept_pos) or my_pos == intercept_pos)):
                                kill_cost = attack_cost_to_destroy(ct, bid)
                                if player.global_titanium >= kill_cost:
                                    player.attack_target = intercept_pos
                                    player.attack_reason = "intercept enemy passable"
                                else:
                                    log(f"intercept: can't afford to kill at {intercept_pos} (need {kill_cost}, have {player.global_titanium})")
                                    clear_state(player, )
                            else:
                                bbid = ct.get_tile_builder_bot_id(intercept_pos)
                                if (bbid is None or bbid == ct.get_id()) and ct.can_destroy(intercept_pos) and safe_destroy(player, ct, intercept_pos, vc):
                                    log("destroyed to build turret")
                                if build_best_turret(ct, intercept_pos, direction, enemy_core_anchor, enemy_pos, player.map):
                                    clear_state(player, )
                        else:
                            if build_best_turret(ct, intercept_pos, direction, enemy_core_anchor, enemy_pos, player.map):
                                clear_state(player, )


    if player.state == State.DEFEND:
        ore_pos = player.harvest_ore_pos
        log(f"defending {ore_pos}")
        if ore_pos is None:
            clear_state(player, )
        elif player.map.get_tile_env(ore_pos) == Environment.ORE_AXIONITE:
            clear_state(player, )
        elif ct.is_in_vision(ore_pos):
            ore_bid = ct.get_tile_building_id(ore_pos)
            ore_etype = ct.get_entity_type(ore_bid) if ore_bid is not None else None

            if ore_etype is None or ore_etype == EntityType.MARKER:
                # No building on ore — place barrier directly
                if my_pos.distance_squared(ore_pos) <= 2:
                    if ct.can_build_barrier(ore_pos):
                        ct.build_barrier(ore_pos)
                        log(f"DEFEND: barrier on bare ore at {ore_pos}")
                    clear_state(player, )

            elif ore_etype == EntityType.HARVESTER:
                # Barrier unprotected cardinal sides, farthest from core first
                targets = get_barrier_targets(ore_pos, player.core_pos, ct, player.map)
                log(f"DEFEND: barrier targets for {ore_pos} are {targets}")
                if not targets:
                    built_support_launcher = (
                        USE_LAUNCHERS
                        and try_build_support_launcher(player, ct, my_pos, vc, [ore_pos], player.core_pos, min_spacing_sq=8)
                    )
                    if not built_support_launcher:
                        log(f"DEFEND: all sides protected at {ore_pos}")
                        clear_state(player, )
                else:
                    target = targets[0]
                    player.nav.set_destination(target, "adjacent")
                    log(f"DEFEND: navigating to {target}")
                    if my_pos.distance_squared(target) <= 2:
                        bid = ct.get_tile_building_id(target)
                        bbid = ct.get_tile_builder_bot_id(target)
                        if bid is not None and (bbid is None or bbid == ct.get_id()) and not is_marker_building(ct, bid) and ct.get_team(bid) == player.my_team:
                            safe_destroy(player, ct, target, vc)
                            log(f"DEFEND: destroyed road at {target} to build barrier")
                        if safe_build_barrier(player, ct, target):
                            log(f"DEFEND: barrier at {target} (protecting {ore_pos})")
                        # Re-check; if can't build (e.g. no resources), move on
                        remaining = get_barrier_targets(ore_pos, player.core_pos, ct, player.map)
                        if not remaining:
                            clear_state(player, )

            else:
                # Something unexpected on ore (barrier already placed, etc.)
                clear_state(player, )

    if (USE_LAUNCHERS
        and player.state == State.EXPLORE
        and len(vc.enemy_units) == 0
        and player.global_titanium >= max(120, ct.get_launcher_cost()[0] * 4)
        and ct.get_current_round() - player.last_support_launcher_round >= 20):
        explore_objective = player.nav.original_destination if player.nav.destination_type == "adjacent" else player.nav.destination
        try_build_support_launcher(player, ct, my_pos, vc, [my_pos], explore_objective, min_spacing_sq=20)

    if player.state == State.SABOTAGE:
        enemy_core_anchor = get_enemy_core_anchor(player)
        dest = player.nav.original_destination
        need_retarget = dest is None or dest == enemy_core_anchor
        # If targeting a specific building, check it's still valid
        if not need_retarget and dest is not None and ct.is_in_vision(dest):
            if not get_sabotage_target_priority(player, ct, dest, vc):
                need_retarget = True
        # When close to enemy core, look for a specific building to destroy
        if need_retarget:
            sd_result = find_sabotage_target(player, ct, my_pos, vc)
            if sd_result is not None:
                sd_target = sd_result[0]
                log(f"sabotage: targeting enemy building at {sd_target}")
                player.nav.set_destination(sd_target, "exact")
            elif enemy_core_anchor is None:
                player.state = State.EXPLORE
                player.nav.clear_destination()
            elif dest != enemy_core_anchor:
                player.nav.set_destination(enemy_core_anchor, "exact")

        # Scan adjacent tiles for sabotage target
        if player.global_titanium < 20 or (not player.rush_enemy_core and player.attack_target is None and player.global_titanium < 100):
            clear_state(player, )
            log(f"no resources to sabotage -> abandoning")
        elif player.attack_target is None:
            for d in ALL_DIRECTIONS:
                if d != Direction.CENTRE and not ct.can_move(d):
                    continue
                target_pos = my_pos.add(d)
                if not on_map(target_pos, player.map.width, player.map.height) or not ct.is_in_vision(target_pos):
                    continue
                sabotage_priority = get_sabotage_target_priority(player, ct, target_pos, vc)
                if sabotage_priority > 0:
                    player.attack_target = target_pos
                    player.attack_reason = "sabotage"
                    log(f"sabotage: targeting enemy building at {player.attack_target}")
                    break

    if player.rush_enemy_core and player.nav.destination is None and player.predicted_enemy_core_pos is not None:
        player.nav.set_destination(player.predicted_enemy_core_pos, "exact")
                
    log_time(ct, "After executing state")

    # Fire on enemy buildings/roads blocking harvest chain, or destroy ally barriers in the way
    if player.attack_target is None and player.state in (State.START_HARVEST_CHAIN, State.EXTEND_HARVEST_CHAIN):
        harvest_dest = player.nav.original_destination
        if harvest_dest is not None and ct.is_in_vision(harvest_dest):
            bid = ct.get_tile_building_id(harvest_dest)
            if bid is not None:
                bid_team = ct.get_team(bid)
                bid_etype = ct.get_entity_type(bid)
                if (bid_team != player.my_team
                    and not is_marker_building(ct, bid)
                    and (ct.is_tile_passable(harvest_dest) or my_pos == harvest_dest)
                    and (player.map is None or not player.map.feeds_ally_turret(harvest_dest, player.my_team))):
                    player.attack_target = harvest_dest
                    player.attack_reason = "chain blocked by enemy passable building"
                elif bid_team == player.my_team and bid_etype == EntityType.BARRIER:
                    player.attack_target = harvest_dest
                    player.attack_reason = "ally barrier blocking chain"

    # --- Unified fire logic ---
    # Move onto target and fire if adjacent; otherwise nav will get us closer
    attacked = False
    if player.attack_target is not None and my_pos.distance_squared(player.attack_target) <= 2:
        if my_pos != player.attack_target:
            move_dir = my_pos.direction_to(player.attack_target)
            if ct.can_move(move_dir):
                ct.move(move_dir)
                my_pos = ct.get_position()
        if ct.can_destroy(player.attack_target):
            bbid = ct.get_tile_builder_bot_id(player.attack_target)
            if (bbid is None or bbid == ct.get_id()) and safe_destroy(player, ct, player.attack_target, vc):
                log(f"Destroyed ally {player.attack_target} for reason: {player.attack_reason}")
                attacked = True
        if ct.can_fire(player.attack_target):
            ct.fire(player.attack_target)
            log(f"ATTACK ({player.attack_reason}) at {player.attack_target}")
            attacked = True
            
    log_time(ct, "After attack logic")

    # Nav to destination (skip if we just attacked)
    issued_launcher_order = False
    if not attacked:
        if USE_LAUNCHERS:
            issued_launcher_order = try_issue_launcher_order(player, ct, my_pos)
        player.nav.refresh_adjacent(ct, player.map)
        log_time(ct, "After refresh adjacent")
        if issued_launcher_order:
            log_time(ct, "After launcher request")
        elif player.nav.destination is not None:
            a_star_target = player.nav.original_destination if player.nav.destination_type == "adjacent" else player.nav.destination
            player.a_star_nav.set_destination(a_star_target, player.nav.destination_type)
            pre_nav_budget = max(0, TURN_CPU_BUDGET_US - ct.get_cpu_time_elapsed() - BUGNAV_RESERVE_US)
            if pre_nav_budget > 0:
                player.a_star_nav.advance_compute(ct, player.map, pre_nav_budget, draw=False)
            
            log_time(ct, "After possible A* compute")
            
            if not player.a_star_nav.step_if_ready(ct):
                player.nav.go_to(ct, player.map)
                log_time(ct, "After bugnav")
            else:
                log_time(ct, "After A* step")
                
            my_pos = ct.get_position()
            log(f"destination={player.nav.destination}")
        else:
            player.a_star_nav.clear_destination()

    try_heal(ct, my_pos, player.my_team, player.map.width, player.map.height)
    
    log_time(ct, "After heal")
    
    # Make sure to update important info before A* final compute
    player.prev_health = player.health
    player.prev_global_titanium = player.global_titanium
    player.prev_global_axionite = player.global_axionite

    # Place a marker encoding symmetry on the first empty adjacent tile
    if not issued_launcher_order and player.map.symmetry != Symmetry.UNKNOWN:
        marker_value = player.comms.encode_symmetry(player.map.symmetry)
        for d in DIRECTIONS:
            marker_pos = my_pos.add(d)
            if on_map(marker_pos, player.map.width, player.map.height) and safe_place_marker(player, ct, marker_pos, marker_value):
                break
            
    log_time(ct, "After marker spam")
    
    player.nav.refresh_adjacent(ct, player.map)
    if player.nav.destination is not None:
        a_star_target = player.nav.original_destination if player.nav.destination_type == "adjacent" else player.nav.destination
        player.a_star_nav.set_destination(a_star_target, player.nav.destination_type)
        end_turn_budget = max(0, TURN_CPU_BUDGET_US - ct.get_cpu_time_elapsed() - END_TURN_RESERVE_US)
        if end_turn_budget > 0:
            player.a_star_nav.advance_compute(ct, player.map, end_turn_budget, draw=True)
    else:
        player.a_star_nav.clear_destination()
        
    log_time(ct, "After end-turn A* compute")
