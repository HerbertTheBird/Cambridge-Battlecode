from cambc import Controller, EntityType

import map as map_mod
import nav

from log import log
from globals import TURRET_TYPES
from units.builder.logic import (
    get_nearest_enemy_threat_pos,
    get_known_core_intercept_threat,
    get_best_intercept_turret_choice,
    is_enemy_armoured_conveyor,
    attack_cost_to_destroy,
    safe_destroy,
    remember_non_passable_build,
    clear_state,
    build_turret,
    find_conveyor_dir_to_ally_turret,
    debug_draw_intercept_masks,
)
from units.builder.build import (
    can_build_selected_conveyor_here,
    safe_build_selected_conveyor,
)

def run(player, ct: Controller) -> None:
    my_pos = player.my_pos
    intercept_pos = nav.original_destination
    enemy_core_pos = player.enemy_core_pos
    log(f"intercepting at {intercept_pos}")
    if intercept_pos is None or my_pos.distance_squared(intercept_pos) > 2:
        return

    enemy_result = get_nearest_enemy_threat_pos(my_pos)
    if enemy_result is None:
        enemy_result = get_known_core_intercept_threat(player, intercept_pos)
    if enemy_result is None:
        return

    log(f"threat at {enemy_result[0]} -> trying to intercept")
    enemy_pos = enemy_result[0]
    turret_type, direction, _score = get_best_intercept_turret_choice(
        intercept_pos,
        ct,
        enemy_core_pos=enemy_core_pos,
    )

    if direction is None:
        return

    # debug_draw_intercept_masks(ct, intercept_pos, turret_type, direction, enemy_core_pos=enemy_core_pos)

    bid = map_mod.get_tile_entity_id(intercept_pos)
    if bid is None:
        conv_dir = find_conveyor_dir_to_ally_turret(intercept_pos, player.my_team, enemy_core_pos)
        if (
            conv_dir is not None
            and can_build_selected_conveyor_here(player, intercept_pos, conv_dir, ct, my_pos, player.my_team)
            and safe_build_selected_conveyor(player, ct, intercept_pos, conv_dir)
        ):
            log(f"intercept: built feeding conveyor at {intercept_pos} facing {conv_dir} instead of turret")
            clear_state(player)
            return
        if build_turret(player, ct, intercept_pos, direction, turret_type):
            clear_state(player)
        elif my_pos == intercept_pos:
            remember_non_passable_build(player, intercept_pos, turret_type, direction)
        return

    bid_team = map_mod.get_tile_entity_team(intercept_pos)
    bid_etype = map_mod.get_tile_entity_type(intercept_pos)
    if (
        bid_team == player.my_team
        and bid_etype in TURRET_TYPES
        and bid_etype == turret_type
        and ct.get_direction(bid) == direction
    ):
        clear_state(player)
        return

    if bid_team != player.my_team and map_mod.feeds_ally_turret_idx(map_mod.pos_to_idx(intercept_pos), player.my_team):
        log(f"intercept at {intercept_pos}: feeds ally turret, abandoning")
        clear_state(player)
        return

    if (
        bid_team != player.my_team
        and bid_etype != EntityType.MARKER
        and not is_enemy_armoured_conveyor(bid_etype, bid_team, player.my_team)
        and (ct.is_tile_passable(intercept_pos) or my_pos == intercept_pos)
    ):
        kill_cost = attack_cost_to_destroy(ct, bid)
        if player.global_titanium >= kill_cost:
            player.attack_target = intercept_pos
            player.attack_reason = "intercept enemy passable"
        else:
            log(f"intercept: can't afford to kill at {intercept_pos} (need {kill_cost}, have {player.global_titanium})")
            clear_state(player)
        return

    turret_cost = ct.get_gunner_cost()[0] if turret_type == EntityType.GUNNER else ct.get_sentinel_cost()[0]
    bbid = ct.get_tile_builder_bot_id(intercept_pos)
    if (bbid is None or bbid == ct.get_id()) and player.global_titanium >= turret_cost and ct.can_destroy(intercept_pos) and safe_destroy(player, ct, intercept_pos):
        log("destroyed to build turret")
    if build_turret(player, ct, intercept_pos, direction, turret_type):
        clear_state(player)
    elif my_pos == intercept_pos:
        remember_non_passable_build(player, intercept_pos, turret_type, direction)
