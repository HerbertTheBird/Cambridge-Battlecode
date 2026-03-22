# map_info.py

from __future__ import annotations
import sys
from typing import Dict, Optional, Set, Tuple

from cambc import Controller, Position, Environment, EntityType, Team, Direction, ResourceType, GameError
from dataclasses import dataclass
def try_or_none(func):
    try:
        return func()
    except GameError:
        return None
CARDINALS = [
    Direction.NORTH,
    Direction.SOUTH,
    Direction.WEST,
    Direction.EAST,
]
class MapInfo:
    @dataclass
    class Building:
        id: int
        type: EntityType
        hp: int
        maxhp: int
        team: Team
        direction: Direction | None = None
        vision_sq: int | None = None
    def in_bounds(self, pos: Position) -> bool:
        rc =  self.rc
        return 0 <= pos.x < rc.get_map_width() and 0 <= pos.y < rc.get_map_height()
    def __init__(self, rc: Controller):
        self.rc = rc
        self.width: int = rc.get_map_width()
        self.height: int = rc.get_map_height()

        self.ground: Dict[Position, Environment] = {}

        self.building: Dict[Position, MapInfo.Building | None] = {}
        
        self.my_core: Position | None = None
        self.their_core: Position | None = None


        self.last_seen: Dict[Position, int] = {}
        self.hor_sym = True
        self.ver_sym = True
        self.rot_sym = True

    def hor_flip(self, pos: Position):
        return Position(self.width - 1 - pos.x, pos.y)
    def ver_flip(self, pos: Position):
        return Position(pos.x, self.height - 1 - pos.y)
    def rot_flip(self, pos: Position):
        return Position(self.width - 1 - pos.x, self.height - 1 - pos.y)
    def update_symmetry(self, tile:Position):
        hor = self.hor_flip(tile)
        if hor in self.ground and self.ground[hor] != self.ground[tile]:
            self.hor_sym = False
        ver = self.ver_flip(tile)
        if ver in self.ground and self.ground[ver] != self.ground[tile]:
            self.ver_sym = False
        rot = self.rot_flip(tile)
        if rot in self.ground and self.ground[rot] != self.ground[tile]:
            self.rot_sym = False
    
    def core_center(self, core_id: int, tile: Position) -> Position:
        rc = self.rc
        def empty(pos: Position) -> bool:
            return self.in_bounds(pos) and rc.is_in_vision(pos) and rc.get_tile_building_id(pos) != core_id

        up = empty(Position(tile.x, tile.y - 1))
        down = empty(Position(tile.x, tile.y + 1))
        left = empty(Position(tile.x - 1, tile.y))
        right = empty(Position(tile.x + 1, tile.y))

        # Corners
        if up and left:
            return Position(tile.x + 1, tile.y + 1)   # top-left -> center is down-right
        if up and right:
            return Position(tile.x - 1, tile.y + 1)   # top-right -> center is down-left
        if down and left:
            return Position(tile.x + 1, tile.y - 1)   # bottom-left -> center is up-right
        if down and right:
            return Position(tile.x - 1, tile.y - 1)   # bottom-right -> center is up-left
        return None
    def update(self) -> None:
        rc = self.rc
        current_round = rc.get_current_round()
        visible_tiles = rc.get_nearby_tiles()
        
        for tile in visible_tiles:
            if tile not in self.last_seen:
                self.ground[tile] = rc.get_tile_env(tile)
                self.update_symmetry(tile)
            id = rc.get_tile_building_id(tile)
            if id is not None:
                self.building[tile] = MapInfo.Building(
                    id=id,
                    type=rc.get_entity_type(id),
                    hp=rc.get_hp(id),
                    maxhp=rc.get_max_hp(id),
                    team=rc.get_team(id),
                    direction=try_or_none(lambda: rc.get_direction(id)),
                    vision_sq=try_or_none(lambda: rc.get_vision_radius_sq(id)),
                )
                if self.my_core is None and self.building[tile].type == EntityType.CORE:
                    if self.building[tile].team == rc.get_team():
                        self.my_core = self.core_center(id, tile)
                    else:
                        self.their_core = self.core_center(id, tile)
            else:
                self.building[tile] = None
            self.last_seen[tile] = current_round
    def get_avoid(self) -> set[Position]:
        avoid = set()
        if self.my_core is not None:
            for x in range(self.my_core.x - 1, self.my_core.x + 2):
                for y in range(self.my_core.y - 1, self.my_core.y + 2):
                    avoid.add(Position(x, y))
        if self.their_core is not None:    
            for x in range(self.their_core.x - 1, self.their_core.x + 2):
                for y in range(self.their_core.y - 1, self.their_core.y + 2):
                    avoid.add(Position(x, y))
        for pos in self.ground:
            if self.ground[pos] != Environment.EMPTY:
                avoid.add(pos)
        for pos in self.building:
            if self.building[pos] is not None:
                avoid.add(pos)
        return avoid