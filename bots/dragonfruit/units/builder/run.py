from cambc import Controller, EntityType, Environment, Position, ResourceType

from globals import *
from helpers import get_foundry_positions
from log import log, log_time
from units.builder.build import *
from units.builder.decide_state import decideState
from units.builder.logic import *
from units.builder.states import (
    run_defend,
    run_explore,
    run_extend_harvest_chain,
    run_intercept,
    run_reroute_titanium,
    run_sabotage,
    run_start_harvest_chain,
)

BUGNAV_RESERVE_US = 200
END_TURN_RESERVE_US = 50


def run_builder(player, ct: Controller, my_pos: Position, vc) -> None:
    
    # States can set this to request attacking a tile
    player.attack_target = None
    player.attack_reason = ""

    # First explore destination follows ray outwards from core
    if not player.initialized and player.core_pos is not None and ct.get_current_round() < 100:
        player.should_explore_ray = True

    # Initialize ideal foundry positions so we can route back to them
    if player.foundry_positions is None and player.core_pos is not None:
        player.foundry_positions = {
            p
            for p in get_foundry_positions(player.core_pos, player.map.width, player.map.height)
            if player.map.get_tile_env(p) != Environment.WALL
        }

    # Look for nearby ores we should harvest
    player.nearest_unserviced = player.map.get_nearest_unserviced_harvester(my_pos, ct)
    player.nearest_unharvested = player.map.get_nearest_ore_without_harvester(my_pos, ct) if player.nearest_unserviced is None else None

    log_time(ct, "After map checks")

    # Upgrade foundry placeholders if possible
    if player.global_titanium >= 1500 and player.core_pos is not None:
        for d in DIRECTIONS:
            adj = my_pos.add(d)
            if not on_map(adj, player.map.width, player.map.height) or not ct.is_in_vision(adj) or not is_foundry_position(player.core_pos, adj):
                continue
            bid = ct.get_tile_building_id(adj)
            if bid is None or ct.get_team(bid) != player.my_team or ct.get_entity_type(bid) != EntityType.CONVEYOR:
                continue
            is_axionite = (
                ct.get_stored_resource(bid) == ResourceType.RAW_AXIONITE
                or player.map.has_recent_conveyor_resource(adj, ResourceType.RAW_AXIONITE)
                or player.map.input_chain_reaches_resource(adj, ResourceType.RAW_AXIONITE)
            )
            is_titanium = (
                ct.get_stored_resource(bid) == ResourceType.TITANIUM
                or player.map.has_recent_conveyor_resource(adj, ResourceType.TITANIUM)
                or player.map.input_chain_reaches_resource(adj, ResourceType.TITANIUM)
            )
            if not ((is_axionite and not is_titanium) and ct.can_destroy(adj)):
                continue
            if ct.get_tile_builder_bot_id(adj) is not None:
                continue

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
            log("destroyed to build foundry")
            if adjacent_foundry_dir is not None and can_build_conveyor_here(adj, adjacent_foundry_dir, ct, my_pos, player.my_team, player.map, vc=vc):
                safe_build_conveyor(player, ct, adj, adjacent_foundry_dir)
                log(f"upgraded axionite conveyor to splitter at {adj} -> foundry")
            else:
                safe_build_foundry(player, ct, adj)
                log(f"upgraded axionite conveyor to foundry at {adj}")
            break

        log_time(ct, "After checking foundry upgrades")

    # Check for incomplete chains
    update_broken_chains(player, ct, vc)
    log(f"broken chains: {player.broken_chains}")
    log_time(ct, "After broken chain scan")

    # State machine
    player.state = decideState(player, ct, my_pos, vc)
    log(f"state={player.state}")

    log_time(ct, "After decideState")

    if player.state == State.START_HARVEST_CHAIN:
        run_start_harvest_chain(player, ct, vc)

    if player.state == State.EXTEND_HARVEST_CHAIN:
        run_extend_harvest_chain(player, ct, vc)

    if player.state == State.REROUTE_TITANIUM:
        run_reroute_titanium(player, ct, vc)

    if player.state == State.INTERCEPT:
        run_intercept(player, ct, vc)

    if player.state == State.DEFEND:
        run_defend(player, ct, vc)

    if player.state == State.SABOTAGE:
        run_sabotage(player, ct, vc)
        
    if player.state == State.EXPLORE:
        run_explore(player, ct, vc)

    log_time(ct, "After executing state")
    my_pos = player.my_pos

    # Check if we need to attack a blocking building
    if player.attack_target is None and player.state in (State.START_HARVEST_CHAIN, State.EXTEND_HARVEST_CHAIN):
        harvest_dest = player.nav.original_destination
        if harvest_dest is not None and ct.is_in_vision(harvest_dest):
            bid = ct.get_tile_building_id(harvest_dest)
            if bid is not None:
                bid_team = ct.get_team(bid)
                bid_etype = ct.get_entity_type(bid)
                if (
                    bid_team != player.my_team
                    and not is_marker_building(ct, bid)
                    and (ct.is_tile_passable(harvest_dest) or my_pos == harvest_dest)
                    and (player.map is None or not player.map.feeds_ally_turret(harvest_dest, player.my_team))
                ):
                    player.attack_target = harvest_dest
                    player.attack_reason = "chain blocked by enemy passable building"
                elif bid_team == player.my_team and bid_etype == EntityType.BARRIER:
                    player.attack_target = harvest_dest
                    player.attack_reason = "ally barrier blocking chain"

    # Attack a building if needed
    attacked = False
    if player.attack_target is not None and my_pos.distance_squared(player.attack_target) <= 2:
        if my_pos != player.attack_target:
            move_dir = my_pos.direction_to(player.attack_target)
            if ct.can_move(move_dir):
                ct.move(move_dir)
                my_pos = ct.get_position()
                player.my_pos = my_pos
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

    # Navigate, either using launchers, A*, or bugnav
    issued_launcher_order = False
    if not attacked:
        if USE_LAUNCHERS:
            issued_launcher_order = try_issue_launcher_order(player, ct, my_pos)
        
        player.nav.refresh_adjacent(ct, player.map)

        # Don't move if we are waiting on launcher
        if issued_launcher_order:
            log_time(ct, "After launcher request")
            
        # Try calculating A*
        elif player.nav.destination is not None:
            advance_a_star(player, ct, BUGNAV_RESERVE_US, draw=False)
            log_time(ct, "After possible A* compute")

            # Try stepping A* but fall back to bugnav if not ready
            if not player.a_star_nav.step_if_ready(ct):
                player.nav.go_to(ct, player.map)
                log_time(ct, "After bugnav")
            else:
                log_time(ct, "After A* step")

            # Refresh position
            my_pos = ct.get_position()
            player.my_pos = my_pos
            log(f"destination={player.nav.destination}")
        else:
            sync_a_star_destination(player)

    # Greedy heal
    try_heal(ct, my_pos, player.my_team, player.map.width, player.map.height)
    log_time(ct, "After heal")

    # Spam markers to communicate map symmetry
    if not issued_launcher_order and player.map.symmetry != Symmetry.UNKNOWN:
        marker_value = player.comms.encode_symmetry(player.map.symmetry)
        for d in DIRECTIONS:
            marker_pos = my_pos.add(d)
            if on_map(marker_pos, player.map.width, player.map.height) and safe_place_marker(player, ct, marker_pos, marker_value):
                break

    log_time(ct, "After marker spam")
    
    # Update previous turn info (we do this before final A* compute to avoid not updating due to TLE)
    player.prev_health = player.health
    player.prev_global_titanium = player.global_titanium
    player.prev_global_axionite = player.global_axionite

    # Continue computing A* until turn end
    player.nav.refresh_adjacent(ct, player.map)
    advance_a_star(player, ct, END_TURN_RESERVE_US, draw=True)
    log_time(ct, "After end-turn A* compute")
