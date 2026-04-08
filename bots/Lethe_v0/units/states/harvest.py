import map_info
from pathing import Pathing
import comms
from cambc import *
import units.builder

rc: Controller = None
nav: Pathing = None

comm_flag = 3

def init(c: Controller):
    global rc, nav
    rc = c
    nav = Pathing(rc)

def _available_ore():
    #filter out spots they can shoot
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
    ore = map_info._bm_env[map_info._IDX_ENV_ORE_TI]
    w = map_info._width
    # Ore tiles surrounded on all 4 cardinal sides by ore — unreachable by conveyor
    landlocked = ore & (ore >> 1 & map_info._not_left_col) & (ore << 1 & map_info._not_right_col) & (ore >> w) & (ore << w)

    # Enemy hard buildings (not road/marker) cardinally adjacent — can't harvest next to these
    enemy_hard = (
        map_info._bm_team[enemy_idx]
        & ~map_info._bm_et[map_info._IDX_ROAD]
        & ~map_info._bm_et[map_info._IDX_MARKER]
    )
    enemy_hard_adj = map_info.expand_manhattan(enemy_hard)

    return (ore
            & ~landlocked
            & ~map_info._bm_et[map_info._IDX_HARVESTER]
            & ~units.builder.forget[comm_flag]
            & ~enemy_blocking
            & ~friendly_blocking
            & ~enemy_hard_adj
            & ~map_info._bm_enemy_turret_threat
            & units.builder._harvest_zone)

def score():
    if rc.get_global_resources()[0] < rc.get_harvester_cost()[0]:
        return 0
    return 3 if _available_ore() else 0

CARD = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]

def _move_adj(target):
    adj = set()
    for d in Direction:
        if d == Direction.CENTRE:
            continue
        p = target.add(d)
        if map_info.in_bounds(p) and map_info.is_passable(p):
            adj.add(p)
    if not adj:
        adj.add(target)
    nav.move_to(adj)

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

    if best_ore is None:
        return

    # Need vision to inspect cardinal neighbors
    if not rc.is_in_vision(best_ore):
        nav.move_to({best_ore})
        comms.mark(best_ore, comm_flag)
        return

    ore_n = best_ore.x + best_ore.y * width
    my_team_idx = map_info._TM_INT[rc.get_team()]

    # --- Secure each cardinal neighbor ---
    all_secured = True
    for d in CARD:
        p = best_ore.add(d)
        if not map_info.in_bounds(p):
            continue
        if not rc.is_in_vision(p):
            nav.move_to({best_ore})
            comms.mark(best_ore, comm_flag)
            return

        pn = p.x + p.y * width
        pbit = 1 << pn

        # Wall — naturally secured
        if map_info._bm_env[map_info._IDX_ENV_WALL] & pbit:
            continue

        pid = map_info._building_id[pn]
        is_road = bool(map_info._bm_et[map_info._IDX_ROAD] & pbit)
        is_marker = bool(map_info._bm_et[map_info._IDX_MARKER] & pbit)

        # Needs barrier if: empty, or road, or marker
        needs_barrier = (not pid) or is_road or is_marker

        if not needs_barrier:
            continue  # has a real building — secured

        all_secured = False

        # Enemy road — move onto it and fire
        if pid and not (map_info._bm_team[my_team_idx] & pbit) and is_road:
            nav.move_to({p})
            if rc.can_fire(p):
                rc.fire(p)
            comms.mark(best_ore, comm_flag)
            return

        # Otherwise — move adjacent, destroy if needed, place barrier
        _move_adj(p)
        if pid and (map_info._bm_team[my_team_idx] & pbit) and rc.can_destroy(p):
            rc.destroy(p)
            map_info.note_destroy(p)
        if rc.can_build_barrier(p):
            rc.build_barrier(p)
        comms.mark(best_ore, comm_flag)
        return

    # --- All 4 secured — place harvester ---
    if all_secured:
        _move_adj(best_ore)
        if rc.can_build_harvester(best_ore):
            rc.build_harvester(best_ore)

    comms.mark(best_ore, comm_flag)
