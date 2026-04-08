import map_info
from pathing import Pathing
import comms
from cambc import *
import units.builder

rc: Controller = None
nav: Pathing = None

comm_flag = 2

def init(c: Controller):
    global rc, nav
    rc = c
    nav = Pathing(rc)

def _available_ore():
    """Bitmask of titanium ore tiles without a harvester and not forgotten."""
    my_team_idx = map_info._TM_INT[rc.get_team()]
    enemy_idx = 1 - my_team_idx

    # Enemy buildings that block harvesting (not road/conveyor/bridge/splitter/marker)
    enemy_blocking = (
        map_info._bm_team[enemy_idx]
        & ~map_info._bm_et[map_info._IDX_ROAD]
        & ~map_info._bm_et[map_info._IDX_CONVEYOR]
        & ~map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR]
        & ~map_info._bm_et[map_info._IDX_BRIDGE]
        & ~map_info._bm_et[map_info._IDX_SPLITTER]
        & ~map_info._bm_et[map_info._IDX_MARKER]
    )

    # Friendly buildings that block harvesting (not road/barrier/marker)
    friendly_blocking = (
        map_info._bm_team[my_team_idx]
        & ~map_info._bm_et[map_info._IDX_ROAD]
        & ~map_info._bm_et[map_info._IDX_BARRIER]
        & ~map_info._bm_et[map_info._IDX_MARKER]
    )
    return (map_info._bm_env[map_info._IDX_ENV_ORE_TI]
            & ~map_info._bm_et[map_info._IDX_HARVESTER]
            & ~units.builder.forget[comm_flag]
            & ~enemy_blocking
            & ~friendly_blocking)

def score():
    return 2 if _available_ore() else 0

def run():
    print("HARVEST")
    available = _available_ore()
    if not available:
        return

    # Find closest ore to routable network using Manhattan ring expansion
    reached = map_info._bm_route_targets
    if not reached:
        return

    width = map_info._width
    best_ore = None

    for _ in range(width + map_info._height):
        found = available & reached
        if found:
            lsb = found & -found
            n = lsb.bit_length() - 1
            best_ore = Position(n % width, n // width)
            break
        reached = map_info.expand_manhattan(reached)

    if best_ore is not None:
        ore_n = best_ore.x + best_ore.y * width
        ore_id = map_info._building_id[ore_n]
        ore_bit = 1 << ore_n
        my_team_idx = map_info._TM_INT[rc.get_team()]

        if ore_id and (map_info._bm_team[my_team_idx] & ore_bit):
            # Friendly building on ore — move adjacent and destroy it
            adj = set()
            for dir in Direction:
                adj_pos = best_ore.add(dir)
                if not map_info.is_passable(adj_pos):
                    continue
                adj.add(adj_pos)
            if len(adj) == 0:
                adj.add(best_ore)
            nav.move_to(adj)
            if rc.can_destroy(best_ore):
                rc.destroy(best_ore)
                map_info.note_destroy(best_ore)
        elif ore_id and not (map_info._bm_team[my_team_idx] & ore_bit):
            # Enemy building on ore — move onto it and fire
            nav.move_to({best_ore})
            if rc.can_fire(rc.get_position()):
                rc.fire(rc.get_position())
        else:
            # Clear tile — move adjacent and build harvester
            adj = set()
            for dir in Direction:
                adj_pos = best_ore.add(dir)
                if not map_info.is_passable(adj_pos):
                    continue
                adj.add(adj_pos)
            if len(adj) == 0:
                adj.add(best_ore)
            nav.move_to(adj)
            if rc.can_build_harvester(best_ore):
                rc.build_harvester(best_ore)

        comms.mark(best_ore, comm_flag)
