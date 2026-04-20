from cambc import Controller, Direction, Position
import map_info
from log import log
from units.spawn_plan import choose_spawn_plan, draw_spawn_plan, INITIAL_SPAWN_COUNT, INITIAL_EXPLORE_MAX_STEPS

rc: Controller

# --- Configurable ---
SCALE_MULT = 0.5

_spawn_plan: list[Direction] | None = None
_num_spawned = 0


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
    core_pos = rc.get_position()
    center = map_info._MAP_CENTER
    best = None
    best_dist = float('inf')
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            p = Position(core_pos.x + dx, core_pos.y + dy)
            if rc.can_spawn(p):
                d = p.distance_squared(center)
                if d < best_dist:
                    best_dist = d
                    best = p
    if best is not None:
        rc.spawn_builder(best)


def run():
    global _spawn_plan
    core_pos = rc.get_position()
    if _spawn_plan is None:
        _spawn_plan = choose_spawn_plan(rc, core_pos, INITIAL_SPAWN_COUNT)
    if rc.get_current_round() <= INITIAL_SPAWN_COUNT + INITIAL_EXPLORE_MAX_STEPS:
        draw_spawn_plan(rc, core_pos, _spawn_plan, rc.get_map_width(), rc.get_map_height())

    titanium = rc.get_global_resources()[0]
    axionite = rc.get_global_resources()[1]
    scaling = rc.get_scale_percent()
    if scaling * SCALE_MULT + 300 < titanium:
        if not _try_spawn_planned(core_pos):
            _spawn_toward_center()
    if rc.get_current_round() < 1500 and titanium < 4 * rc.get_harvester_cost()[0]:
        rc.convert(min(max(axionite - 1, 0), max((3 * rc.get_harvester_cost()[0] - titanium) // 4, 0)))


def init(c: Controller):
    global rc
    rc = c
