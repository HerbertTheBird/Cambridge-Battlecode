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

def build_nearby_harvester() -> bool:
    me = rc.get_position()

    for d in CARDINALS:
        ore_pos = me.add(d)
        if rc.can_build_harvester(ore_pos):
            rc.build_harvester(ore_pos)
            return True
    return False

def conveyer_move(move_dir: Direction, avoid: set[Position]) -> bool:
    next_pos = rc.get_position().add(move_dir)
    if next_pos == target:
        return False
    if next_pos in avoid:
        return False
    if rc.can_move(move_dir):
        rc.move(move_dir)
        return False

    if rc.can_build_conveyor(next_pos, move_dir.opposite()):
        rc.build_conveyor(next_pos, move_dir.opposite())
        if rc.can_move(move_dir):
            rc.move(move_dir)
        return True

    return False

def run():
    global target, nearby_ore
    map_info.update()
    build_nearby_harvester()
    if target is None:
        return
    rc.draw_indicator_line(rc.get_position(), target, 0, 255, 0)
    nearest_ore()
    avoid = map_info.get_avoid(False)
    for pos in avoid:
        rc.draw_indicator_dot(pos, 255, 0, 0)
    if target in avoid:
        avoid.remove(target)
    # if nearby_ore is not None:
    #     move_dir = move_adjacent(rc.get_position(), target, nearby_ore, avoid)[0]
    # else:
    #     move_dir = move_card(rc.get_position(), target, avoid)[0]
    pathing.explore_move(target)
    # if move_dir is None:
    #     return

    # conveyer_move(move_dir, avoid)

def init(c: Controller):
    global rc, target
    print(c.get_id(), c.get_current_round(), file=sys.stderr)
    rc = c
    target = random_edge()
    map_info.init(c)
    pathing.init(c)


