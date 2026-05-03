from cambc import Controller, Direction, EntityType, Position

from globals import *
from comms import LAUNCH_ORDER_ID_MASK
from units.builder.build import safe_place_marker
from log import log
    
def run_launcher(player, ct: Controller, my_pos: Position, vc) -> None:
    player.comms.reset_turn(ct.get_current_round())
    player.map.update_vision(ct, player.comms)

    # Find allies we could pick up
    adjacent_ally_builders = []
    for d in DIRECTIONS:
        adj = my_pos.add(d)
        bid = ct.get_tile_builder_bot_id(adj)
        if bid is not None and ct.get_team(bid) == player.my_team:
            adjacent_ally_builders.append((bid, adj))

    # Iterate over launch orders
    for target, builder_id_tag, marker_pos, marker_id, _created_round in tuple(player.comms.launch_orders):
        
        # Since we mod builder ID, check for possible matches
        matched = None
        for bid, bot_pos in adjacent_ally_builders:
            if (bid & LAUNCH_ORDER_ID_MASK) == builder_id_tag:
                matched = (bid, bot_pos)
                break

        if matched is None:
            continue

        # Launch based on orders
        builder_id, bot_pos = matched
        if ct.can_launch(bot_pos, target):
            ct.launch(bot_pos, target)
            player.comms.remove_launch_order(marker_id)
            log(f"launcher executed order for {builder_id} to {target}")
            
            # Clear marker so we don't reuse
            if marker_pos is not None:
                safe_place_marker(player, ct, marker_pos, 0)
            return
        
    # If we didn't execute launch order, try to find best launch target

    attack_range = ct.get_attackable_tiles()
    
    defending_tiles = []
    enemy_targets = []
    
    # Find enemies we could pick up
    for d in DIRECTIONS:
        adj = my_pos.add(d)
        bid = ct.get_tile_builder_bot_id(adj)
        if bid is not None:
            team = ct.get_team(bid)
            if team != player.my_team:
                enemy_targets.append((adj, bid))

    if len(enemy_targets) == 0:
        return
    
    # Prioritize throwing away from core if known
    if player.core_pos is not None:
        defending_tiles.append(player.core_pos)
        
    # Otherwise, prioritize throw away from our conveyors
    else:
        for (eid, etype, pos) in vc.ally_conveyors:
            defending_tiles.append(pos)

    # Prioritize throwing into ally turret range
    gunner_front_tiles = set()
    sentinel_tiles = []
    for (eid, etype, pos) in vc.ally_turrets:
        if etype == EntityType.GUNNER:
            gunner_front_tiles.add(pos.add(ct.get_direction(eid)))
        elif etype == EntityType.SENTINEL:
            sentinel_tiles.append((pos, ct.get_direction(eid)))

    # Find best tile to launch to
    best_pos = None
    best_score = None
    
    for tile in attack_range:
        # Skip if not passable
        if not ct.is_tile_passable(tile):
            continue
        
        # Score based on dist from defending tiles
        total_dist = 0
        for defend in defending_tiles:
            dist = tile.distance_squared(defend)
            total_dist += dist

        # Score based on being in ally turret range
        gunner_front_bonus = 1 if tile in gunner_front_tiles else 0
        sentinel_cover_count = 0
        for sentinel_pos, sentinel_dir in sentinel_tiles:
            if tile in ct.get_attackable_tiles_from(sentinel_pos, sentinel_dir, EntityType.SENTINEL):
                sentinel_cover_count += 1

        # Update scores
        score = (
            total_dist,
            gunner_front_bonus,
            sentinel_cover_count,
            tile.distance_squared(my_pos),
        )
        if best_score is None or score > best_score:
            best_pos = tile
            best_score = score
    
    # Launch at best target if we can
    if best_pos is not None and ct.can_launch(enemy_targets[0][0], best_pos):
        ct.launch(enemy_targets[0][0], best_pos)
        log(f"launched at {best_pos} targeting {enemy_targets[0][0]}")
        ct.draw_indicator_dot(enemy_targets[0][0], 255, 0, 0)
        ct.draw_indicator_line(best_pos, enemy_targets[0][0], 255, 255, 0)
