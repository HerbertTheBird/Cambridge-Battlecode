import random

from cambc import Controller, Position

from globals import DIRECTIONS
from units.core.spawn import *

def run_core(player, ct: Controller, my_pos: Position, vc) -> None:
    # Calculate initial directions we should spawn in
    if player.initial_spawn_plan is None:
        player.initial_spawn_plan = choose_spawn_plan(player, ct, my_pos)
    
    sees_enemy = len(vc.enemy_units) > 0

    if len(vc.ally_builder_bots) > 0:
        player.last_seen_builder_bot_round = ct.get_current_round()

    if should_spawn(player, ct, vc):
        # Prioritize spawning toward enemies to protect core
        if sees_enemy:
            spawn_pos = choose_enemy_facing_spawn(my_pos, vc.enemy_units)
            
        # Follow spawn plan for first few builder bots
        elif player.num_spawned < len(player.initial_spawn_plan):
            spawn_dir = player.initial_spawn_plan[player.num_spawned]
            spawn_pos = my_pos.add(spawn_dir)
            draw_spawn_plan(ct, my_pos, player.initial_spawn_plan, player.map.width, player.map.height)
            
        # Randomly spawn otherwise
        else:
            spawn_dir = random.choice(DIRECTIONS)
            spawn_pos = my_pos.add(spawn_dir)

        # Spawn if possible
        if spawn_pos is not None and ct.can_spawn(spawn_pos):
            ct.spawn_builder(spawn_pos)
            player.num_spawned += 1
            player.last_spawn_round = ct.get_current_round()
