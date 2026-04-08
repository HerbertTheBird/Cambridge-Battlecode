from itertools import chain

from cambc import Controller, Direction, Environment, GameConstants, Position, EntityType, ResourceType, Team

from globals import DIRECTIONS, ALL_DIRECTIONS, CARDINAL_DIRECTIONS, CONVEYOR_TYPES, TURRET_TYPES, State, Symmetry, INF

from helpers import (
    get_nearest_core_tile,
    is_core_tile,
    is_foundry_position,
    is_marker_building,
    on_map,
)

from units.builder.build import (
    can_build_launcher_here,
    safe_build_launcher,
    safe_destroy,
    safe_place_marker,
)
from vision import VisionCache

from log import log, log_time

_INTERCEPT_RESOURCES = (ResourceType.TITANIUM, ResourceType.REFINED_AXIONITE)

_INTERCEPT_MAX_TRAVEL_DIST_SQ = 13

_INTERCEPT_THREAT_RADIUS_SQ = GameConstants.SENTINEL_VISION_RADIUS_SQ

KNOWN_CORE_INTERCEPT_TRIGGER_DIST_SQ = 50

def count_ally_turrets_covering(ct: Controller, vc: VisionCache, target_pos: Position) -> int:
    """Count ally turrets whose raw attack pattern covers target_pos."""
    count = 0
    for (eid, etype, tpos) in vc.ally_turrets:
        if target_pos in ct.get_attackable_tiles_from(tpos, ct.get_direction(eid), etype):
            count += 1
    return count

def get_best_bridge_build_pos(harvester_pos: Position, core_pos: Position | None, ct: Controller, my_team: Team, map_obj, vc: VisionCache, opposite_ore: set | None = None) -> Position | None:
    """Return the best adjacent tile to harvester_pos for starting a conveyor chain toward core_pos.
    Deprioritizes positions adjacent to opposite_ore (the 'wrong' ore type).
    Also considers enemy passable buildings (conveyors, roads) as candidates with lower priority.
    If vc is provided and enemies are visible, refuses to build over ally sentinels."""
    if core_pos is None:
        return None
    log(f"  bridge_build_pos: harvester={harvester_pos} core={core_pos}")
    enemies_visible = len(vc.enemy_units) > 0
    best = None
    best_dist = INF
    best_conflicts = True  # whether best has adjacent conflict ore
    best_has_enemy = True  # whether best requires destroying an enemy building
    width = map_obj.width
    height = map_obj.height
    for d in CARDINAL_DIRECTIONS:
        pos = harvester_pos.add(d)
        if not on_map(pos, width, height):
            log(f"    {pos} ({d}): SKIP off map")
            continue
        if not ct.is_in_vision(pos):
            log(f"    {pos} ({d}): SKIP not in vision")
            continue
        if map_obj.get_tile_env(pos) == Environment.WALL:
            log(f"    {pos} ({d}): SKIP wall")
            continue
        bid = ct.get_tile_building_id(pos)
        has_enemy = False
        if bid is not None:
            btype = ct.get_entity_type(bid)
            bteam = ct.get_team(bid)
            # Don't destroy ally turrets when enemies are visible
            if (btype in TURRET_TYPES or btype == EntityType.LAUNCHER) and bteam == my_team and enemies_visible:
                log(f"    {pos} ({d}): SKIP ally turret (enemies visible)")
                continue
            if btype == EntityType.MARKER or (btype in (EntityType.ROAD, EntityType.BARRIER) and bteam == my_team) or (btype in TURRET_TYPES and bteam == my_team):
                pass  # can build over these freely
            elif bteam != my_team and ct.is_tile_passable(pos):
                has_enemy = True  # enemy passable building — can fire on it
            else:
                log(f"    {pos} ({d}): SKIP blocked by {btype}")
                continue
        dist = pos.distance_squared(core_pos)
        has_conflict = False
        if opposite_ore:
            for d2 in CARDINAL_DIRECTIONS:
                neighbor = pos.add(d2)
                if neighbor in opposite_ore:
                    has_conflict = True
                    break
        # Prefer: no enemy > enemy; no conflict > conflict; closer distance
        if ((not has_enemy and best_has_enemy)
            or (has_enemy == best_has_enemy and not has_conflict and best_conflicts)
            or (has_enemy == best_has_enemy and has_conflict == best_conflicts and dist < best_dist)):
            log(f"    {pos} ({d}): NEW BEST dist²={dist} conflict={has_conflict} enemy={has_enemy}")
            best_dist = dist
            best = pos
            best_conflicts = has_conflict
            best_has_enemy = has_enemy
        else:
            log(f"    {pos} ({d}): WORSE dist²={dist} conflict={has_conflict} enemy={has_enemy} (best={best_dist} best_conflict={best_conflicts})")
    log(f"  bridge_build_pos result: {best}")
    return best

def get_barrier_targets(ore_pos: Position, core_pos: Position | None, ct: Controller, map_obj) -> list[Position]:
    """Return cardinal positions around ore_pos that need barriers,
    sorted by decreasing distance from core (farthest first).
    Only titanium ore gets defended this way.
    Skips positions with conveyors, turrets, or existing barriers."""
    if map_obj.get_tile_env(ore_pos) != Environment.ORE_TITANIUM:
        return []
    my_team = ct.get_team()
    targets = []
    width = map_obj.width
    height = map_obj.height
    for d in CARDINAL_DIRECTIONS:
        adj = ore_pos.add(d)
        if not on_map(adj, width, height) or not ct.is_in_vision(adj):
            continue
        if map_obj.get_tile_env(adj) == Environment.WALL:
            continue
        adj_bid = ct.get_tile_building_id(adj)
        builder_id = ct.get_tile_builder_bot_id(adj)
        if builder_id is not None and builder_id != ct.get_id():
            continue
        if adj_bid is not None:
            etype = ct.get_entity_type(adj_bid)
            if etype in CONVEYOR_TYPES or etype in TURRET_TYPES or etype == EntityType.LAUNCHER or etype == EntityType.BARRIER or etype == EntityType.HARVESTER or etype == EntityType.FOUNDRY:
                continue
            adj_team = ct.get_team(adj_bid)
            if adj_team != my_team and etype != EntityType.MARKER:
                continue
        targets.append(adj)
    if core_pos is not None:
        targets.sort(key=lambda p: p.distance_squared(core_pos), reverse=True)
    return targets

def attack_cost_to_destroy(ct: Controller, bid) -> int:
    """Titanium cost to destroy a building by attacking it with a builder bot."""
    hp = ct.get_hp(bid)
    shots = (hp + GameConstants.BUILDER_BOT_ATTACK_DAMAGE - 1) // GameConstants.BUILDER_BOT_ATTACK_DAMAGE
    return shots * GameConstants.BUILDER_BOT_ATTACK_COST[0]

def _get_intercept_output_state(output: Position, ct: Controller, my_team: Team, global_titanium: int, gunner_cost: int, sentinel_cost: int, map_obj, enemy_core_pos: Position | None = None) -> tuple[int, int | None, EntityType | None, Team | None]:
    """Check if the output pos is buildable for intercept.
    Returns 0 = invalid, 1 = enemy building we can afford to kill (lower priority), 2 = fully valid.
    Rejects positions where an ally conveyor feeds an ally turret downstream."""
    if not ct.is_in_vision(output):
        return 0, None, None, None
    if map_obj.get_tile_env(output) == Environment.WALL:
        return 0, None, None, None
    turret_cost = gunner_cost if get_best_turret_type(output, enemy_core_pos, ct, None, map_obj) == EntityType.GUNNER else sentinel_cost
    if global_titanium < turret_cost:
        return 0, None, None, None
    bid = ct.get_tile_building_id(output)
    if bid is None:
        return 2, None, None, None

    etype = ct.get_entity_type(bid)
    team = ct.get_team(bid)
    if etype == EntityType.MARKER:
        return 2, bid, etype, team

    if team == my_team:
        if etype == EntityType.ROAD:
            return 2, bid, etype, team
        if etype in CONVEYOR_TYPES:
            # Don't destroy ally conveyors that feed ally turrets
            if map_obj.feeds_ally_turret(output, my_team):
                return 0, bid, etype, team
            return 1, bid, etype, team
        if etype == EntityType.BARRIER:
            return 1, bid, etype, team
    else:
        if etype in (EntityType.ROAD, *CONVEYOR_TYPES):
            if etype in CONVEYOR_TYPES and map_obj.feeds_ally_turret(output, my_team):
                return 0, bid, etype, team
            if global_titanium < attack_cost_to_destroy(ct, bid) + turret_cost:
                return 0, bid, etype, team
            return 1, bid, etype, team

    return 0, bid, etype, team

def _has_matching_ally_intercept_turret(bid: int | None, etype: EntityType | None, team: Team | None, ct: Controller, pos: Position, direction: Direction, my_team: Team, enemy_core_pos: Position | None) -> bool:
    """True if the cached tile data already matches the intercept turret we would build."""
    if bid is not None:
        return (
            team == my_team
            and etype in TURRET_TYPES
            and etype == get_best_turret_type(pos, enemy_core_pos, ct, None)
            and ct.get_direction(bid) == direction
        )
    return False

def find_intercept_pos(ct: Controller, my_pos: Position, my_team: Team, vc: VisionCache, threat_pos: Position, map_obj, enemy_only: bool = False, global_titanium: int = 0, enemy_core_pos: Position | None = None) -> Position | None:
    """Find the nearest position that is the output of a conveyor-type entity."""
    best_pos = None
    best_dist = INF
    fallback_pos = None
    fallback_dist = INF
    gunner_cost = ct.get_gunner_cost()[0]
    sentinel_cost = ct.get_sentinel_cost()[0]

    def _is_fed(output):
        return map_obj.has_adjacent_ore_harvester(output) or map_obj.has_valid_input_chain(output)

    def _is_candidate(output: Position) -> bool:
        return (
            my_pos.distance_squared(output) <= _INTERCEPT_MAX_TRAVEL_DIST_SQ
            and output.distance_squared(threat_pos) <= _INTERCEPT_THREAT_RADIUS_SQ
        )

    def _add_candidate(output: Position):
        if _is_candidate(output):
            candidate_outputs.add(output)

    def _consider(output):
        nonlocal best_pos, best_dist, fallback_pos, fallback_dist

        dist = my_pos.distance_squared(output)
        if dist >= best_dist:
            return

        validity, bid, etype, team = _get_intercept_output_state(
            output, ct, my_team, global_titanium,
            gunner_cost, sentinel_cost,
            map_obj=map_obj, enemy_core_pos=enemy_core_pos
        )
        if validity == 0:
            return

        if validity == 1 and (dist >= fallback_dist or best_dist != INF):
            return

        if not _is_fed(output):
            return

        direction = get_sentinel_direction(output, threat_pos, ct, map_obj)
        if direction is None:
            return

        if _has_matching_ally_intercept_turret(bid, etype, team, ct, output, direction, my_team, enemy_core_pos):
            return

        if etype == EntityType.HARVESTER or etype == EntityType.CORE:
            return

        builder_id = ct.get_tile_builder_bot_id(output)
        if builder_id is not None and output != my_pos:
            return

        if validity == 2:
            if dist < best_dist:
                best_dist = dist
                best_pos = output
        else:
            if dist < fallback_dist:
                fallback_dist = dist
                fallback_pos = output

    width = map_obj.width
    height = map_obj.height

    candidate_outputs: set[Position] = set()

    # Harvesters on titanium ore
    for (bid, pos, _team) in vc.harvesters:
        if map_obj.get_tile_env(pos) != Environment.ORE_TITANIUM:
            continue
        for d in CARDINAL_DIRECTIONS:
            adj = pos.add(d)
            if on_map(adj, width, height):
                _add_candidate(adj)

    # Conveyors
    conveyors = vc.enemy_conveyors if enemy_only else chain(vc.ally_conveyors, vc.enemy_conveyors)
    for (bid, etype, pos) in conveyors:
        resource = ct.get_stored_resource(bid)
        if resource not in _INTERCEPT_RESOURCES:
            tracked = map_obj.get_recent_conveyor_resources(pos)
            if not any(r in tracked for r in _INTERCEPT_RESOURCES):
                continue

        if not map_obj.has_valid_input_chain(pos):
            continue

        if etype is EntityType.BRIDGE:
            output = ct.get_bridge_target(bid)
        else:
            output = pos.add(ct.get_direction(bid))

        _add_candidate(output)

    candidates = list(candidate_outputs)
    candidates.sort(key=lambda p: p.distance_squared(threat_pos))  # prioritize outputs closer to the threat

    for output in candidates:
        _consider(output)

    return best_pos if best_pos is not None else fallback_pos

def is_valid_intercept_pos(pos: Position, ct: Controller, my_team: Team, threat_pos: Position, my_pos: Position, map_obj, global_titanium: int = 0, enemy_core_pos: Position | None = None) -> bool:
    """Lightweight check: is an existing intercept position still valid?
    Runs the same checks as _consider() in find_intercept_pos but for a single position."""
    if not ct.is_in_vision(pos):
        return True  # can't see it, assume still valid
    gunner_cost = ct.get_gunner_cost()[0]
    sentinel_cost = ct.get_sentinel_cost()[0]
    validity, bid, etype, team = _get_intercept_output_state(
        pos, ct, my_team, global_titanium, gunner_cost, sentinel_cost,
        map_obj=map_obj, enemy_core_pos=enemy_core_pos
    )
    if validity == 0:
        return False
    if pos.distance_squared(threat_pos) > _INTERCEPT_THREAT_RADIUS_SQ:
        return False
    if not (map_obj.has_adjacent_ore_harvester(pos) or map_obj.has_valid_input_chain(pos)):
        return False
    direction = get_sentinel_direction(pos, threat_pos, ct, map_obj)
    if direction is None:
        return False
    if _has_matching_ally_intercept_turret(bid, etype, team, ct, pos, direction, my_team, enemy_core_pos):
        return False
    if etype in (EntityType.HARVESTER, EntityType.CORE):
        return False
    builder_id = ct.get_tile_builder_bot_id(pos)
    if builder_id is not None and pos != my_pos:
        return False
    return True

def should_intercept(vc: VisionCache, my_pos: Position, core_pos: Position | None = None) -> bool:
    """True if we see an enemy core, an enemy combat unit, 2+ enemy builder bots,
    or 1+ enemy builder bot while within distance² 25 of our own core."""
    near_core = core_pos is not None and my_pos.distance_squared(core_pos) <= 25
    _BB = EntityType.BUILDER_BOT
    enemy_builders = 0
    for (_eid, etype, _pos) in vc.enemy_units:
        if etype is not _BB:
            return True  # CORE or COMBAT_TYPE
        enemy_builders += 1
        if enemy_builders >= 2 or near_core:
            return True
    return False

def get_nearest_enemy_threat_pos(vc: VisionCache, my_pos: Position) -> tuple[Position, bool] | None:
    """Return (position, is_core) of the highest-priority nearest enemy threat.
    Priority: enemy core > enemy combat > enemy builder bot.
    Returns None if no threat found."""
    if not vc.enemy_units:
        return None
    _CORE = EntityType.CORE
    _BB = EntityType.BUILDER_BOT
    best_pos = None
    best_dist = INF
    best_prio = 3  # lower = higher priority
    for (_eid, etype, pos) in vc.enemy_units:
        if etype is _CORE:
            prio = 0
        elif etype is not _BB:
            prio = 1  # COMBAT_TYPE
        else:
            prio = 2
        dist = my_pos.distance_squared(pos)
        if prio < best_prio or (prio == best_prio and dist < best_dist):
            best_prio = prio
            best_dist = dist
            best_pos = pos
    if best_pos is None:
        return None
    return (best_pos, best_prio == 0)

def get_blocked_sentinel_directions(intercept_pos: Position, ct: Controller, map_obj) -> set:
    """Return the set of cardinal directions a sentinel at intercept_pos cannot face.
    A direction is only blocked if the feeder on that side is the ONLY valid feeder.
    With 2+ valid feeders, no directions are blocked.
    If map_obj is provided, only counts feeders with valid input chains."""
    feeder_dirs = []
    for feeder_pos, feeder_type in map_obj.get_feeders(intercept_pos):
        if feeder_type == EntityType.HARVESTER and intercept_pos.distance_squared(feeder_pos) == 1:
            feeder_dirs.append(intercept_pos.direction_to(feeder_pos))
        elif (feeder_type in CONVEYOR_TYPES
              and intercept_pos.distance_squared(feeder_pos) == 1
              and map_obj.has_valid_input_chain(feeder_pos)):
            feeder_dirs.append(intercept_pos.direction_to(feeder_pos))
    if len(feeder_dirs) == 1:
        return {feeder_dirs[0]}
    return set()

def get_sentinel_direction(intercept_pos: Position, enemy_pos: Position, ct: Controller, map_obj) -> Direction | None:
    """Pick the best direction for a sentinel at intercept_pos facing enemy_pos,
    avoiding directions blocked by feeding conveyors/harvesters."""
    blocked = get_blocked_sentinel_directions(intercept_pos, ct, map_obj)

    desired = intercept_pos.direction_to(enemy_pos)
    if desired not in blocked:
        return desired
    # Try rotating to find a non-blocked direction
    for rot in [desired.rotate_left(), desired.rotate_right(),
                desired.rotate_left().rotate_left(), desired.rotate_right().rotate_right()]:
        if rot not in blocked:
            return rot
    return None

def is_gunner_position(
    core_pos: Position | None,
    pos: Position,
    ct: Controller,
    primary_threat: Position | None,
    map_obj,
) -> bool:
    """
    True if pos is a good gunner location.

    Satisfies one of:
    1. Original heuristic: near enemy core
    2. Has line-of-sight to primary_threat
    """
    if core_pos is not None:
        dist = core_pos.distance_squared(pos)
        if 2 < dist <= 18:
            return True

    if primary_threat is None or map_obj is None or not ct.is_in_vision(primary_threat):
        return False

    if ct.get_tile_builder_bot_id(primary_threat) is not None:
        return False

    my_team = ct.get_team()
    width = map_obj.width
    height = map_obj.height

    for d in DIRECTIONS:
        dx, dy = d.delta()
        max_range = 3 if d in CARDINAL_DIRECTIONS else 2

        x, y = pos.x, pos.y
        for _ in range(max_range):
            x += dx
            y += dy

            if not on_map(Position(x, y), width, height):
                break

            cur = Position(x, y)

            if map_obj.get_tile_env(cur) == Environment.WALL:
                break

            if cur == primary_threat:
                return True

            bbid = ct.get_tile_builder_bot_id(cur)
            if bbid is not None:
                if ct.get_team(bbid) == my_team:
                    break
                continue

            bid = ct.get_tile_building_id(cur)
            if bid is not None:
                etype = ct.get_entity_type(bid)
                team = ct.get_team(bid)

                if etype == EntityType.MARKER or etype == EntityType.ROAD:
                    continue

                if team == my_team:
                    break
                continue

    return False

def get_best_turret_type(pos: Position, enemy_core_pos: Position | None, ct: Controller, primary_threat: Position | None = None, map_obj = None) -> EntityType:
    """Return the preferred turret type for an intercept build at pos."""
    if is_gunner_position(enemy_core_pos, pos, ct, primary_threat, map_obj):
        return EntityType.GUNNER
    return EntityType.SENTINEL

def build_best_turret(ct: Controller, pos: Position, direction: Direction, enemy_core_pos: Position | None, primary_threat: Position | None = None, map_obj = None) -> bool:
    """Try to build a gunner (if valid position near enemy core) or sentinel at pos.
    Returns True if a turret was built."""
    turret_type = get_best_turret_type(pos, enemy_core_pos, ct, primary_threat, map_obj)
    if turret_type == EntityType.GUNNER and ct.can_build_gunner(pos, direction):
        gunner_direction = pos.direction_to(primary_threat) if primary_threat is not None else direction
        ct.build_gunner(pos, gunner_direction)
        log(f"BUILT gunner at {pos} facing {gunner_direction}")
        return True
    if turret_type == EntityType.SENTINEL and ct.can_build_sentinel(pos, direction):
        ct.build_sentinel(pos, direction)
        log(f"BUILT sentinel at {pos} facing {direction}")
        return True
    return False

def find_nearest_titanium_conveyor(ct: Controller, my_pos: Position, vc: VisionCache, map_obj, my_team: Team, target_foundry: Position | None = None) -> Position | None:
    best_pos = None
    best_dist = INF
    for (bid, etype, pos) in vc.ally_conveyors:
        carries_titanium = ct.get_stored_resource(bid) == ResourceType.TITANIUM
        if not carries_titanium:
            carries_titanium = map_obj.input_chain_reaches_resource(pos, ResourceType.TITANIUM)
        if not carries_titanium:
            continue
        if map_obj.feeds_other_ally_foundry(pos, my_team, target_foundry):
            continue
        dist = my_pos.distance_squared(pos)
        if dist < best_dist:
            best_dist = dist
            best_pos = pos
    return best_pos

def try_heal(ct: Controller, my_pos: Position, my_team: Team, width: int, height: int):
    """Heal nearby friendly builder bots or buildings, prioritizing builder bots."""
    if ct.get_global_resources()[0] < 25 or ct.get_action_cooldown() > 0:
        return
    
    best_heal_pos = None
    best_can_heal_builder = False
    best_heal_amount = 0
    
    for direction in ALL_DIRECTIONS:
        heal_pos = my_pos.add(direction)
        if not on_map(heal_pos, width, height):
            continue
        
        bbid = ct.get_tile_builder_bot_id(heal_pos)
        bid = ct.get_tile_building_id(heal_pos)
        
        heal_amount = 0
        can_heal_builder = False
        
        if bbid is not None and ct.get_team(bbid) == my_team:
            deficit = ct.get_max_hp(bbid) - ct.get_hp(bbid)
            heal_amount += min(deficit, GameConstants.HEAL_AMOUNT)
            if deficit > 0:
                can_heal_builder = True
        
        if not can_heal_builder and best_can_heal_builder:
            continue
            
        if (
            bid is not None
            and ct.get_team(bid) == my_team
            and not is_marker_building(ct, bid)
            and ct.get_entity_type(bid) != EntityType.ROAD
        ):
            deficit = ct.get_max_hp(bid) - ct.get_hp(bid)
            heal_amount += min(deficit, GameConstants.HEAL_AMOUNT)
            
        if can_heal_builder and not best_can_heal_builder:
            best_heal_pos = heal_pos
            best_can_heal_builder = True
            best_heal_amount = heal_amount
        elif can_heal_builder == best_can_heal_builder and heal_amount > best_heal_amount:
            best_heal_pos = heal_pos
            best_heal_amount = heal_amount
            
    if not best_can_heal_builder and ct.get_global_resources()[0] < 50:
        return

    if best_heal_pos is not None and ct.can_heal(best_heal_pos):
        ct.heal(best_heal_pos)
        log(f"HEAL at {best_heal_pos} amount={best_heal_amount} can_heal_builder={best_can_heal_builder}")

def get_visible_allied_launchers(player, ct: Controller) -> list[tuple[int, Position]]:
    launchers = []
    for bid in ct.get_nearby_buildings():
        if ct.get_team(bid) != player.my_team or ct.get_entity_type(bid) != EntityType.LAUNCHER:
            continue
        launchers.append((bid, ct.get_position(bid)))
    return launchers

def find_launcher_site(player, ct: Controller, my_pos: Position, vc: VisionCache, anchors: list[Position], objective: Position | None, min_spacing_sq: int) -> Position | None:
    if not anchors or player.global_titanium < ct.get_launcher_cost()[0]:
        return None

    visible_launchers = get_visible_allied_launchers(player, ct)
    if any(lp.distance_squared(anchor) <= min_spacing_sq for anchor in anchors for _, lp in visible_launchers):
        return None

    anchor_set = set(anchors)
    best_site = None
    best_score = -INF

    for anchor in anchors:
        for d in DIRECTIONS:
            candidate = anchor.add(d)
            if not on_map(candidate, player.map.width, player.map.height):
                continue
            if candidate in anchor_set:
                continue
            if my_pos.distance_squared(candidate) > 2 or not ct.is_in_vision(candidate):
                continue

            env = player.map.get_tile_env(candidate)
            if env == Environment.WALL or env in (Environment.ORE_TITANIUM, Environment.ORE_AXIONITE):
                continue
            if is_core_tile(player.core_pos, candidate) or is_foundry_position(player.core_pos, candidate):
                continue

            bid = ct.get_tile_building_id(candidate)
            if bid is not None:
                etype = ct.get_entity_type(bid)
                team = ct.get_team(bid)
                if etype in CONVEYOR_TYPES or etype in (EntityType.HARVESTER, EntityType.FOUNDRY, EntityType.CORE, EntityType.LAUNCHER):
                    continue
                if etype in TURRET_TYPES:
                    continue
                if team != player.my_team and etype != EntityType.MARKER and not ct.is_tile_passable(candidate):
                    continue

            if not can_build_launcher_here(candidate, ct, my_pos, player.my_team, player.map, vc):
                continue

            coverage = sum(1 for other in anchors if candidate.distance_squared(other) <= 2)
            score = coverage * 20
            if objective is not None:
                score -= candidate.distance_squared(objective)
            if player.harvest_ore_pos is not None and candidate.distance_squared(player.harvest_ore_pos) <= 2:
                score += 8
            if bid is not None:
                score -= 3

            if score > best_score:
                best_score = score
                best_site = candidate

    return best_site

def try_build_support_launcher(player, ct: Controller, my_pos: Position, vc: VisionCache, anchors: list[Position], objective: Position | None, min_spacing_sq: int = 8) -> bool:
    if ct.get_action_cooldown() > 0:
        return False

    site = find_launcher_site(player, ct, my_pos, vc, anchors, objective, min_spacing_sq)
    if site is None:
        return False

    if ct.get_tile_building_id(site) is not None:
        if not safe_destroy(player, ct, site, vc):
            return False

    if safe_build_launcher(player, ct, site):
        log(f"BUILT launcher at {site} for anchors {anchors}")
        return True
    return False

def try_issue_launcher_order(player, ct: Controller, my_pos: Position) -> bool:
    objective = player.nav.original_destination if player.nav.destination_type == "adjacent" else player.nav.destination
    if objective is None:
        return False

    current_dist = my_pos.distance_squared(objective)
    goal_dist_limit = 2 if player.nav.destination_type == "adjacent" else 0
    if current_dist <= goal_dist_limit:
        return False

    best_order = None
    best_score = -INF

    for _launcher_id, launcher_pos in get_visible_allied_launchers(player, ct):
        if launcher_pos.distance_squared(my_pos) > 2:
            continue

        for target in ct.get_attackable_tiles_from(launcher_pos, Direction.NORTH, EntityType.LAUNCHER):
            if target == my_pos or not ct.is_in_vision(target) or not ct.is_tile_passable(target):
                continue

            target_dist = target.distance_squared(objective)
            improvement = current_dist - target_dist
            if improvement < 5:
                continue
            if player.nav.destination_type == "adjacent" and target_dist > 2 and improvement < 10:
                continue

            score = improvement * 5 - target_dist
            if target_dist <= 2:
                score += 25
            if player.attack_target is not None and target.distance_squared(player.attack_target) <= 2:
                score += 8
            if score > best_score:
                best_score = score
                best_order = (launcher_pos, target)

    if best_order is None:
        return False

    launcher_pos, target = best_order
    for d in ALL_DIRECTIONS:
        marker_pos = launcher_pos.add(d)
        if not on_map(marker_pos, player.map.width, player.map.height) or not ct.is_in_vision(marker_pos):
            continue
        if safe_place_marker(player, ct, marker_pos, player.comms.encode_launch_order(ct.get_id(), target)):
            log(f"Requested launcher shortcut via {launcher_pos} to {target}")
            return True
    return False

def clear_state(player):
    player.state = State.EXPLORE
    player.nav.clear_destination()
    player.harvest_ore_type = None
    player.harvest_ore_pos = None
    player.foundry_pos = None
    player.timeout_turns = 0

def can_start_harvest_chain_now(player, ct: Controller, my_pos: Position, target: Position, vc: VisionCache) -> bool:
    """Lightweight pre-check so we do not enter START_HARVEST_CHAIN for a target
    that is already visibly impossible this turn."""
    if not ct.is_in_vision(target):
        return True

    barrier_count = 0
    for d in CARDINAL_DIRECTIONS:
        adj = target.add(d)
        if not on_map(adj, player.map.width, player.map.height) or not ct.is_in_vision(adj):
            continue
        env = player.map.get_tile_env(adj)
        adj_bid = ct.get_tile_building_id(adj)
        if env == Environment.WALL:
            barrier_count += 1
        elif adj_bid is not None:
            adj_etype = ct.get_entity_type(adj_bid)
            adj_team = ct.get_team(adj_bid)
            if adj_team != player.my_team and (adj_etype == EntityType.BARRIER or adj_etype in TURRET_TYPES):
                barrier_count += 1
    return barrier_count < 4

def get_sabotage_target_priority(player, ct: Controller, pos: Position, vc: VisionCache, ally_bot_positions: set | None = None) -> int:
    """Check if an enemy conveyor/bridge at pos is a valid sabotage target.
    Follows the chain in both directions to validate.
    Returns 0 = not valid, 1 = valid chain, 2 = feeds enemy turret, 3 = feeds enemy core."""
    bid = ct.get_tile_building_id(pos)
    if bid is None:
        return 0
    team = ct.get_team(bid)
    btype = ct.get_entity_type(bid)
    if btype != EntityType.BRIDGE and btype != EntityType.CONVEYOR:
        return 0

    if player.map is None:
        return 1

    if player.map.feeds_ally_turret(pos, player.my_team):
        return 0

    if ally_bot_positions is None:
        ally_bot_positions = {p for (_, p) in vc.ally_builder_bots}
    target_damage = ct.get_max_hp(bid) - ct.get_hp(bid)

    def is_low_priority_tile(check_pos: Position) -> bool:
        if check_pos not in ally_bot_positions:
            return False
        check_bid = ct.get_tile_building_id(check_pos)
        if check_bid is None:
            return False
        check_damage = ct.get_max_hp(check_bid) - ct.get_hp(check_bid)
        return check_damage >= target_damage + 4 and target_damage <= 10

    def adj_to_ti_harvester(p: Position) -> bool:
        for d in CARDINAL_DIRECTIONS:
            adj = p.add(d)
            if not on_map(adj, player.map.width, player.map.height):
                continue
            entity = player.map.get_tile_entity(adj)
            if entity is None or entity[1] != EntityType.HARVESTER:
                continue
            env = player.map.get_tile_env(adj)
            if env == Environment.ORE_TITANIUM:
                return True
        return False

    if adj_to_ti_harvester(pos) and team != player.my_team:
        return 1

    def get_input_prio(pos: Position) -> int:
        queue = [pos]
        visited_in = {pos}
        while queue:
            cur = queue.pop()
            feeders = player.map.get_feeders(cur)
            if not feeders:
                return 0
            for input_pos, input_type in feeders:
                if input_type == EntityType.HARVESTER:
                    if player.map.get_tile_env(input_pos) == Environment.ORE_TITANIUM:
                        return 1
                    continue
                if input_pos in visited_in:
                    continue
                visited_in.add(input_pos)
                if is_low_priority_tile(input_pos):
                    return 0
                if not ct.is_in_vision(input_pos):
                    continue
                entity = player.map.get_tile_entity(input_pos)
                if entity is None or entity[1] not in CONVEYOR_TYPES:
                    return 0
                queue.append(input_pos)
        return 1

    input_prio = get_input_prio(pos)

    if input_prio == 0:
        return 0

    cur = pos
    visited_out = {pos}
    while player.map.has_conveyor_output(cur):
        next_pos = player.map.get_conveyor_output(cur)
        if next_pos is None:
            break
        if next_pos in visited_out:
            break
        visited_out.add(next_pos)
        if is_low_priority_tile(next_pos):
            return 0
        entity = player.map.get_tile_entity(next_pos)
        if entity is None:
            break
        _, etype, _team = entity
        if etype in CONVEYOR_TYPES:
            cur = next_pos
            continue
        break

    return player.map.get_sabotage_downstream_priority(pos, player.my_team)

def find_sabotage_target(player, ct: Controller, my_pos: Position, vc: VisionCache) -> tuple[Position, int] | None:
    """Find the best visible enemy conveyor/bridge to sabotage.
    Prioritizes core-feeding targets, then nearest.
    Returns (position, priority) or None."""
    log("trying to find sabotage target")
    best_pos = None
    best_dist = INF
    best_type = 0
    ally_bot_positions = {p for (_, p) in vc.ally_builder_bots}
    if len(vc.enemy_units) > 0:
        consider = chain(vc.ally_conveyors, vc.enemy_conveyors)
    else:
        consider = vc.enemy_conveyors
    for (bid, etype, pos) in consider:
        if etype is not EntityType.CONVEYOR and etype is not EntityType.BRIDGE:
            continue
        dist = my_pos.distance_squared(pos)
        if dist > 13:
            continue
        downstream_priority = player.map.get_sabotage_downstream_priority(pos, player.my_team) if player.map is not None else 1
        if downstream_priority == 0:
            continue
        if downstream_priority < best_type:
            continue
        if downstream_priority == best_type and dist >= best_dist:
            continue
        sabotage_priority = get_sabotage_target_priority(player, ct, pos, vc, ally_bot_positions)
        if sabotage_priority == 0:
            continue
        if sabotage_priority > best_type or (sabotage_priority == best_type and dist < best_dist):
            best_type = sabotage_priority
            best_dist = dist
            best_pos = pos
    if best_pos is None:
        return None
    return (best_pos, best_type)

def find_defend_target(player, ct: Controller, my_pos: Position, vc: VisionCache) -> Position | None:
    """Find best ore position to defend with barriers.
    Condition 1: harvester with ally conveyor/turret adjacent that has unprotected sides.
    Condition 2: titanium ore without any building on it."""
    best_pos = None
    best_dist = INF

    for (_bid, pos, _team) in vc.harvesters:
        if player.map.get_tile_env(pos) != Environment.ORE_TITANIUM:
            continue
        has_ally_infra = False
        for d in CARDINAL_DIRECTIONS:
            adj = pos.add(d)
            if not on_map(adj, player.map.width, player.map.height) or not ct.is_in_vision(adj):
                continue
            adj_bid = ct.get_tile_building_id(adj)
            if adj_bid is not None and ct.get_team(adj_bid) == player.my_team:
                etype = ct.get_entity_type(adj_bid)
                if etype in CONVEYOR_TYPES or etype in TURRET_TYPES or etype == EntityType.LAUNCHER:
                    has_ally_infra = True
                    break
        if not has_ally_infra:
            continue
        targets = get_barrier_targets(pos, player.core_pos, ct, player.map)
        if not targets:
            continue
        dist = my_pos.distance_squared(pos)
        if dist < best_dist:
            best_dist = dist
            best_pos = pos

    if best_pos is not None:
        return best_pos

    for ore_pos in player.map.ore_ti:
        entity = player.map.get_tile_entity(ore_pos)
        if entity is not None:
            continue
        dist = my_pos.distance_squared(ore_pos)
        if dist < best_dist:
            best_dist = dist
            best_pos = ore_pos
    return best_pos

def count_closer_allies(player, target: Position, my_pos: Position, vc: VisionCache) -> int:
    my_dist = my_pos.distance_squared(target)
    closer = 0
    for (_eid, apos) in vc.ally_builder_bots:
        if apos.distance_squared(target) < my_dist:
            closer += 1
    return closer

def find_upgradeable_axionite_placeholder(player, ct: Controller, my_pos: Position, vc: VisionCache) -> Position | None:
    """Find a visible foundry-position axionite conveyor that can be upgraded."""
    if player.core_pos is None:
        return None

    best_pos = None
    best_dist = INF
    for (bid, etype, pos) in vc.ally_conveyors:
        if etype != EntityType.CONVEYOR or not is_foundry_position(player.core_pos, pos):
            continue
        if ct.get_tile_builder_bot_id(pos) is not None:
            continue
        is_axionite = (
            ct.get_stored_resource(bid) == ResourceType.RAW_AXIONITE
            or player.map.has_recent_conveyor_resource(pos, ResourceType.RAW_AXIONITE)
            or player.map.input_chain_reaches_resource(pos, ResourceType.RAW_AXIONITE)
        )
        if not is_axionite:
            continue
        dist = my_pos.distance_squared(pos)
        if dist < best_dist:
            best_dist = dist
            best_pos = pos
    return best_pos

def find_adjacent_foundry_reroute_source(player, ct: Controller, my_pos: Position, foundry_pos: Position) -> Position | None:
    """Find a cardinal-adjacent ally conveyor/bridge carrying titanium that can be broken and rerouted into foundry_pos."""
    best_pos = None
    best_dist = INF
    for d in CARDINAL_DIRECTIONS:
        pos = foundry_pos.add(d)
        if not on_map(pos, player.map.width, player.map.height) or not ct.is_in_vision(pos):
            continue
        bid = ct.get_tile_building_id(pos)
        if bid is None or ct.get_team(bid) != player.my_team:
            continue
        etype = ct.get_entity_type(bid)
        if etype not in CONVEYOR_TYPES:
            continue
        carries_titanium = ct.get_stored_resource(bid) == ResourceType.TITANIUM
        if not carries_titanium:
            carries_titanium = player.map.input_chain_reaches_resource(pos, ResourceType.TITANIUM)
        if not carries_titanium:
            continue
        if player.map.feeds_other_ally_foundry(pos, player.my_team, foundry_pos):
            continue
        dist = my_pos.distance_squared(pos)
        if dist < best_dist:
            best_dist = dist
            best_pos = pos
    return best_pos

def get_predicted_enemy_core_pos(player) -> Position | None:
    if player.core_pos is None or player.map is None:
        return None
    if player.map.symmetry is not Symmetry.UNKNOWN:
        return player.map.get_symmetric_pos(player.core_pos, player.map.symmetry)
    if player.map.can_rotate:
        return player.map.get_symmetric_pos(player.core_pos, Symmetry.ROTATE)
    if player.map.can_flip_x:
        return player.map.get_symmetric_pos(player.core_pos, Symmetry.FLIP_X)
    if player.map.can_flip_y:
        return player.map.get_symmetric_pos(player.core_pos, Symmetry.FLIP_Y)
    return None

def get_enemy_core_anchor(player) -> Position | None:
    return player.enemy_core_pos or player.predicted_enemy_core_pos

def get_known_core_intercept_threat(player, reference_pos: Position, log_reason: str | None = None) -> tuple[Position, bool] | None:
    enemy_core_anchor = get_enemy_core_anchor(player)
    if enemy_core_anchor is None:
        return None
    core_tile = get_nearest_core_tile(enemy_core_anchor, reference_pos)
    if core_tile is None:
        return None
    if reference_pos.distance_squared(core_tile) > KNOWN_CORE_INTERCEPT_TRIGGER_DIST_SQ:
        return None
    if log_reason is not None:
        log(f"{log_reason}: using known enemy core tile {core_tile} from anchor {enemy_core_anchor}")
    return core_tile, True

def can_repair_broken_chain_now(player, ct: Controller, output_pos: Position, vc: VisionCache) -> bool:
    """True if output_pos looks repairable under current visible conditions."""
    if not ct.is_in_vision(output_pos):
        return True

    bid = ct.get_tile_building_id(output_pos)
    if bid is None:
        return True

    etype = ct.get_entity_type(bid)
    team = ct.get_team(bid)

    if etype == EntityType.MARKER or etype == EntityType.ROAD:
        return True
    if etype == EntityType.BARRIER and team == player.my_team:
        return True
    if etype in TURRET_TYPES or etype == EntityType.LAUNCHER:
        return team == player.my_team and len(vc.enemy_units) == 0
    if etype in CONVEYOR_TYPES:
        return True
    if team != player.my_team and ct.is_tile_passable(output_pos):
        return True
    return False
