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

rc = None
width = height = 0
ground: Dict[Position, Environment] = {}
building: Dict[Position, Building | None] = {}
my_core: Position | None = None
their_core: Position | None = None
core_id: int | None = None
last_seen: Dict[Position, int] = {}
hor_sym = True
ver_sym = True
rot_sym = True

@dataclass
class Building:
    id: int
    type: EntityType
    hp: int
    maxhp: int
    team: Team
    direction: Direction | None = None
    vision_sq: int | None = None

def in_bounds(pos: Position) -> bool:
    return 0 <= pos.x < width and 0 <= pos.y < height

def init(c: Controller):
    global rc, width, height
    rc = c
    width = rc.get_map_width()
    height = rc.get_map_height()

def hor_flip(pos: Position):
    return Position(width - 1 - pos.x, pos.y)

def ver_flip(pos: Position):
    return Position(pos.x, height - 1 - pos.y)

def rot_flip(pos: Position):
    return Position(width - 1 - pos.x, height - 1 - pos.y)

def update_symmetry(tile: Position):
    global hor_sym, ver_sym, rot_sym
    hor = hor_flip(tile)
    if hor in ground and ground[hor] != ground[tile]:
        hor_sym = False
    ver = ver_flip(tile)
    if ver in ground and ground[ver] != ground[tile]:
        ver_sym = False
    rot = rot_flip(tile)
    if rot in ground and ground[rot] != ground[tile]:
        rot_sym = False

def possible_flips(pos: Position) -> Set[Position]:
    flips = set()
    if hor_sym:
        flips.add(hor_flip(pos))
    if ver_sym:
        flips.add(ver_flip(pos))
    if rot_sym:
        flips.add(rot_flip(pos))
    return flips

def core_center(core_id: int, tile: Position) -> Position:
    def empty(pos: Position) -> bool:
        return in_bounds(pos) and rc.is_in_vision(pos) and rc.get_tile_building_id(pos) != core_id

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

def update() -> None:
    global my_core, their_core, core_id
    current_round = rc.get_current_round()
    visible_tiles = rc.get_nearby_tiles()
    
    for tile in visible_tiles:
        if tile not in last_seen:
            ground[tile] = rc.get_tile_env(tile)
            update_symmetry(tile)
        id = rc.get_tile_building_id(tile)
        if id is not None:
            building[tile] = Building(
                id=id,
                type=rc.get_entity_type(id),
                hp=rc.get_hp(id),
                maxhp=rc.get_max_hp(id),
                team=rc.get_team(id),
                direction=try_or_none(lambda: rc.get_direction(id)),
                vision_sq=try_or_none(lambda: rc.get_vision_radius_sq(id)),
            )
            if my_core is None and building[tile].type == EntityType.CORE:
                if building[tile].team == rc.get_team():
                    my_core = core_center(id, tile)
                    core_id = id
                else:
                    their_core = core_center(id, tile)
        else:
            building[tile] = None
        last_seen[tile] = current_round

def get_avoid(avoid_walkables: bool) -> set[Position]:
    avoid = set()
    for unit in rc.get_nearby_units():
        if rc.get_entity_type(unit) == EntityType.BUILDER_BOT:
            avoid.add(rc.get_position(unit))
    avoid_core = rc.get_tile_building_id(rc.get_position()) != core_id
    if my_core is not None and avoid_core:
        for x in range(my_core.x - 1, my_core.x + 2):
            for y in range(my_core.y - 1, my_core.y + 2):
                avoid.add(Position(x, y))
    if their_core is not None:    
        for x in range(their_core.x - 1, their_core.x + 2):
            for y in range(their_core.y - 1, their_core.y + 2):
                avoid.add(Position(x, y))
    for pos in ground:
        if ground[pos] != Environment.EMPTY:
            avoid.add(pos)
    for pos in building:
        if building[pos] is not None:
            type = building[pos].type
            if type == EntityType.CORE and not avoid_core:
                continue
            if not avoid_walkables and (type == EntityType.CONVEYOR or type == EntityType.ARMOURED_CONVEYOR or type == EntityType.BRIDGE or type == EntityType.SPLITTER or type == EntityType.ROAD):
                continue
            avoid.add(pos)
    return avoid