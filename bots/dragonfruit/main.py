import sys
import traceback
import random

from cambc import Controller, Direction, EntityType, Position, ResourceType, Team

from globals import *
from nav import Navigator
from a_star_nav import AStarNavigator
from map import Map
from comms import Comms
from helpers import bot_path_color, check_for_resource_increase, get_predicted_enemy_core_pos
from vision import VisionCache
from log import log, log_time
from units import run_core, run_builder, run_breach, run_gunner, run_sentinel, run_launcher

class Player:
    def __init__(self):
        self.initialized = False
        
        self.nav = Navigator()
        self.a_star_nav = AStarNavigator()
        self.comms = Comms()
        self.vc = VisionCache()
        self.map: Map
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
        self.foundry_positions: set | None = None
        
        self.initial_spawn_plan = None
        self.broken_chains: dict = {}
        self.health = 0
        self.prev_health: int
        self.global_titanium = 0
        self.global_axionite = 0
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
                
                self.a_star_nav.path_color = bot_path_color(self.my_id)
                
                self.map = Map(ct.get_map_width(), ct.get_map_height())
                self.nav.set_statics(self.map.width, self.map.height, self.my_id)
                self.a_star_nav.set_statics(self.map.width, self.map.height, self.my_id, ct.get_team())
            
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
            
            vc = self.vc
            vc.refresh(ct, self)
            self.map.update_vision(ct, self.comms)
            
            self.predicted_enemy_core_pos = get_predicted_enemy_core_pos(self)
            log(f"predicted enemy core position at {self.predicted_enemy_core_pos}")
            
            log_time(ct, "After vision update")

            # Run logic based on entity type

            if self.etype == EntityType.CORE:
                run_core(self, ct, my_pos, vc)

            elif self.etype == EntityType.BUILDER_BOT:
                run_builder(self, ct, my_pos, vc)

            elif self.etype == EntityType.GUNNER:
                run_gunner(self, ct, my_pos, vc)

            elif self.etype == EntityType.SENTINEL:
                run_sentinel(self, ct, my_pos, vc)

            elif self.etype == EntityType.BREACH:
                run_breach(self, ct, my_pos, vc)

            elif self.etype == EntityType.LAUNCHER:
                run_launcher(self, ct, my_pos, vc)
    
            # Update previous values for next turn
            
            self.prev_health = self.health
            self.prev_global_titanium = self.global_titanium
            self.prev_global_axionite = self.global_axionite
            
            self.map.update_all_symmetric_tiles(ct)
            
            # self.map.indicate_seen(ct)

        except Exception as e:
            print(f"Error: {e} on turn {ct.get_current_round()} by {self.etype}, ID: {ct.get_id()}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
