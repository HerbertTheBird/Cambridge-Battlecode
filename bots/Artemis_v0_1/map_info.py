from __future__ import annotations
from typing import Optional, Set, Tuple

from cambc import Controller, Position, Environment, EntityType, Team, Direction, ResourceType, GameError
from dataclasses import dataclass


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

# --- FIX 1: pre-compute integer-value frozensets for enum membership tests.
# entity_type in has_direction triggers enum.__hash__ which is slow (Python-level).
# entity_type.value is a plain int; int hashing is a single C operation.
_has_direction_vals    = frozenset(e.value for e in has_direction)
_has_vision_vals       = frozenset(e.value for e in has_vision)
_has_bridge_target_vals = frozenset(e.value for e in has_bridge_target)
_has_stored_resource_vals = frozenset(e.value for e in has_stored_resource)
_CONVEYOR_TYPE_VALS = frozenset(
    e.value for e in (EntityType.CONVEYOR, EntityType.ARMOURED_CONVEYOR,
                      EntityType.BRIDGE, EntityType.SPLITTER)
)

# --- FIX 2: cache singleton enum members for fast `is` identity comparisons.
# `is` is a pointer comparison (single C instruction); `==` on enums can be slower.
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
_ET_GUNNER            = EntityType.GUNNER
_ET_SENTINEL          = EntityType.SENTINEL
_ET_BREACH            = EntityType.BREACH

_ENV_EMPTY   = Environment.EMPTY
_ENV_ORE_AX  = Environment.ORE_AXIONITE
_ENV_ORE_TI  = Environment.ORE_TITANIUM

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


# --- FIX 3: slots=True eliminates the per-instance __dict__,
# reducing memory and speeding up attribute access and object construction.
# is_conveyor_type is a cached flag so callers never recompute the type check.
@dataclass(slots=True)
class Building:
    id: int
    type: EntityType
    hp: int
    maxhp: int
    team: Team
    is_conveyor_type: bool          # pre-computed; used by _update_building_blocked_at
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
    # Kept for any external callers; update() uses the inlined version.
    global hor_sym, ver_sym, rot_sym

    tx = tile.x
    ty = tile.y
    env = ground[tx][ty]
    w1 = width  - 1
    h1 = height - 1

    if hor_sym:
        hx = w1 - tx
        if ground_seen[hx][ty] and ground[hx][ty] != env:
            hor_sym = False

    if ver_sym:
        vy = h1 - ty
        if ground_seen[tx][vy] and ground[tx][vy] != env:
            ver_sym = False

    if rot_sym:
        rx = w1 - tx
        ry = h1 - ty
        if ground_seen[rx][ry] and ground[rx][ry] != env:
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

    up    = empty(Position(tile.x,     tile.y - 1))
    down  = empty(Position(tile.x,     tile.y + 1))
    left  = empty(Position(tile.x - 1, tile.y))
    right = empty(Position(tile.x + 1, tile.y))

    if up and left:   return Position(tile.x + 1, tile.y + 1)
    if up and right:  return Position(tile.x - 1, tile.y + 1)
    if down and left: return Position(tile.x + 1, tile.y - 1)
    if down and right:return Position(tile.x - 1, tile.y - 1)
    return None


def is_conveyor(type: EntityType):
    return type is _ET_CONVEYOR or type is _ET_ARMOURED_CONVEYOR or type is _ET_BRIDGE or type is _ET_SPLITTER


def is_turret(type: EntityType):
    return type is _ET_GUNNER or type is _ET_SENTINEL or type is _ET_BREACH


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


# --- FIX 4: accept Position directly instead of (x, y) ints.
# The caller already owns a Position; creating Position(x, y) inside was pure waste.
def _update_ground_blocked_at(pos: Position) -> None:
    x, y = pos.x, pos.y
    env = ground[x][y]

    ground_blocked_all.discard(pos)
    ground_blocked_no_ore.discard(pos)

    if env is None:
        return
    if env is _ENV_EMPTY or env is _ENV_ORE_AX:
        return

    ground_blocked_all.add(pos)
    if env is not _ENV_ORE_TI:
        ground_blocked_no_ore.add(pos)


def _update_building_blocked_at(pos: Position) -> None:
    x, y = pos.x, pos.y
    b = building[x][y]

    building_blocked_all.discard(pos)
    building_blocked_no_barrier.discard(pos)
    building_blocked_no_conveyors.discard(pos)
    building_blocked_no_barrier_no_conveyors.discard(pos)

    if b is None:
        return

    t = b.type
    if t is _ET_ROAD or t is _ET_MARKER:
        return

    is_barrier = t is _ET_BARRIER
    is_conv    = b.is_conveyor_type  # use cached field; no enum work at all

    building_blocked_all.add(pos)
    if not is_barrier:
        building_blocked_no_barrier.add(pos)
    if not is_conv:
        building_blocked_no_conveyors.add(pos)
    if not is_barrier and not is_conv:
        building_blocked_no_barrier_no_conveyors.add(pos)


def update() -> None:
    global my_core, their_core, core_id, solved_sym
    global hor_sym, ver_sym, rot_sym

    current_round = rc.get_current_round()
    visible_tiles = rc.get_nearby_tiles()
    my_team       = rc.get_team()

    # Pull frequently-used globals into locals (one LOAD_GLOBAL → LOAD_FAST per access).
    ground_local      = ground
    ground_seen_local = ground_seen
    building_local    = building
    stuck_turns_local = stuck_turns
    past_filled_local = past_filled
    last_seen_local   = last_seen

    solved_sym_local = solved_sym
    hor_sym_local    = hor_sym
    ver_sym_local    = ver_sym
    rot_sym_local    = rot_sym

    width_m1  = width  - 1
    height_m1 = height - 1

    # Cache method references so each call is a LOAD_FAST + CALL, not LOAD_FAST + LOAD_ATTR + CALL.
    rc_get_tile_building_id = rc.get_tile_building_id
    rc_get_entity_type      = rc.get_entity_type
    rc_get_team             = rc.get_team
    rc_get_hp               = rc.get_hp
    rc_get_max_hp           = rc.get_max_hp
    rc_get_direction        = rc.get_direction
    rc_get_vision_radius_sq = rc.get_vision_radius_sq
    rc_get_bridge_target    = rc.get_bridge_target
    rc_get_stored_resource_id = rc.get_stored_resource_id
    rc_get_stored_resource  = rc.get_stored_resource
    rc_draw_indicator_dot   = rc.draw_indicator_dot
    rc_get_tile_env         = rc.get_tile_env

    for tile in visible_tiles:
        x = tile.x
        y = tile.y

        if not ground_seen_local[x][y]:
            env = rc_get_tile_env(tile)
            ground_local[x][y]      = env
            ground_seen_local[x][y] = True
            _update_ground_blocked_at(tile)  # pass existing Position, no new allocation

            # --- FIX 5: inline the flip + symmetry-update logic.
            # update_symmetry() previously created 3 Position objects on every call
            # (one per flip function). Inlining lets us work with raw ints instead.
            if hor_sym_local:
                hx = width_m1 - x
                if ground_seen_local[hx][y] and ground_local[hx][y] != env:
                    hor_sym_local = False
                    hor_sym = False

            if ver_sym_local:
                vy = height_m1 - y
                if ground_seen_local[x][vy] and ground_local[x][vy] != env:
                    ver_sym_local = False
                    ver_sym = False

            if rot_sym_local:
                rx = width_m1 - x
                ry = height_m1 - y
                if ground_seen_local[rx][ry] and ground_local[rx][ry] != env:
                    rot_sym_local = False
                    rot_sym = False

            if solved_sym_local:
                # Inline flip() to avoid branching through the function each time.
                if hor_sym_local:
                    flipped = Position(width_m1 - x, y)
                elif ver_sym_local:
                    flipped = Position(x, height_m1 - y)
                else:
                    flipped = Position(width_m1 - x, height_m1 - y)
                fx = flipped.x
                fy = flipped.y
                ground_local[fx][fy]      = env
                ground_seen_local[fx][fy] = True
                _update_ground_blocked_at(flipped)

        entity_id = rc_get_tile_building_id(tile)
        if entity_id is None:
            if building_local[x][y] is not None:
                building_local[x][y] = None
                _update_building_blocked_at(tile)
            last_seen_local[x][y] = current_round
            continue

        prev_building = building_local[x][y]
        seen_last_turn = last_seen_local[x][y] == current_round - 1

        entity_type = rc_get_entity_type(entity_id)
        etv = entity_type.value  # cache: used for multiple int-set lookups below

        # --- FIX 1 in action: int lookup instead of enum hash.
        is_conv  = etv in _CONVEYOR_TYPE_VALS
        # --- FIX 3 in action: read cached field instead of recomputing.
        prev_is_conv = prev_building is not None and prev_building.is_conveyor_type

        has_sr = etv in _has_stored_resource_vals

        stored_resource_id = rc_get_stored_resource_id(entity_id) if has_sr else None
        stored_resource = rc_get_stored_resource(entity_id) if (is_conv and has_sr) else None

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

        direction    = rc_get_direction(entity_id)        if etv in _has_direction_vals     else None
        vision_sq    = rc_get_vision_radius_sq(entity_id) if etv in _has_vision_vals        else None
        bridge_target= rc_get_bridge_target(entity_id)   if etv in _has_bridge_target_vals else None

        team = rc_get_team(entity_id)
        new_building = Building(
            id=entity_id,
            type=entity_type,
            hp=rc_get_hp(entity_id),
            maxhp=rc_get_max_hp(entity_id),
            team=team,
            is_conveyor_type=is_conv,
            direction=direction,
            vision_sq=vision_sq,
            bridge_target=bridge_target,
            stored_resource_id=stored_resource_id,
            conveyor_speed=speed,
            load=load,
        )
        building_local[x][y] = new_building

        # --- FIX 6: skip the 4-discard / 4-add cycle when the building type is
        # unchanged.  Blocked-set membership depends only on type, so persistent
        # buildings (the vast majority) need no update at all.
        if prev_building is None or prev_building.type is not entity_type:
            _update_building_blocked_at(tile)

        if load is not None:
            rc_draw_indicator_dot(tile, 0, 0, 50 * load)

        if my_core is None and entity_type is _ET_CORE:
            if team == my_team:
                my_core = core_center(entity_id, tile)
                core_id = entity_id
                _rebuild_core_areas()
            else:
                their_core = core_center(entity_id, tile)
                _rebuild_core_areas()
                last_seen_local[x][y] = current_round

    possible_syms = int(hor_sym_local) + int(ver_sym_local) + int(rot_sym_local)

    if possible_syms == 1 and not solved_sym_local:
        solved_sym = True
        solved_sym_local = True
        if my_core:
            their_core = flip(my_core)
            core = Building(
                id=-1,
                type=_ET_CORE,
                hp=500,
                maxhp=500,
                team=rc.get_team() + 1,
                is_conveyor_type=False,
                direction=None,
                vision_sq=36,
            )
            for x in range(their_core.x - 1, their_core.x + 2):
                for y in range(their_core.y - 1, their_core.y + 2):
                    building[x][y] = core

        if hor_sym_local:
            flip_func = hor_flip
        elif ver_sym_local:
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
                        ground_local[fx][fy]      = ground_local[x][y]
                        ground_seen_local[fx][fy] = True
                        _update_ground_blocked_at(flipped)


def is_tile_empty(pos: Position):
    return in_bounds(pos) and (rc.is_tile_empty(pos) or (rc.get_tile_building_id(pos) != None and rc.get_entity_type(rc.get_tile_building_id(pos)) is _ET_MARKER))


def get_avoid(
    avoid_conveyors: bool,
    avoid_builders: bool,
    avoid_barrier: bool = True,
    avoid_ore: bool = True,
) -> set[Position]:
    avoid_core = rc.get_tile_building_id(rc.get_position()) != core_id

    if avoid_ore:
        ground_set = ground_blocked_all
    else:
        ground_set = ground_blocked_no_ore

    if avoid_conveyors:
        building_set = building_blocked_all if avoid_barrier else building_blocked_no_barrier
    else:
        building_set = building_blocked_no_conveyors if avoid_barrier else building_blocked_no_barrier_no_conveyors

    # Use | to create the union in one C-level operation.
    avoid = ground_set | building_set

    if avoid_core:
        avoid |= my_core_area

    avoid |= their_core_area

    if not avoid_core and my_core is not None:
        avoid -= my_core_area

    if avoid_builders:
        rc_get_entity_type = rc.get_entity_type
        rc_get_position    = rc.get_position
        for unit in rc.get_nearby_units():
            if rc_get_entity_type(unit) is _ET_BUILDER_BOT:
                avoid.add(rc_get_position(unit))

    return avoid


def best_sentinel_dir(pos: Position):
    valid = set()
    for dir in CARDINALS:
        new_pos = pos.add(dir)
        if in_bounds(new_pos):
            b = building[new_pos.x][new_pos.y]
            if b and b.team != rc.get_team() and b.type is _ET_HARVESTER:
                for dir2 in Direction:
                    if dir2 == Direction.CENTRE or dir2 == dir:
                        continue
                    valid.add(dir2)

    mx_harvesters = 0
    mx_base       = 0
    mx_conveyors  = 0
    mx_other      = 0
    best_dir      = None

    my_team = rc.get_team()

    for dir in valid:
        see = set()
        pew = pos
        for i in range(1, 6):
            pew = pew.add(dir)
            see.update(pew.add(d) for d in Direction)

        harvesters = conveyors = other = 0
        base = 0

        for s in see:
            if not in_bounds(s):
                continue
            b = building[s.x][s.y]
            if not (b and b.team != my_team):
                continue

            t = b.type
            if t is _ET_HARVESTER:
                harvesters += 1
            elif t is _ET_CORE:
                base = 1
            elif b.is_conveyor_type:
                conveyors += 1
            else:
                other += 1

        win = None
        if win is None and base       > mx_base:       win = True
        if win is None and base       < mx_base:       win = False
        if win is None and conveyors  > mx_conveyors:  win = True
        if win is None and conveyors  < mx_conveyors:  win = False
        if win is None and harvesters > mx_harvesters: win = True
        if win is None and harvesters < mx_harvesters: win = False
        if win is None and other      > mx_other:      win = True

        if win:
            mx_harvesters = harvesters
            mx_base       = base
            mx_conveyors  = conveyors
            mx_other      = other
            best_dir      = dir

    return best_dir