from __future__ import annotations
import sys
from typing import Optional, Set, Tuple

from cambc import Controller, Position, Environment, EntityType, Team, Direction, ResourceType, GameError
from dataclasses import dataclass
import time
def is_on_map(pos: Position):
    return 0 <= pos.x < width and 0 <= pos.y < height

CARDINALS = [
    Direction.NORTH,
    Direction.SOUTH,
    Direction.WEST,
    Direction.EAST,
]

has_direction = {EntityType.ARMOURED_CONVEYOR, EntityType.BREACH, EntityType.CONVEYOR, EntityType.GUNNER, EntityType.SENTINEL, EntityType.SPLITTER}
has_vision = {EntityType.BREACH, EntityType.GUNNER, EntityType.LAUNCHER, EntityType.SENTINEL}
has_bridge_target = {EntityType.BRIDGE}
has_stored_resource = {EntityType.ARMOURED_CONVEYOR, EntityType.BRIDGE, EntityType.CONVEYOR, EntityType.FOUNDRY, EntityType.SPLITTER}

rc = None
width = height = 0

ground: list[list[Environment | None]] = []
ground_seen: list[list[bool]] = []
building: list[list["Building | None"]] = []
stuck_turns: list[list[int]] = []
past_filled: list[list[int]] = []
last_seen: list[list[int]] = []

my_core: Position | None = None
their_core: Position | None = None
core_id: int | None = None
hor_sym = True
ver_sym = True
rot_sym = True
solved_sym = False

ground_blocked_all: set[Position] = set()
ground_blocked_no_ore: set[Position] = set()

building_blocked_all: set[Position] = set()
building_blocked_no_barrier: set[Position] = set()
building_blocked_no_conveyors: set[Position] = set()
building_blocked_no_barrier_no_conveyors: set[Position] = set()

my_core_area: set[Position] = set()
their_core_area: set[Position] = set()

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
    conveyor_speed: int | None = None
    stored_resource_id: int | None = None
    load: int | None = None


def in_bounds(pos: Position) -> bool:
    return 0 <= pos.x < width and 0 <= pos.y < height


def init(c: Controller):
    global rc, width, height
    global ground, ground_seen, building, stuck_turns, past_filled, last_seen
    global ground_blocked_all, ground_blocked_no_ore
    global building_blocked_all, building_blocked_no_barrier
    global building_blocked_no_conveyors, building_blocked_no_barrier_no_conveyors
    global my_core_area, their_core_area

    rc = c
    width = rc.get_map_width()
    height = rc.get_map_height()

    ground = [[None for _ in range(height)] for _ in range(width)]
    ground_seen = [[False for _ in range(height)] for _ in range(width)]
    building = [[None for _ in range(height)] for _ in range(width)]
    stuck_turns = [[0 for _ in range(height)] for _ in range(width)]
    past_filled = [[0 for _ in range(height)] for _ in range(width)]
    last_seen = [[-2 for _ in range(height)] for _ in range(width)]

    ground_blocked_all = set()
    ground_blocked_no_ore = set()

    building_blocked_all = set()
    building_blocked_no_barrier = set()
    building_blocked_no_conveyors = set()
    building_blocked_no_barrier_no_conveyors = set()

    my_core_area = set()
    their_core_area = set()
def hor_flip(pos: Position):
    return Position(width - 1 - pos.x, pos.y)


def ver_flip(pos: Position):
    return Position(pos.x, height - 1 - pos.y)


def rot_flip(pos: Position):
    return Position(width - 1 - pos.x, height - 1 - pos.y)


def update_symmetry(tile: Position):
    global hor_sym, ver_sym, rot_sym

    tx = tile.x
    ty = tile.y

    hor = hor_flip(tile)
    if ground_seen[hor.x][hor.y] and ground[hor.x][hor.y] != ground[tx][ty]:
        hor_sym = False

    ver = ver_flip(tile)
    if ground_seen[ver.x][ver.y] and ground[ver.x][ver.y] != ground[tx][ty]:
        ver_sym = False

    rot = rot_flip(tile)
    if ground_seen[rot.x][rot.y] and ground[rot.x][rot.y] != ground[tx][ty]:
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

    if up and left:
        return Position(tile.x + 1, tile.y + 1)
    if up and right:
        return Position(tile.x - 1, tile.y + 1)
    if down and left:
        return Position(tile.x + 1, tile.y - 1)
    if down and right:
        return Position(tile.x - 1, tile.y - 1)
    return None


def is_conveyor(type: EntityType):
    return type == EntityType.CONVEYOR or type == EntityType.ARMOURED_CONVEYOR or type == EntityType.BRIDGE or type == EntityType.SPLITTER


def is_turret(type: EntityType):
    return type == EntityType.GUNNER or type == EntityType.SENTINEL or type == EntityType.BREACH

def _rebuild_core_areas() -> None:
    global my_core_area, their_core_area

    my_core_area = set()
    their_core_area = set()

    if my_core is not None:
        for x in range(my_core.x - 1, my_core.x + 2):
            for y in range(my_core.y - 1, my_core.y + 2):
                if 0 <= x < width and 0 <= y < height:
                    my_core_area.add(Position(x, y))

    if their_core is not None:
        for x in range(their_core.x - 1, their_core.x + 2):
            for y in range(their_core.y - 1, their_core.y + 2):
                if 0 <= x < width and 0 <= y < height:
                    their_core_area.add(Position(x, y))


def _update_ground_blocked_at(x: int, y: int) -> None:
    pos = Position(x, y)
    env = ground[x][y]

    ground_blocked_all.discard(pos)
    ground_blocked_no_ore.discard(pos)

    if env is None:
        return
    if env == Environment.EMPTY or env == Environment.ORE_AXIONITE:
        return

    ground_blocked_all.add(pos)
    if env != Environment.ORE_TITANIUM:
        ground_blocked_no_ore.add(pos)


def _update_building_blocked_at(x: int, y: int) -> None:
    pos = Position(x, y)
    b = building[x][y]

    building_blocked_all.discard(pos)
    building_blocked_no_barrier.discard(pos)
    building_blocked_no_conveyors.discard(pos)
    building_blocked_no_barrier_no_conveyors.discard(pos)

    if b is None:
        return

    t = b.type
    if t == EntityType.ROAD or t == EntityType.MARKER:
        return

    is_barrier = t == EntityType.BARRIER
    is_conv = (
        t == EntityType.CONVEYOR
        or t == EntityType.ARMOURED_CONVEYOR
        or t == EntityType.BRIDGE
        or t == EntityType.SPLITTER
    )

    building_blocked_all.add(pos)
    if not is_barrier:
        building_blocked_no_barrier.add(pos)
    if not is_conv:
        building_blocked_no_conveyors.add(pos)
    if not is_barrier and not is_conv:
        building_blocked_no_barrier_no_conveyors.add(pos)
def update() -> None:
    print("start update", rc.get_cpu_time_elapsed())
    start_time = time.perf_counter()

    global my_core, their_core, core_id, solved_sym

    current_round = rc.get_current_round()
    visible_tiles = rc.get_nearby_tiles()
    my_team = rc.get_team()

    ground_local = ground
    ground_seen_local = ground_seen
    building_local = building
    stuck_turns_local = stuck_turns
    past_filled_local = past_filled
    last_seen_local = last_seen

    solved_sym_local = solved_sym
    hor_sym_local = hor_sym
    ver_sym_local = ver_sym
    rot_sym_local = rot_sym

    for tile in visible_tiles:
        x = tile.x
        y = tile.y

        if not ground_seen_local[x][y]:
            env = rc.get_tile_env(tile)
            ground_local[x][y] = env
            ground_seen_local[x][y] = True
            _update_ground_blocked_at(x, y)

            if solved_sym_local:
                flipped = flip(tile)
                fx = flipped.x
                fy = flipped.y
                ground_local[fx][fy] = env
                ground_seen_local[fx][fy] = True
                _update_ground_blocked_at(fx, fy)

            update_symmetry(tile)

        entity_id = rc.get_tile_building_id(tile)
        if entity_id is None:
            if building_local[x][y] is not None:
                building_local[x][y] = None
                _update_building_blocked_at(x, y)
            last_seen_local[x][y] = current_round
            continue

        prev_building = building_local[x][y]
        seen_last_turn = last_seen_local[x][y] == current_round - 1

        entity_type = rc.get_entity_type(entity_id)
        is_conv = (
            entity_type == EntityType.CONVEYOR
            or entity_type == EntityType.ARMOURED_CONVEYOR
            or entity_type == EntityType.BRIDGE
            or entity_type == EntityType.SPLITTER
        )
        prev_is_conv = prev_building is not None and (
            prev_building.type == EntityType.CONVEYOR
            or prev_building.type == EntityType.ARMOURED_CONVEYOR
            or prev_building.type == EntityType.BRIDGE
            or prev_building.type == EntityType.SPLITTER
        )

        has_sr = entity_type in has_stored_resource

        stored_resource_id = rc.get_stored_resource_id(entity_id) if has_sr else None
        stored_resource = None
        if is_conv and has_sr:
            stored_resource = rc.get_stored_resource(entity_id)

        speed = None
        if seen_last_turn and is_conv and prev_is_conv:
            if stored_resource_id == prev_building.stored_resource_id and stored_resource_id is not None:
                stuck_turns_local[x][y] += 1
            else:
                speed = stuck_turns_local[x][y] + 1
                stuck_turns_local[x][y] = 0
        else:
            stuck_turns_local[x][y] = 0

        load = None
        if is_conv:
            if seen_last_turn and prev_is_conv:
                pf = past_filled_local[x][y]
                pf = ((pf & 15) << 1) | (pf & (~15))
                if stored_resource is not None:
                    pf += 1
                past_filled_local[x][y] = pf
                if pf & 16:
                    load = (pf & 15).bit_count()
            else:
                past_filled_local[x][y] = 2 + (1 if stored_resource is not None else 0)

        direction = rc.get_direction(entity_id) if entity_type in has_direction else None
        vision_sq = rc.get_vision_radius_sq(entity_id) if entity_type in has_vision else None
        bridge_target = rc.get_bridge_target(entity_id) if entity_type in has_bridge_target else None

        team = rc.get_team(entity_id)
        new_building = Building(
            id=entity_id,
            type=entity_type,
            hp=rc.get_hp(entity_id),
            maxhp=rc.get_max_hp(entity_id),
            team=team,
            direction=direction,
            vision_sq=vision_sq,
            bridge_target=bridge_target,
            stored_resource_id=stored_resource_id,
            conveyor_speed=speed,
            load=load,
        )
        building_local[x][y] = new_building
        _update_building_blocked_at(x, y)

        if load is not None:
            rc.draw_indicator_dot(tile, 0, 0, 50 * load)

        if my_core is None and entity_type == EntityType.CORE:
            if team == my_team:
                my_core = core_center(entity_id, tile)
                core_id = entity_id
                _rebuild_core_areas()
            else:
                their_core = core_center(entity_id, tile)
                _rebuild_core_areas()
                last_seen_local[x][y] = current_round

    possible_syms = int(hor_sym) + int(ver_sym) + int(rot_sym)

    if possible_syms == 1 and not solved_sym:
        solved_sym = True
        if my_core:
            their_core = flip(my_core)
            core = Building(
                id=-1,
                type=EntityType.CORE,
                hp=500,
                maxhp=500,
                team=rc.get_team()+1,
                direction=None,
                vision_sq=36,
            )
            for x in range(their_core.x-1, their_core.x+2):
                for y in range(their_core.y-1, their_core.y+2):
                    building[x][y] = core
                    

        # pick one flip function once, instead of branching in flip() for every tile
        if hor_sym:
            flip_func = hor_flip
        elif ver_sym:
            flip_func = ver_flip
        else:
            flip_func = rot_flip

        for x in range(width):
            for y in range(height):
                if ground_seen_local[x][y]:
                    flipped = flip_func(Position(x, y))
                    fx = flipped.x
                    fy = flipped.y
                    if not ground_seen_local[fx][fy]:
                        ground_local[fx][fy] = ground_local[x][y]
                        ground_seen_local[fx][fy] = True
                        _update_ground_blocked_at(fx, fy)

    end_time = time.perf_counter()
    print("end update", rc.get_cpu_time_elapsed(), (end_time - start_time) * 1000000)
def is_tile_empty(pos: Position):
    return in_bounds(pos) and (rc.is_tile_empty(pos) or (rc.get_tile_building_id(pos) != None and rc.get_entity_type(rc.get_tile_building_id(pos)) == EntityType.MARKER))


def get_avoid(
    avoid_conveyors: bool,
    avoid_builders: bool,
    avoid_barrier: bool = True,
    avoid_ore: bool = True,
) -> set[Position]:
    avoid_core = rc.get_tile_building_id(rc.get_position()) != core_id

    if avoid_ore:
        avoid = ground_blocked_all.copy()
    else:
        avoid = ground_blocked_no_ore.copy()

    if avoid_conveyors:
        if avoid_barrier:
            avoid.update(building_blocked_all)
        else:
            avoid.update(building_blocked_no_barrier)
    else:
        if avoid_barrier:
            avoid.update(building_blocked_no_conveyors)
        else:
            avoid.update(building_blocked_no_barrier_no_conveyors)

    if avoid_core:
        avoid.update(my_core_area)

    avoid.update(their_core_area)

    if not avoid_core and my_core is not None:
        avoid.difference_update(my_core_area)

    if avoid_builders:
        for unit in rc.get_nearby_units():
            if rc.get_entity_type(unit) == EntityType.BUILDER_BOT:
                avoid.add(rc.get_position(unit))

    return avoid

def best_sentinel_dir(pos: Position):
    valid = set()
    for dir in CARDINALS:
        new_pos = pos.add(dir)
        if in_bounds(new_pos):
            b = building[new_pos.x][new_pos.y]
            if b and b.team != rc.get_team() and b.type == EntityType.HARVESTER:
                for dir2 in Direction:
                    if dir2 == Direction.CENTRE or dir2 == dir:
                        continue
                    valid.add(dir2)
    mx_harvesters = 0
    mx_base = 0
    mx_conveyors = 0
    mx_other = 0
    best_dir = None

    for dir in valid:
        see = set()
        pew = pos
        for i in range(1, 6):
            pew = pew.add(dir)
            see.update(pew.add(d) for d in Direction)

        harvesters = 0
        base = 0
        conveyors = 0
        other = 0

        for s in see:
            if not in_bounds(s):
                continue
            b = building[s.x][s.y]
            if not (b and b.team != rc.get_team()):
                continue

            type = b.type
            if type == EntityType.HARVESTER:
                harvesters += 1
            elif type == EntityType.CORE:
                base = 1
            elif is_conveyor(type):
                conveyors += 1
            else:
                other += 1

        win = None
        if win is None and base > mx_base:
            win = True
        if win is None and base < mx_base:
            win = False
        if win is None and conveyors > mx_conveyors:
            win = True
        if win is None and conveyors < mx_conveyors:
            win = False
        if win is None and harvesters > mx_harvesters:
            win = True
        if win is None and harvesters < mx_harvesters:
            win = False
        if win is None and other > mx_other:
            win = True

        if win:
            mx_harvesters = harvesters
            mx_base = base
            mx_conveyors = conveyors
            mx_other = other
            best_dir = dir

    return best_dir