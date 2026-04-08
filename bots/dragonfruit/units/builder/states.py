from cambc import Controller, Direction, EntityType, Environment, Position, ResourceType

from globals import *
from helpers import get_cardinal_direction_into_core, get_opposite_ore
from log import log
from units.builder.build import *
from units.builder.logic import *


def run_start_harvest_chain(player, ct: Controller, vc) -> None:
    my_pos = player.my_pos
    nearest_unserviced = player.nearest_unserviced
    if nearest_unserviced is not None:
        player.harvest_ore_pos = nearest_unserviced
    else:
        nearest_without_harvester = player.nearest_unharvested
        if player.harvest_ore_pos is not None and my_pos.distance_squared(player.harvest_ore_pos) <= 2:
            pass
        else:
            player.harvest_ore_pos = nearest_without_harvester
    ore_pos = player.harvest_ore_pos

    if ore_pos is None:
        log("no harvest target found on START_HARVEST_CHAIN")
        clear_state(player)
        return

    if count_closer_allies(player, ore_pos, my_pos, vc) >= 2:
        log(f"2+ closer allies to {ore_pos} -> abandoning harvest")
        clear_state(player)
        return

    if not ct.is_in_vision(ore_pos):
        return

    ore_entity = player.map.get_tile_entity(ore_pos)
    if ore_entity is not None and ore_entity[1] == EntityType.HARVESTER:
        if not player.map.is_unserviced_harvester(ore_pos, player.my_team):
            log(f"ore {ore_pos} already serviced -> done")
            clear_state(player)
            return

        best_build_pos = get_best_bridge_build_pos(
            ore_pos,
            player.core_pos,
            ct,
            player.my_team,
            player.map,
            vc,
            opposite_ore=get_opposite_ore(player.map, ore_pos in player.map.ore_ax),
        )
        if best_build_pos is None:
            player.timeout_turns += 1
            if player.timeout_turns >= TIMEOUT_TURNS:
                log(f"timeout trying to build bridge from {ore_pos} -> abandoning")
                clear_state(player)
                player.timeout_turns = 0
                player.map.unreachable_harvesters.add(ore_pos)
            return

        player.nav.set_destination(best_build_pos, "adjacent")
        player.state = State.EXTEND_HARVEST_CHAIN
        player.harvest_ore_type = ResourceType.RAW_AXIONITE if ore_pos in player.map.ore_ax else ResourceType.TITANIUM
        player.harvest_ore_pos = ore_pos
        return

    is_titanium_ore = player.map.get_tile_env(ore_pos) == Environment.ORE_TITANIUM
    if not is_ore_unblocked(player, ct, ore_pos):
        clear_state(player)
        player.map.unreachable_harvesters.add(ore_pos)
        log(f"marked {ore_pos} as unreachable due to barriers")
        return

    if (
        ore_entity is not None
        and ore_entity[2] != player.my_team
        and ore_entity[1] != EntityType.MARKER
        and player.global_titanium >= 100
        and (ct.is_tile_passable(ore_pos) or my_pos == ore_pos)
    ):
        player.attack_target = ore_pos
        player.attack_reason = "ore covered"

    bridge_pos = get_best_bridge_build_pos(
        ore_pos,
        player.core_pos,
        ct,
        player.my_team,
        player.map,
        vc,
        opposite_ore=get_opposite_ore(player.map, ore_pos in player.map.ore_ax),
    )

    bbid = ct.get_tile_builder_bot_id(ore_pos)
    if (bbid is None or bbid == ct.get_id()) and ct.can_destroy(ore_pos):
        safe_destroy(player, ct, ore_pos, vc)

    barrier_targets = []
    if is_titanium_ore and bridge_pos is not None and my_pos.distance_squared(ore_pos) <= 2:
        barrier_targets = [
            pos for pos in get_barrier_targets(ore_pos, player.core_pos, ct, player.map)
            if pos != bridge_pos
        ]

    barrier_cost = len(barrier_targets) * ct.get_barrier_cost()[0]
    harvester_cost = ct.get_harvester_cost()[0]
    if player.global_titanium < barrier_cost + harvester_cost:
        barrier_targets = []

    if (
        my_pos.distance_squared(ore_pos) <= 2
        and my_pos != ore_pos
        and bridge_pos is not None
        and not barrier_targets
    ):
        player.nav.set_destination(bridge_pos, "exact")

    if my_pos == ore_pos and barrier_targets:
        current_barrier_targets = [
            pos for pos in get_barrier_targets(ore_pos, player.core_pos, ct, player.map)
            if pos != bridge_pos
        ]
        if current_barrier_targets:
            target = current_barrier_targets[0]
            bid_t = ct.get_tile_building_id(target)
            if bid_t is not None:
                team_t = ct.get_team(bid_t)
                etype_t = ct.get_entity_type(bid_t)
                if team_t == player.my_team and etype_t == EntityType.ROAD and ct.can_destroy(target):
                    safe_destroy(player, ct, target, vc)
                    log(f"START_CHAIN: destroyed ally road at {target} for barrier")
            if safe_build_barrier(player, ct, target):
                log(f"START_CHAIN: barrier at {target} (protecting {ore_pos})")
        return

    if my_pos == ore_pos and not barrier_targets:
        if bridge_pos is not None:
            bp_bid = ct.get_tile_building_id(bridge_pos)
            bbid = ct.get_tile_builder_bot_id(bridge_pos)
            if bp_bid is not None and ct.get_team(bp_bid) == player.my_team and ct.get_entity_type(bp_bid) == EntityType.BARRIER:
                if ct.can_destroy(bridge_pos) and (bbid is None or bbid == ct.get_id()):
                    safe_destroy(player, ct, bridge_pos, vc)
                    log(f"START_CHAIN: destroyed ally barrier at {bridge_pos} to reach bridge side")
            player.nav.set_destination(bridge_pos, "exact")
        elif safe_build_harvester(player, ct, ore_pos):
            log(f"built harvester at {ore_pos}")
        return

    if my_pos == bridge_pos:
        bbid = ct.get_tile_builder_bot_id(ore_pos)
        if (
            ore_entity is not None
            and ore_entity[1] not in (EntityType.HARVESTER, EntityType.MARKER)
            and player.global_titanium >= ct.get_harvester_cost()[0]
            and ct.can_destroy(ore_pos)
            and (bbid is None or bbid == ct.get_id())
        ):
            log(f"destroyed {ore_pos} to build harvester")
            safe_destroy(player, ct, ore_pos, vc)
        if safe_build_harvester(player, ct, ore_pos):
            log(f"built harvester at {ore_pos}")
        return

    if player.nav.original_destination != ore_pos or player.nav.destination_type != "exact":
        bid = ct.get_tile_building_id(ore_pos)
        etype = ct.get_entity_type(bid) if bid is not None else None
        if bid is None or etype in CONVEYOR_TYPES or etype == EntityType.ROAD or etype == EntityType.MARKER:
            player.nav.set_destination(ore_pos, "exact")
        else:
            player.nav.set_destination(ore_pos, "adjacent")


def run_extend_harvest_chain(player, ct: Controller, vc) -> None:
    my_pos = player.my_pos
    dest = player.nav.original_destination

    if dest is not None and player.harvest_ore_pos is not None and ct.is_in_vision(player.harvest_ore_pos):
        new_pos = get_best_bridge_build_pos(
            player.harvest_ore_pos,
            player.core_pos,
            ct,
            player.my_team,
            player.map,
            vc,
            opposite_ore=get_opposite_ore(player.map, player.harvest_ore_type == ResourceType.RAW_AXIONITE),
        )
        if new_pos is not None and new_pos != dest:
            log(f"recalculated first bridge pos: {dest} -> {new_pos}")
            dest = new_pos
            player.nav.set_destination(new_pos, "adjacent")

    dest_entity = player.map.get_tile_entity(dest) if dest is not None else None
    if dest is None:
        log("error: no destination for harvest chain")
        clear_state(player)
        return
    if is_core_tile(player.core_pos, dest):
        log("chain reaches core -> done")
        clear_state(player)
        return
    if dest_entity is not None and dest_entity[1] == EntityType.FOUNDRY and dest_entity[2] == player.my_team:
        log("chain reaches foundry -> done")
        clear_state(player)
        return

    if (
        player.harvest_ore_type == ResourceType.RAW_AXIONITE
        and is_foundry_position(player.core_pos, dest)
        and ct.is_in_vision(dest)
        and my_pos.distance_squared(dest) <= 2
    ):
        builder_on_dest = ct.get_tile_builder_bot_id(dest) is not None
        if (
            player.global_titanium >= 1500
            and not builder_on_dest
            and can_build_foundry_here(dest, ct, my_pos, player.my_team, player.map, vc=vc)
        ):
            bid = ct.get_tile_building_id(dest)
            bbid = ct.get_tile_builder_bot_id(dest)
            if bid is not None and (bbid is None or bbid == ct.get_id()) and safe_destroy(player, ct, dest, vc):
                log("destroyed to build foundry")
            if safe_build_foundry(player, ct, dest):
                log(f"BUILT foundry at {dest}")
            clear_state(player)
            return

        existing_bid = ct.get_tile_building_id(dest)
        existing_etype = ct.get_entity_type(existing_bid) if existing_bid is not None else None
        core_dir = get_cardinal_direction_into_core(player.core_pos, dest)
        if (
            existing_etype not in CONVEYOR_TYPES
            and core_dir is not None
            and player.global_titanium >= ct.get_conveyor_cost()[0]
            and can_build_conveyor_here(dest, core_dir, ct, my_pos, player.my_team, player.map, vc=vc)
        ):
            if existing_bid is not None and safe_destroy(player, ct, dest, vc):
                log("destroyed to build conveyor")
            if safe_build_conveyor(player, ct, dest, core_dir):
                if player.harvest_ore_type is not None:
                    player.map.tag_conveyor_resource(dest, player.harvest_ore_type)
                log(f"placed axionite conveyor at {dest} as foundry placeholder")
            else:
                log(f"failed to place axionite conveyor at {dest} as foundry placeholder")
        clear_state(player)
        return

    if not ct.is_in_vision(dest):
        return

    build_pos = dest
    harvest_anchor = player.harvest_ore_pos
    player.harvest_ore_pos = None

    built_support_launcher = False
    if USE_LAUNCHERS:
        launcher_anchors = [build_pos]
        if harvest_anchor is not None:
            launcher_anchors.append(harvest_anchor)
        built_support_launcher = try_build_support_launcher(
            player, ct, my_pos, vc, launcher_anchors, player.core_pos, min_spacing_sq=8
        )

    if not built_support_launcher:
        inferred_resource = player.map.infer_chain_resource_at_output(build_pos, ct)
        if inferred_resource is not None and inferred_resource != player.harvest_ore_type:
            log(f"updated chain resource at {build_pos}: {player.harvest_ore_type} -> {inferred_resource}")
            player.harvest_ore_type = inferred_resource

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
                if not player.map.is_single_input_foundry(player.foundry_pos, player.my_team):
                    log(f"foundry at {player.foundry_pos} no longer needs titanium reroute -> redirecting to core")
                    player.foundry_pos = None
                else:
                    end_positions = {player.foundry_pos}
            else:
                foundry_target = player.map.find_single_input_foundry(player.core_pos, player.my_team)
                if foundry_target is not None:
                    end_positions = set()
                    for dx in range(-1, 2):
                        for dy in range(-1, 2):
                            end_positions.add(Position(player.core_pos.x + dx, player.core_pos.y + dy))
                    end_positions.add(foundry_target)

        existing_bid = ct.get_tile_building_id(build_pos)
        existing_etype = ct.get_entity_type(existing_bid) if existing_bid is not None else None
        existing_team = ct.get_team(existing_bid) if existing_bid is not None else None
        if existing_bid is not None and existing_etype in CONVEYOR_TYPES:
            if existing_etype == EntityType.BRIDGE:
                next_pos = ct.get_bridge_target(existing_bid)
            else:
                next_pos = build_pos.add(ct.get_direction(existing_bid))
            if existing_team == player.my_team:
                if is_core_tile(player.core_pos, next_pos):
                    log(f"ally {existing_etype.name} at {build_pos} feeds core -> done")
                    clear_state(player)
                    return
                next_entity = player.map.get_tile_entity(next_pos)
                if next_entity is not None and next_entity[1] == EntityType.FOUNDRY and next_entity[2] == player.my_team:
                    log(f"ally {existing_etype.name} at {build_pos} feeds foundry -> done")
                    clear_state(player)
                    return
                player.nav.set_destination(next_pos, "adjacent")
                log(f"ally {existing_etype.name} at {build_pos} -> following to {next_pos}")
            elif player.core_pos is not None and next_pos.distance_squared(player.core_pos) < build_pos.distance_squared(player.core_pos):
                player.nav.set_destination(next_pos, "adjacent")
                log(f"enemy {existing_etype.name} at {build_pos} outputs toward core -> following to {next_pos}")
            elif (ct.is_tile_passable(build_pos) or my_pos == build_pos) and (len(vc.enemy_units) == 0 or not player.map.feeds_ally_turret(build_pos, player.my_team)):
                player.attack_target = build_pos
                player.attack_reason = "enemy conveyor blocking chain"
                log(f"enemy {existing_etype.name} at {build_pos} blocks chain -> firing to destroy")
            else:
                log(f"enemy {existing_etype.name} at {build_pos} blocks chain -> abandoning")
                clear_state(player)
            return

        if (
            existing_bid is None
            or existing_etype in (EntityType.ROAD, EntityType.MARKER)
            or (existing_etype == EntityType.BARRIER and existing_team == player.my_team)
            or (existing_etype in TURRET_TYPES and existing_team == player.my_team and len(vc.enemy_units) == 0)
            or (existing_etype == EntityType.LAUNCHER and existing_team == player.my_team and len(vc.enemy_units) == 0)
        ):
            chain_resource = player.harvest_ore_type
            allow_launcher_replacement = existing_etype == EntityType.LAUNCHER and existing_team == player.my_team and len(vc.enemy_units) == 0
            conveyor_info = player.map.get_best_conveyor_output(
                build_pos, player.core_pos, ct, player.my_team, end_positions=end_positions, resource=chain_resource
            )
            if conveyor_info is None:
                bridge_output_pos = player.map.get_best_bridge_output(
                    build_pos, player.core_pos, ct, player.my_team, end_positions=end_positions, resource=chain_resource
                )
            else:
                bridge_output_pos = None

            built = False
            if conveyor_info is not None:
                conv_dir, conv_target = conveyor_info
                target_entity = player.map.get_tile_entity(conv_target)
                feeds_foundry = (
                    (target_entity is not None and target_entity[1] == EntityType.FOUNDRY)
                    or (end_positions is not None and conv_target in end_positions and is_foundry_position(player.core_pos, conv_target))
                )
                splitter_dir = None
                if feeds_foundry:
                    chain_resource = player.harvest_ore_type
                    foundry_dir = build_pos.direction_to(conv_target)

                    def _splitter_sides_clear(candidate_dir):
                        back = candidate_dir.opposite()
                        for sd in CARDINAL_DIRECTIONS:
                            if sd == back:
                                continue
                            side_pos = build_pos.add(sd)
                            if player.map.has_conflict(chain_resource, side_pos, ct):
                                return False
                        return True

                    for check_d in CARDINAL_DIRECTIONS:
                        if check_d == foundry_dir:
                            continue
                        adj_entity = player.map.get_tile_entity(build_pos.add(check_d))
                        if (
                            adj_entity is not None
                            and adj_entity[1] in CONVEYOR_TYPES
                            and adj_entity[1] != EntityType.BRIDGE
                            and adj_entity[2] == player.my_team
                        ):
                            feeder_output = player.map.get_conveyor_output(build_pos.add(check_d))
                            if feeder_output == build_pos:
                                candidate = check_d.opposite()
                                if _splitter_sides_clear(candidate):
                                    splitter_dir = candidate
                                break
                    if splitter_dir is None and _splitter_sides_clear(conv_dir):
                        splitter_dir = conv_dir
                use_splitter = (
                    splitter_dir is not None
                    and player.harvest_ore_type == ResourceType.TITANIUM
                    and can_build_splitter_here(
                        build_pos, splitter_dir, ct, my_pos, player.my_team, player.map, vc=vc, allow_launchers=allow_launcher_replacement
                    )
                )
                can_build = use_splitter or can_build_conveyor_here(
                    build_pos, conv_dir, ct, my_pos, player.my_team, player.map, vc=vc, allow_launchers=allow_launcher_replacement
                )
                if can_build:
                    if ct.get_tile_building_id(build_pos) is not None:
                        safe_destroy(player, ct, build_pos, vc)
                    if use_splitter:
                        if safe_build_splitter(player, ct, build_pos, splitter_dir):
                            log(f"BUILT splitter at {build_pos} facing {splitter_dir}")
                            built = True
                            player.nav.set_destination(conv_target, "adjacent")
                    elif safe_build_conveyor(player, ct, build_pos, conv_dir):
                        log(f"BUILT conveyor at {build_pos} -> {conv_target}")
                        built = True
                        player.nav.set_destination(conv_target, "adjacent")
            elif bridge_output_pos and can_build_bridge_here(
                build_pos, bridge_output_pos, ct, my_pos, player.my_team, player.map, vc=vc, allow_launchers=allow_launcher_replacement
            ):
                if ct.get_tile_building_id(build_pos) is not None:
                    safe_destroy(player, ct, build_pos, vc)
                if safe_build_bridge(player, ct, build_pos, bridge_output_pos):
                    log(f"BUILT bridge at {build_pos} -> {bridge_output_pos}")
                    built = True
                    player.nav.set_destination(bridge_output_pos, "adjacent")

            if built and player.harvest_ore_type is not None:
                player.map.tag_conveyor_resource(build_pos, player.harvest_ore_type)

            if not built and my_pos.distance_squared(build_pos) <= 2 and ct.is_tile_empty(build_pos):
                move_dir = my_pos.direction_to(build_pos)
                if ct.can_move(move_dir):
                    ct.move(move_dir)
                    my_pos = ct.get_position()
                    player.my_pos = my_pos
                    log(f"moved onto {build_pos} to block enemies")
            return

        log(f"{existing_etype.name if existing_etype else 'unknown'} at {build_pos} blocks chain -> abandoning")
        clear_state(player)
        return

    if player.nav.original_destination is None:
        log("error after updating: no destination for harvest chain")
        clear_state(player)


def run_reroute_titanium(player, ct: Controller, vc) -> None:
    my_pos = player.my_pos
    foundry_inputs = player.map.get_conveyor_input_count(player.foundry_pos) if player.foundry_pos and player.map else 0
    if player.foundry_pos is None or foundry_inputs >= 2:
        log("foundry reroute no longer needed -> done")
        clear_state(player)
        return

    ti_source = find_adjacent_foundry_reroute_source(player, ct, my_pos, player.foundry_pos)
    if ti_source is None:
        ti_source = find_nearest_titanium_conveyor(ct, my_pos, vc, map_obj=player.map, my_team=player.my_team, target_foundry=player.foundry_pos)
    if ti_source is None:
        ti_source = player.map.find_nearest_conveyor_with_resource(my_pos, ResourceType.TITANIUM, my_team=player.my_team, target_foundry=player.foundry_pos)
    if ti_source is not None:
        ti_pos = ti_source
        if my_pos.distance_squared(ti_pos) <= 2:
            if safe_destroy(player, ct, ti_pos, vc):
                log(f"destroyed titanium conveyor at {ti_pos} for foundry reroute")
                player.nav.set_destination(ti_pos, "adjacent")
                player.state = State.EXTEND_HARVEST_CHAIN
                player.harvest_ore_type = ResourceType.TITANIUM
        else:
            player.nav.set_destination(ti_pos, "adjacent")
        return

    nearest_ti = player.map.get_nearest_titanium_ore(my_pos)
    if nearest_ti is not None:
        player.nav.set_destination(nearest_ti, "adjacent")
    if player.nav.is_destination_reached(ct, player.map):
        clear_state(player)


def run_intercept(player, ct: Controller, vc) -> None:
    my_pos = player.my_pos
    intercept_pos = player.nav.original_destination
    predicted_enemy_core = player.predicted_enemy_core_pos
    log(f"intercepting at {intercept_pos}")
    if intercept_pos is None or my_pos.distance_squared(intercept_pos) > 2:
        return

    enemy_result = get_nearest_enemy_threat_pos(vc, my_pos)
    if enemy_result is None:
        enemy_result = get_known_core_intercept_threat(player, intercept_pos)
    if enemy_result is None:
        return

    log(f"threat at {enemy_result[0]} -> trying to intercept")
    enemy_pos = enemy_result[0]
    direction = get_sentinel_direction(intercept_pos, enemy_pos, ct, player.map)
    if direction is None:
        return

    bid = ct.get_tile_building_id(intercept_pos)
    if bid is None:
        if build_best_turret(ct, intercept_pos, direction, predicted_enemy_core, enemy_pos, player.map):
            clear_state(player)
        return

    bid_team = ct.get_team(bid)
    bid_etype = ct.get_entity_type(bid)
    if (
        bid_team == player.my_team
        and bid_etype in TURRET_TYPES
        and bid_etype == get_best_turret_type(intercept_pos, predicted_enemy_core, ct, None, player.map)
        and ct.get_direction(bid) == direction
    ):
        clear_state(player)
        return

    if player.map is not None and bid_team != player.my_team and player.map.feeds_ally_turret(intercept_pos, player.my_team):
        log(f"intercept at {intercept_pos}: feeds ally turret, abandoning")
        clear_state(player)
        return

    if bid_team != player.my_team and bid_etype != EntityType.MARKER and (ct.is_tile_passable(intercept_pos) or my_pos == intercept_pos):
        kill_cost = attack_cost_to_destroy(ct, bid)
        if player.global_titanium >= kill_cost:
            player.attack_target = intercept_pos
            player.attack_reason = "intercept enemy passable"
        else:
            log(f"intercept: can't afford to kill at {intercept_pos} (need {kill_cost}, have {player.global_titanium})")
            clear_state(player)
        return

    bbid = ct.get_tile_builder_bot_id(intercept_pos)
    if (bbid is None or bbid == ct.get_id()) and ct.can_destroy(intercept_pos) and safe_destroy(player, ct, intercept_pos, vc):
        log("destroyed to build turret")
    if build_best_turret(ct, intercept_pos, direction, predicted_enemy_core, enemy_pos, player.map):
        clear_state(player)


def run_defend(player, ct: Controller, vc) -> None:
    my_pos = player.my_pos
    ore_pos = player.harvest_ore_pos
    log(f"defending {ore_pos}")
    if ore_pos is None:
        clear_state(player)
        return
    if player.map.get_tile_env(ore_pos) == Environment.ORE_AXIONITE:
        clear_state(player)
        return
    if not ct.is_in_vision(ore_pos):
        return

    ore_bid = ct.get_tile_building_id(ore_pos)
    ore_etype = ct.get_entity_type(ore_bid) if ore_bid is not None else None
    if ore_etype is None or ore_etype == EntityType.MARKER:
        if my_pos.distance_squared(ore_pos) <= 2:
            if ct.can_build_barrier(ore_pos):
                ct.build_barrier(ore_pos)
                log(f"DEFEND: barrier on bare ore at {ore_pos}")
            clear_state(player)
        return

    if ore_etype == EntityType.HARVESTER:
        targets = get_barrier_targets(ore_pos, player.core_pos, ct, player.map)
        log(f"DEFEND: barrier targets for {ore_pos} are {targets}")
        if not targets:
            built_support_launcher = (
                USE_LAUNCHERS
                and try_build_support_launcher(player, ct, my_pos, vc, [ore_pos], player.core_pos, min_spacing_sq=8)
            )
            if not built_support_launcher:
                log(f"DEFEND: all sides protected at {ore_pos}")
                clear_state(player)
            return

        target = targets[0]
        player.nav.set_destination(target, "adjacent")
        log(f"DEFEND: navigating to {target}")
        if my_pos.distance_squared(target) <= 2:
            current_targets = get_barrier_targets(ore_pos, player.core_pos, ct, player.map)
            if current_targets:
                target = current_targets[0]
                bid = ct.get_tile_building_id(target)
                bbid = ct.get_tile_builder_bot_id(target)
                if bid is not None:
                    etype = ct.get_entity_type(bid)
                    if (bbid is None or bbid == ct.get_id()) and ct.get_team(bid) == player.my_team and etype == EntityType.ROAD:
                        safe_destroy(player, ct, target, vc)
                        log(f"DEFEND: destroyed ally road at {target} to build barrier")
                if safe_build_barrier(player, ct, target):
                    log(f"DEFEND: barrier at {target} (protecting {ore_pos})")
            remaining = get_barrier_targets(ore_pos, player.core_pos, ct, player.map)
            if not remaining:
                clear_state(player)
        return

    clear_state(player)


def run_explore(player, ct: Controller, vc) -> None:
    if (
        not USE_LAUNCHERS
        or len(vc.enemy_units) != 0
        or player.global_titanium < max(120, ct.get_launcher_cost()[0] * 4)
        or ct.get_current_round() - player.last_support_launcher_round < 20
    ):
        return

    my_pos = player.my_pos
    explore_objective = player.nav.original_destination if player.nav.destination_type == "adjacent" else player.nav.destination
    try_build_support_launcher(player, ct, my_pos, vc, [my_pos], explore_objective, min_spacing_sq=20)


def run_sabotage(player, ct: Controller, vc) -> None:
    my_pos = player.my_pos
    predicted_enemy_core = player.predicted_enemy_core_pos
    dest = player.nav.original_destination
    need_retarget = dest is None or dest == predicted_enemy_core
    if not need_retarget and dest is not None and ct.is_in_vision(dest):
        if not get_sabotage_target_priority(player, ct, dest, vc):
            need_retarget = True
    if need_retarget:
        sd_result = find_sabotage_target(player, ct, my_pos, vc)
        if sd_result is not None:
            sd_target = sd_result[0]
            log(f"sabotage: targeting enemy building at {sd_target}")
            player.nav.set_destination(sd_target, "exact")
        elif predicted_enemy_core is None:
            player.state = State.EXPLORE
            player.nav.clear_destination()
        elif dest != predicted_enemy_core:
            player.nav.set_destination(predicted_enemy_core, "exact")

    if player.global_titanium < 20 or (player.attack_target is None and player.global_titanium < 100):
        clear_state(player)
        log("no resources to sabotage -> abandoning")
        return

    if player.attack_target is not None:
        return

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
            return
