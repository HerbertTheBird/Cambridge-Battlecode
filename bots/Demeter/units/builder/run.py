from cambc import Controller, Position, EntityType

import map as map_mod
import nav
import bfs_nav
import comms
from globals import (
    State,
    Symmetry,
    DIRECTIONS,
    USE_ARMOURED_CONVEYORS,
    USE_LAUNCHERS,
    START_USING_ARMOURED_CONVEYORS_THRESHOLD,
    NUM_RUSHING,
    BUGNAV_RESERVE_US,
    END_TURN_RESERVE_US,
)
from helpers import get_foundry_position_idxs, is_in_vision
from log import log, log_time
from units.builder.decide_state import decide_state
from units.builder.build import safe_destroy
from units.builder.logic import (
    clear_state,
    try_upgrade_foundry_placeholder,
    update_broken_chains,
    prune_no_output_found,
    is_enemy_armoured_conveyor,
    try_issue_launcher_order,
    try_build_remembered,
    try_heal,
    try_upgrade_conveyor,
    advance_bfs,
    sync_bfs_destination,
    safe_place_marker,
)
from map import on_map

from units.builder.states import start_harvest_chain
from units.builder.states import extend_harvest_chain
from units.builder.states import reroute_titanium
from units.builder.states import intercept
from units.builder.states import defend
from units.builder.states import sabotage
from units.builder.states import explore

def run_builder(player, ct: Controller, my_pos: Position) -> None:
    armoured_ti_cost, armoured_ax_cost = ct.get_armoured_conveyor_cost()
    player.use_armoured_conveyors = (
        USE_ARMOURED_CONVEYORS
        and player.global_titanium >= START_USING_ARMOURED_CONVEYORS_THRESHOLD
        and player.global_titanium >= armoured_ti_cost
        and player.global_axionite >= armoured_ax_cost
    )
    
    # States can set this to request attacking a tile
    player.attack_target = None
    player.attack_reason = ""
    player.build_pos = None
    player.build_direction = None
    player.build_type = None

    # First explore destination follows ray outwards from core
    if not player.initialized_explore_ray:
        if player.core_pos is not None and ct.get_current_round() < 100:
            player.should_explore_ray = True
        player.initialized_explore_ray = True

    # Initialize ideal foundry positions so we can route back to them
    if player.foundry_position_idxs is None and player.core_pos is not None:
        player.foundry_position_idxs = {
            idx
            for idx in get_foundry_position_idxs(player.core_pos, map_mod.width, map_mod.height)
            if not map_mod.is_wall(map_mod.idx_to_pos(idx))
        }
        
        
    if ct.get_current_round() < NUM_RUSHING + 1:
        player.rushing_enemy = True

    log_time(ct, "After map checks")

    # Upgrade foundry placeholders if possible
    try_upgrade_foundry_placeholder(player, ct, my_pos)
    log_time(ct, "After checking foundry upgrades")

    prune_no_output_found(player, ct.get_current_round())

    # Check for incomplete chains
    update_broken_chains(player, ct, my_pos)
    log(f"broken chains: {player.broken_chains}")
    log_time(ct, "After broken chain scan")

    # State machine
    player.state = decide_state(player, ct, my_pos)
    if (
        nav.original_destination is not None
        and map_mod.is_confirmed_unreachable(nav.original_destination)
        and player.state in (State.START_HARVEST_CHAIN, State.EXTEND_HARVEST_CHAIN, State.DEFEND, State.REROUTE_TITANIUM)
    ):
        log(f"destination {nav.original_destination} became confirmed unreachable -> clearing state")
        clear_state(player)
    log(f"state={player.state}")

    log_time(ct, "After decide_state")

    if player.state == State.START_HARVEST_CHAIN:
        start_harvest_chain.run(player, ct)

    if player.state == State.EXTEND_HARVEST_CHAIN:
        extend_harvest_chain.run(player, ct)

    if player.state == State.REROUTE_TITANIUM:
        reroute_titanium.run(player, ct)

    if player.state == State.INTERCEPT:
        intercept.run(player, ct)

    if player.state == State.DEFEND:
        defend.run(player, ct)

    if player.state == State.SABOTAGE:
        sabotage.run(player, ct)

    if player.state == State.EXPLORE:
        explore.run(player, ct)

    log_time(ct, "After executing state")
    my_pos = player.my_pos

    # Check if we need to attack a blocking building
    if player.attack_target is None and player.state in (State.START_HARVEST_CHAIN, State.EXTEND_HARVEST_CHAIN):
        harvest_dest = nav.original_destination
        if harvest_dest is not None and is_in_vision(my_pos, harvest_dest):
            bid = map_mod.get_tile_entity_id(harvest_dest)
            if bid is not None:
                bid_team = map_mod.get_tile_entity_team(harvest_dest)
                bid_etype = map_mod.get_tile_entity_type(harvest_dest)
                if (
                    bid_team != player.my_team
                    and bid_etype != EntityType.MARKER
                    and not is_enemy_armoured_conveyor(bid_etype, bid_team, player.my_team)
                    and (ct.is_tile_passable(harvest_dest) or my_pos == harvest_dest)
                    and (map_mod is None or not map_mod.feeds_ally_turret_idx(map_mod.pos_to_idx(harvest_dest), player.my_team))
                ):
                    player.attack_target = harvest_dest
                    player.attack_reason = "chain blocked by enemy passable building"
                elif bid_team == player.my_team and bid_etype == EntityType.BARRIER:
                    player.attack_target = harvest_dest
                    player.attack_reason = "ally barrier blocking chain"

    # Attack a building if needed
    attacked = False
    log("attack target: ", player.attack_target, "reason: ", player.attack_reason)
    if player.attack_target is not None and my_pos.distance_squared(player.attack_target) <= 2:
        if my_pos != player.attack_target:
            move_dir = my_pos.direction_to(player.attack_target)
            if ct.can_move(move_dir):
                ct.move(move_dir)
                my_pos = ct.get_position()
                player.my_pos = my_pos
        if ct.can_destroy(player.attack_target):
            bbid = ct.get_tile_builder_bot_id(player.attack_target)
            if (bbid is None or bbid == ct.get_id()) and safe_destroy(player, ct, player.attack_target):
                log(f"Destroyed ally {player.attack_target} for reason: {player.attack_reason}")
                attacked = True
        if ct.can_fire(player.attack_target):
            ct.fire(player.attack_target)
            log(f"ATTACK ({player.attack_reason}) at {player.attack_target}")
            attacked = True

    log_time(ct, "After attack logic")

    # Navigate, either using launchers, BFS nav, or bugnav
    issued_launcher_order = False
    if not attacked:
        if USE_LAUNCHERS:
            issued_launcher_order = try_issue_launcher_order(player, ct, my_pos)
        
        nav.refresh_adjacent(ct)

        # Don't move if we are waiting on launcher
        if issued_launcher_order:
            log_time(ct, "After launcher request")
            
        # Try calculating global BFS
        elif nav.destination is not None:
            advance_bfs(ct, BUGNAV_RESERVE_US, draw=False)
            log_time(ct, "After possible BFS compute")

            # Try stepping BFS nav but fall back to bugnav if not ready
            if not bfs_nav.step_if_ready(player, ct):
                nav.go_to(ct)
                log_time(ct, "After bugnav")
            else:
                log_time(ct, "After BFS step")

            # Refresh position
            my_pos = ct.get_position()
            player.my_pos = my_pos
            log(f"destination={nav.destination}")
        else:
            sync_bfs_destination()

    if not attacked:
        if try_build_remembered(player, ct):
            my_pos = ct.get_position()
            player.my_pos = my_pos

    # Greedy heal
    try_heal(ct, my_pos, player.my_team, map_mod.width, map_mod.height)
    log_time(ct, "After heal")
    try_upgrade_conveyor(player, ct, my_pos)
    log_time(ct, "After conveyor upgrade")

    # Spam markers to communicate map symmetry
    if not issued_launcher_order and map_mod.symmetry != Symmetry.UNKNOWN:
        marker_value = comms.encode_symmetry(map_mod.symmetry)
        for d in DIRECTIONS:
            marker_pos = my_pos.add(d)
            if on_map(marker_pos, map_mod.width, map_mod.height) and safe_place_marker(player, ct, marker_pos, marker_value):
                break

    log_time(ct, "After marker spam")
    
    # Update previous turn info (we do this before final BFS compute to avoid not updating due to TLE)
    player.prev_health = player.health
    player.prev_global_titanium = player.global_titanium
    player.prev_global_axionite = player.global_axionite

    # Continue computing global BFS until turn end
    nav.refresh_adjacent(ct)
    advance_bfs(ct, END_TURN_RESERVE_US, draw=True)
    log_time(ct, "After end-turn BFS compute")
