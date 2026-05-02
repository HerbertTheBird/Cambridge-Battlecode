from cambc import Controller, Position

import map_info
import pathing
from pathing import Pathing
import comms
from _config import USE_CHOKEPOINTS
from units.spawn_plan import get_ray_endpoint, INITIAL_EXPLORE_MAX_STEPS, INITIAL_SPAWN_COUNT

import units.states.explore  as explore
import units.states.disrupt  as disrupt
import units.states.harvest  as harvest
import units.states.route    as route
import units.states.heal     as heal
import units.states.attack   as attack
import units.states.secure   as secure

from log import DRAW_DEBUG


rc: Controller
nav: Pathing = None

WAIT_FOR_CHOKEPOINT = True
_waiting_for_chokepoint = False

# Sorted in descending order of max score to allow early break in selection loop
states = tuple(sorted(
    [explore, disrupt, harvest, route, heal, attack, secure],
    key=lambda s: s.MAX_SCORE,
    reverse=True
))

# Harvvest zones are calculated based on map symmetry with fallback
harvest_radius = 0
_harvest_zone = 0
_harvest_zone_final = False

# Initial explore target for first few builders
INITIAL_EXPLORE_TIMEOUT = 30
_initial_explore_calculated = False
_initial_explore_target: Position | None = None
_initial_explore_round = -1

# Builders spawned on round 1 stay close to the core; their state targets and
# explore tiles are restricted to within STAY_NEAR_CORE_DSQ of the core.
STAY_NEAR_CORE_DSQ = 100
_stay_near_core = False
_first_run_done = False
_near_core_mask_cache: tuple[Position | None, int] = (None, 0)


def near_core_mask() -> int:
    """Bitmask of in-bounds tiles within STAY_NEAR_CORE_DSQ of my core."""
    global _near_core_mask_cache
    core = map_info._my_core
    if core is None:
        return map_info._board_mask
    if _near_core_mask_cache[0] == core:
        return _near_core_mask_cache[1]
    w = map_info._width
    h = map_info._height
    cx, cy = core.x, core.y
    result = 0
    for y in range(h):
        dy2 = (y - cy) * (y - cy)
        if dy2 > STAY_NEAR_CORE_DSQ:
            continue
        for x in range(w):
            dx = x - cx
            if dx * dx + dy2 <= STAY_NEAR_CORE_DSQ:
                result |= 1 << (x + y * w)
    _near_core_mask_cache = (core, result)
    return result


def init(c: Controller):
    global rc, harvest_radius, nav
    rc = c
    nav = Pathing(c)
    harvest_radius = (c.get_map_width() + c.get_map_height()) // 3
    for s in states:
        s.init(c)


def draw_mask(mask, r, g, b):
    if not DRAW_DEBUG:
        return
    for p in map_info.iter_mask(mask):
        rc.draw_indicator_dot(p, r, g, b)


def wait_for_chokepoint() -> None:
    global _waiting_for_chokepoint
    _waiting_for_chokepoint = True


def handle_comms():
    if map_info._solved_sym:
        return
    for v, _sender_pos, _marker_pos, _marker_id, _estimated_turn in comms.get_new_messages():
        sym = comms.decode_sym(v)
        map_info.update_symmetry_from_comms(sym)


def _compute_voronoi_harvest_zone():
    """Flood-fill Manhattan from both cores simultaneously.
    Tiles reached by my core first are my harvest zone."""
    w = map_info._width
    h = map_info._height
    board = (1 << (w * h)) - 1
    walls = map_info._bm_env[map_info._IDX_ENV_WALL]
    passable = board & ~walls

    my_core = map_info._my_core
    enemy_core = map_info._predicted_enemy_core

    my_front = 1 << (my_core.x + my_core.y * w)
    enemy_front = 1 << (enemy_core.x + enemy_core.y * w)

    my_claimed = my_front
    enemy_claimed = enemy_front
    claimed = my_claimed | enemy_claimed

    while my_front or enemy_front:
        if my_front:
            my_expand = map_info.expand_manhattan(my_front) & passable & ~claimed
            my_claimed |= my_expand
            claimed |= my_expand
            my_front = my_expand
        if enemy_front:
            enemy_expand = map_info.expand_manhattan(enemy_front) & passable & ~claimed
            enemy_claimed |= enemy_expand
            claimed |= enemy_expand
            enemy_front = enemy_expand

    return my_claimed


def _update_harvest_zone():
    global _harvest_zone, _harvest_zone_final

    my_core = map_info._my_core
    if not my_core or _harvest_zone_final:
        return

    if map_info._solved_sym and map_info._predicted_enemy_core is not None:
        # Symmetry solved - compute Voronoi partition once
        _harvest_zone = _compute_voronoi_harvest_zone()
        _harvest_zone_final = True
        return

    if not _harvest_zone:
        # Fallback: radius-based until symmetry is solved
        w = map_info._width
        zone = 1 << (my_core.x + my_core.y * w)
        for _ in range(harvest_radius):
            zone = map_info.expand_chebyshev(zone)
        _harvest_zone = zone


def _update_initial_explore(current_round: int):
    global _initial_explore_target, _initial_explore_calculated, _initial_explore_round

    if not _initial_explore_calculated:
        # Only first few builders follow initial explore plan
        if current_round <= INITIAL_SPAWN_COUNT + 1 and map_info._my_core is not None:
            # Choose explore direction based on where we are relative to core
            spawn_dir = map_info.direction_to(map_info._my_core, map_info._my_pos)
            _initial_explore_target = get_ray_endpoint(map_info._my_pos, spawn_dir, map_info._width, map_info._height, max_steps=INITIAL_EXPLORE_MAX_STEPS)
            _initial_explore_round = current_round
        
        _initial_explore_calculated = True

    # Auto-clear stale initial target if we couldn't reach it in time
    if _initial_explore_target is not None and current_round - _initial_explore_round >= INITIAL_EXPLORE_TIMEOUT:
        _initial_explore_target = None


def select_best_state():
    best_state = None
    best_score = 0

    for state in states:
        # Since states are sorted, break early if we can't beat best score
        if best_score >= state.MAX_SCORE:
            break

        score = state.score()
        if score > best_score:
            best_score = score
            best_state = state

    return best_state


def run():
    global _waiting_for_chokepoint, _stay_near_core, _first_run_done
    _waiting_for_chokepoint = False

    # Sync round info
    current_round = rc.get_current_round()
    if not _first_run_done:
        _first_run_done = True
        if current_round == 1:
            _stay_near_core = True
    map_info.update(recompute=False)
    handle_comms()
    map_info.recompute_derived()
    _update_harvest_zone()

    # First few builder bots derive explore target from spawn position
    _update_initial_explore(current_round)

    # If we broke barrier last turn, try rebuilding first
    pathing.rebuild_broken_barriers(rc)

    # Run state-specific logic
    best_state = select_best_state()
    best_state.run()

    if USE_CHOKEPOINTS:
        explore.try_passive_chokepoint_action_only()

    if _waiting_for_chokepoint:
        return
    # Road-spam if an enemy is closing in
    try_road_spam()

    # Try healing adjacent building
    heal._do_best_heal()

    # Fall back to healing self
    if rc.can_heal(map_info._my_pos):
        rc.heal(map_info._my_pos)

    # Tear down any of our own conveyors/bridges that are feeding an enemy turret
    _destroy_enemy_feeders()

    # Broadcast symmetry via a marker on the first available adjacent tile
    comms.broadcast_symmetry()


def _destroy_enemy_feeders():
    """End-of-turn cleanup over the 3x3 around me (8 directions + own tile):
    if a friendly conveyor / armoured conveyor / bridge outputs onto a tile
    occupied by an enemy turret (gunner/sentinel/breach/launcher), destroy
    it. destroy() does not cost action cooldown, so if cooldown was already
    0 we then drop a road on the freed tile. Exits after the first action."""
    w = map_info._width
    my_pos = map_info._my_pos
    my_bit = 1 << (my_pos.x + my_pos.y * w)
    region = map_info.expand_chebyshev(my_bit)

    my_team_idx = map_info._my_team_idx
    my_feeders = (
        map_info._bm_et[map_info._IDX_CONVEYOR]
        | map_info._bm_et[map_info._IDX_ARMOURED_CONVEYOR]
        | map_info._bm_et[map_info._IDX_BRIDGE]
    ) & map_info._bm_team[my_team_idx] & region
    if not my_feeders:
        return

    enemy_turrets = (
        map_info._bm_et[map_info._IDX_GUNNER]
        | map_info._bm_et[map_info._IDX_SENTINEL]
        | map_info._bm_et[map_info._IDX_BREACH]
        | map_info._bm_et[map_info._IDX_LAUNCHER]
    ) & map_info._bm_team[1 - my_team_idx]
    if not enemy_turrets:
        return

    mask = my_feeders
    while mask:
        lsb = mask & -mask
        if map_info._conveyor_target_tiles(lsb) & enemy_turrets:
            n = lsb.bit_length() - 1
            p = Position(n % w, n // w)
            if rc.can_destroy(p):
                rc.destroy(p)
                map_info.update_at(p)
                if rc.get_action_cooldown() == 0:
                    enemy_bots = map_info._bm_enemy_bots
                    threatened = enemy_bots and (lsb & map_info.expand_chebyshev(enemy_bots, 2))
                    reserve = map_info.builder_ti_reserve()
                    ti_have = rc.get_global_resources()[0]
                    if threatened and rc.can_build_barrier(p) and ti_have >= rc.get_barrier_cost()[0] + reserve:
                        rc.build_barrier(p)
                        map_info.update_at(p)
                    elif rc.can_build_road(p) and ti_have >= rc.get_road_cost()[0] + reserve:
                        rc.build_road(p)
                        map_info.update_at(p)
                return
        mask ^= lsb


def _try_mask(candidates):
    w = map_info._width
    mask = candidates
    road_cost = rc.get_road_cost()[0]
    reserve = map_info.builder_ti_reserve()
    while mask:
        lsb = mask & -mask
        n = lsb.bit_length() - 1
        p = Position(n % w, n // w)
        if rc.can_build_road(p) and rc.get_global_resources()[0] >= road_cost + reserve:
            rc.build_road(p)
            map_info.update_at(p)
            return True
        mask ^= lsb
    return False


def try_road_spam():
    """Opportunistic road placement. Priority order:
      1. (Always) tiles adjacent to a friendly conveyor or on a friendly
         conveyor target — pave conveyor lanes regardless of enemies.
      2. (Enemy within 4 pathing dist) tile adjacent to an enemy bot.
      3. (Enemy within 4) tile adjacent to enemy non-marker non-road building.
      4. (Enemy within 4) tile under me.
      5. (Enemy within 4) any adjacent buildable tile.
    Tiles covered by my gunners' current shooting rays are excluded.
    Action-cooldown gated."""
    if rc.get_action_cooldown() != 0:
        return False

    w = map_info._width
    my_pos = map_info._my_pos
    my_x = my_pos.x
    my_y = my_pos.y
    my_bit = 1 << (my_x + my_y * w)
    my_neighbors = map_info.expand_chebyshev(my_bit) & ~my_bit

    # Don't build roads where my own gunners are shooting through.
    avoid = map_info._bm_my_gunner_claims
    allowed_neighbors = my_neighbors & ~avoid
    draw_mask(avoid & my_neighbors, 255, 0, 0)

    # Priority 1: pave around our conveyors (always, regardless of enemies)
    my_team_idx = map_info._my_team_idx
    friendly_convs = map_info._bm_conveyors & map_info._bm_team[my_team_idx]
    if friendly_convs:
        conv_zone = (
            map_info.expand_chebyshev(friendly_convs)
            | map_info._conveyor_target_tiles(friendly_convs)
        )
        if _try_mask(conv_zone & allowed_neighbors):
            return True

    enemy_bots = map_info._bm_enemy_bots
    if not enemy_bots:
        return False
    closest_enemy, dist = nav.closest(enemy_bots)
    if closest_enemy is None or dist > 4:
        return False

    # Restrict near-enemy spam to tiles adjacent to any conveyor or harvester.
    spam_sources = (
        map_info._bm_conveyors
        | map_info._bm_et[map_info._IDX_HARVESTER]
    )
    if not spam_sources:
        return False
    spam_zone = map_info.expand_chebyshev(spam_sources)
    spam_neighbors = allowed_neighbors & spam_zone

    if _try_mask(map_info.expand_chebyshev(enemy_bots) & spam_neighbors):
        return True

    enemy_hard = (
        map_info._bm_team[1 - my_team_idx]
        & ~map_info._bm_et[map_info._IDX_MARKER]
        & ~map_info._bm_et[map_info._IDX_ROAD]
    )
    if enemy_hard:
        if _try_mask(map_info.expand_chebyshev(enemy_hard) & spam_neighbors):
            return True

    if (spam_zone & my_bit) and not (avoid & my_bit) and rc.can_build_road(my_pos) and rc.get_global_resources()[0] >= rc.get_road_cost()[0] + map_info.builder_ti_reserve():
        rc.build_road(my_pos)
        map_info.update_at(my_pos)
        return True

    if _try_mask(spam_neighbors):
        return True
    return False
