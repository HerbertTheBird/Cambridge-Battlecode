# main.py

from cambc import Controller, EntityType

import random
import time

import units.builder as builder
import units.core as core
import units.turret_gunner as gunner
import units.turret_sentinel as sentinel
import units.turret_breach as breach
import units.turret_launcher as launcher
import traceback
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
            self.me.init(c)
            self.initialized = True
        self.me.run()
        end_time = time.perf_counter()
        print(int((end_time-start_time)*1_000_000),"μs")
        if end_time-start_time > 0.002:
            print("timed out", c.get_id(), c.get_current_round(), int((end_time-start_time)*1_000_000),"μs", file=sys.stderr)