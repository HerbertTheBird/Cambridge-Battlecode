# main.py

import random
from cambc import Controller, Direction, EntityType, Position, Environment
import builder
import core
import turret
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
                self.me = core
                self.me.init(c)
            elif etype == EntityType.BUILDER_BOT:
                self.me = builder
                self.me.init(c)  # Ensure builder.init(c) is called
            else:
                self.me = turret
                self.me.init(c)  # Ensure turret.init(c) is called if needed
            self.initialized = True

        self.me.run()

        end_time = time.perf_counter()
        if c.get_current_round() < 110:
            print(c.get_id(), c.get_current_round(), (end_time - start_time) * 1000, file=sys.stderr)