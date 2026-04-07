import random

from cambc import Controller, Direction, EntityType, Position

from globals import *
from helpers import *

def run_core(player, ct: Controller, my_pos: Position, vc) -> None:
    builder_cost = ct.get_builder_bot_cost()[0]
    bridge_cost = ct.get_bridge_cost()[0]

    _CORE = EntityType.CORE
    sees_enemy = any(etype is not _CORE for (_eid, etype, _pos) in vc.enemy_units)

    player.turns_since_wealthy_spawn += 1
    
    wealthy = (
        player.turns_since_wealthy_spawn >= SPAWN_WEALTHY_INTERVAL and
        player.global_titanium >= bridge_cost * SPAWN_WEALTHY_BRIDGE_MULT and
        player.global_titanium >= builder_cost * SPAWN_WEALTHY_BUILDER_MULT and
        player.global_titanium >= SPAWN_WEALTHY_RESOURCE_THRESHOLD
    )
    threatened = (
        sees_enemy and
        (player.global_titanium >= builder_cost * SPAWN_THREATENED_BUILDER_MULT or player.health - player.prev_health < 0) and
        (len(vc.ally_builder_bots) == 0)
    )
    
    if len(vc.ally_builder_bots) > 0:
        player.last_seen_builder_bot_round = ct.get_current_round()

    if ct.get_unit_count() <= 1 or player.num_spawned < SPAWN_INITIAL_COUNT or (player.num_spawned < SPAWN_LATER_COUNT and player.global_titanium - ct.get_builder_bot_cost()[0] > 200 and ct.get_current_round() - player.last_global_titanium_increase < 10) or wealthy or threatened or (ct.get_current_round() - player.last_seen_builder_bot_round > 30 and player.global_titanium - ct.get_builder_bot_cost()[0] > 200):
        if sees_enemy:
            nearest_enemy_pos = min(
                (pos for (_eid, etype, pos) in vc.enemy_units if etype is not _CORE),
                key=lambda p: my_pos.distance_squared(p)
            )
            spawn_dir = my_pos.direction_to(nearest_enemy_pos)
            spawn_pos = my_pos.add(spawn_dir)
        else:
            if player.initial_spawn_plan is None:
                valid_dirs = get_valid_directions(ct, my_pos, player.map.width, player.map.height)
                rotational_core_dir = my_pos.direction_to(player.map.get_symmetric_pos(my_pos, Symmetry.ROTATE))

                if len(valid_dirs) == 0:
                    # fallback (shouldn't happen, but safe)
                    player.initial_spawn_plan = prioritize_direction(random.sample(DIRECTIONS, 3), rotational_core_dir)
                else:
                    chosen = pick_three_directions(my_pos, player.map.width, player.map.height, valid_dirs)
                    player.initial_spawn_plan = prioritize_direction([d for (d, _) in chosen], rotational_core_dir)

            if player.num_spawned < len(player.initial_spawn_plan):
                spawn_dir = player.initial_spawn_plan[player.num_spawned]
                spawn_pos = my_pos.add(spawn_dir)
                
                for d in player.initial_spawn_plan:
                    endpoint = get_ray_endpoint(my_pos, d, player.map.width, player.map.height)
                    ct.draw_indicator_line(my_pos, endpoint, 0, 255, 0)
            else:
                spawn_dir = random.choice(DIRECTIONS)
                spawn_pos = my_pos.add(spawn_dir)
        if ct.can_spawn(spawn_pos):
            ct.spawn_builder(spawn_pos)
            player.num_spawned += 1
            if wealthy:
                player.turns_since_wealthy_spawn = 0