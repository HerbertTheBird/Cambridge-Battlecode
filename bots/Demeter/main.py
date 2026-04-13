import sys
import traceback
import random

from cambc import Controller, Direction, EntityType, Position, ResourceType, Team

from log import (
    log, 
    log_time
)
from globals import (
    State,
    SPAWN_WEALTHY_INTERVAL,
)
import nav
import bfs_nav
import map as map_mod
import vision as vc
from helpers import (
    bot_path_color, 
    check_for_resource_increase, 
    get_predicted_enemy_core_pos
)
from units import (
    run_core, 
    run_builder, 
    run_breach, 
    run_gunner, 
    run_sentinel, 
    run_launcher
)

class Player:
    def __init__(self):
        self.initialized = False

        self.my_team: Team
        self.etype: EntityType
        self.my_id: int
        self.my_pos: Position | None = None

        self.num_spawned = 0
        self.last_spawn_round = -SPAWN_WEALTHY_INTERVAL
        self.core_pos: Position | None = None
        self.enemy_core_pos: Position | None = None
        self.predicted_enemy_core_pos: Position | None = None

        self.state: State = State.EXPLORE
        self.timeout_turns = 0
        self.should_explore_ray = False
        self.last_fired_round = 0
        self.skipped_firing_turns = 0
        self.harvest_ore_type: ResourceType | None = None
        self.harvest_ore_pos: Position | None = None
        self.nearest_unserviced: Position | None = None
        self.nearest_unharvested: Position | None = None
        self.foundry_pos: Position | None = None
        self.foundry_position_idxs: set[int] | None = None

        self.initial_spawn_plan = None
        self.broken_chains: dict[int, ResourceType] = {}
        self.no_output_found_mask: int = 0
        self.no_output_found_expiry_round: dict[int, int] = {}
        self.health = 0
        self.prev_health: int
        self.global_titanium = 0
        self.global_axionite = 0
        self.use_armoured_conveyors = False
        self.prev_global_titanium: int
        self.prev_global_axionite: int
        self.last_global_titanium_increase = -2000
        self.last_global_axionite_increase = -2000

        self.attack_target: Position | None = None
        self.attack_reason: str | None = None
        self.build_pos: Position | None = None
        self.build_direction: Direction | None = None
        self.build_type: EntityType | None = None

        self.last_seen_builder_bot_round = 0
        self.last_support_launcher_round = -2000

        self.rushing_enemy = False
        self.initialized_explore_ray = False


    def run(self, ct: Controller) -> None:
        try:

            if self.rushing_enemy:
                log("RUSHING")

            # Init info that depends on ct

            if not self.initialized:
                self.my_id = ct.get_id()

                random.seed(self.my_id)

                bfs_nav.path_color = bot_path_color(self.my_id)

                map_mod.init(ct.get_map_width(), ct.get_map_height())
                nav.set_statics(map_mod.width, map_mod.height, self.my_id)
                bfs_nav.set_statics(map_mod.width, map_mod.height, self.my_id, ct.get_team())

                self.my_team = ct.get_team()

                self.etype = ct.get_entity_type()

                self.prev_health = ct.get_hp()

                self.prev_global_titanium, self.prev_global_axionite = ct.get_global_resources()

                self.initialized = True

            # Update turn info

            my_pos = ct.get_position()
            self.my_pos = my_pos
            log(f"pos={my_pos}")

            self.health = ct.get_hp()

            self.global_titanium, self.global_axionite = ct.get_global_resources()
            check_for_resource_increase(self, ct)

            vc.refresh(ct, self)
            map_mod.update_vision(ct)

            self.predicted_enemy_core_pos = get_predicted_enemy_core_pos(self)
            log(f"predicted enemy core position at {self.predicted_enemy_core_pos}")

            log_time(ct, "After vision update")

            # Run logic based on entity type

            if self.etype == EntityType.CORE:
                run_core(self, ct, my_pos)

            elif self.etype == EntityType.BUILDER_BOT:
                run_builder(self, ct, my_pos)

            elif self.etype == EntityType.GUNNER:
                run_gunner(self, ct, my_pos)

            elif self.etype == EntityType.SENTINEL:
                run_sentinel(self, ct, my_pos)

            elif self.etype == EntityType.BREACH:
                run_breach(self, ct, my_pos)

            elif self.etype == EntityType.LAUNCHER:
                run_launcher(self, ct, my_pos)

            # Update previous values for next turn

            self.prev_health = self.health
            self.prev_global_titanium = self.global_titanium
            self.prev_global_axionite = self.global_axionite

            map_mod.update_all_symmetric_tiles(ct)

            # map_mod.indicate_seen(ct)

        except Exception as e:
            print(f"Error: {e} on turn {ct.get_current_round()} by {self.etype}, ID: {ct.get_id()}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
