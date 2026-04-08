from cambc import Controller, EntityType, Position

import random
import time
import traceback
import sys
from types import ModuleType
import inspect

import cambc
class Player:

    def run(self, c: Controller) -> None:
        rc = c
        tiles = rc.get_nearby_tiles()
        print(inspect.getfile(cambc))
        # for i in tiles:
        #     print(rc.get_tile_building_id((i.x, i.y)))
        if rc.get_current_round() == 100:
            rc.resign()