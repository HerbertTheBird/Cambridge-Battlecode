from cambc import Controller, EntityType, ResourceType

import map as map_mod
import nav

from log import log
from globals import (
    TIMEOUT_TURNS, 
    State, 
    CONVEYOR_TYPES
)
from helpers import (
    get_opposite_ore_mask, 
    is_in_vision
)
from units.builder.build import (
    safe_build_road,
    safe_build_harvester,
    safe_destroy,
)
from units.builder.logic import (
    clear_state,
    count_closer_allies,
    is_ore_unblocked,
    get_best_bridge_build_pos,
    get_barrier_targets,
    remember_non_passable_build,
)

def run(player, ct: Controller) -> None:
    my_pos = player.my_pos
    if player.nearest_unserviced is not None:
        player.harvest_ore_pos = player.nearest_unserviced
    elif player.harvest_ore_pos is None or my_pos.distance_squared(player.harvest_ore_pos) > 2:
            player.harvest_ore_pos = player.nearest_unharvested

    ore_pos = player.harvest_ore_pos

    if ore_pos is None:
        log("no harvest target found on START_HARVEST_CHAIN")
        clear_state(player)
        return

    bbid = ct.get_tile_builder_bot_id(ore_pos) if is_in_vision(my_pos, ore_pos) else None
    if bbid is not None and bbid != ct.get_id():
        log(f"another builder {bbid} targeting {ore_pos} -> abandoning harvest")
        clear_state(player)
        return

    if count_closer_allies(player, ore_pos, my_pos) >= 2:
        log(f"2+ closer allies to {ore_pos} -> abandoning harvest")
        clear_state(player)
        return

    if not is_in_vision(my_pos, ore_pos):
        return

    if not is_ore_unblocked(player, ct, ore_pos, my_pos):
        clear_state(player)
        map_mod.add_unreachable_harvester(ore_pos)
        log(f"marked {ore_pos} as unreachable due to barriers")
        return

    ore_etype = map_mod.get_tile_entity_type(ore_pos)
    ore_team = map_mod.get_tile_entity_team(ore_pos)
    if ore_etype == EntityType.HARVESTER:
        if not map_mod.is_unserviced_harvester(ore_pos, player.my_team):
            log(f"ore {ore_pos} already serviced -> done")
            clear_state(player)
            return

        if is_ore_unblocked(player, ct, ore_pos, my_pos, allow_out_of_vision=False):
            best_build_pos = get_best_bridge_build_pos(
                ore_pos,
                player.core_pos,
                ct,
                my_pos,
                player.my_team,
                opposite_ore_mask=get_opposite_ore_mask(map_mod.is_axionite_ore(ore_pos)),
            )
            if best_build_pos is None:
                player.timeout_turns += 1
                if player.timeout_turns >= TIMEOUT_TURNS:
                    log(f"timeout trying to build bridge from {ore_pos} -> abandoning")
                    clear_state(player)
                    player.timeout_turns = 0
                    map_mod.add_unreachable_harvester(ore_pos)
                return

            nav.set_destination(best_build_pos, "adjacent")
            player.state = State.EXTEND_HARVEST_CHAIN
            player.harvest_ore_type = ResourceType.RAW_AXIONITE if map_mod.is_axionite_ore(ore_pos) else ResourceType.TITANIUM
            player.harvest_ore_pos = ore_pos
            return

    if (
        ore_etype is not None
        and ore_team != player.my_team
        and ore_etype != EntityType.MARKER
        and player.global_titanium >= 100
        and (ct.is_tile_passable(ore_pos) or my_pos == ore_pos)
    ):
        player.attack_target = ore_pos
        player.attack_reason = "ore covered"

    bridge_pos = get_best_bridge_build_pos(
        ore_pos,
        player.core_pos,
        ct,
        my_pos,
        player.my_team,
        opposite_ore_mask=get_opposite_ore_mask(map_mod.is_axionite_ore(ore_pos)),
    )

    is_titanium_ore = map_mod.is_titanium_ore(ore_pos)

    barrier_targets = []
    if is_titanium_ore and bridge_pos is not None and my_pos.distance_squared(ore_pos) <= 2:
        barrier_targets = [
            pos for pos in get_barrier_targets(ore_pos, player.core_pos, ct, my_pos)
            if pos != bridge_pos
        ]

    if (
        my_pos.distance_squared(ore_pos) <= 2
        and my_pos != ore_pos
        and bridge_pos is not None
        and not barrier_targets
    ):
        nav.set_destination(bridge_pos, "exact")

    if my_pos == ore_pos and barrier_targets:
        current_barrier_targets = [
            pos for pos in get_barrier_targets(ore_pos, player.core_pos, ct, my_pos)
            if pos != bridge_pos
        ]
        if current_barrier_targets:
            target = current_barrier_targets[0]
            if safe_build_road(player, ct, target):
                log(f"START_CHAIN: road at {target} (protecting {ore_pos})")
        return

    if my_pos == ore_pos and not barrier_targets:
        if bridge_pos is not None:
            nav.set_destination(bridge_pos, "exact")
        elif safe_build_harvester(player, ct, ore_pos):
            log(f"built harvester at {ore_pos}")
        elif my_pos == ore_pos:
            remember_non_passable_build(player, ore_pos, EntityType.HARVESTER)
        return

    if my_pos == bridge_pos and not barrier_targets:
        bbid = ct.get_tile_builder_bot_id(ore_pos)
        if (
            ore_etype is not None
            and ore_etype not in (EntityType.HARVESTER, EntityType.MARKER)
            and player.global_titanium >= ct.get_harvester_cost()[0]
            and ct.can_destroy(ore_pos)
            and (bbid is None or bbid == ct.get_id())
        ):
            log(f"destroyed {ore_pos} to build harvester")
            safe_destroy(player, ct, ore_pos)
        if safe_build_harvester(player, ct, ore_pos):
            log(f"built harvester at {ore_pos}")
        elif my_pos == ore_pos:
            remember_non_passable_build(player, ore_pos, EntityType.HARVESTER)
        return

    barrier_targets = []
    if is_titanium_ore and bridge_pos is not None and my_pos.distance_squared(ore_pos) <= 2:
        barrier_targets = [
            pos for pos in get_barrier_targets(ore_pos, player.core_pos, ct, my_pos)
            if pos != bridge_pos
        ]

    if barrier_targets:
        nav.set_destination(ore_pos, "exact")
    elif bridge_pos is not None:
        nav.set_destination(bridge_pos, "exact")
    else:
        bid = map_mod.get_tile_entity_id(ore_pos)
        etype = map_mod.get_tile_entity_type(ore_pos)
        if bid is None or etype in CONVEYOR_TYPES or etype == EntityType.ROAD or etype == EntityType.MARKER:
            nav.set_destination(ore_pos, "exact")
        else:
            nav.set_destination(ore_pos, "adjacent")
