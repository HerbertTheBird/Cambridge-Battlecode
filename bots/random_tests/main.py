import random
from cambc import Controller, Direction, EntityType, Position, Environment
from core import Core
from builder import Builder
from turret import Turret
import time
import sys

class Player:
    def __init__(self):
        self.initialized = False

    def run(self, c: Controller) -> None:
        start_time = time.perf_counter()
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
        end_time = time.perf_counter()
        # if c.get_current_round() < 10:
            # print(c.get_id(), (end_time - start_time)*1000, file=sys.stderr)
