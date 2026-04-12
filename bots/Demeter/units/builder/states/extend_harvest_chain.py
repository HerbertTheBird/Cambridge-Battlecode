from cambc import Controller, EntityType, Position, ResourceType

import map as map_mod
import nav
import vision as vc

from log import log
from globals import (
    USE_LAUNCHERS, 
    CONVEYOR_TYPES,
    DELTAS, 
    TURRET_TYPES, 
    CARDINAL_DIRECTIONS
)
from helpers import (
    get_cardinal_direction_into_core, 
    get_opposite_ore_mask, 
    is_in_vision
)
from units.builder.build import (
    can_build_foundry_here,
    safe_build_foundry,
    safe_destroy,
    can_build_selected_conveyor_here,
    safe_build_selected_conveyor,
    can_build_splitter_here,
    safe_build_splitter,
    can_build_bridge_here,
    safe_build_bridge,
)
from units.builder.logic import (
    clear_state, 
    get_best_bridge_build_pos, 
    is_core_tile, 
    is_foundry_position, 
    remember_non_passable_build, 
    is_enemy_armoured_conveyor, 
    try_build_support_launcher
)

def run(player, ct: Controller) -> None:
    my_pos = player.my_pos
    dest = nav.original_destination

    if dest is not None and player.harvest_ore_pos is not None and is_in_vision(my_pos, player.harvest_ore_pos):
        new_pos = get_best_bridge_build_pos(
            player.harvest_ore_pos,
            player.core_pos,
            ct,
            my_pos,
            player.my_team,
            opposite_ore_mask=get_opposite_ore_mask(player.harvest_ore_type == ResourceType.RAW_AXIONITE),
        )
        if new_pos is not None and new_pos != dest:
            log(f"recalculated first bridge pos: {dest} -> {new_pos}")
            dest = new_pos
            nav.set_destination(new_pos, "adjacent")

    dest_etype = map_mod.get_tile_entity_type(dest) if dest is not None else None
    dest_team = map_mod.get_tile_entity_team(dest) if dest is not None else None
    if dest is None:
        log("error: no destination for harvest chain")
        clear_state(player)
        return
    if is_core_tile(player.core_pos, dest):
        log("chain reaches core -> done")
        clear_state(player)
        return
    if dest_etype == EntityType.FOUNDRY and dest_team == player.my_team:
        log("chain reaches foundry -> done")
        clear_state(player)
        return

    if (
        player.harvest_ore_type == ResourceType.RAW_AXIONITE
        and is_foundry_position(player.core_pos, dest)
        and is_in_vision(my_pos, dest)
        and my_pos.distance_squared(dest) <= 2
    ):
        builder_on_dest = ct.get_tile_builder_bot_id(dest)
        if (
            player.global_titanium >= 1500
            and (builder_on_dest is None or builder_on_dest == ct.get_id())
            and can_build_foundry_here(dest, ct, my_pos, player.my_team)
        ):
            bid = map_mod.get_tile_entity_id(dest)
            bbid = ct.get_tile_builder_bot_id(dest)
            if bid is not None and (bbid is None or bbid == ct.get_id()) and player.global_titanium >= ct.get_foundry_cost()[0] and safe_destroy(player, ct, dest):
                log("destroyed to build foundry")
            if safe_build_foundry(player, ct, dest):
                log(f"BUILT foundry at {dest}")
                clear_state(player)
            else:
                clear_state(player)
            return
        if player.global_titanium >= 1500 and my_pos == dest:
            remember_non_passable_build(player, dest, EntityType.FOUNDRY)
            return

        existing_bid = map_mod.get_tile_entity_id(dest)
        existing_etype = map_mod.get_tile_entity_type(dest)
        existing_team = map_mod.get_tile_entity_team(dest)
        core_dir = get_cardinal_direction_into_core(player.core_pos, dest)
        if (
            existing_etype not in CONVEYOR_TYPES
            and core_dir is not None
            and can_build_selected_conveyor_here(player, dest, core_dir, ct, my_pos, player.my_team)
        ):
            bbid_conv = ct.get_tile_builder_bot_id(dest)
            if existing_bid is not None and (bbid_conv is None or bbid_conv == ct.get_id()) and safe_destroy(player, ct, dest):
                log("destroyed to build conveyor")
            if safe_build_selected_conveyor(player, ct, dest, core_dir):
                if player.harvest_ore_type is not None:
                    map_mod.tag_conveyor_resource(dest, player.harvest_ore_type)
                log(f"placed axionite conveyor at {dest} as foundry placeholder")
            else:
                log(f"failed to place axionite conveyor at {dest} as foundry placeholder")
            clear_state(player)
            return

        # If an enemy building blocks the placeholder, mark it for attack
        if (
            existing_bid is not None
            and existing_team != player.my_team
            and not is_enemy_armoured_conveyor(existing_etype, existing_team, player.my_team)
            and core_dir is not None
        ):
            player.attack_target = dest
            player.attack_reason = "enemy building blocking foundry placeholder"
            log(f"marking enemy {existing_etype} at {dest} for attack to clear for placeholder")
            return  # Stay in state, run.py attack logic handles the rest

        clear_state(player)
        return

    if not is_in_vision(my_pos, dest):
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
            player, ct, my_pos, launcher_anchors, player.core_pos, min_spacing_sq=8
        )

    if not built_support_launcher:
        inferred_resource = map_mod.infer_chain_resource_at_output(build_pos, ct, my_pos)
        if inferred_resource is not None and inferred_resource != player.harvest_ore_type:
            log(f"updated chain resource at {build_pos}: {player.harvest_ore_type} -> {inferred_resource}")
            player.harvest_ore_type = inferred_resource

        end_position_idxs = None
        if player.harvest_ore_type == ResourceType.RAW_AXIONITE:
            end_position_idxs = set()
            if player.foundry_position_idxs is not None:
                for foundry_idx in player.foundry_position_idxs:
                    p = map_mod.idx_to_pos(foundry_idx)
                    p_idx = foundry_idx
                    has_titanium = (
                        map_mod.has_recent_conveyor_resource_idx(p_idx, ResourceType.TITANIUM)
                        or map_mod.input_chain_reaches_resource_idx(p_idx, ResourceType.TITANIUM)
                    )
                    if not has_titanium and is_in_vision(my_pos, p):
                        p_bid = map_mod.get_tile_entity_id(p)
                        p_etype = map_mod.get_tile_entity_type(p)
                        if p_bid is not None and p_etype in CONVEYOR_TYPES:
                            has_titanium = ct.get_stored_resource(p_bid) == ResourceType.TITANIUM
                    if not has_titanium:
                        end_position_idxs.add(map_mod.pos_to_idx(p))
        elif player.harvest_ore_type == ResourceType.TITANIUM and player.core_pos is not None:
            if player.foundry_pos is not None:
                if not map_mod.is_single_input_foundry_idx(map_mod.pos_to_idx(player.foundry_pos), player.my_team):
                    log(f"foundry at {player.foundry_pos} no longer needs titanium reroute -> redirecting to core")
                    player.foundry_pos = None
                else:
                    end_position_idxs = {map_mod.pos_to_idx(player.foundry_pos)}
            else:
                foundry_target = map_mod.find_single_input_foundry(player.core_pos, player.my_team)
                if foundry_target is not None:
                    end_position_idxs = set()
                    for dx in range(-1, 2):
                        for dy in range(-1, 2):
                            end_position_idxs.add(map_mod.pos_to_idx(Position(player.core_pos.x + dx, player.core_pos.y + dy)))
                    end_position_idxs.add(map_mod.pos_to_idx(foundry_target))

        existing_bid = map_mod.get_tile_entity_id(build_pos)
        existing_etype = map_mod.get_tile_entity_type(build_pos)
        existing_team = map_mod.get_tile_entity_team(build_pos)
        if existing_bid is not None and existing_etype in CONVEYOR_TYPES:
            next_pos = map_mod.get_conveyor_output(build_pos)
            if next_pos is None:
                clear_state(player)
                return
            if existing_team == player.my_team:
                if is_core_tile(player.core_pos, next_pos):
                    log(f"ally {existing_etype.name} at {build_pos} feeds core -> done")
                    clear_state(player)
                    return
                next_etype = map_mod.get_tile_entity_type(next_pos)
                next_team = map_mod.get_tile_entity_team(next_pos)
                if next_etype == EntityType.FOUNDRY and next_team == player.my_team:
                    log(f"ally {existing_etype.name} at {build_pos} feeds foundry -> done")
                    clear_state(player)
                    return
                nav.set_destination(next_pos, "adjacent")
                log(f"ally {existing_etype.name} at {build_pos} -> following to {next_pos}")
            elif player.core_pos is not None and next_pos.distance_squared(player.core_pos) < build_pos.distance_squared(player.core_pos):
                nav.set_destination(next_pos, "adjacent")
                log(f"enemy {existing_etype.name} at {build_pos} outputs toward core -> following to {next_pos}")
            elif (
                not is_enemy_armoured_conveyor(existing_etype, existing_team, player.my_team)
                and (ct.is_tile_passable(build_pos) or my_pos == build_pos)
                and (len(vc.enemy_units) == 0 or not map_mod.feeds_ally_turret_idx(map_mod.pos_to_idx(build_pos), player.my_team))
            ):
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
            conveyor_info, conveyor_is_fallback = map_mod.get_best_conveyor_output_with_fallback_idx(
                build_pos, player.core_pos, ct, my_pos, player.my_team, end_position_idxs=end_position_idxs, resource=chain_resource
            )
            if conveyor_info is None or conveyor_is_fallback:
                bridge_output_pos, bridge_is_fallback = map_mod.get_best_bridge_output_with_fallback_idx(
                    build_pos, player.core_pos, ct, my_pos, player.my_team, end_position_idxs=end_position_idxs, resource=chain_resource
                )
                if conveyor_info is not None and not bridge_is_fallback:
                    conveyor_info = None
            else:
                bridge_output_pos = None

            built = False
            if conveyor_info is not None:
                conv_dir, conv_target = conveyor_info
                conv_target_idx = map_mod.pos_to_idx(conv_target)
                feeds_foundry = (
                    (map_mod.get_tile_entity_type(conv_target) == EntityType.FOUNDRY)
                    or (end_position_idxs is not None and conv_target_idx in end_position_idxs and is_foundry_position(player.core_pos, conv_target))
                )
                splitter_dir = None
                if feeds_foundry:
                    chain_resource = player.harvest_ore_type
                    foundry_dir = build_pos.direction_to(conv_target)

                    def _splitter_sides_clear(candidate_dir):
                        back = candidate_dir.opposite()
                        bx = build_pos.x
                        by = build_pos.y
                        for sd in CARDINAL_DIRECTIONS:
                            if sd == back:
                                continue
                            dx, dy = DELTAS[sd]
                            side_pos = Position(bx + dx, by + dy)
                            if map_mod.has_conflict_idx(chain_resource, map_mod.pos_to_idx(side_pos), ct):
                                return False
                        return True

                    for check_d in CARDINAL_DIRECTIONS:
                        if check_d == foundry_dir:
                            continue
                        dx, dy = DELTAS[check_d]
                        adj_pos = Position(build_pos.x + dx, build_pos.y + dy)
                        adj_etype = map_mod.get_tile_entity_type(adj_pos)
                        adj_team = map_mod.get_tile_entity_team(adj_pos)
                        if (
                            adj_etype is not None
                            and adj_etype in CONVEYOR_TYPES
                            and adj_etype != EntityType.BRIDGE
                            and adj_team == player.my_team
                        ):
                            feeder_output = map_mod.get_conveyor_output(adj_pos)
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
                        build_pos, splitter_dir, ct, my_pos, player.my_team, allow_launchers=allow_launcher_replacement
                    )
                )
                can_build = use_splitter or can_build_selected_conveyor_here(
                    player, build_pos, conv_dir, ct, my_pos, player.my_team, allow_launchers=allow_launcher_replacement
                )
                if can_build:
                    bbid_bp = ct.get_tile_builder_bot_id(build_pos)
                    if ct.get_tile_building_id(build_pos) is not None and (bbid_bp is None or bbid_bp == ct.get_id()):
                        safe_destroy(player, ct, build_pos)
                    if use_splitter:
                        if safe_build_splitter(player, ct, build_pos, splitter_dir):
                            log(f"BUILT splitter at {build_pos} facing {splitter_dir}")
                            built = True
                            nav.set_destination(conv_target, "adjacent")
                    elif safe_build_selected_conveyor(player, ct, build_pos, conv_dir):
                        log(f"BUILT conveyor at {build_pos} -> {conv_target}")
                        built = True
                        nav.set_destination(conv_target, "adjacent")
            elif bridge_output_pos and can_build_bridge_here(
                build_pos, bridge_output_pos, ct, my_pos, player.my_team, allow_launchers=allow_launcher_replacement
            ):
                bbid_br = ct.get_tile_builder_bot_id(build_pos)
                if ct.get_tile_building_id(build_pos) is not None and (bbid_br is None or bbid_br == ct.get_id()):
                    safe_destroy(player, ct, build_pos)
                if safe_build_bridge(player, ct, build_pos, bridge_output_pos):
                    log(f"BUILT bridge at {build_pos} -> {bridge_output_pos}")
                    built = True
                    nav.set_destination(bridge_output_pos, "adjacent")

            if built and player.harvest_ore_type is not None:
                map_mod.tag_conveyor_resource(build_pos, player.harvest_ore_type)

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

    if nav.original_destination is None:
        log("error after updating: no destination for harvest chain")
        clear_state(player)