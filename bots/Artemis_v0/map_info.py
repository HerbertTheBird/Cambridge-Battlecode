# map_info.py

from __future__ import annotations
import sys
from typing import Dict, Optional, Set, Tuple

from cambc import Controller, Position, Environment, EntityType, Team, Direction, ResourceType, GameError
from dataclasses import dataclass

def is_on_map(pos : Position):
    return 0 <= pos.x < width and 0 <= pos.y < height 

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
stuck_turns: Dict[Position, int] = {}
past_filled: Dict[Position, int] = {}
my_core: Position | None = None
their_core: Position | None = None
core_id: int | None = None
last_seen: Dict[Position, int] = {}
hor_sym = True
ver_sym = True
rot_sym = True
solved_sym = False
@dataclass
class Building:
    id: int
    type: EntityType
    hp: int
    maxhp: int
    team: Team
    direction: Direction | None = None
    vision_sq: int | None = None
    bridge_target: Position | None = None
    conveyor_speed: int | None = None #number of turns it takes to transfer one resource
    stored_resource_id: int | None = None #current stored id
    load: int | None = None #load is how many turns this contains a resource over a 4 turn cycle

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

def flip(pos: Position):
    if not solved_sym:
        return None
    if hor_sym:
        return hor_flip(pos)
    if ver_sym:
        return ver_flip(pos)
    if rot_sym:
        return rot_flip(pos)
    return None

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

def is_conveyor(type: EntityType):
    return type == EntityType.CONVEYOR or type == EntityType.ARMOURED_CONVEYOR or type == EntityType.BRIDGE or type == EntityType.SPLITTER
def is_turret(type: EntityType):
    return type == EntityType.GUNNER or type == EntityType.SENTINEL or type == EntityType.BREACH
def update() -> None:

    global my_core, their_core, core_id, solved_sym
    current_round = rc.get_current_round()
    visible_tiles = rc.get_nearby_tiles()
    
    for tile in visible_tiles:
        if tile not in ground:
            ground[tile] = rc.get_tile_env(tile)
            if solved_sym:
                ground[flip(tile)] = ground[tile]
            update_symmetry(tile)
        id = rc.get_tile_building_id(tile)
        if id is not None:
            speed = None
            type = rc.get_entity_type(id)
            if last_seen.get(tile, -2) == current_round-1 and is_conveyor(type) and building[tile] is not None and is_conveyor(building[tile].type):
                if rc.get_stored_resource_id(id) == building[tile].stored_resource_id and rc.get_stored_resource_id(id) is not None:
                    stuck_turns[tile] = stuck_turns.get(tile, 0) + 1
                else:
                    speed = stuck_turns.get(tile, 0)+1
                    stuck_turns[tile] = 0
            else:
                stuck_turns[tile] = 0
            
            load = None
            if is_conveyor(type):
                if last_seen.get(tile, -2) == current_round-1 and building[tile] is not None and is_conveyor(building[tile].type):
                    past_filled[tile] = ((past_filled[tile]&15) << 1) | (past_filled[tile]&(~15))
                    past_filled[tile] += 1 if rc.get_stored_resource(id) is not None else 0
                    if (past_filled[tile]&16) != 0:
                        load = (past_filled[tile]&15).bit_count()
                else:
                    past_filled[tile] = 2 + (1 if rc.get_stored_resource(id) is not None else 0)
            
            building[tile] = Building(
                id=id,
                type=type,
                hp=rc.get_hp(id),
                maxhp=rc.get_max_hp(id),
                team=rc.get_team(id),
                direction=try_or_none(lambda: rc.get_direction(id)),
                vision_sq=try_or_none(lambda: rc.get_vision_radius_sq(id)),
                bridge_target=try_or_none(lambda: rc.get_bridge_target(id)),
                stored_resource_id=try_or_none(lambda: rc.get_stored_resource_id(id)),
                conveyor_speed=speed,
                load=load
            )
            # if speed == 1:
            #     rc.draw_indicator_dot(tile, 0, 255, 0)
            # elif speed != None:
            #     rc.draw_indicator_dot(tile, 255, 255, 0)
            if load != None:
                rc.draw_indicator_dot(tile, 0, 0, 50*load)
            if my_core is None and building[tile].type == EntityType.CORE:
                if building[tile].team == rc.get_team():
                    my_core = core_center(id, tile)
                    core_id = id
                else:
                    their_core = core_center(id, tile)
        else:
            building[tile] = None
        last_seen[tile] = current_round
    possible_syms = 0
    if hor_sym:
        possible_syms += 1
    if ver_sym:
        possible_syms += 1
    if rot_sym:
        possible_syms += 1

    if possible_syms == 1 and not solved_sym:
        solved_sym = True
        if my_core:
            their_core = flip(my_core)
        for tile in list(ground):
            flipped = flip(tile)
            if flipped not in ground:
                ground[flipped] = ground[tile]

def is_tile_empty(pos : Position):
    return rc.is_tile_empty(pos) or (rc.get_tile_building_id(pos) != None and rc.get_entity_type(rc.get_tile_building_id(pos)) == EntityType.MARKER)

def get_avoid(avoid_conveyors: bool, avoid_builders: bool, avoid_barriers_ore: bool = True) -> set[Position]:
    avoid = set()
    if avoid_builders:
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
        if ground[pos] != Environment.EMPTY and ground[pos] != Environment.ORE_AXIONITE:
            if not avoid_barriers_ore and ground[pos] == Environment.ORE_TITANIUM:
                continue
            avoid.add(pos)
    for pos in building:
        if building[pos] is not None:
            type = building[pos].type
            if type == EntityType.CORE and not avoid_core:
                continue
            if type == EntityType.ROAD:
                continue
            if type == EntityType.MARKER:
                continue
            if type == EntityType.BARRIER and not avoid_barriers_ore:
                continue
            if not avoid_conveyors and (type == EntityType.CONVEYOR or type == EntityType.ARMOURED_CONVEYOR or type == EntityType.BRIDGE or type == EntityType.SPLITTER):
                continue
            avoid.add(pos)
    return avoid