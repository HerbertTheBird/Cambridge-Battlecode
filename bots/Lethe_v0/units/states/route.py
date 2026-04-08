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

def _dead_end_conveyors():
    """Bitmask of routable conveyors whose output is not connected to my ore-accepting network."""
    routable = map_info._bm_routable & ~units.builder.forget[comm_flag]
    if not routable:
        return 0

    my_team_idx = map_info._TM_INT[rc.get_team()]

    my_accept = 0
    for et in map_info._ACCEPT_ORE:
        my_accept |= map_info._bm_et[map_info._ET_INT[et]]
    my_accept &= map_info._bm_team[my_team_idx]

    enemy_idx = 1 - my_team_idx
    enemy_replaceable = (
        map_info._bm_et[map_info._IDX_CONVEYOR]
        | map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR]
        | map_info._bm_et[map_info._IDX_BRIDGE]
        | map_info._bm_et[map_info._IDX_SPLITTER]
        | map_info._bm_et[map_info._IDX_ROAD]
        | map_info._bm_et[map_info._IDX_MARKER]
    ) & map_info._bm_team[enemy_idx]

    dead_ends = 0
    tiles = map_info._width * map_info._height
    building_id = map_info._building_id
    conv_target = map_info._building_conv_target
    bm_my = map_info._bm_team[my_team_idx]

    mask = routable
    while mask:
        lsb = mask & -mask
        n = lsb.bit_length() - 1
        tn = conv_target[n]
        if tn and 0 <= tn < tiles:
            tbit = 1 << tn
            if not (my_accept & tbit):
                if building_id[tn] == 0 or (bm_my & tbit) or (enemy_replaceable & tbit):
                    dead_ends |= lsb
        else:
            dead_ends |= lsb
        mask ^= lsb
    return dead_ends

def _orphan_harvesters():
    """Bitmask of my harvesters with no adjacent conveyor/turret/core."""
    my_team_idx = map_info._TM_INT[rc.get_team()]
    my_harvesters = map_info._bm_et[map_info._IDX_HARVESTER] & map_info._bm_team[my_team_idx]
    if not my_harvesters:
        return 0

    my_connected = (
        map_info._bm_et[map_info._IDX_CONVEYOR]
        | map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR]
        | map_info._bm_et[map_info._IDX_BRIDGE]
        | map_info._bm_et[map_info._IDX_SPLITTER]
        | map_info._bm_et[map_info._IDX_GUNNER]
        | map_info._bm_et[map_info._IDX_SENTINEL]
        | map_info._bm_et[map_info._IDX_BREACH]
        | map_info._bm_et[map_info._IDX_LAUNCHER]
        | map_info._bm_et[map_info._IDX_CORE]
    ) & map_info._bm_team[my_team_idx]

    served = map_info.expand_chebyshev(my_connected)
    return my_harvesters & ~served & ~units.builder.forget[comm_flag]

def score():
    return 3 if (_dead_end_conveyors() or _orphan_harvesters()) else 0

def run():
    print("ROUTE")
    dead_ends = _dead_end_conveyors()
    orphans = _orphan_harvesters()
    candidates = dead_ends | orphans
    if not candidates:
        return

    core = map_info._my_core
    if core is None:
        return

    width = map_info._width
    height = map_info._height

    # Find closest candidate to core via Chebyshev
    reached = 1 << (core.x + core.y * width)
    best = None
    for _ in range(max(width, height)):
        found = candidates & reached
        if found:
            lsb = found & -found
            n = lsb.bit_length() - 1
            best = Position(n % width, n // width)
            break
        reached = map_info.expand_chebyshev(reached)

    if best is None:
        return

    best_bit = 1 << (best.x + best.y * width)
    is_harvester = bool(orphans & best_bit)

    if is_harvester:
        # Move adjacent to harvester
        adj = set()
        for d in Direction:
            if d == Direction.CENTRE:
                continue
            p = best.add(d)
            if map_info.in_bounds(p) and map_info.is_passable(p):
                adj.add(p)
        if not adj:
            adj.add(best)
        nav.move_to(adj)

        # Route from harvester: expand start to cardinal neighbors
        path = nav.calculate_conveyor_path(best, update=False)
    else:
        # Dead-end conveyor: route from its output tile
        best_n = best.x + best.y * width
        target_n = map_info._building_conv_target[best_n]
        tiles = width * height
        if not target_n or not (0 <= target_n < tiles):
            comms.mark(best, comm_flag)
            return
        output_tile = Position(target_n % width, target_n // width)

        # Move adjacent to output tile
        adj = set()
        for d in Direction:
            if d == Direction.CENTRE:
                continue
            p = output_tile.add(d)
            if map_info.in_bounds(p) and map_info.is_passable(p):
                adj.add(p)
        if not adj:
            adj.add(output_tile)
        nav.move_to(adj)

        # Route from output tile directly
        path = nav.calculate_conveyor_path(output_tile, update=True)

    if path and len(path) >= 2:
        build_pos = path[0]
        next_pos = path[1]

        # Check what's on the build tile
        bp_n = build_pos.x + build_pos.y * width
        bp_id = map_info._building_id[bp_n]
        if bp_id != 0:
            bp_bit = 1 << bp_n
            my_team_idx = map_info._TM_INT[rc.get_team()]
            is_mine = bool(map_info._bm_team[my_team_idx] & bp_bit)

            if is_mine:
                # Friendly: destroy if road/barrier/marker, else abort
                is_clearable = bool(
                    (map_info._bm_et[map_info._IDX_ROAD]
                     | map_info._bm_et[map_info._IDX_BARRIER]
                     | map_info._bm_et[map_info._IDX_MARKER]) & bp_bit
                )
                if is_clearable:
                    if rc.can_destroy(build_pos):
                        rc.destroy(build_pos)
                        map_info.note_destroy(build_pos)
                else:
                    comms.mark(best, comm_flag)
                    return
            else:
                # Enemy: fire if road/conveyor/bridge/splitter/marker, else abort
                is_enemy_clearable = bool(
                    (map_info._bm_et[map_info._IDX_ROAD]
                     | map_info._bm_et[map_info._IDX_CONVEYOR]
                     | map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR]
                     | map_info._bm_et[map_info._IDX_BRIDGE]
                     | map_info._bm_et[map_info._IDX_SPLITTER]
                     | map_info._bm_et[map_info._IDX_MARKER]) & bp_bit
                )
                if is_enemy_clearable:
                    # Move onto tile and fire
                    nav.move_to({build_pos})
                    if rc.can_fire(rc.get_position()):
                        rc.fire(rc.get_position())
                    comms.mark(best, comm_flag)
                    return
                else:
                    comms.mark(best, comm_flag)
                    return

        dx = next_pos.x - build_pos.x
        dy = next_pos.y - build_pos.y
        dist_sq = dx * dx + dy * dy

        if dist_sq <= 1:
            direction = build_pos.direction_to(next_pos)
            if rc.can_build_conveyor(build_pos, direction):
                rc.build_conveyor(build_pos, direction)
        else:
            if rc.can_build_bridge(build_pos, next_pos):
                rc.build_bridge(build_pos, next_pos)

    comms.mark(best, comm_flag)
