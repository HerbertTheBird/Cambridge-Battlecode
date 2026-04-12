from cambc import Controller, ResourceType

import map as map_mod
import nav

from log import log
from globals import State
from units.builder.build import (
    safe_destroy, 
    get_selected_conveyor_cost
)
from units.builder.logic import (
    clear_state,
    find_adjacent_foundry_reroute_source,
    find_nearest_titanium_conveyor,
)

def run(player, ct: Controller) -> None:
    my_pos = player.my_pos
    foundry_inputs = map_mod.get_conveyor_input_count(player.foundry_pos) if player.foundry_pos else 0
    if player.foundry_pos is None or foundry_inputs >= 2:
        log("foundry reroute no longer needed -> done")
        clear_state(player)
        return

    ti_source = find_adjacent_foundry_reroute_source(player, ct, my_pos, player.foundry_pos)
    if ti_source is None:
        ti_source = find_nearest_titanium_conveyor(ct, my_pos, my_team=player.my_team, target_foundry=player.foundry_pos)
    if ti_source is None:
        ti_source_idx = map_mod.find_nearest_conveyor_with_resource_idx(
            map_mod.pos_to_idx(my_pos),
            ResourceType.TITANIUM,
            my_team=player.my_team,
            target_foundry_idx=None if player.foundry_pos is None else map_mod.pos_to_idx(player.foundry_pos),
        )
        ti_source = map_mod.idx_to_pos(ti_source_idx) if ti_source_idx is not None else None
    if ti_source is not None:
        ti_pos = ti_source
        if my_pos.distance_squared(ti_pos) <= 2:
            bbid_ti = ct.get_tile_builder_bot_id(ti_pos)
            conveyor_ti_cost, conveyor_ax_cost = get_selected_conveyor_cost(player, ct)
            if (
                (bbid_ti is None or bbid_ti == ct.get_id())
                and player.global_titanium >= conveyor_ti_cost
                and player.global_axionite >= conveyor_ax_cost
                and safe_destroy(player, ct, ti_pos)
            ):
                log(f"destroyed titanium conveyor at {ti_pos} for foundry reroute")
                nav.set_destination(ti_pos, "adjacent")
                player.state = State.EXTEND_HARVEST_CHAIN
                player.harvest_ore_type = ResourceType.TITANIUM
        else:
            nav.set_destination(ti_pos, "adjacent")
        return

    nearest_ti = map_mod.get_nearest_titanium_ore(my_pos)
    if nearest_ti is not None:
        nav.set_destination(nearest_ti, "adjacent")
    if nav.is_destination_reached(my_pos):
        clear_state(player)
