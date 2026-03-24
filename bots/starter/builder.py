from cambc import Controller, Position, Direction, EntityType, Environment
import map_info
import pathing
import random
import sys

CARDINALS = [
    Direction.NORTH,
    Direction.SOUTH,
    Direction.WEST,
    Direction.EAST,
]

rc = None
target = None
nearby_ore = None
path = None
path_index = 0


def dist(a: Position, b: Position) -> int:
    dx = a.x - b.x
    dy = a.y - b.y
    return abs(dx) + abs(dy)

def random_edge() -> Position:
    w = rc.get_map_width()
    h = rc.get_map_height()

    side = random.randint(0, 3)

    if side == 0:
        return Position(random.randint(0, w - 1), 0)
    if side == 1:
        return Position(random.randint(0, w - 1), h - 1)
    if side == 2:
        return Position(0, random.randint(0, h - 1))
    return Position(w - 1, random.randint(0, h - 1))

def in_bounds(pos: Position) -> bool:
    return 0 <= pos.x < rc.get_map_width() and 0 <= pos.y < rc.get_map_height()

def nearest_ore() -> Position | None:
    global nearby_ore
    me = rc.get_position()
    best = None
    best_d = 10**9

    for tile in map_info.ground:
        if not rc.is_in_vision(tile):
            continue
        if map_info.ground[tile] != Environment.ORE_AXIONITE and map_info.ground[tile] != Environment.ORE_TITANIUM:
            continue
        if map_info.building[tile] is not None:
            continue
        d = dist(me, tile)
        if d < best_d:
            best_d = d
            best = tile
    nearby_ore = best
    return best

def run():
    global target, nearby_ore, path, path_index
    map_info.update()
    if pathing.core_to:
        rc.draw_indicator_dot(pathing.core_to, 0, 255, 0)
    if target is None:
        return
    if path is None:
        nearest_ore()

    if nearby_ore is not None:
        rc.draw_indicator_line(rc.get_position(), nearby_ore, 0, 255, 0)
        if rc.can_build_harvester(nearby_ore) and rc.get_global_resources()[0] > 400:
            rc.build_harvester(nearby_ore)
            path = pathing.conveyor_path(nearby_ore)
            path_index = 0
        elif path is None:
            pathing.explore_move(nearby_ore.add(Direction.NORTH))
        else:
            if path is not None:
                for i in path:
                    rc.draw_indicator_dot(i, 255, 0, 0)
                rc.draw_indicator_dot(path[path_index], 255, 255, 0)
            path_index += pathing.build_path(path, path_index)
            if path_index >= len(path) - 1:
                path = None
                path_index = 0
                nearby_ore = None
    else:
        if rc.get_global_resources()[0] > 500:
            rc.draw_indicator_line(rc.get_position(), target, 0, 255, 0)
            if not pathing.explore_move(target):
                target = random_edge()

def init(c: Controller):
    global rc, target
    print(c.get_id(), c.get_current_round(), file=sys.stderr)
    rc = c
    target = random_edge()
    map_info.init(c)
    pathing.init(c)


