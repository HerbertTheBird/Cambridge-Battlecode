from cambc import Controller, Position, Direction, EntityType, Environment, GameError

from enum import Enum
import random
import sys

import map_info
import pathing
from pathing import Pathing
import comms
import comms_positional
import comms_stats
from units.spawn_plan import get_ray_endpoint, INITIAL_EXPLORE_MAX_STEPS, INITIAL_SPAWN_COUNT

import units.states.explore  as explore
import units.states.disrupt  as disrupt
import units.states.harvest  as harvest
import units.states.route    as route
import units.states.heal     as heal
import units.states.sabotage as sabotage
import units.states.attack   as attack
import units.states.secure   as secure

from log import DRAW_DEBUG, log


rc: Controller
nav: Pathing = None
harvest_radius = 0
_harvest_zone = 0
states = [explore, disrupt, harvest, route, heal, sabotage, attack, secure]
def init(c: Controller):
    global rc, harvest_radius, nav
    rc = c
    nav = Pathing(c)
    harvest_radius = (c.get_map_width() + c.get_map_height()) // 3
    if comms_stats.is_enabled():
        comms_stats.init(c)
    for s in states:
        s.init(c)
    states.sort(key=lambda s: s.MAX_SCORE, reverse=True)

claimed_targets = [0] * (len(states) + 1)   # target bitmask per comm flag (vision-derived)
claimed_senders = [0] * (len(states) + 1)   # sender position bitmask per comm flag (vision-derived)
USE_CLAIM_VISION = True
crowded_claims = [0] * (len(states) + 1)    # locally observed crowded targets per comm flag
_crowded_seen_rounds = [dict() for _ in range(len(states) + 1)]
_crowded_claim_rounds = [dict() for _ in range(len(states) + 1)]
_active_target_flag = 0
_active_target_idx = -1
# Snapshot of (flag, idx) from the previous turn — preserved through this turn's
# score()/run() so states can keep their Voronoi claim sticky on the target
# they were already working on.
_last_active_target_flag = 0
_last_active_target_idx = -1
# Per-flag history: (target_idx, round_set). Survives state transitions like
# heal interruptions, so a bot resuming harvest after a few turns of healing
# still claims its old ore. Capped by STICKY_TARGET_TTL.
_last_target_per_flag: list[tuple[int, int]] = [(-1, -1)] * 9  # 9 = max comm_flag (heal=8) + 1
STICKY_TARGET_TTL = 8


def my_voronoi_mask(flag: int) -> int:
    """my_mask for voronoi_claim with stickiness on the most recent same-flag
    active target — including ones from a few turns ago, so heal/explore
    interruptions don't permanently surrender our existing claim."""
    w = map_info._width
    my_pos = map_info._my_pos
    mask = 1 << (my_pos.x + my_pos.y * w)
    # Last turn's active target gets stickiness regardless of flag history age.
    if _last_active_target_flag == flag and _last_active_target_idx >= 0:
        mask |= 1 << _last_active_target_idx
    # Multi-turn TTL fallback: same-flag target from up to STICKY_TARGET_TTL
    # turns ago. Useful when an interrupting state (e.g. heal) clears
    # _last_active_target_*.
    if 0 <= flag < len(_last_target_per_flag):
        idx, round_set = _last_target_per_flag[flag]
        if idx >= 0 and rc.get_current_round() - round_set <= STICKY_TARGET_TTL:
            mask |= 1 << idx
    return mask


def _clear_crowded_claim(flag: int, idx: int):
    if not USE_CLAIM_VISION:
        return
    crowded_claims[flag] &= ~(1 << idx)
    _crowded_seen_rounds[flag].pop(idx, None)
    _crowded_claim_rounds[flag].pop(idx, None)


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


def _update_crowded_claims():
    if not USE_CLAIM_VISION:
        return
    current_round = rc.get_current_round()

    for flag in range(len(crowded_claims)):
        stale = [idx for idx, seen_round in _crowded_claim_rounds[flag].items() if seen_round + 2 < current_round]
        for idx in stale:
            _clear_crowded_claim(flag, idx)

    if _active_target_flag == 0 or _active_target_idx < 0 or _active_target_flag == heal.comm_flag:
        return

    bit = 1 << _active_target_idx
    if not (map_info._bm_visible & bit):
        return

    flag = _active_target_flag
    idx = _active_target_idx
    if _adjacent_friendly_builder_count(idx) >= 2:
        prev_round = _crowded_seen_rounds[flag].get(idx)
        _crowded_seen_rounds[flag][idx] = current_round
        if prev_round == current_round - 1 or (crowded_claims[flag] & bit):
            crowded_claims[flag] |= bit
            _crowded_claim_rounds[flag][idx] = current_round
    else:
        _clear_crowded_claim(flag, idx)


def exclude_crowded_claims(flag: int, mask: int) -> int:
    if not USE_CLAIM_VISION:
        return mask
    return mask & ~crowded_claims[flag]


def register_active_target(flag: int, target: Position | None):
    global _active_target_flag, _active_target_idx
    if not USE_CLAIM_VISION:
        return
    if target is None or flag == heal.comm_flag:
        return
    _active_target_flag = flag
    _active_target_idx = target.x + target.y * map_info._width
    if 0 <= flag < len(_last_target_per_flag):
        _last_target_per_flag[flag] = (_active_target_idx, rc.get_current_round())

_CLASSIFY_REACH = 8  # Chebyshev steps to consider a friend "near" a state's candidates

def handle_comms():
    w = map_info._width
    my_pos = map_info._my_pos
    my_bit = 1 << (my_pos.x + my_pos.y * w)
    # Use ALL tracked friendlies (stale tracked positions are still useful — they
    # represent "this bot was here recently and is probably nearby still").
    friends = map_info._bm_friendly_bots & ~my_bit

    for i in range(len(claimed_senders)):
        claimed_senders[i] = 0
    for i in range(len(claimed_targets)):
        claimed_targets[i] = 0

    if not friends:
        return

    bm_et = map_info._bm_et
    bm_team = map_info._bm_team
    bm_env = map_info._bm_env
    enemy_idx = 1 - map_info._my_team_idx
    my_team_idx = map_info._my_team_idx
    enemy_team = bm_team[enemy_idx]
    my_team = bm_team[my_team_idx]

    IDX_ROAD = map_info._IDX_ROAD
    IDX_MARKER = map_info._IDX_MARKER
    IDX_CONVEYOR = map_info._IDX_CONVEYOR
    IDX_ARMOURED_CONVEYOR = map_info._IDX_ARMOURED_CONVEYOR
    IDX_BRIDGE = map_info._IDX_BRIDGE
    IDX_SPLITTER = map_info._IDX_SPLITTER
    IDX_HARVESTER = map_info._IDX_HARVESTER
    IDX_FOUNDRY = map_info._IDX_FOUNDRY
    IDX_CORE = map_info._IDX_CORE
    IDX_ENV_ORE_TI = map_info._IDX_ENV_ORE_TI
    IDX_ENV_ORE_AX = map_info._IDX_ENV_ORE_AX

    # Per-state global candidate sets (cheap bitop proxies; ignore per-bot caches
    # like cant_harvest/_too_expensive that vary across bots and would mis-classify
    # other friends if applied from MY perspective).
    attack_candidates = enemy_team & ~bm_et[IDX_ROAD] & ~bm_et[IDX_MARKER]

    sabotage_candidates = (
        (bm_et[IDX_CONVEYOR] | bm_et[IDX_SPLITTER] | bm_et[IDX_BRIDGE])
        & enemy_team
    )

    my_connected = (
        bm_et[IDX_CONVEYOR] | bm_et[IDX_ARMOURED_CONVEYOR]
        | bm_et[IDX_BRIDGE] | bm_et[IDX_SPLITTER] | bm_et[IDX_CORE]
    ) & my_team
    served = map_info.expand_manhattan(my_connected) if my_connected else 0
    my_harvesters = bm_et[IDX_HARVESTER] & my_team
    my_foundries = bm_et[IDX_FOUNDRY] & my_team
    route_candidates = map_info._bm_dead_end | (my_harvesters & ~served) | (my_foundries & ~served)

    ti_ore = bm_env[IDX_ENV_ORE_TI]
    ax_ore = bm_env[IDX_ENV_ORE_AX]
    if _harvest_zone:
        harvest_candidates = ti_ore & ~bm_et[IDX_HARVESTER] & _harvest_zone
        disrupt_candidates = (ti_ore | ax_ore) & ~_harvest_zone
    else:
        harvest_candidates = ti_ore & ~bm_et[IDX_HARVESTER]
        disrupt_candidates = 0

    # Expand each candidate set by K Chebyshev steps to find friends "heading there"
    K = _CLASSIFY_REACH
    expand_cheb = map_info.expand_chebyshev

    def _expand_n(m):
        for _ in range(K):
            m = expand_cheb(m)
        return m

    attack_zone   = _expand_n(attack_candidates)   if attack_candidates   else 0
    sabotage_zone = _expand_n(sabotage_candidates) if sabotage_candidates else 0
    route_zone    = _expand_n(route_candidates)    if route_candidates    else 0
    harvest_zone_ = _expand_n(harvest_candidates)  if harvest_candidates  else 0
    disrupt_zone  = _expand_n(disrupt_candidates)  if disrupt_candidates  else 0

    # Classify each friend by HIGHEST-PRIORITY state whose candidates are within reach.
    # Hades priority order (by MAX_SCORE descending): attack(9) > heal(8) > route(7.75)
    # > secure(7.5) > harvest(4) > disrupt(2) > explore(1) > sabotage(0).
    # heal uses uid space (not positions) and secure._my_claims uses _bm_friendly_bots
    # directly — both leave their claimed_senders[] at 0, so we skip them here.
    attack_friends   = friends & attack_zone
    remaining = friends & ~attack_friends
    route_friends    = remaining & route_zone
    remaining &= ~route_friends
    harvest_friends  = remaining & harvest_zone_
    remaining &= ~harvest_friends
    disrupt_friends  = remaining & disrupt_zone
    remaining &= ~disrupt_friends
    sabotage_friends = remaining & sabotage_zone
    explore_friends  = remaining & ~sabotage_friends

    claimed_senders[attack.comm_flag]   = attack_friends
    claimed_senders[sabotage.comm_flag] = sabotage_friends
    claimed_senders[route.comm_flag]    = route_friends
    claimed_senders[harvest.comm_flag]  = harvest_friends
    claimed_senders[disrupt.comm_flag]  = disrupt_friends
    claimed_senders[explore.comm_flag]  = explore_friends
    # heal flag uses uid space, not positions — leave at 0.
    # secure._my_claims uses _bm_friendly_bots directly — leave at 0.

    # Explore seeds: friend positions push my BFS frontier away from teammates.
    # Also add an extrapolated point along (my_core -> friend) outward by a
    # large distance (toward map edge), approximating where the friend is
    # likely heading. Markers used to convey actual explore TARGETS; without
    # them this heuristic gives the BFS roughly the same "claimed corridor"
    # information so the explore frontier stays away from teammates' rays.
    explore_seeds = friends
    my_core = map_info._my_core
    if my_core is not None and friends:
        cx, cy = my_core.x, my_core.y
        h = map_info._height
        m = friends
        while m:
            lsb = m & -m
            n = lsb.bit_length() - 1
            fx, fy = n % w, n // w
            dx = fx - cx
            dy = fy - cy
            if dx or dy:
                # Walk outward in (dx, dy) direction by ~map size, clamped to bounds.
                steps = max(abs(dx), abs(dy))
                scale = max(w, h) // max(1, steps) if steps else 0
                ex = max(0, min(w - 1, fx + dx * scale))
                ey = max(0, min(h - 1, fy + dy * scale))
                explore_seeds |= 1 << (ex + ey * w)
            m ^= lsb
    claimed_targets[explore.comm_flag] = explore_seeds
def draw_mask(mask, r, g, b):
    if not DRAW_DEBUG:
        return
    for p in map_info.iter_mask(mask):
        rc.draw_indicator_dot(p, r, g, b)

_harvest_zone_final = False

# First-tick ray explore target (derived from spawn tile relative to core).
# Cleared by explore state once reached.
_initial_explore_target: Position | None = None
_initial_explore_done = False
_initial_explore_round = -1  # round the target was set; used for timeout
INITIAL_EXPLORE_TIMEOUT = 30

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

def run():
    global _harvest_zone, _harvest_zone_final, _active_target_flag, _active_target_idx
    global _last_active_target_flag, _last_active_target_idx
    global _initial_explore_target, _initial_explore_done, _initial_explore_round
    map_info.update(recompute=False)
    if not _initial_explore_done:
        if rc.get_current_round() > INITIAL_SPAWN_COUNT + 1:
            _initial_explore_done = True
        elif map_info._my_core is not None:
            spawn_dir = map_info.direction_to(map_info._my_core, map_info._my_pos)
            _initial_explore_target = get_ray_endpoint(
                map_info._my_pos, spawn_dir, map_info._width, map_info._height,
                max_steps=INITIAL_EXPLORE_MAX_STEPS,
            )
            _initial_explore_round = rc.get_current_round()
            _initial_explore_done = True
    # Auto-clear stale initial target if we couldn't reach it in time
    if (
        _initial_explore_target is not None
        and rc.get_current_round() - _initial_explore_round >= INITIAL_EXPLORE_TIMEOUT
    ):
        _initial_explore_target = None
    _update_crowded_claims()
    # Snapshot last turn's target BEFORE reset so this turn's score()/run() can
    # check stickiness via my_voronoi_mask().
    _last_active_target_flag = _active_target_flag
    _last_active_target_idx = _active_target_idx
    _active_target_flag = 0
    _active_target_idx = -1
    map_info.recompute_derived()
    pathing.rebuild_broken_barriers(rc)
    if map_info._my_core and not _harvest_zone_final:
        if map_info._solved_sym and map_info._predicted_enemy_core is not None:
            # Symmetry solved — compute Voronoi partition once
            _harvest_zone = _compute_voronoi_harvest_zone()
            _harvest_zone_final = True
        elif not _harvest_zone:
            # Fallback: radius-based until symmetry is solved
            w = map_info._width
            zone = 1 << (map_info._my_core.x + map_info._my_core.y * w)
            for _ in range(harvest_radius):
                zone = map_info.expand_chebyshev(zone)
            _harvest_zone = zone
    handle_comms()
    best_state = None
    best_score = 0
    for i in states:
        if best_score >= i.MAX_SCORE:
            break
        score = i.score()
        if score > best_score:
            best_score = score
            best_state = i
    best_state.run()
    # Heal the most damaged adjacent building, fall back to self
    heal._do_best_heal()
    if rc.can_heal(map_info._my_pos):
        rc.heal(map_info._my_pos)
    # if rc.get_tile_building_id(rc.get_position()) and rc.get_team(rc.get_tile_building_id(rc.get_position())) != rc.get_team() and rc.can_fire(rc.get_position()):
    #     rc.fire(rc.get_position())
