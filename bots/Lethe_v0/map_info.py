from __future__ import annotations
from typing import Optional, Set, Tuple
from cambc import Controller, Position, Environment, EntityType, Team, Direction, ResourceType, GameError
from dataclasses import dataclass, field
from collections import deque
import time
import units.builder as builder

_HAS_DIRECTION    = frozenset(e for e in (EntityType.ARMOURED_CONVEYOR, EntityType.BREACH, EntityType.CONVEYOR, EntityType.GUNNER, EntityType.SENTINEL, EntityType.SPLITTER))
_CONVEYOR_TYPES = frozenset(
    e for e in (EntityType.CONVEYOR, EntityType.ARMOURED_CONVEYOR,
                      EntityType.BRIDGE, EntityType.SPLITTER)
)
_ACCEPT_ORE = frozenset(
    e for e in (EntityType.CONVEYOR, EntityType.ARMOURED_CONVEYOR,
                      EntityType.BRIDGE, EntityType.SPLITTER, EntityType.BREACH, EntityType.CORE, EntityType.FOUNDRY, EntityType.GUNNER, EntityType.SENTINEL)
)
_TURRET_TYPES = frozenset(
    e for e in (EntityType.LAUNCHER, EntityType.GUNNER,
                      EntityType.SENTINEL, EntityType.BREACH)
)
_ET_ROAD              = EntityType.ROAD
_ET_MARKER            = EntityType.MARKER
_ET_BARRIER           = EntityType.BARRIER
_ET_CONVEYOR          = EntityType.CONVEYOR
_ET_ARMOURED_CONVEYOR = EntityType.ARMOURED_CONVEYOR
_ET_BRIDGE            = EntityType.BRIDGE
_ET_SPLITTER          = EntityType.SPLITTER
_ET_CORE              = EntityType.CORE
_ET_BUILDER_BOT       = EntityType.BUILDER_BOT
_ET_HARVESTER         = EntityType.HARVESTER
_ET_FOUNDRY           = EntityType.FOUNDRY
_ET_LAUNCHER          = EntityType.LAUNCHER
_ET_GUNNER            = EntityType.GUNNER
_ET_SENTINEL          = EntityType.SENTINEL
_ET_BREACH            = EntityType.BREACH
_RT_AXIONITE          = ResourceType.RAW_AXIONITE
_RT_TITANIUM          = ResourceType.TITANIUM
_ENV_EMPTY   = Environment.EMPTY
_ENV_ORE_AX  = Environment.ORE_AXIONITE
_ENV_ORE_TI  = Environment.ORE_TITANIUM
_ET_INT = {t: i for i, t in enumerate(EntityType)}
_INT_ET = {i: t for i, t in enumerate(EntityType)}
_RT_INT = {t: i for i, t in enumerate(ResourceType)}
_INT_RT = {i: t for i, t in enumerate(ResourceType)}
_ENV_INT = {t: i for i, t in enumerate(Environment)}
_INT_ENV = {i: t for i, t in enumerate(Environment)}
_DIR_INT = {t: i for i, t in enumerate(Direction)}
_INT_DIR = {i: t for i, t in enumerate(Direction)}
_TM_INT = {t: i for i, t in enumerate(Team)}
_INT_TM = {i: t for i, t in enumerate(Team)}
_DIR_CENTRE = Direction.CENTRE
_ALL_DIRECTIONS = tuple(Direction)
_CARDINAL = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]
_DIRECTION_DELTAS = {d: d.delta() for d in Direction}
_rc: Controller
_width = _height = 0
_MAP_CENTER = None
_ground: list[int] = []
_seen: list[bool] = []
_building_id: list[int] = []
_building_type: list[int] = []
_building_hp: list[int] = []
_building_team: list[int] = []
_building_dir: list[int] = []
_building_conv_target: list[int] = []
_building_load: list[float] = []
_building_ore: list[int] = []
_building_my: list[int] = []
_building_my_key = 1

_my_core: Position | None = None
_their_core: Position | None = None
_predicted_enemy_core: Position | None = None
_core_id: int | None = None
_hor_sym = True
_ver_sym = True
_rot_sym = True
_solved_sym = False
_rush_tiebroken = 0


_blocked: set[Position] = set() #walls, all buildings but my barriers and all roads/conveyors
_conveyors: set[Position] = set()
_conveyors_targets: set[Position] = set()
_my_barriers: set[Position] = set()
_ores: set[Position] = set()
_enemy_launch_adj: set[Position] = set()
_enemy_launch: set[Position] = set()

_my_core_area: set[Position] = set()
_their_core_area: set[Position] = set()

my_conveyors: set[tuple[Position, Position]] = set()
def ground_at(x, y):
    return _INT_ENV[_ground[x+y*_width]]
def seen_at(x, y):
    return _seen[x+y*_width]
def id_at(x, y):
    return _building_id[x+y*_width]
def type_at(x, y):
    return _INT_ET[_building_type[x+y*_width]]
def hp_at(x, y):
    return _building_hp[x+y*_width]
def team_at(x, y):
    return _INT_TM[_building_team[x+y*_width]]
def dir_at(x, y):
    return _INT_DIR[_building_dir[x+y*_width]]
def conv_target_at(x, y):
    return Position(_building_conv_target[x+y*_width]%_width, _building_conv_target[x+y*_width]//_width)
def load_at(x, y):
    return _building_load[x+y*_width]
def trans_ore_at(x, y):
    return _INT_ENV[_building_ore[x+y*_width]]
def is_conveyor(type):
    return type in _CONVEYOR_TYPES
def is_turret(type):
    return type in _TURRET_TYPES
def in_bounds(pos: Position) -> bool:
    return 0 <= pos.x < _width and 0 <= pos.y < _height
def can_route(x, y):
    return _building_my[x+y*_width] == _building_my_key


def note_destroy(pos: Position) -> None:
    if not in_bounds(pos):
        return

    n = pos.x + pos.y * _width
    building_id = _building_id[n]
    if building_id == 0:
        return

    building_type = _INT_ET[_building_type[n]]

    _blocked.discard(pos)
    _my_barriers.discard(pos)
    _enemy_launch.discard(pos)

    if building_type in _CONVEYOR_TYPES:
        _conveyors.discard(pos)
        target_idx = _building_conv_target[n]
        if target_idx:
            _conveyors_targets.discard(Position(target_idx % _width, target_idx // _width))
    else:
        _conveyors.discard(pos)

    if building_type in _CONVEYOR_TYPES or building_type is _ET_HARVESTER:
        stale = [entry for entry in my_conveyors if entry[0] == pos or entry[1] == pos]
        for entry in stale:
            my_conveyors.discard(entry)

    _building_id[n] = 0
    _building_hp[n] = 0
    _building_type[n] = 0
    _building_team[n] = 0
    _building_dir[n] = 0
    _building_conv_target[n] = 0
    _building_load[n] = 0
    _building_ore[n] = 0
    _building_my[n] = 0

def init(c: Controller):
    global _rc, _width, _height
    global _ground, _seen, _building_id, _building_type, _building_hp, _building_team, _building_dir, _building_conv_target, _building_load, _building_ore, _building_my
    global _MAP_CENTER
    _rc = c
    _width = _rc.get_map_width()
    _height = _rc.get_map_height()
    _MAP_CENTER = Position(_width // 2, _height // 2)
    tiles = _width * _height
    _ground               = [0] * tiles
    _seen                 = [False] * tiles
    _building_id          = [0] * tiles
    _building_hp          = [0] * tiles
    _building_type        = [0] * tiles
    _building_team        = [0] * tiles
    _building_dir         = [0] * tiles
    _building_conv_target = [0] * tiles
    _building_load        = [0] * tiles
    _building_ore         = [0] * tiles
    _building_my          = [0] * tiles
def hor_flip(pos: Position):
    return Position(_width - 1 - pos.x, pos.y)
def ver_flip(pos: Position):
    return Position(pos.x, _height - 1 - pos.y)
def rot_flip(pos: Position):
    return Position(_width - 1 - pos.x, _height - 1 - pos.y)
def update_symmetry(tile: Position):
    global _hor_sym, _ver_sym, _rot_sym
    tx = tile.x
    ty = tile.y
    env = _ground[tx+ty*_width]
    rx = _width-1 - tx
    ry = _height-1 - ty
    if _hor_sym:
        if _seen[rx+ty*_width] and _ground[rx+ty*_width] != env:
            _hor_sym = False
    if _ver_sym:
        if _seen[tx+ry*_width] and _ground[tx+ry*_width] != env:
            _ver_sym = False
    if _rot_sym:
        if _seen[rx+ry*_width] and _ground[rx+ry*_width] != env:
            _rot_sym = False
def flip(pos: Position):
    if not _solved_sym:
        return None
    if _hor_sym:
        return hor_flip(pos)
    if _ver_sym:
        return ver_flip(pos)
    if _rot_sym:
        return rot_flip(pos)
    return None
def core_center(core_id: int, tile: Position) -> Position | None:
    def empty(pos: Position) -> bool:
        return not in_bounds(pos) or (_rc.is_in_vision(pos) and _rc.get_tile_building_id(pos) != core_id)
    up    = empty(Position(tile.x,     tile.y - 1))
    down  = empty(Position(tile.x,     tile.y + 1))
    left  = empty(Position(tile.x - 1, tile.y))
    right = empty(Position(tile.x + 1, tile.y))
    if up and left:   return Position(tile.x + 1, tile.y + 1)
    if up and right:  return Position(tile.x - 1, tile.y + 1)
    if down and left: return Position(tile.x + 1, tile.y - 1)
    if down and right:return Position(tile.x - 1, tile.y - 1)
    return None

def build_core_areas() -> None:
    global _my_core_area, _their_core_area
    _my_core_area = set()
    _their_core_area = set()
    if _my_core is not None:
        n = _my_core.x+_my_core.y*_width
        for x in range(_my_core.x - 1, _my_core.x + 2):
            for y in range(_my_core.y - 1, _my_core.y + 2):
                m = x+y*_width
                _building_id[m] = _building_id[n]
                _building_type[m] = _building_type[n]
                _building_hp[m] = _building_hp[n]
                _building_team[m] = _building_team[n]
                _my_core_area.add(Position(x, y))
                _conveyors.add(Position(x, y)) #so i dont path through core somehow
    if _their_core is not None:
        n = _their_core.x+_their_core.y*_width
        for x in range(_their_core.x - 1, _their_core.x + 2):
            for y in range(_their_core.y - 1, _their_core.y + 2):
                    m = x+y*_width
                    _building_id[m] = _building_id[n]
                    _building_type[m] = _building_type[n]
                    _building_hp[m] = _building_hp[n]
                    _building_team[m] = _building_team[n]
                    _their_core_area.add(Position(x, y))
                    _blocked.add(Position(x, y))
def update(update_conv = True) -> None:
    # from units.builder import log
    global _my_core, _their_core, _core_id, _solved_sym
    global _hor_sym, _ver_sym, _rot_sym
    global _rush_tiebroken, _predicted_enemy_core
    rc = _rc
    ground = _ground
    seen = _seen
    building_id = _building_id
    building_type = _building_type
    building_hp = _building_hp
    building_team = _building_team
    building_dir = _building_dir
    building_conv_target = _building_conv_target
    building_load = _building_load
    building_ore = _building_ore
    
    blocked = _blocked
    conveyors = _conveyors
    conveyor_targets = _conveyors_targets
    my_barriers = _my_barriers
    ores = _ores
    enemy_launch_adj = _enemy_launch_adj
    enemy_launch = _enemy_launch
    
    
    width = _width
    height = _height
    
    visible_tiles = rc.get_nearby_tiles()
    my_team       = rc.get_team()
    my_pos        = rc.get_position()
    rc_get_tile_building_id   = rc.get_tile_building_id
    rc_get_entity_type        = rc.get_entity_type
    rc_get_team               = rc.get_team
    rc_get_hp                 = rc.get_hp
    rc_get_direction          = rc.get_direction
    rc_get_bridge_target      = rc.get_bridge_target
    rc_get_tile_env           = rc.get_tile_env

    for tile in visible_tiles:
        x = tile.x
        y = tile.y
        n = x+y*width
        if not seen[n]:
            env = rc_get_tile_env(tile)
            ground[n] = _ENV_INT[env]
            seen[n] = True
            if env is Environment.WALL:
                blocked.add(tile)
            elif env is not Environment.EMPTY:
                ores.add(tile)
            rx = width-1-x
            ry = height-1-y
            if _hor_sym:
                if seen[rx+y*width] and ground[rx+y*width] != _ENV_INT[env]:
                    _hor_sym = False
            if _ver_sym:
                if seen[x+ry*width] and ground[x+ry*width] != _ENV_INT[env]:
                    _ver_sym = False
            if _rot_sym:
                if seen[rx+ry*width] and ground[rx+ry*width] != _ENV_INT[env]:
                    _rot_sym = False
            if _solved_sym:
                if _hor_sym:
                    fx, fy = width-1 - x, y
                elif _ver_sym:
                    fx, fy = x, height-1 - y
                else:
                    fx, fy = width-1 - x, height-1 - y
                ground[fx+fy*width]      = _ENV_INT[env]
                seen[fx+fy*width] = True
                if env is not None and env is not Environment.EMPTY:
                    flipped = Position(fx, fy)
                    if env is Environment.WALL:
                        blocked.add(flipped)
                    elif env is not Environment.EMPTY:
                        ores.add(flipped)
        
        
        entity_id = rc_get_tile_building_id(tile)
        if entity_id is None:
            if building_id[n] != 0:
                blocked.discard(tile)
                conveyors.discard(tile)
                my_barriers.discard(tile)
                enemy_launch.discard(tile)
                building_id[n] = 0
            continue
        et = rc_get_entity_type(entity_id)
        if et == EntityType.MARKER:
            building_id[n] = 0
            continue
        if building_id[n] == entity_id:
            building_hp[n] = rc_get_hp(entity_id)
        else:
            direction     = rc_get_direction(entity_id) if et in _HAS_DIRECTION else None
            team = rc_get_team(entity_id)
            target = None
            if et == EntityType.BRIDGE:
                target = rc_get_bridge_target(entity_id)
            elif et in _CONVEYOR_TYPES and direction is not None:
                target = tile.add(direction)
            building_id[n] = entity_id
            building_type[n] = _ET_INT[et]
            building_hp[n] = rc_get_hp(entity_id)
            building_team[n] = _TM_INT[team]
            building_dir[n] = _DIR_INT[direction] if direction else 0
            building_conv_target[n] = (target.x+target.y*width) if target else 0
            if et in _CONVEYOR_TYPES:
                conveyors.add(tile)
            elif et == EntityType.BARRIER and team == my_team:
                my_barriers.add(tile)
            elif et != EntityType.ROAD and et != EntityType.CORE:
                blocked.add(tile)
            if et == EntityType.LAUNCHER and team != my_team:
                enemy_launch.add(tile)
            if et is EntityType.CORE:
                if _my_core is None and team == my_team:
                    _my_core = core_center(entity_id, tile)
                    _core_id = entity_id
                    build_core_areas()
                elif _their_core is None and team != my_team:
                    _their_core = core_center(entity_id, tile)
                    build_core_areas()
    possible_syms = int(_hor_sym) + int(_ver_sym) + int(_rot_sym)
    if possible_syms == 1 and not _solved_sym:
        _solved_sym = True
        if _my_core:
            _their_core = flip(_my_core)
            if _their_core is not None:
                pos = _their_core.x+_their_core.y*width
                building_id[pos] = -1 #0 means junk data, so -1 means theres something here???
                building_type[pos] = _ET_INT[EntityType.CORE]
                building_hp[pos] = 500
                building_team[pos] = 1-_TM_INT[my_team]
            build_core_areas()
        for x in range(width):
            for y in range(height):
                n = x+y*width
                if seen[n]:
                    if _ver_sym:
                        flipped = (x)+(height-1-y)*width
                    elif _hor_sym:
                        flipped = (width-1-x)+(y)*width
                    else:
                        flipped = (width-1-x)+(height-1-y)*width
                    if not seen[flipped]:
                        env = ground[n]
                        ground[flipped] = env
                        seen[flipped] = True
                        ev = _INT_ENV[env]
                        if ev is not Environment.EMPTY:
                            if ev is Environment.WALL:
                                blocked.add(Position(flipped%width, flipped//width))
                            elif ev is not Environment.EMPTY:
                                ores.add(Position(flipped%width, flipped//width))
    if _my_core:
        if _their_core:
            _predicted_enemy_core = _their_core
        else:
            if _rot_sym:
                _predicted_enemy_core = rot_flip(_my_core)
            else:
                hsym_core = hor_flip(_my_core)
                vsym_core = ver_flip(_my_core)
                if _rush_tiebroken == 1 and _ver_sym:
                    _predicted_enemy_core = vsym_core
                elif _rush_tiebroken == 2 and _hor_sym:
                    _predicted_enemy_core = hsym_core
                elif _ver_sym and _hor_sym:
                    if abs(my_pos.x - hsym_core.x) + abs(my_pos.y - hsym_core.y) < abs(my_pos.x - vsym_core.x) + abs(my_pos.y - vsym_core.y):
                        _predicted_enemy_core = hsym_core
                        _rush_tiebroken = 2
                        print("Tiebreaking enemy core sym - HORIZONTAL")
                    else:
                        _predicted_enemy_core = vsym_core
                        _rush_tiebroken = 1
                        print("Tiebreaking enemy core sym - VERTICAL")
                elif _ver_sym:
                    _predicted_enemy_core = vsym_core
                else:
                    _predicted_enemy_core = hsym_core
    enemy_launch_adj.clear()
    for launcher_pos in enemy_launch:
        lx = launcher_pos.x
        ly = launcher_pos.y
        for dx, dy in _DIRECTION_DELTAS.values():
            nx = lx + dx
            ny = ly + dy
            if 0 <= nx < width and 0 <= ny < height:
                enemy_launch_adj.add(Position(nx, ny))
    for c in conveyors:
        conveyor_targets.add(Position(building_conv_target[c.x+c.y*width]%width, building_conv_target[c.x+c.y*width]//width))
    if update_conv:
        compute_conveyor_loads()
def is_tile_empty(pos: Position):
    return in_bounds(pos) and (_rc.is_tile_empty(pos) or (_rc.get_tile_building_id(pos) != None and _rc.get_entity_type(_rc.get_tile_building_id(pos)) is EntityType.MARKER))

def can_place_at_restrictive(pos: Position):
    return is_tile_empty(pos) or in_bounds(pos) and _rc.can_destroy(pos) and (_rc.get_tile_building_id(pos) != None and _rc.get_entity_type(_rc.get_tile_building_id(pos)) is EntityType.ROAD)
def is_passable(pos: Position):
    if not in_bounds(pos): return False
    n = pos.x + pos.y * _width
    if _INT_ENV[_ground[n]] is Environment.WALL: return False
    if _building_id[n] == 0: return True
    t = _INT_ET[_building_type[n]]
    return t in _CONVEYOR_TYPES or t is EntityType.ROAD or t is EntityType.MARKER or (_building_team[n] == _TM_INT[_rc.get_team()] and t is EntityType.BARRIER)
def get_avoid(
    avoid_conveyors: bool,
    avoid_builders: bool,
    avoid_ore: bool,
) -> set[Position]:
    avoid_core = _rc.get_tile_building_id(_rc.get_position()) != _core_id
    avoid = set(_blocked)
    if avoid_conveyors:
        avoid |= _conveyors
        avoid |= _conveyors_targets
    if avoid_ore:
        avoid |= _ores
    if avoid_core:
        avoid |= _my_core_area
    if avoid_builders:
        for unit in _rc.get_nearby_units():
            if _rc.get_entity_type(unit) is EntityType.BUILDER_BOT:
                avoid.add(_rc.get_position(unit))
    return avoid
def best_sentinel_dir(pos: Position, avoid_dir = None):
    # from units.builder_states.builder_rush import log
    # from units.builder import log
    valid = set()
    my_team = _rc.get_team()
    w = _width
    h = _height
    if avoid_dir == None:
        for dir in _CARDINAL:
            new_pos = pos.add(dir)
            nx, ny = new_pos.x, new_pos.y
            n = nx+ny*w
            if 0 <= nx < w and 0 <= ny < h:
                if _building_id[n] == 0:
                    continue
                type = _INT_ET[_building_type[n]]
                if _building_id[n] != 0 and (type is EntityType.HARVESTER or type is EntityType.CONVEYOR and _INT_DIR[_building_dir[n]] == new_pos.direction_to(pos)):
                    print(f"Validated {new_pos.direction_to(pos)}")
                    for dir2 in _ALL_DIRECTIONS:
                        if dir2 is not _DIR_CENTRE and dir2 is not dir:
                            valid.add(dir2)
        if len(valid) == 0: #THIS IS IN CASE OF A BRIDGE, MAY BREAK THINGS IF ASSUMED TO RETURN NONE IF NOTHING NEARBY
            for dir in _ALL_DIRECTIONS:
                valid.add(dir)
    else:
        for dir in _ALL_DIRECTIONS:
            if dir != avoid_dir:
                valid.add(dir)

    mx_harvesters = 0
    mx_base       = 0
    mx_conveyors  = 0
    mx_other      = 0
    best_dir      = None
    for dir in valid:
        harvesters = conveyors = other = 0
        base = 0
        pew = pos
        seen = set()

        for i in range(1, 6):
            pew = pew.add(dir)
            for d in _ALL_DIRECTIONS:
                s = pew.add(d)

                if s in seen:
                    continue
                seen.add(s)

                sx, sy = s.x, s.y
                if not (0 <= sx < w and 0 <= sy < h):
                    continue

                n = sx+sy*_width
                if _building_id[n] == 0:
                    continue
                if _INT_TM[_building_team[n]] == _rc.get_team():
                    continue

                t = _INT_ET[_building_type[n]]
                if t is EntityType.HARVESTER:
                    harvesters += 1
                elif t is EntityType.CORE:
                    base = 1
                elif t in _CONVEYOR_TYPES:
                    conveyors += 1
                else:
                    other += 1
        win = None
        if win is None and base       > mx_base:       win = True
        if win is None and base       < mx_base:       win = False
        if win is None and conveyors  > mx_conveyors:  win = True
        if win is None and conveyors  < mx_conveyors:  win = False
        if win is None and harvesters > mx_harvesters:  win = True
        if win is None and harvesters < mx_harvesters:  win = False
        if win is None and other      > mx_other:      win = True
        if win:
            mx_harvesters = harvesters
            mx_base       = base
            mx_conveyors  = conveyors
            mx_other      = other
            best_dir      = dir
    return best_dir
def handle_turret(pos: Position, avoid_dir = None):
    dir = best_sentinel_dir(pos, avoid_dir)
    if _rc.can_build_sentinel(pos, dir):
        _rc.build_sentinel(pos, dir)
def push_load(pos: int):
    building_conv_target = _building_conv_target
    building_load = _building_load
    building_ore = _building_ore
    building_type = _building_type
    building_id = _building_id
    building_my = _building_my

    type = building_ore[pos]
    for i in range(100):
        building_load[pos] += 1
        building_ore[pos] = max(building_ore[pos], type)
        building_my[pos] = _building_my_key
        next = building_conv_target[pos]
        if building_id[next] == 0 or _INT_ET[building_type[next]] not in _CONVEYOR_TYPES:
            if (building_id[next] == 0 or _INT_ET[building_type[next]] not in _ACCEPT_ORE):
                building_load[pos] = 100
            break
        pos = next
def propogate_load(pos: int, depth: int = 0):
    if depth == 100:
        return 100, 0
    building_conv_target = _building_conv_target
    building_load = _building_load
    building_type = _building_type
    building_ore = _building_ore
    building_id = _building_id
    next = building_conv_target[pos]
    if building_id[next] == 0 or _INT_ET[building_type[next]] not in _CONVEYOR_TYPES:
        if Position(pos%_width, pos//_width) in builder.target_splitters:
            return building_load[pos], _ENV_INT[Environment.ORE_AXIONITE]
        else:
            return building_load[pos], _ENV_INT[Environment.ORE_TITANIUM]
    building_load[pos], building_ore[pos] = propogate_load(next, depth + 1)
    return (building_load[pos], building_ore[pos])

    
def compute_conveyor_loads():
    global _building_my_key
    _building_my_key += 1
    building_load = _building_load
    building_ore = _building_ore

    width = _width
    for i in _conveyors:
        building_load[i.x+i.y*width] = 0
        building_ore[i.x+i.y*width] = 0
    for i in my_conveyors:
        c = i[0]
        h = i[1]
        # building_ore[c.x+c.y*width] = _ground[h.x+h.y*width]
    for i in my_conveyors:
        push_load(i[0].x+i[0].y*width)
    for i in my_conveyors:
        propogate_load(i[0].x+i[0].y*width)
    
