import map_info
from pathing import Pathing
import comms
from cambc import *
import units.builder

rc: Controller = None
nav: Pathing = None
comm_flag = 4
_cost_map: dict[int, int] = {}  # tile index -> min titanium cost to route

def init(c: Controller):
    global rc, nav
    rc = c
    nav = Pathing(rc)

def _too_expensive():
    """Bitmask of tiles we know we can't afford right now."""
    ti = rc.get_global_resources()[0]
    result = 0
    for n, cost in _cost_map.items():
        if cost > ti:
            result |= 1 << n
    return result

def _dead_end_conveyors():
    """Bitmask of routable conveyors whose output is not connected to my ore-accepting network."""
    return map_info._bm_dead_end & ~units.builder.forget[comm_flag] & ~map_info._bm_enemy_turret_threat

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
        | map_info._bm_et[map_info._IDX_CORE]
    ) & map_info._bm_team[my_team_idx]

    served = map_info.expand_manhattan(my_connected)
    return my_harvesters & ~served & ~units.builder.forget[comm_flag] & ~map_info._bm_enemy_turret_threat

def score():
    # units.builder.draw_mask(_orphan_harvesters(), 0, 0, 255)
    # units.builder.draw_mask(_dead_end_conveyors() , 0, 255, 0)
    expensive = _too_expensive()
    return 4 if ((_dead_end_conveyors() & ~expensive) or (_orphan_harvesters() & ~expensive)) else 0

def run():
    print("ROUTE")
    expensive = _too_expensive()
    dead_ends = _dead_end_conveyors() & ~expensive
    orphans = _orphan_harvesters() & ~expensive
    candidates = dead_ends | orphans

    if not candidates:
        print("no candidates")
        return

    width = map_info._width
    height = map_info._height

    best, _ = nav.closest(candidates)
    if best is None:
        print("no closest???")
        return
    
    best_bit = 1 << (best.x + best.y * width)
    is_harvester = bool(orphans & best_bit)

    if is_harvester:
        path = nav.calculate_conveyor_path(best, update=False)
        if not path:
            comms.mark(best, comm_flag)
            return

        # Move adjacent to target conveyor to place
        to_move = path[0]

        # If path[1] is near an enemy bot, override to secure it
        override = None
        if len(path) >= 2:
            for uid in rc.get_nearby_units():
                if rc.get_team(uid) != rc.get_team() and rc.get_entity_type(uid) == EntityType.BUILDER_BOT:
                    ep = rc.get_position(uid)
                    if max(abs(ep.x - path[1].x), abs(ep.y - path[1].y)) <= 2:
                        override = path[1]
                        break
        if override:
            nav.move_to({override})
        else:
            nav.move_adjacent(to_move, fallback=best)

        # Route from harvester: expand start to cardinal neighbors
    else:
        # Dead-end conveyor: route from its output tile
        best_n = best.x + best.y * width
        target_n = map_info._building_conv_target[best_n]
        tiles = width * height
        if not target_n or not (0 <= target_n < tiles):
            comms.mark(best, comm_flag)
            return
        output_tile = Position(target_n % width, target_n // width)

        path = nav.calculate_conveyor_path(output_tile, update=True)

        # If path[1] is near an enemy bot, override to secure it
        override = None
        if path and len(path) >= 2:
            for uid in rc.get_nearby_units():
                if rc.get_team(uid) != rc.get_team() and rc.get_entity_type(uid) == EntityType.BUILDER_BOT:
                    ep = rc.get_position(uid)
                    if max(abs(ep.x - path[1].x), abs(ep.y - path[1].y)) <= 2:
                        override = path[1]
                        break

        if override:
            nav.move_to({override})
        else:
            nav.move_adjacent(output_tile)

    if override:
        my_team_idx = map_info._TM_INT[rc.get_team()]
        ov_n = override.x + override.y * width
        ov_bit = 1 << ov_n
        ov_id = map_info._building_id[ov_n]
        # If enemy owns it, fire on it
        if ov_id and not (map_info._bm_team[my_team_idx] & ov_bit):
            if rc.can_fire(override):
                rc.fire(override)
            comms.mark(best, comm_flag)
            return
        # Need my road on the tile before building
        my_road = map_info._bm_et[map_info._IDX_ROAD] & map_info._bm_team[my_team_idx] & ov_bit
        if not my_road:
            if rc.can_build_road(override):
                rc.build_road(override)
                map_info.update_at(override)
            else:
                comms.mark(best, comm_flag)
                return

    if path and len(path) >= 2:
        cost = nav.conveyor_cost(path)
        best_n = best.x + best.y * width
        _cost_map[best_n] = cost
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
                    if rc.can_destroy(build_pos) and rc.get_action_cooldown() == 0:
                        print("destroy", build_pos)
                        rc.destroy(build_pos)
                        map_info.update_at(build_pos)
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
                    if rc.can_fire(build_pos):
                        rc.fire(build_pos)
                    comms.mark(best, comm_flag)
                    return
                else:
                    comms.mark(best, comm_flag)
                    return

        dx = next_pos.x - build_pos.x
        dy = next_pos.y - build_pos.y
        dist_sq = dx * dx + dy * dy
        done = False
        if dist_sq <= 1:
            direction = build_pos.direction_to(next_pos)
            if rc.can_build_conveyor(build_pos, direction):
                rc.build_conveyor(build_pos, direction)
                map_info.update_at(build_pos)
                if len(path) == 2:
                    done = True
        else:
            if rc.can_build_bridge(build_pos, next_pos):
                rc.build_bridge(build_pos, next_pos)
                map_info.update_at(build_pos)
                if len(path) == 2:
                    done = True
        if done:
            # Trace downstream from best, mark the furthest unloaded conveyor as loaded
            conv_target = map_info._building_conv_target
            tiles = width * height
            cur_n = best.x + best.y * width
            last_unloaded_bit = 0
            visited = 0
            while True:
                print("at", cur_n%width, cur_n//width)
                print("next", conv_target[cur_n]%width, conv_target[cur_n]//width)
                cur_bit = 1 << cur_n
                if visited & cur_bit:
                    print("cycle detected")
                    break
                visited |= cur_bit
                if (map_info._bm_routable & cur_bit) and not (map_info._bm_conv_loaded & cur_bit):
                    last_unloaded_bit = cur_bit
                tn = conv_target[cur_n]
                if not tn or tn < 0 or tn >= tiles:
                    print("invalid target", tn)
                    break
                tbit = 1 << tn
                if not (map_info._bm_conveyors & tbit):
                    break
                cur_n = tn
            if last_unloaded_bit:
                map_info._bm_conv_loaded |= last_unloaded_bit
                print("set loaded", (last_unloaded_bit.bit_length() - 1) % width, (last_unloaded_bit.bit_length() - 1) // width)

    comms.mark(best, comm_flag)
