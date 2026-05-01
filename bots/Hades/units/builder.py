from cambc import Controller, Position

import map_info
import pathing
from pathing import Pathing
import comms
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

# Comms-based claims
claimed_targets = [0] * (len(states) + 1)   # target bitmask per comm flag
claimed_senders = [0] * (len(states) + 1)   # sender position bitmask per comm flag
_target_rounds = [dict() for _ in range(len(states) + 1)]
_sender_rounds = [dict() for _ in range(len(states) + 1)]

# Vision-based claims
crowded_claims = [0] * (len(states) + 1)    # locally observed crowded targets per comm flag
_crowded_seen_rounds = [dict() for _ in range(len(states) + 1)]
_crowded_claim_rounds = [dict() for _ in range(len(states) + 1)]
_active_target_flag = 0
_active_target_idx = -1


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


def _adjacent_friendly_builder_count(target_idx: int) -> int:
    w = map_info._width
    h = map_info._height
    tx = target_idx % w
    ty = target_idx // w
    count = 0

    my_pos = map_info._my_pos
    if max(abs(my_pos.x - tx), abs(my_pos.y - ty)) <= 1:
        count += 1

    friendly = map_info._bm_friendly_bots
    for dy in (-1, 0, 1):
        ny = ty + dy
        if ny < 0 or ny >= h:
            continue
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            nx = tx + dx
            if nx < 0 or nx >= w:
                continue
            if friendly & (1 << (nx + ny * w)):
                count += 1
                if count >= 2:
                    return count
    return count


def handle_comms(current_round: int):
    w = map_info._width
    visible = map_info._bm_visible
    for v, sender_pos, _marker_pos, _marker_id, estimated_turn in comms.get_new_messages():
        sym = comms.decode_sym(v)
        map_info.update_symmetry_from_comms(sym)
        if estimated_turn + 3 < current_round:
            continue
        idx = comms.decode_location(v)
        flag = comms.decode_type(v)
        claimed_targets[flag] |= 1 << idx
        _target_rounds[flag][idx] = estimated_turn
        if map_info.in_bounds(sender_pos):
            sn = sender_pos.x + sender_pos.y * w
            claimed_senders[flag] |= 1 << sn
            _sender_rounds[flag][sn] = estimated_turn
    for i, rounds in enumerate(_target_rounds):
        expired = [
            idx for idx, t in rounds.items()
            if (visible & (1 << idx)) and t + 3 < current_round
        ]
        for idx in expired:
            del rounds[idx]
            claimed_targets[i] &= ~(1 << idx)
    for i in range(len(claimed_senders)):
        expired = [idx for idx, t in _sender_rounds[i].items() if t + 20 < current_round]
        for idx in expired:
            del _sender_rounds[i][idx]
            claimed_senders[i] &= ~(1 << idx)


def _update_crowded_claims(current_round: int):
    # Clear stale crowded claims
    for flag in range(len(crowded_claims)):
        stale = [idx for idx, seen_round in _crowded_claim_rounds[flag].items() if seen_round + 2 < current_round]
        for idx in stale:
            _clear_crowded_claim(flag, idx)
            
    flag = _active_target_flag
    idx = _active_target_idx

    # If we don't have active target position, no need to check crowdedness
    if flag == 0 or idx < 0 or flag == heal.comm_flag:
        return

    # If active target not visible, can't check crowdedness
    bit = 1 << idx
    if not (map_info._bm_visible & bit):
        return

    # If 2 or more ally builders are adjacent to target, check crowdedness
    if _adjacent_friendly_builder_count(idx) >= 2:
        prev_round = _crowded_seen_rounds[flag].get(idx)
        _crowded_seen_rounds[flag][idx] = current_round
        
        # Only mark crowded if target also crowded last turn
        if prev_round == current_round - 1 or (crowded_claims[flag] & bit):
            crowded_claims[flag] |= bit
            _crowded_claim_rounds[flag][idx] = current_round
            
    # Otherwise, no longer crowded
    else:
        _clear_crowded_claim(flag, idx)


def exclude_crowded_claims(flag: int, mask: int) -> int:
    return mask & ~crowded_claims[flag]


def _clear_crowded_claim(flag: int, idx: int):
    crowded_claims[flag] &= ~(1 << idx)
    _crowded_seen_rounds[flag].pop(idx, None)
    _crowded_claim_rounds[flag].pop(idx, None)


def register_active_target(flag: int, target: Position | None):
    global _active_target_flag, _active_target_idx
    if target is None or flag == heal.comm_flag:
        return
    _active_target_flag = flag
    _active_target_idx = target.x + target.y * map_info._width


def clear_active_target():
    global _active_target_flag, _active_target_idx
    _active_target_flag = 0
    _active_target_idx = -1


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
    global _active_target_flag, _active_target_idx

    # Sync round info
    current_round = rc.get_current_round()
    map_info.update()
    _update_harvest_zone()

    # First few builder bots derive explore target from spawn position
    _update_initial_explore(current_round)

    # Check if active target from last turn is crowded by allies
    _update_crowded_claims(current_round)
    clear_active_target()

    # If we broke barrier last turn, try rebuilding first
    pathing.rebuild_broken_barriers(rc)

    # Run state-specific logic
    best_state = select_best_state()
    best_state.run()

    # Road-spam if an enemy is closing in
    try_road_spam()

    # Try healing adjacent building
    heal._do_best_heal()

    # Fall back to healing self
    if rc.can_heal(map_info._my_pos):
        rc.heal(map_info._my_pos)


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

    def _try_mask(candidates):
        mask = candidates
        while mask:
            lsb = mask & -mask
            n = lsb.bit_length() - 1
            p = Position(n % w, n // w)
            if rc.can_build_road(p):
                rc.build_road(p)
                map_info.update_at(p)
                return True
            mask ^= lsb
        return False

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

    if (spam_zone & my_bit) and not (avoid & my_bit) and rc.can_build_road(my_pos):
        rc.build_road(my_pos)
        map_info.update_at(my_pos)
        return True

    if _try_mask(spam_neighbors):
        return True
    return False
