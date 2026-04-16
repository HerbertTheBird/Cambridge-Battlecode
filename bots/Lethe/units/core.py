from cambc import Controller, Position
import map_info

rc: Controller

# --- Configurable ---
SCALE_MULT = 0.5


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
    # if rc.get_current_round() == 1100:
    #     rc.resign()
    titanium = rc.get_global_resources()[0]
    axionite = rc.get_global_resources()[1]
    scaling = rc.get_scale_percent()
    if scaling * SCALE_MULT + 300 < titanium:
        _spawn_toward_center()
    if rc.get_current_round() < 1500 and titanium < 4 * rc.get_harvester_cost()[0]:
        rc.convert(min(max(axionite - 1, 0), max((3 * rc.get_harvester_cost()[0] - titanium) // 4, 0)))


def init(c: Controller):
    global rc
    rc = c
    map_info.init(c)
