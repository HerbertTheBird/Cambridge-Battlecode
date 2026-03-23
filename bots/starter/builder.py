from cambc import Controller, Position, Direction, EntityType, Environment
from map_info import MapInfo
from pathing import move_card, move_adjacent, init as pathing_init
import random
import time
import sys
CARDINALS = [
    Direction.NORTH,
    Direction.SOUTH,
    Direction.WEST,
    Direction.EAST,
]
class Builder:
    def dist(self, a: Position, b: Position) -> int:
        dx = a.x - b.x
        dy = a.y - b.y
        return abs(dx) + abs(dy)

    def random_edge(self) -> Position:
        rc = self.rc
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

    def in_bounds(self, pos: Position) -> bool:
        rc =  self.rc
        return 0 <= pos.x < rc.get_map_width() and 0 <= pos.y < rc.get_map_height()

    def nearest_ore(self) -> Position | None:
        rc = self.rc
        me = rc.get_position()
        best = None
        best_d = 10**9

        for tile in self.map_info.ground:
            if not rc.is_in_vision(tile):
                continue
            if self.map_info.ground[tile] != Environment.ORE_AXIONITE and self.map_info.ground[tile] != Environment.ORE_TITANIUM:
                continue
            if self.map_info.building[tile] is not None:
                continue
            d = self.dist(me, tile)
            if d < best_d:
                best_d = d
                best = tile
        return best

    def build_nearby_harvester(self) -> bool:
        rc = self.rc
        me = rc.get_position()

        for d in CARDINALS:
            ore_pos = me.add(d)
            if rc.can_build_harvester(ore_pos):
                rc.build_harvester(ore_pos)
                return True
        return False

    def conveyer_move(self, move_dir: Direction, avoid: set[Position]) -> bool:
        rc = self.rc
        next_pos = rc.get_position().add(move_dir)
        if next_pos == self.target:
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

    def run(self):
        rc = self.rc
        self.map_info.update()
        self.build_nearby_harvester()
        if self.target is None:
            return
        rc.draw_indicator_line(rc.get_position(), self.target, 0, 255, 0)
        self.nearby_ore = self.nearest_ore()
        avoid = self.map_info.get_avoid(True)
        for pos in avoid:
            rc.draw_indicator_dot(pos, 255, 0, 0)
        if self.target in avoid:
            avoid.remove(self.target)
        if self.nearby_ore is not None:
            move_dir = move_adjacent(rc.get_position(), self.target, self.nearby_ore, avoid)[0]
        else:
            move_dir = move_card(rc.get_position(), self.target, avoid)[0]

        if move_dir is None:
            return

        self.conveyer_move(move_dir, avoid)
    def __init__(self, c: Controller):
        self.rc = c
        self.target = self.random_edge()
        self.nearby_ore = None
        self.map_info = MapInfo(c)
        pathing_init(c)
        
        
    