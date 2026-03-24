# main.py

import random
from cambc import Controller, Direction, EntityType, Position, Environment
import builder
import core
import turret
import time
import sys

DEBUG = False  # Set to True to enable debug logs

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

        if DEBUG and c.get_current_round() > 240:
            print(f"DEBUG: Round {c.get_current_round()} start for entity {c.get_id()}", file=sys.stderr)

        self.me.run()

        if DEBUG and c.get_current_round() > 240:
            print(f"DEBUG: Round {c.get_current_round()} end for entity {c.get_id()}", file=sys.stderr)

        end_time = time.perf_counter()
        if DEBUG and c.get_current_round() > 240:
            print(f"DEBUG: Round {c.get_current_round()} execution time: {(end_time - start_time) * 1000:.2f} ms", file=sys.stderr)