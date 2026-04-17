from cambc import Controller, Position, Environment, EntityType, GameError

import map_info
from log import log

rc: Controller

# --- Configurable ---
SCALE_MULT = 0.5


def get_closest_titanium_tile() -> Position | None:
    """Return the closest visible titanium ore without an allied harvester."""
    core_pos = rc.get_position()
    min_dist_sq = float('inf')
    closest_ore = None

    for pos in rc.get_nearby_tiles():
        if rc.get_tile_env(pos) != Environment.ORE_TITANIUM:
            continue

        building_id = rc.get_tile_building_id(pos)
        has_allied_harvester = False
        if building_id is not None:
            try:
                building_type = rc.get_entity_type(building_id)
                building_team = rc.get_team(building_id)
                if building_type == EntityType.HARVESTER and building_team == rc.get_team():
                    has_allied_harvester = True
            except GameError:
                pass

        if not has_allied_harvester:
            dist_sq = pos.distance_squared(core_pos)
            if dist_sq < min_dist_sq:
                min_dist_sq = dist_sq
                closest_ore = pos

    return closest_ore


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
    # if rc.get_current_round() == 200:
    #     rc.resign()
    round_num = rc.get_current_round()
    core_pos = rc.get_position()

    # --- Spawn towards closest titanium on turn 1 ---
    if round_num == 1:
        titanium_pos = get_closest_titanium_tile()
        if titanium_pos is not None:
            dx = max(-1, min(1, titanium_pos.x - core_pos.x))
            dy = max(-1, min(1, titanium_pos.y - core_pos.y))
            spawn_pos = Position(core_pos.x + dx, core_pos.y + dy)
            if rc.can_spawn(spawn_pos):
                rc.spawn_builder(spawn_pos)
                return  # Only spawn 1 builder for turn 1

    titanium, axionite = rc.get_global_resources()
    scaling = rc.get_scale_percent()
    if scaling * SCALE_MULT + 300 < titanium:
        _spawn_toward_center()
    if rc.get_current_round() < 1500 and titanium < 4 * rc.get_harvester_cost()[0]:
        # Idea: change this to axionite - 2 before final submission
        # Since other teams are also keeping 1 axionite in reserve for tiebreakers
        rc.convert(min(max(axionite - 1, 0), max((3 * rc.get_harvester_cost()[0] - titanium) // 4, 0)))


def init(c: Controller):
    global rc
    rc = c
    map_info.init(c)
