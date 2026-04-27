from cambc import Controller, Direction, Position, EntityType
import map_info
from log import log
from units.spawn_plan import choose_spawn_plan, draw_spawn_plan, INITIAL_SPAWN_COUNT, INITIAL_EXPLORE_MAX_STEPS

rc: Controller

# --- Configurable ---
SCALE_MULT = 1
DEFENSE_FRIENDLY_RADIUS_SQ = 20

_spawn_plan: list[Direction] | None = None
_num_spawned = 0
_core_area: tuple[Position, ...] = ()


def _core_area_positions(pos: Position) -> tuple[Position, ...]:
    return tuple(
        Position(pos.x + dx, pos.y + dy)
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
    )


def _try_spawn_planned(core_pos: Position) -> bool:
    global _num_spawned
    if _spawn_plan is None or _num_spawned >= len(_spawn_plan):
        return False

    planned_dir = _spawn_plan[_num_spawned]
    for d in (planned_dir, planned_dir.rotate_left(), planned_dir.rotate_right()):
        p = core_pos.add(d)
        if rc.can_spawn(p):
            rc.spawn_builder(p)
            _num_spawned += 1
            return True
    return False


def _spawn_toward_center():
    """Spawn on the core tile closest to map center."""
    center = Position(map_info._width//2, map_info._height//2)
    best = None
    best_dist = float('inf')
    for p in _core_area:
        if rc.can_spawn(p):
            d = p.distance_squared(center)
            if d < best_dist:
                best_dist = d
                best = p
    if best is not None:
        rc.spawn_builder(best)


def _spawn_toward_enemy_if_undefended(core_pos: Position, has_close_ally: bool, closest_enemy: Position | None) -> bool:
    """If an enemy builder bot is in vision and no friendly builder bot sits
    within dist² DEFENSE_FRIENDLY_RADIUS_SQ of the core, spawn a defender on
    the core tile closest to the nearest enemy bot. Returns True if spawned."""
    if has_close_ally or closest_enemy is None:
        return False
    best = None
    best_d = None
    for p in _core_area:
        if rc.can_spawn(p):
            d = p.distance_squared(closest_enemy)
            if best_d is None or d < best_d:
                best_d = d
                best = p
    if best is None:
        return False
    rc.spawn_builder(best)
    return True


def run():
    global _spawn_plan
    map_info.update()
    core_pos = rc.get_position()
    if _spawn_plan is None:
        _spawn_plan = choose_spawn_plan(rc, core_pos, INITIAL_SPAWN_COUNT)
    if rc.get_current_round() <= INITIAL_SPAWN_COUNT + INITIAL_EXPLORE_MAX_STEPS:
        draw_spawn_plan(rc, core_pos, _spawn_plan, rc.get_map_width(), rc.get_map_height())

    # if rc.get_current_round() == 400:
    #     rc.resign()
    titanium = rc.get_global_resources()[0]
    axionite = rc.get_global_resources()[1]
    scaling = rc.get_scale_percent()
    my_team = map_info._my_team
    ally_builder_count = 0
    has_close_ally = False
    closest_enemy = None
    closest_enemy_d = None
    for uid in rc.get_nearby_units():
        if rc.get_entity_type(uid) != EntityType.BUILDER_BOT:
            continue
        p = rc.get_position(uid)
        if rc.get_team(uid) == my_team:
            if p.distance_squared(core_pos) <= DEFENSE_FRIENDLY_RADIUS_SQ:
                ally_builder_count += 1
                has_close_ally = True
        else:
            d = p.distance_squared(core_pos)
            if closest_enemy_d is None or d < closest_enemy_d:
                closest_enemy_d = d
                closest_enemy = p

    if not _spawn_toward_enemy_if_undefended(core_pos, has_close_ally, closest_enemy):
        threshold = 400 if ally_builder_count >= 12 else 200
        if scaling * SCALE_MULT + threshold < titanium:
            if not _try_spawn_planned(core_pos):
                _spawn_toward_center()
    if rc.get_current_round() < 1500 and titanium < 4 * rc.get_harvester_cost()[0]:
        rc.convert(min(max(axionite - 1, 0), max((3 * rc.get_harvester_cost()[0] - titanium) // 4, 0)))


def init(c: Controller):
    global rc, _core_area
    rc = c
    _core_area = _core_area_positions(rc.get_position())
