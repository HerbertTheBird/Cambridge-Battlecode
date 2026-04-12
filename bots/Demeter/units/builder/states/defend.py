from cambc import Controller, EntityType, ResourceType

import map as map_mod
import nav

from log import log
from globals import USE_LAUNCHERS
from units.builder.build import safe_build_road
from units.builder.logic import (
    clear_state, 
    get_barrier_targets, 
    try_build_support_launcher
)
from helpers import is_in_vision

def run(player, ct: Controller) -> None:
    my_pos = player.my_pos
    ore_pos = player.harvest_ore_pos
    log(f"defending {ore_pos}")
    if ore_pos is None:
        clear_state(player)
        return
    if map_mod.is_axionite_ore(ore_pos):
        clear_state(player)
        return
    if not is_in_vision(my_pos, ore_pos):
        return

    ore_bid = map_mod.get_tile_entity_id(ore_pos)
    ore_etype = map_mod.get_tile_entity_type(ore_pos)
    if ore_etype is None or ore_etype == EntityType.MARKER:
        if my_pos.distance_squared(ore_pos) <= 2:
            if safe_build_road(player, ct, ore_pos):
                log(f"DEFEND: road on bare ore at {ore_pos}")
                clear_state(player)
        return

    if ore_etype == EntityType.HARVESTER:
        targets = get_barrier_targets(ore_pos, player.core_pos, ct, my_pos)
        log(f"DEFEND: road targets for {ore_pos} are {targets}")
        if not targets:
            built_support_launcher = (
                USE_LAUNCHERS
                and try_build_support_launcher(player, ct, my_pos, [ore_pos], player.core_pos, min_spacing_sq=8)
            )
            if not built_support_launcher:
                log(f"DEFEND: all sides protected at {ore_pos}")
                clear_state(player)
            return

        target = targets[0]
        nav.set_destination(target, "adjacent")
        log(f"DEFEND: navigating to {target}")
        if my_pos.distance_squared(target) <= 2:
            current_targets = get_barrier_targets(ore_pos, player.core_pos, ct, my_pos)
            if current_targets:
                target = current_targets[0]
                if safe_build_road(player, ct, target):
                    log(f"DEFEND: barrier at {target} (protecting {ore_pos})")
            remaining = get_barrier_targets(ore_pos, player.core_pos, ct, my_pos)
            if not remaining:
                clear_state(player)
        return

    clear_state(player)