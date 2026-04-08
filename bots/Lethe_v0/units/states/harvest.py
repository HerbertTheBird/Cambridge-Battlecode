import map_info
from pathing import Pathing
import comms
from cambc import *
import units.builder

rc: Controller = None
nav: Pathing = None

comm_flag = 2
forget = None
def init(c: Controller):
    global rc, nav, forget
    rc = c
    nav = Pathing(rc)
    forget = units.builder.forget[comm_flag]
def valid_ore(i, val):
    ore = map_info._ENV_INT[Environment.ORE_TITANIUM]
    harvester = map_info._ET_INT[EntityType.HARVESTER]
    return val == ore and i not in forget and not (map_info._building_id[i] != 0 and map_info._building_type[i] == harvester)
def score():
    for i, val in enumerate(map_info._ground):
        if valid_ore(i, val):
            return 2
    return 0
def run():
    print("HARVEST")
    best_ore = None
    for i, val in enumerate(map_info._ground):
        if valid_ore(i, val):
            pos = Position(i % map_info._width, i // map_info._width)
            if best_ore is None or map_info._my_core.distance_squared(pos) < map_info._my_core.distance_squared(best_ore):
                best_ore = pos
    if best_ore is not None:
        adj = set()
        for dir in Direction:
            adj_pos = best_ore.add(dir)
            if not map_info.is_passable(adj_pos):
                continue
            adj.add(adj_pos)
        if len(adj) == 0:
            adj.add(best_ore)
        print(best_ore)
        nav.move_to(adj)
        if rc.can_build_harvester(best_ore):
            rc.build_harvester(best_ore)
        comms.mark(best_ore, comm_flag)

    pass