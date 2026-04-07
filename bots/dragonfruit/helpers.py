from itertools import chain

from cambc import Controller, Direction, Environment, GameConstants, Position, EntityType, ResourceType, Team

from globals import DIRECTIONS, ALL_DIRECTIONS, CARDINAL_DIRECTIONS, CONVEYOR_TYPES, TURRET_TYPES, INF

from vision import VisionCache

from log import log, log_time

_GOLDEN = 0.618033988749895
_INTERCEPT_RESOURCES = (ResourceType.TITANIUM, ResourceType.REFINED_AXIONITE)
_INTERCEPT_MAX_TRAVEL_DIST_SQ = 13
_INTERCEPT_THREAT_RADIUS_SQ = GameConstants.SENTINEL_VISION_RADIUS_SQ

def bot_path_color(bot_id: int) -> tuple[int, int, int]:
    hue = (bot_id * _GOLDEN) % 1.0
    h6 = hue * 6.0
    sector = int(h6)
    f = h6 - sector
    q = int((1 - f) * 255)
    t = int(f * 255)
    match sector % 6:
        case 0: return 255, t,   0
        case 1: return q,   255, 0
        case 2: return 0,   255, t
        case 3: return 0,   q,   255
        case 4: return t,   0,   255
        case _: return 255, 0,   q


def is_marker_building(ct: Controller, bid: int | None) -> bool:
    return bid is not None and ct.get_entity_type(bid) == EntityType.MARKER

def count_ally_turrets_covering(ct: Controller, vc: VisionCache, target_pos: Position) -> int:
    """Count ally turrets whose raw attack pattern covers target_pos."""
    count = 0
    for (eid, etype, tpos) in vc.ally_turrets:
        if target_pos in ct.get_attackable_tiles_from(tpos, ct.get_direction(eid), etype):
            count += 1
    return count

def on_map_coords(x: int, y: int, width: int, height: int) -> bool:
    return 0 <= x < width and 0 <= y < height

def on_map(pos: Position, width: int, height: int) -> bool:
    return 0 <= pos.x < width and 0 <= pos.y < height

def is_core_tile(core_pos: Position | None, pos: Position) -> bool:
    """True if pos is one of the 9 tiles occupied by the allied core."""
    if core_pos is None:
        return False
    return core_pos.distance_squared(pos) <= 2

def get_core_tiles(core_pos: Position | None) -> list[Position]:
    """Return the 9 occupied tiles for a core anchor position."""
    if core_pos is None:
        return []
    return [
        Position(core_pos.x + dx, core_pos.y + dy)
        for dx in range(-1, 2)
        for dy in range(-1, 2)
    ]

def get_nearest_core_tile(core_pos: Position | None, reference_pos: Position) -> Position | None:
    """Return the core tile closest to reference_pos."""
    tiles = get_core_tiles(core_pos)
    if not tiles:
        return None
    return min(tiles, key=lambda pos: pos.distance_squared(reference_pos))

def get_cardinal_direction_into_core(core_pos: Position | None, pos: Position) -> Direction | None:
    """Return the cardinal direction from pos into one of the core's 3x3 tiles."""
    if core_pos is None:
        return None
    for d in CARDINAL_DIRECTIONS:
        if is_core_tile(core_pos, pos.add(d)):
            return d
    return None

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

def can_build_over_existing(pos: Position, ct: Controller, my_pos: Position, my_team: Team, map_obj, vc: VisionCache, allow_launchers: bool = False) -> bool:
    """True if pos has an ally road/sentinel within action range that can be destroyed to build something.
    If vc is provided and enemies are visible, refuses to destroy ally sentinels."""
    if my_pos.distance_squared(pos) > 2:
        return False
    if not ct.is_in_vision(pos):
        return False
    if map_obj.get_tile_env(pos) == Environment.WALL:
        return False
    bid = ct.get_tile_building_id(pos)
    if bid is None:
        return True
    etype = ct.get_entity_type(bid)
    if etype == EntityType.MARKER:
        return True
    if ct.get_team(bid) != my_team:
        return False
    # Don't destroy ally turrets or launchers when enemies are visible.
    if (etype in TURRET_TYPES or etype == EntityType.LAUNCHER) and len(vc.enemy_units) > 0:
        return False
    if etype == EntityType.LAUNCHER:
        return allow_launchers
    return etype in (EntityType.ROAD, EntityType.BARRIER) or etype in TURRET_TYPES

def can_build_conveyor_here(pos: Position, direction: Direction, ct: Controller, my_pos: Position, my_team: Team, map_obj, vc: VisionCache, allow_launchers: bool = False) -> bool:
    """True if we can build a conveyor at pos facing direction — either directly,
    or because the tile holds an ally road/sentinel we can first destroy."""
    if direction not in CARDINAL_DIRECTIONS:
        return False
    if ct.can_build_conveyor(pos, direction):
        return True
    return (can_build_over_existing(pos, ct, my_pos, my_team, map_obj, vc, allow_launchers=allow_launchers)
            and ct.get_global_resources()[0] >= ct.get_conveyor_cost()[0])

def can_build_splitter_here(pos: Position, direction: Direction, ct: Controller, my_pos: Position, my_team: Team, map_obj, vc: VisionCache, allow_launchers: bool = False) -> bool:
    """True if we can build a splitter at pos facing direction — either directly,
    or because the tile holds an ally road/sentinel we can first destroy."""
    if direction not in CARDINAL_DIRECTIONS:
        return False
    if ct.can_build_splitter(pos, direction):
        return True
    return (can_build_over_existing(pos, ct, my_pos, my_team, map_obj, vc=vc, allow_launchers=allow_launchers)
            and ct.get_global_resources()[0] >= ct.get_splitter_cost()[0])

def can_build_bridge_here(pos: Position, output: Position, ct: Controller, my_pos: Position, my_team: Team, map_obj, vc: VisionCache, allow_launchers: bool = False) -> bool:
    """True if we can build a bridge at pos with given output — either directly,
    or because the tile holds an ally road/sentinel we can first destroy."""
    if ct.can_build_bridge(pos, output):
        return True
    return (can_build_over_existing(pos, ct, my_pos, my_team, map_obj, vc, allow_launchers=allow_launchers)
            and ct.get_global_resources()[0] >= ct.get_bridge_cost()[0])

def can_build_launcher_here(pos: Position, ct: Controller, my_pos: Position, my_team: Team, map_obj, vc: VisionCache, allow_launchers: bool = False) -> bool:
    """True if we can build a launcher at pos, possibly after destroying an allied support building."""
    if ct.get_tile_builder_bot_id(pos) is not None:
        return False
    if ct.can_build_launcher(pos):
        return True
    return (can_build_over_existing(pos, ct, my_pos, my_team, map_obj, vc, allow_launchers=allow_launchers)
            and ct.get_global_resources()[0] >= ct.get_launcher_cost()[0])

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
    turret_cost = gunner_cost if get_best_turret_type(output, enemy_core_pos) == EntityType.GUNNER else sentinel_cost
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
            and etype == get_best_turret_type(pos, enemy_core_pos)
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

def is_foundry_position(core_pos: Position | None, pos: Position) -> bool:
    """True if pos is cardinally adjacent to the core's 3x3 area (valid foundry location)."""
    if core_pos is None:
        return False
    dist = core_pos.distance_squared(pos)
    return 2 < dist <= 5

def get_foundry_positions(core_pos: Position | None, width: int, height: int) -> set:
    """Return set of valid foundry positions (cardinally adjacent to core's 3x3)."""
    positions = set()
    if core_pos is None:
        return positions
    cx, cy = core_pos.x, core_pos.y
    for x in range(cx - 1, cx + 2):
        for dy in (-2, 2):
            y = cy + dy
            if on_map_coords(x, y, width, height):
                positions.add(Position(x, y))
    for y in range(cy - 1, cy + 2):
        for dx in (-2, 2):
            x = cx + dx
            if on_map_coords(x, y, width, height):
                positions.add(Position(x, y))
    return positions

def is_gunner_position(core_pos: Position | None, pos: Position) -> bool:
    """True if pos is adjacent to the core's 3x3 area (valid gunner location)."""
    if core_pos is None:
        return False
    dist = core_pos.distance_squared(pos)
    return 2 < dist <= 18

def get_best_turret_type(pos: Position, enemy_core_pos: Position | None) -> EntityType:
    """Return the preferred turret type for an intercept build at pos."""
    if enemy_core_pos is not None and is_gunner_position(enemy_core_pos, pos):
        return EntityType.GUNNER
    return EntityType.SENTINEL

def build_best_turret(ct: Controller, pos: Position, direction: Direction, enemy_core_pos: Position | None) -> bool:
    """Try to build a gunner (if valid position near enemy core) or sentinel at pos.
    Returns True if a turret was built."""
    turret_type = get_best_turret_type(pos, enemy_core_pos)
    if turret_type == EntityType.GUNNER and ct.can_build_gunner(pos, direction):
        ct.build_gunner(pos, direction)
        log(f"BUILT gunner at {pos} facing {direction}")
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

def can_build_foundry_here(pos: Position, ct: Controller, my_pos: Position, my_team: Team, map_obj, vc: VisionCache) -> bool:
    """True if we can build a foundry at pos, possibly after destroying an ally road/turret."""
    if ct.get_tile_builder_bot_id(pos) is not None:
        return False
    if ct.can_build_foundry(pos):
        return True
    return can_build_over_existing(pos, ct, my_pos, my_team, map_obj, vc=vc)

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

def dir_distance(a, b):
    ia = DIRECTIONS.index(a)
    ib = DIRECTIONS.index(b)
    diff = abs(ia - ib)
    return min(diff, 8 - diff)


def get_ray_endpoint(start: Position, direction: Direction, width: int, height: int) -> Position:
    dx, dy = direction.delta()
    x, y = start.x, start.y

    while True:
        nx, ny = x + dx, y + dy
        if nx < 0 or nx >= width or ny < 0 or ny >= height:
            return Position(x, y)
        x, y = nx, ny


def get_valid_directions(ct, core_pos, width, height):
    valid = []
    for d in DIRECTIONS:
        endpoint = get_ray_endpoint(core_pos, d, width, height)
        if not ct.is_in_vision(endpoint):
            valid.append((d, endpoint))
    return valid


def pick_three_directions(core_pos, width, height, valid_dirs):
    if len(valid_dirs) <= 3:
        return valid_dirs

    center = Position(width // 2, height // 2)
    half_w, half_h = width // 2, height // 2
    max_dist_sq = half_w * half_w + half_h * half_h

    best_triplet = (valid_dirs[0], valid_dirs[1], valid_dirs[2])
    best_score = -1

    for i in range(len(valid_dirs)):
        for j in range(i + 1, len(valid_dirs)):
            for k in range(j + 1, len(valid_dirs)):
                sep01 = dir_distance(valid_dirs[i][0], valid_dirs[j][0])
                sep02 = dir_distance(valid_dirs[i][0], valid_dirs[k][0])
                sep12 = dir_distance(valid_dirs[j][0], valid_dirs[k][0])

                # product of pairwise separations: rewards balanced spread
                # e.g. (3,3,2)->18 beats "T" shape (2,2,4)->16
                spread = sep01 * sep02 * sep12

                # center closeness: best of the 3 endpoints (0 to 1)
                best_closeness = max(
                    1.0 - valid_dirs[i][1].distance_squared(center) / max_dist_sq,
                    1.0 - valid_dirs[j][1].distance_squared(center) / max_dist_sq,
                    1.0 - valid_dirs[k][1].distance_squared(center) / max_dist_sq,
                )

                # spread ranges 0-64 (max 4*4*4), closeness 0-1
                score = spread * 10 + best_closeness * 30

                if score > best_score:
                    best_score = score
                    best_triplet = (valid_dirs[i], valid_dirs[j], valid_dirs[k])

    return list(best_triplet)

def prioritize_direction(directions: list[Direction], preferred_dir: Direction) -> list[Direction]:
    """Move preferred_dir to the front, adding it if needed."""
    ordered = [d for d in directions if d != preferred_dir]
    return [preferred_dir, *ordered][:3]