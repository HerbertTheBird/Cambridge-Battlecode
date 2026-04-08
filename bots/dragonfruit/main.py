import sys
import traceback

from cambc import Controller, EntityType, Position, ResourceType, Team

from globals import *
from nav import Navigator
from a_star_nav import AStarNavigator
from map import Map
from comms import Comms
from helpers import bot_path_color
from vision import VisionCache
from log import log, log_time
from units import run_core, run_builder, run_gunner, run_turret, run_launcher

class Player:
    def __init__(self):
        self.nav = Navigator()
        self.a_star_nav = AStarNavigator()
        self.comms = Comms()
        self.vc = VisionCache()
        self.map: Map
        self.my_team: Team
        self.etype: EntityType
        self.my_id: int
        self.path_color: tuple[int, int, int] = (0, 100, 255)

        self.num_spawned = 0
        self.last_spawn_round = -SPAWN_WEALTHY_INTERVAL
        self.core_pos: Position | None = None
        self.enemy_core_pos: Position | None = None
        self.predicted_enemy_core_pos: Position | None = None

        self.state: State = State.EXPLORE
        self.timeout_turns = 0
        self.has_explored_first_destination = False
        self.last_fired_round = 0
        self.skipped_firing_turns = 0
        self.harvest_ore_type: ResourceType | None = None
        self.harvest_ore_pos: Position | None = None   # position of the ore/harvester we're chaining from
        self.foundry_pos: Position | None = None
        self.foundry_positions: set | None = None
        
        self.initial_spawn_plan = None
        self.broken_chains: dict = {}  # output_pos -> resource type
        self.health = 0
        self.prev_health = 0
        self.global_titanium = 0
        self.global_axionite = 0
        self.prev_global_titanium = -1
        self.prev_global_axionite = -1
        self.last_global_titanium_increase = -2000
        self.last_global_axionite_increase = -2000

        self.attack_target: Position | None = None
        self.attack_reason: str | None = None
        
        self.last_seen_builder_bot_round = 0
        self.last_support_launcher_round = -2000
        self.rush_enemy_core = False


        
    def run(self, ct: Controller) -> None:
        try:
            log_time(ct, "Start")
            # Init info that depends on ct
            if not hasattr(self, 'my_id'):
                self.my_id = ct.get_id()
                self.path_color = bot_path_color(self.my_id)
                self.a_star_nav.path_color = self.path_color
            if not hasattr(self, 'map'):
                self.map = Map(ct.get_map_width(), ct.get_map_height())
                self.nav.set_statics(self.map.width, self.map.height, self.my_id)
                self.a_star_nav.set_statics(self.map.width, self.map.height, self.my_id, ct.get_team())
            if not hasattr(self, 'my_team'):
                self.my_team = ct.get_team()
            if not hasattr(self, 'etype'):
                self.etype = ct.get_entity_type()
            
            log_time(ct, "After init")
                
            # Update info that could change each turn
                
            self.health = ct.get_hp()
            if self.prev_health == 0:
                self.prev_health = self.health
                
            self.global_titanium, self.global_axionite = ct.get_global_resources()
            
            if self.prev_global_titanium == -1:
                self.prev_global_titanium = self.global_titanium
            if self.prev_global_axionite == -1:
                self.prev_global_axionite = self.global_axionite
                
            # We gain passive titanium income every 4 rounds, so ignore for inferring harvest success
            if ct.get_current_round() % 4 != 0:
                if self.global_titanium > self.prev_global_titanium:
                    self.last_global_titanium_increase = ct.get_current_round()
                if self.global_axionite > self.prev_global_axionite:
                    self.last_global_axionite_increase = ct.get_current_round()

            my_pos = ct.get_position()
            vc = self.vc
            
            vc.refresh(ct, self.my_team)
            
            if self.core_pos is None and vc.core_pos is not None:
                self.core_pos = vc.core_pos
                log("core position at", self.core_pos)
                
            log(f"pos={my_pos}")

            if self.etype == EntityType.CORE:
                run_core(self, ct, my_pos, vc)

            elif self.etype == EntityType.BUILDER_BOT:
                run_builder(self, ct, my_pos, vc)

            elif self.etype == EntityType.GUNNER:
                run_gunner(self, ct, my_pos, vc)

            elif self.etype in TURRET_TYPES:
                run_turret(self, ct, my_pos, vc)

            elif self.etype == EntityType.LAUNCHER:
                run_launcher(self, ct, my_pos, vc)
            
            self.prev_health = self.health
            self.prev_global_titanium = self.global_titanium
            self.prev_global_axionite = self.global_axionite

        except Exception as e:
            print(f"Error: {e} on turn {ct.get_current_round()} by {self.etype}, ID: {ct.get_id()}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
