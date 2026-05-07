from cambc import Controller, EntityType

import random
import sys
from types import ModuleType

import units.builder as builder
import units.core as core
import units.turret_gunner as gunner
import units.turret_sentinel as sentinel
import units.turret_breach as breach
import units.turret_launcher as launcher
import map_info
import comms
import chokepoint

class Player:
    def __init__(self):
        self.initialized = False
        self.me: ModuleType
        self.spawn_turn = 0
        self.current_round: int = None


    def run(self, c: Controller) -> None:
        round_num = c.get_current_round()

        try:
            etype = c.get_entity_type()

            if not self.initialized:
                random.seed(c.get_id())

                if etype == EntityType.CORE:
                    self.me = core
                elif etype == EntityType.BUILDER_BOT:
                    self.me = builder
                elif etype == EntityType.GUNNER:
                    self.me = gunner
                elif etype == EntityType.SENTINEL:
                    self.me = sentinel
                elif etype == EntityType.BREACH:
                    self.me = breach
                elif etype == EntityType.LAUNCHER:
                    self.me = launcher

                map_info.init(c)
                comms.init(c)
                self.me.init(c)
                self.current_round = round_num
                self.spawn_turn = round_num
                self.initialized = True

            self.me.run()

            chokepoint.post_turn(c)

        except Exception as e:
            print("Error:", e)
            print(f"Error: {e}", file=sys.stderr)
