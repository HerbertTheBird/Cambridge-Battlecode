from __future__ import annotations
from typing import Optional, Set, Tuple
from cambc import Controller, Position, Environment, EntityType, Team, Direction, ResourceType, GameError
from dataclasses import dataclass, field
from collections import deque
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
# --- FIX 1: pre-compute integer-value frozensets for enum membership tests.
_has_direction_vals    = frozenset(e.value for e in has_direction)
_has_vision_vals       = frozenset(e.value for e in has_vision)
_has_bridge_target_vals = frozenset(e.value for e in has_bridge_target)
_has_stored_resource_vals = frozenset(e.value for e in has_stored_resource)
_CONVEYOR_TYPE_VALS = frozenset(
    e.value for e in (EntityType.CONVEYOR, EntityType.ARMOURED_CONVEYOR,
                      EntityType.BRIDGE, EntityType.SPLITTER)
)
# --- FIX 2: cache singleton enum members for fast \`is\` identity comparisons.
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
_ET_LAUNCHER          = EntityType.LAUNCHER
_ET_GUNNER            = EntityType.GUNNER
_ET_SENTINEL          = EntityType.SENTINEL
_ET_BREACH            = EntityType.BREACH
_RT_AXIONITE          = ResourceType.RAW_AXIONITE
_RT_TITANIUM          = ResourceType.TITANIUM
_ENV_EMPTY   = Environment.EMPTY
_ENV_ORE_AX  = Environment.ORE_AXIONITE
_ENV_ORE_TI  = Environment.ORE_TITANIUM
# --- FIX 7: pre-cache Direction members to avoid repeated enum iteration.
_DIR_CENTRE = Direction.CENTRE
_ALL_DIRECTIONS = tuple(Direction)
_CARDINAL_OFFSETS = {
    Direction.NORTH: (0, -1),
    Direction.SOUTH: (0, 1),
    Direction.WEST:  (-1, 0),
    Direction.EAST:  (1, 0),
}
_DIRECTION_DELTAS = {d: d.delta() for d in Direction}
rc = None
width = height = 0
MAP_CENTER = None
ground: list[list[Environment | None]] = []
ground_seen: list[list[bool]] = []
building: list[list["Building | None"]] = []
stuck_turns: list[list[int]] = []
past_filled: list[list[int]] = []
last_seen: list[list[int]] = []
my_core: Position | None = None
their_core: Position | None = None
predicted_enemy_core: Position | None = None
core_id: int | None = None
hor_sym = True
ver_sym = True
rot_sym = True
solved_sym = False
rush_tiebroken = 0
ground_blocked_all: set[Position] = set()
ground_blocked_no_ore: set[Position] = set()
building_blocked_all: set[Position] = set()
building_blocked_no_barrier: set[Position] = set()
building_blocked_no_conveyors: set[Position] = set()
building_blocked_no_barrier_no_conveyors: set[Position] = set()
my_core_area: set[Position] = set()
their_core_area: set[Position] = set()
_load_next_idx: list[int] = []
_load_indegree: list[int] = []
_load_accum: list[float] = []
_load_final: list[float] = []
_load_touched: list[bool] = []
_load_harvester_fed: list[bool] = []
_load_cycle_seen: list[bool] = []
_load_snapshot_conveyor: list[bool] = []
_load_snapshot_next_raw: list[int] = []
_load_snapshot_seed: list[float | None] = []
_load_terminal_confirm: list[bool] = []
_load_confirmed: list[bool] = []
_load_ore_code: list[int] = []
_load_queue: list[int] = []
_load_order: list[int] = []
_load_recompute_state = None
_CYCLE_DEFAULT_LOAD = 4.0
# --- FIX 3: slots=True eliminates per-instance __dict__.
@dataclass(slots=True)
class Building:
    id: int
    type: EntityType
    hp: int
    maxhp: int
    team: Team
    is_conveyor_type: bool
    direction: Direction | None = None
    vision_sq: int | None = None
    bridge_target: Position | None = None
    conveyor_speed: int | None = None
    stored_resource_id: int | None = None
    load: float | None = None
    load_confirmed: bool = True
    transporting_ore: Environment | None = None
def in_bounds(pos: Position) -> bool:
    return 0 <= pos.x < width and 0 <= pos.y < height
def init(c: Controller):
    global rc, width, height
    global ground, ground_seen, building, stuck_turns, past_filled, last_seen
    global ground_blocked_all, ground_blocked_no_ore
    global building_blocked_all, building_blocked_no_barrier
    global building_blocked_no_conveyors, building_blocked_no_barrier_no_conveyors
    global my_core_area, their_core_area
    global _load_next_idx, _load_indegree, _load_accum, _load_final
    global _load_touched, _load_harvester_fed, _load_cycle_seen
    global _load_snapshot_conveyor, _load_snapshot_next_raw, _load_snapshot_seed
    global _load_terminal_confirm, _load_confirmed, _load_ore_code
    global _load_queue, _load_order
    global _load_recompute_state
    global MAP_CENTER
    rc = c
    width = rc.get_map_width()
    height = rc.get_map_height()
    MAP_CENTER = Position(width // 2, height // 2)
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
    grid_size = width * height
    _load_next_idx = [-1] * grid_size
    _load_indegree = [0] * grid_size
    _load_accum = [0.0] * grid_size
    _load_final = [0.0] * grid_size
    _load_touched = [False] * grid_size
    _load_harvester_fed = [False] * grid_size
    _load_cycle_seen = [False] * grid_size
    _load_snapshot_conveyor = [False] * grid_size
    _load_snapshot_next_raw = [-1] * grid_size
    _load_snapshot_seed = [None] * grid_size
    _load_terminal_confirm = [False] * grid_size
    _load_confirmed = [False] * grid_size
    _load_ore_code = [0] * grid_size
    _load_queue = []
    _load_order = []
    _load_recompute_state = None
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
    return type is _ET_LAUNCHER or type is _ET_GUNNER or type is _ET_SENTINEL or type is _ET_BREACH
def leads_to_friendly_turret(start_building_id, max_depth=20):
    """
    Returns True if following the outputs of this enemy bridge/conveyor
    eventually reaches a friendly turret.
    max_depth prevents infinite loops in cycles.
    """
    visited = set()
    queue = deque()
    queue.append((start_building_id, 0))

    while queue:
        b_id, depth = queue.popleft()
        if depth > max_depth:
            continue
        if b_id in visited:
            continue
        visited.add(b_id)

        try:
            b_type = rc.get_entity_type(b_id)
        except GameError:
            continue

        # Stop if we reach a friendly turret
        if b_type == EntityType.GUNNER and rc.get_team(b_id) == rc.get_team():
            return True
        if b_type == EntityType.SENTINEL and rc.get_team(b_id) == rc.get_team():
            return True

        # Follow outputs
        neighbors = []

        # For bridges, enqueue the target tile's building
        if b_type == EntityType.BRIDGE:
            try:
                target_pos = rc.get_bridge_target(b_id)
                target_building_id = rc.get_tile_building_id(target_pos)
                if target_building_id is not None:
                    neighbors.append(target_building_id)
            except GameError:
                pass

        # For conveyors/splitters, follow adjacent tiles in their direction
        elif b_type in (EntityType.CONVEYOR, EntityType.ARMOURED_CONVEYOR, EntityType.SPLITTER):
            try:
                dir = rc.get_direction(b_id)
                # Output tile in direction
                out_pos = rc.get_position(b_id).add(dir)
                out_id = rc.get_tile_building_id(out_pos)
                if out_id is not None:
                    neighbors.append(out_id)
            except GameError:
                pass

        for n_id in neighbors:
            if n_id not in visited:
                queue.append((n_id, depth + 1))

    return False
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
# Kept for external callers; update() uses inlined versions.
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
# Kept for external callers; update() uses inlined versions.
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
    is_conv    = b.is_conveyor_type
    building_blocked_all.add(pos)
    if not is_barrier:
        building_blocked_no_barrier.add(pos)
    if not is_conv:
        building_blocked_no_conveyors.add(pos)
    if not is_barrier and not is_conv:
        building_blocked_no_barrier_no_conveyors.add(pos)
def mark_known_conveyors() -> None:
    rc_draw_indicator_dot = rc.draw_indicator_dot
    building_local = building

    for x in range(width):
        col = building_local[x]
        for y in range(height):
            b = col[y]
            if b is None or not b.is_conveyor_type:
                continue

            load = b.load
            if load is None:
                r, g, bl = 0, 0, 0          # black
            elif load > 4:
                r, g, bl = 255, 0, 0        # red
            elif load < 0.999:
                r, g, bl = 0, 0, 255        # blue
            else:
                r, g, bl = 0, min(255, int(load * 60)), 0  # green: 80, 160, 240 for 1,2,3

            # rc_draw_indicator_dot(Position(x, y), r, g, bl)
def update() -> None:
    from units.builder import log
    mark_known_conveyors()
    global my_core, their_core, core_id, solved_sym
    global hor_sym, ver_sym, rot_sym
    global rush_tiebroken, predicted_enemy_core
    current_round = rc.get_current_round()
    visible_tiles = rc.get_nearby_tiles()
    my_team       = rc.get_team()
    my_pos        = rc.get_position()
    # Pull frequently-used globals into locals (LOAD_FAST vs LOAD_GLOBAL).
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
    # --- FIX 8: cache method references (LOAD_FAST + CALL vs LOAD_FAST + LOAD_ATTR + CALL).
    rc_get_tile_building_id   = rc.get_tile_building_id
    rc_get_entity_type        = rc.get_entity_type
    rc_get_team               = rc.get_team
    rc_get_hp                 = rc.get_hp
    rc_get_max_hp             = rc.get_max_hp
    rc_get_direction          = rc.get_direction
    rc_get_vision_radius_sq   = rc.get_vision_radius_sq
    rc_get_bridge_target      = rc.get_bridge_target
    rc_get_stored_resource_id = rc.get_stored_resource_id
    rc_get_stored_resource    = rc.get_stored_resource
    rc_draw_indicator_dot     = rc.draw_indicator_dot
    rc_get_tile_env           = rc.get_tile_env
    # --- FIX 9: cache set method references for inlined blocked-set updates.
    gb_all_add       = ground_blocked_all.add
    gb_all_discard   = ground_blocked_all.discard
    gb_nore_add      = ground_blocked_no_ore.add
    gb_nore_discard  = ground_blocked_no_ore.discard
    bb_all_add       = building_blocked_all.add
    bb_all_discard   = building_blocked_all.discard
    bb_nbar_add      = building_blocked_no_barrier.add
    bb_nbar_discard  = building_blocked_no_barrier.discard
    bb_nconv_add     = building_blocked_no_conveyors.add
    bb_nconv_discard = building_blocked_no_conveyors.discard
    bb_nbnc_add      = building_blocked_no_barrier_no_conveyors.add
    bb_nbnc_discard  = building_blocked_no_barrier_no_conveyors.discard
    # --- FIX 10: local-cache enum singletons for identity comparisons in loop.
    env_empty  = _ENV_EMPTY
    env_ore_ax = _ENV_ORE_AX
    env_ore_ti = _ENV_ORE_TI
    et_road    = _ET_ROAD
    et_marker  = _ET_MARKER
    et_barrier = _ET_BARRIER
    et_core    = _ET_CORE
    prev_round = current_round - 1
    # --- FIX 11: keep frozenset references local.
    conv_vals  = _CONVEYOR_TYPE_VALS
    hsr_vals   = _has_stored_resource_vals
    hdir_vals  = _has_direction_vals
    hvis_vals  = _has_vision_vals
    hbt_vals   = _has_bridge_target_vals
    for tile in visible_tiles:
        x = tile.x
        y = tile.y
        if not ground_seen_local[x][y]:
            env = rc_get_tile_env(tile)
            ground_local[x][y]      = env
            ground_seen_local[x][y] = True
            # --- FIX 12: inlined _update_ground_blocked_at, NO discards needed.
            # The tile was never seen before, so it cannot be in any blocked set.
            if env is not None and env is not env_empty and env is not env_ore_ax:
                gb_all_add(tile)
                if env is not env_ore_ti:
                    gb_nore_add(tile)
            # --- FIX 5 (kept): inline symmetry update with raw ints.
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
                # --- FIX 13: compute flipped coords as ints first;
                # only create Position if we need to add to blocked sets.
                if hor_sym_local:
                    fx, fy = width_m1 - x, y
                elif ver_sym_local:
                    fx, fy = x, height_m1 - y
                else:
                    fx, fy = width_m1 - x, height_m1 - y
                ground_local[fx][fy]      = env
                ground_seen_local[fx][fy] = True
                # Inlined ground blocked for flipped (no discards: never-seen tile).
                if env is not None and env is not env_empty and env is not env_ore_ax:
                    flipped = Position(fx, fy)
                    gb_all_add(flipped)
                    if env is not env_ore_ti:
                        gb_nore_add(flipped)
        entity_id = rc_get_tile_building_id(tile)
        if entity_id is None:
            if building_local[x][y] is not None:
                building_local[x][y] = None
                # --- FIX 14: inlined _update_building_blocked_at for None building.
                bb_all_discard(tile)
                bb_nbar_discard(tile)
                bb_nconv_discard(tile)
                bb_nbnc_discard(tile)
            last_seen_local[x][y] = current_round
            continue
        
        prev_building = building_local[x][y]
        seen_last_turn = last_seen_local[x][y] == prev_round
        last_seen_local[x][y] = current_round

        entity_type = rc_get_entity_type(entity_id)
        etv = entity_type.value
        is_conv = etv in conv_vals
        has_sr  = etv in hsr_vals
        stored_resource_id = rc_get_stored_resource_id(entity_id) if has_sr else None
        stored_resource = rc_get_stored_resource(entity_id) if (is_conv and has_sr) else None
        transporting_ore = _ore_env_from_code(_ore_code_from_resource(stored_resource)) if is_conv else None
        prev_is_conv = prev_building is not None and prev_building.is_conveyor_type
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
            if stored_resource is None:
                load = 3
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
        # --- FIX 15: reuse Building object when entity_id is unchanged.
        # Same entity => same type, team, maxhp, direction, vision_sq, bridge_target.
        # This skips 5+ rc.get_*() C-API calls and a Building() allocation per tile.
        
        team = rc_get_team(entity_id)
        # if entity_type == _ET_CORE and team != my_team:
        #     # do smth
        if prev_building is not None and prev_building.id == entity_id:
            prev_building.hp = rc_get_hp(entity_id)
            prev_building.stored_resource_id = stored_resource_id
            prev_building.conveyor_speed = speed
            prev_building.load = load
            prev_building.transporting_ore = transporting_ore
            if not is_conv:
                prev_building.load_confirmed = True
            new_building = prev_building
            # Type unchanged => blocked sets unchanged; skip entirely.
        else:
            direction     = rc_get_direction(entity_id)        if etv in hdir_vals else None
            vision_sq     = rc_get_vision_radius_sq(entity_id) if etv in hvis_vals else None
            bridge_target = rc_get_bridge_target(entity_id)    if etv in hbt_vals  else None
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
                load_confirmed=not is_conv,
                transporting_ore=transporting_ore,
            )
            building_local[x][y] = new_building
            # --- FIX 16: inlined _update_building_blocked_at, only when type changed.
            if prev_building is None or prev_building.type is not entity_type:
                # Discard old entries (no-op if prev was None).
                bb_all_discard(tile)
                bb_nbar_discard(tile)
                bb_nconv_discard(tile)
                bb_nbnc_discard(tile)
                t = entity_type
                if t is not et_road and t is not et_marker:
                    is_barrier = t is et_barrier
                    bb_all_add(tile)
                    if not is_barrier:
                        bb_nbar_add(tile)
                    if not is_conv:
                        bb_nconv_add(tile)
                    if not is_barrier and not is_conv:
                        bb_nbnc_add(tile)
        if my_core is None and entity_type is et_core:
            if new_building.team == my_team:
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
                team=None,
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
            gs_x = ground_seen_local[x]
            g_x  = ground_local[x]
            for y in range(height):
                if gs_x[y]:
                    flipped = flip_func(Position(x, y))
                    fx = flipped.x
                    fy = flipped.y
                    if not ground_seen_local[fx][fy]:
                        env = g_x[y]
                        ground_local[fx][fy]      = env
                        ground_seen_local[fx][fy] = True
                        # Inlined _update_ground_blocked_at (no discards: unseen tile).
                        if env is not None and env is not _ENV_EMPTY and env is not _ENV_ORE_AX:
                            gb_all_add(flipped)
                            if env is not _ENV_ORE_TI:
                                gb_nore_add(flipped)
    if my_core:
        if their_core:
            predicted_enemy_core = their_core
        else:
            if rot_sym_local:
                predicted_enemy_core = rot_flip(my_core)
            else:
                hsym_core = hor_flip(my_core)
                vsym_core = ver_flip(my_core)
                if rush_tiebroken == 1 and ver_sym_local:
                    predicted_enemy_core = vsym_core
                elif rush_tiebroken == 2 and hor_sym_local:
                    predicted_enemy_core = hsym_core
                elif ver_sym_local and hor_sym_local:
                    if abs(my_pos.x - hsym_core.x) + abs(my_pos.y - hsym_core.y) < abs(my_pos.x - vsym_core.x) + abs(my_pos.y - vsym_core.y):
                        predicted_enemy_core = hsym_core
                        rush_tiebroken = 2
                        log("Tiebreaking enemy core sym - HORIZONTAL")
                    else:
                        predicted_enemy_core = vsym_core
                        rush_tiebroken = 1
                        log("Tiebreaking enemy core sym - VERTICAL")
                elif ver_sym_local:
                    predicted_enemy_core = vsym_core
                else:
                    predicted_enemy_core = hsym_core
    recompute_all_conveyor_loads()
def is_tile_empty(pos: Position):
    return in_bounds(pos) and (rc.is_tile_empty(pos) or (rc.get_tile_building_id(pos) != None and rc.get_entity_type(rc.get_tile_building_id(pos)) is _ET_MARKER))

def can_place_at_restrictive(pos: Position):
    return is_tile_empty(pos) or in_bounds(pos) and rc.can_destroy(pos) and (rc.get_tile_building_id(pos) != None and rc.get_entity_type(rc.get_tile_building_id(pos)) is _ET_ROAD)

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
    # from units.builder_states.builder_rush import log
    from units.builder import log
    valid = set()
    pos_x = pos.x
    pos_y = pos.y
    my_team = rc.get_team()
    building_local = building
    w = width
    h = height
    for dir in CARDINALS:
        new_pos = pos.add(dir)
        nx, ny = new_pos.x, new_pos.y
        if 0 <= nx < w and 0 <= ny < h:
            b = building_local[nx][ny]
            if b:
                log(f"Checking {pos.direction_to(new_pos)}: {b.type}")
            else:
                log(f"Checking {pos.direction_to(new_pos)}: {b}")
            if b and (b.type is _ET_HARVESTER or b.type is _ET_CONVEYOR and b.direction == new_pos.direction_to(pos)):
                log(f"Validated {new_pos.direction_to(pos)}")
                for dir2 in _ALL_DIRECTIONS:
                    if dir2 is not _DIR_CENTRE and dir2 is not dir:
                        valid.add(dir2)

    if not valid:
        return None
    mx_harvesters = 0
    mx_base       = 0
    mx_conveyors  = 0
    mx_other      = 0
    best_dir      = None
    for dir in valid:
        # Walk 5 tiles in direction, collect visible positions.
        harvesters = conveyors = other = 0
        base = 0
        pew = pos
        seen = set()  # Reintroduce a set to track deduplication

        for i in range(1, 6):
            pew = pew.add(dir)
            for d in _ALL_DIRECTIONS:
                s = pew.add(d)

                # Check if we've already counted this tile's building
                if s in seen:
                    continue
                seen.add(s)

                sx, sy = s.x, s.y
                if not (0 <= sx < w and 0 <= sy < h):
                    continue

                b = building_local[sx][sy]
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
def next_conveyor_pos(pos: Position, b: Building) -> Position | None:
    """Return the next position in the conveyor chain."""
    if b.type is _ET_BRIDGE:
        return b.bridge_target

    if b.type is _ET_CONVEYOR or b.type is _ET_ARMOURED_CONVEYOR or b.type is _ET_SPLITTER:
        if b.direction is None:
            return None
        return pos.add(b.direction)

    return None

def _ore_code_from_env(env: Environment | None) -> int:
    if env is _ENV_ORE_AX:
        return 2
    if env is _ENV_ORE_TI:
        return 1
    return 0

def _ore_code_from_resource(resource: ResourceType | None) -> int:
    if resource is _RT_AXIONITE:
        return 2
    if resource is _RT_TITANIUM:
        return 1
    return 0

def _ore_env_from_code(code: int) -> Environment | None:
    if code >= 2:
        return _ENV_ORE_AX
    if code == 1:
        return _ENV_ORE_TI
    return None


@dataclass(slots=True)
class _LoadRecomputeState:
    phase: int
    my_team: Team
    default_initial_load: float
    harvester_output: float
    round_started: int
    scan_idx: int = 0
    edge_idx: int = 0
    harvest_idx: int = 0
    root_seed_idx: int = 0
    queue_init_idx: int = 0
    queue_head: int = 0
    cycle_idx: int = 0
    reverse_idx: int = -1
    apply_idx: int = 0
    conveyor_indices: list[int] = field(default_factory=list)
    harvester_indices: list[int] = field(default_factory=list)
    cycle_active: bool = False
    cycle_node: int = -1
    cycle_nodes: list[int] = field(default_factory=list)
    cycle_total: float = 0.0
    cycle_touched: bool = False
    cycle_ore: int = 0


def _cleanup_load_state(st: _LoadRecomputeState | None) -> None:
    if st is None:
        return

    snapshot_conveyor = _load_snapshot_conveyor
    snapshot_next_raw = _load_snapshot_next_raw
    snapshot_seed = _load_snapshot_seed
    terminal_confirm = _load_terminal_confirm
    confirmed = _load_confirmed
    ore_code = _load_ore_code
    for idx in st.conveyor_indices:
        snapshot_conveyor[idx] = False
        snapshot_next_raw[idx] = -1
        snapshot_seed[idx] = None
        terminal_confirm[idx] = False
        confirmed[idx] = False
        ore_code[idx] = 0


def recompute_all_conveyor_loads(
    harvester_output: float = 1.0,
    default_initial_load: float = 1.0,
    max_cpu_us: int = 300,
) -> bool:
    start_time = time.perf_counter_ns()
    global _load_recompute_state

    start_us = rc.get_cpu_time_elapsed()

    def over_budget() -> bool:
        return rc.get_cpu_time_elapsed() - start_us >= max_cpu_us

    width_l = width
    height_l = height
    total_tiles = width_l * height_l
    building_local = building
    ground_local = ground
    next_idx = _load_next_idx
    indegree = _load_indegree
    accum = _load_accum
    final = _load_final
    touched = _load_touched
    harvester_fed = _load_harvester_fed
    cycle_seen = _load_cycle_seen
    snapshot_conveyor = _load_snapshot_conveyor
    snapshot_next_raw = _load_snapshot_next_raw
    snapshot_seed = _load_snapshot_seed
    terminal_confirm = _load_terminal_confirm
    confirmed = _load_confirmed
    ore_code = _load_ore_code
    dir_deltas = _DIRECTION_DELTAS
    queue = _load_queue
    order = _load_order

    st = _load_recompute_state
    current_team = rc.get_team()
    current_round = rc.get_current_round()
    if st is None:
        st = _LoadRecomputeState(
            phase=0,
            my_team=current_team,
            default_initial_load=default_initial_load,
            harvester_output=harvester_output,
            round_started=current_round,
        )
        _load_recompute_state = st
    elif (
        st.round_started != current_round
        or
        st.my_team != current_team
        or st.default_initial_load != default_initial_load
        or st.harvester_output != harvester_output
    ):
        _cleanup_load_state(st)
        st = _LoadRecomputeState(
            phase=0,
            my_team=current_team,
            default_initial_load=default_initial_load,
            harvester_output=harvester_output,
            round_started=current_round,
        )
        _load_recompute_state = st

    while True:
        if st.phase == 0:
            while st.scan_idx < total_tiles:
                if (st.scan_idx & 15) == 0 and over_budget():
                    return False

                idx = st.scan_idx
                st.scan_idx += 1
                x = idx % width_l
                y = idx // width_l
                b = building_local[x][y]
                if b is None or b.team != st.my_team:
                    continue

                if b.is_conveyor_type:
                    st.conveyor_indices.append(idx)
                    next_idx[idx] = -1
                    indegree[idx] = 0
                    accum[idx] = 0.0
                    final[idx] = 0.0
                    touched[idx] = False
                    harvester_fed[idx] = False
                    cycle_seen[idx] = False
                    terminal_confirm[idx] = False
                    confirmed[idx] = False
                    ore_code[idx] = 0
                    snapshot_conveyor[idx] = True
                    snapshot_seed[idx] = float(b.load) if b.load is not None else None

                    raw_next = -1
                    if b.type is _ET_BRIDGE:
                        bridge_target = b.bridge_target
                        if (
                            bridge_target is not None
                            and 0 <= bridge_target.x < width_l
                            and 0 <= bridge_target.y < height_l
                        ):
                            raw_next = bridge_target.y * width_l + bridge_target.x
                    else:
                        direction = b.direction
                        if direction is not None:
                            dx, dy = dir_deltas[direction]
                            nx = x + dx
                            ny = y + dy
                            if 0 <= nx < width_l and 0 <= ny < height_l:
                                raw_next = ny * width_l + nx
                    snapshot_next_raw[idx] = raw_next

                elif b.type is _ET_HARVESTER:
                    st.harvester_indices.append(idx)

            if not st.conveyor_indices:
                _cleanup_load_state(st)
                _load_recompute_state = None
                return True

            st.phase = 1
            continue

        if st.phase == 1:
            conveyors = st.conveyor_indices
            while st.edge_idx < len(conveyors):
                if (st.edge_idx & 31) == 0 and over_budget():
                    return False

                idx = conveyors[st.edge_idx]
                st.edge_idx += 1
                raw_next = snapshot_next_raw[idx]
                if raw_next != -1 and snapshot_conveyor[raw_next]:
                    next_idx[idx] = raw_next
                    indegree[raw_next] += 1
                else:
                    next_idx[idx] = -1
                    if raw_next != -1:
                        tx = raw_next % width_l
                        ty = raw_next // width_l
                        terminal_confirm[idx] = building_local[tx][ty] is not None
                    else:
                        terminal_confirm[idx] = False

            st.phase = 2
            continue

        if st.phase == 2:
            harvesters = st.harvester_indices
            while st.harvest_idx < len(harvesters):
                if (st.harvest_idx & 15) == 0 and over_budget():
                    return False

                hidx = harvesters[st.harvest_idx]
                st.harvest_idx += 1
                x = hidx % width_l
                y = hidx // width_l
                adjacent: list[int] = []
                for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                    nx = x + dx
                    ny = y + dy
                    if nx < 0 or nx >= width_l or ny < 0 or ny >= height_l:
                        continue
                    nidx = ny * width_l + nx
                    if snapshot_conveyor[nidx]:
                        adjacent.append(nidx)

                if not adjacent:
                    continue

                share = st.harvester_output / len(adjacent)
                if share == 0:
                    continue
                h_ore = _ore_code_from_env(ground_local[x][y])
                for nidx in adjacent:
                    accum[nidx] += share
                    touched[nidx] = True
                    harvester_fed[nidx] = True
                    if h_ore > ore_code[nidx]:
                        ore_code[nidx] = h_ore

            st.phase = 3
            continue

        if st.phase == 3:
            conveyors = st.conveyor_indices
            while st.root_seed_idx < len(conveyors):
                if (st.root_seed_idx & 31) == 0 and over_budget():
                    return False

                idx = conveyors[st.root_seed_idx]
                st.root_seed_idx += 1
                if indegree[idx] != 0 or harvester_fed[idx]:
                    continue

                x = idx % width_l
                y = idx // width_l
                b = building_local[x][y]
                if b is not None and b.is_conveyor_type:
                    source_ore = _ore_code_from_env(b.transporting_ore)
                    if source_ore > ore_code[idx]:
                        ore_code[idx] = source_ore

                seed = snapshot_seed[idx]
                seed_value = st.default_initial_load if seed is None else seed
                if seed_value == 0:
                    continue
                accum[idx] += seed_value
                touched[idx] = True

            queue.clear()
            order.clear()
            st.queue_head = 0
            st.phase = 4
            continue

        if st.phase == 4:
            conveyors = st.conveyor_indices
            while st.queue_init_idx < len(conveyors):
                if (st.queue_init_idx & 63) == 0 and over_budget():
                    return False

                idx = conveyors[st.queue_init_idx]
                st.queue_init_idx += 1
                if indegree[idx] == 0:
                    queue.append(idx)

            st.phase = 5
            continue

        if st.phase == 5:
            while st.queue_head < len(queue):
                if (st.queue_head & 63) == 0 and over_budget():
                    return False

                idx = queue[st.queue_head]
                st.queue_head += 1
                order.append(idx)

                nxt = next_idx[idx]
                if nxt == -1:
                    continue

                if ore_code[idx] > ore_code[nxt]:
                    ore_code[nxt] = ore_code[idx]

                if touched[idx]:
                    accum[nxt] += accum[idx]
                    touched[nxt] = True

                new_indegree = indegree[nxt] - 1
                indegree[nxt] = new_indegree
                if new_indegree == 0:
                    queue.append(nxt)

            st.phase = 6
            st.cycle_idx = 0
            st.cycle_active = False
            continue

        if st.phase == 6:
            conveyors = st.conveyor_indices
            while True:
                if over_budget():
                    return False

                if not st.cycle_active:
                    if st.cycle_idx >= len(conveyors):
                        st.phase = 7
                        st.reverse_idx = len(order) - 1
                        break

                    idx = conveyors[st.cycle_idx]
                    st.cycle_idx += 1
                    if indegree[idx] == 0 or cycle_seen[idx]:
                        continue

                    st.cycle_active = True
                    st.cycle_node = idx
                    st.cycle_nodes.clear()
                    st.cycle_total = 0.0
                    st.cycle_touched = False
                    st.cycle_ore = 0

                node = st.cycle_node
                if cycle_seen[node]:
                    cycle_value = st.cycle_total
                    if cycle_value < _CYCLE_DEFAULT_LOAD:
                        cycle_value = _CYCLE_DEFAULT_LOAD
                    cycle_ore = st.cycle_ore
                    for n in st.cycle_nodes:
                        final[n] = cycle_value
                        touched[n] = True
                        if cycle_ore > ore_code[n]:
                            ore_code[n] = cycle_ore
                    st.cycle_active = False
                    continue

                cycle_seen[node] = True
                st.cycle_nodes.append(node)
                st.cycle_total += accum[node]
                if touched[node]:
                    st.cycle_touched = True
                if ore_code[node] > st.cycle_ore:
                    st.cycle_ore = ore_code[node]

                nxt = next_idx[node]
                if nxt == -1:
                    st.cycle_node = node
                else:
                    st.cycle_node = nxt
            continue

        if st.phase == 7:
            while st.reverse_idx >= 0:
                if (st.reverse_idx & 63) == 0 and over_budget():
                    return False

                idx = order[st.reverse_idx]
                st.reverse_idx -= 1
                nxt = next_idx[idx]
                if nxt == -1:
                    confirmed[idx] = terminal_confirm[idx]
                else:
                    confirmed[idx] = confirmed[nxt]
                if not touched[idx]:
                    continue

                value = accum[idx]
                if nxt != -1 and touched[nxt] and final[nxt] > value:
                    value = final[nxt]
                final[idx] = value

            st.phase = 8
            st.apply_idx = 0
            continue

        if st.phase == 8:
            conveyors = st.conveyor_indices
            while st.apply_idx < len(conveyors):
                if (st.apply_idx & 63) == 0 and over_budget():
                    return False

                idx = conveyors[st.apply_idx]
                st.apply_idx += 1
                x = idx % width_l
                y = idx // width_l
                b = building_local[x][y]
                if b is not None and b.team == st.my_team and b.is_conveyor_type:
                    b.load = final[idx] if touched[idx] else None
                    b.load_confirmed = confirmed[idx]
                    b.transporting_ore = _ore_env_from_code(ore_code[idx])

            _cleanup_load_state(st)
            _load_recompute_state = None
            end_time = time.perf_counter_ns()
            print("converyor loads", (end_time-start_time))
            return True

        # Defensive fallback if phase ever gets corrupted.
        _cleanup_load_state(st)
        _load_recompute_state = None
        print("converyor loads, fail?", (end_time-start_time))

        return True
