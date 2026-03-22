# main.py

import random
from cambc import Controller, Direction, EntityType, Position, Environment
from core import Core
from builder import Builder
from turret import Turret



class Player:
    def __init__(self):
        self.initialized = False
    def run(self, c: Controller) -> None:
        etype = c.get_entity_type()

        if not self.initialized:
            random.seed(c.get_current_round())
            if etype == EntityType.CORE:
                self.me = Core(c)
            elif etype == EntityType.BUILDER_BOT:
                self.me = Builder(c)
            else:
                self.me = Turret(c)
            self.initialized = True
        self.me.run()
        