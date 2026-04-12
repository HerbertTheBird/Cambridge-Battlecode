import random

from cambc import Controller, EntityType, Position

import map as map_mod
import vision as vc

from globals import (
    DIRECTIONS, 
    NUM_RUSHING
)
from units.core.spawn import (
    choose_enemy_facing_spawn,
    choose_rushing_spawn,
    choose_spawn_plan,
    should_spawn,
    draw_spawn_plan,
)

def run_core(player, ct: Controller, my_pos: Position) -> None:
    # Calculate initial directions we should spawn in
    if player.initial_spawn_plan is None:
        player.initial_spawn_plan = choose_spawn_plan(ct, my_pos)

    sees_enemy = len(vc.enemy_units) > 0

    if len(vc.ally_builder_bots) > 0:
        player.last_seen_builder_bot_round = ct.get_current_round()

    if should_spawn(player, ct):
        # Prioritize spawning toward enemies to protect core
        if sees_enemy:
            spawn_pos = choose_enemy_facing_spawn(my_pos, vc.enemy_units)

        elif player.num_spawned < NUM_RUSHING:
            spawn_pos = choose_rushing_spawn(ct, my_pos, player.predicted_enemy_core_pos)

        # Follow spawn plan for first few builder bots
        elif player.num_spawned - NUM_RUSHING < len(player.initial_spawn_plan):
            spawn_dir = player.initial_spawn_plan[player.num_spawned - NUM_RUSHING]
            spawn_pos = my_pos.add(spawn_dir)
            draw_spawn_plan(ct, my_pos, player.initial_spawn_plan, map_mod.width, map_mod.height)

        # Randomly spawn otherwise
        else:
            spawn_dir = random.choice(DIRECTIONS)
            spawn_pos = my_pos.add(spawn_dir)

        # Spawn if possible
        if spawn_pos is not None and ct.can_spawn(spawn_pos):
            bid = ct.spawn_builder(spawn_pos)
            vc.add_entity(player, bid, EntityType.BUILDER_BOT, player.my_team, spawn_pos)
            player.num_spawned += 1
            player.last_spawn_round = ct.get_current_round()
