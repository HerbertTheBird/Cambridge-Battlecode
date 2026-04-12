from cambc import Controller, Direction, Position, EntityType, Team

from globals import CARDINAL_DIRECTIONS, TURRET_TYPES

from vision import VisionCache

def can_build_over_existing(pos: Position, ct: Controller, my_pos: Position, my_team: Team, map_obj, vc: VisionCache, allow_launchers: bool = False) -> bool:
    """True if pos has an ally road/sentinel within action range that can be destroyed to build something.
    If vc is provided and enemies are visible, refuses to destroy ally sentinels."""
    if my_pos.distance_squared(pos) > 2:
        return False
    if not ct.is_in_vision(pos):
        return False
    if map_obj.is_wall(pos):
        return False
    bid = map_obj.get_tile_entity_id(pos)
    if bid is None:
        return True
    etype = map_obj.get_tile_entity_type(pos)
    team = map_obj.get_tile_entity_team(pos)
    assert etype is not None
    assert team is not None
    if etype == EntityType.MARKER:
        return True
    if team != my_team:
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

def can_build_armoured_conveyor_here(pos: Position, direction: Direction, ct: Controller, my_pos: Position, my_team: Team, map_obj, vc: VisionCache, allow_launchers: bool = False) -> bool:
    """True if we can build a conveyor at pos facing direction — either directly,
    or because the tile holds an ally road/sentinel we can first destroy."""
    if direction not in CARDINAL_DIRECTIONS:
        return False
    if ct.can_build_armoured_conveyor(pos, direction):
        return True
    titanium, axionite = ct.get_global_resources()
    titanium_cost, axionite_cost = ct.get_armoured_conveyor_cost()
    return (can_build_over_existing(pos, ct, my_pos, my_team, map_obj, vc, allow_launchers=allow_launchers)
            and titanium >= titanium_cost and axionite >= axionite_cost)


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

def can_build_foundry_here(pos: Position, ct: Controller, my_pos: Position, my_team: Team, map_obj, vc: VisionCache) -> bool:
    """True if we can build a foundry at pos, possibly after destroying an ally road/turret."""
    if ct.get_tile_builder_bot_id(pos) is not None:
        return False
    if ct.can_build_foundry(pos):
        return True
    return can_build_over_existing(pos, ct, my_pos, my_team, map_obj, vc=vc)

def safe_destroy(player, ct: Controller, pos: Position, vc: VisionCache) -> bool:
    """Destroy a non-marker building at pos. Returns True if destroyed."""
    bid = player.map.get_tile_entity_id(pos)
    if bid is None:
        return False
    etype = player.map.get_tile_entity_type(pos)
    team = player.map.get_tile_entity_team(pos)
    assert etype is not None
    assert team is not None
    if etype == EntityType.MARKER:
        return False
    if not ct.can_destroy(pos):
        return False
    ct.destroy(pos)
    vc.remove_entity(player, bid, etype, team, pos)
    player.map.on_local_destroy(pos)
    return True

def safe_build_road(player, ct: Controller, pos: Position) -> bool:
    if not ct.can_build_road(pos):
        return False
    bid = ct.build_road(pos)
    player.vc.add_entity(player, bid, EntityType.ROAD, player.my_team, pos)
    player.map.on_local_build(pos, bid, EntityType.ROAD, player.my_team)
    return True

def safe_build_conveyor(player, ct: Controller, pos: Position, direction) -> bool:
    if direction not in CARDINAL_DIRECTIONS:
        return False
    if not ct.can_build_conveyor(pos, direction):
        return False
    bid = ct.build_conveyor(pos, direction)
    player.vc.add_entity(player, bid, EntityType.CONVEYOR, player.my_team, pos)
    player.map.on_local_build(pos, bid, EntityType.CONVEYOR, player.my_team, direction=direction)
    return True

def get_selected_conveyor_cost(player, ct: Controller) -> tuple[int, int]:
    if player.use_armoured_conveyors:
        return ct.get_armoured_conveyor_cost()
    return ct.get_conveyor_cost()

def can_build_selected_conveyor_here(player, pos: Position, direction: Direction, ct: Controller, my_pos: Position, my_team: Team, map_obj, vc: VisionCache, allow_launchers: bool = False) -> bool:
    if player.use_armoured_conveyors:
        return can_build_armoured_conveyor_here(
            pos, direction, ct, my_pos, my_team, map_obj, vc, allow_launchers=allow_launchers
        )
    return can_build_conveyor_here(
        pos, direction, ct, my_pos, my_team, map_obj, vc, allow_launchers=allow_launchers
    )

def safe_build_selected_conveyor(player, ct: Controller, pos: Position, direction: Direction) -> bool:
    if player.use_armoured_conveyors:
        return safe_build_armoured_conveyor(player, ct, pos, direction)
    return safe_build_conveyor(player, ct, pos, direction)

def safe_build_armoured_conveyor(player, ct: Controller, pos: Position, direction) -> bool:
    if direction not in CARDINAL_DIRECTIONS:
        return False
    if not ct.can_build_armoured_conveyor(pos, direction):
        return False
    bid = ct.build_armoured_conveyor(pos, direction)
    player.vc.add_entity(player, bid, EntityType.ARMOURED_CONVEYOR, player.my_team, pos)
    player.map.on_local_build(pos, bid, EntityType.ARMOURED_CONVEYOR, player.my_team, direction=direction)
    return True

def safe_build_splitter(player, ct: Controller, pos: Position, direction) -> bool:
    if direction not in CARDINAL_DIRECTIONS:
        return False
    if not ct.can_build_splitter(pos, direction):
        return False
    bid = ct.build_splitter(pos, direction)
    player.vc.add_entity(player, bid, EntityType.SPLITTER, player.my_team, pos)
    player.map.on_local_build(pos, bid, EntityType.SPLITTER, player.my_team, direction=direction)
    return True

def safe_build_bridge(player, ct: Controller, pos: Position, target: Position) -> bool:
    if not ct.can_build_bridge(pos, target):
        return False
    bid = ct.build_bridge(pos, target)
    player.vc.add_entity(player, bid, EntityType.BRIDGE, player.my_team, pos)
    player.map.on_local_build(pos, bid, EntityType.BRIDGE, player.my_team, output_target=target)
    return True

def safe_build_foundry(player, ct: Controller, pos: Position) -> bool:
    if not ct.can_build_foundry(pos):
        return False
    bid = ct.build_foundry(pos)
    player.vc.add_entity(player, bid, EntityType.FOUNDRY, player.my_team, pos)
    player.map.on_local_build(pos, bid, EntityType.FOUNDRY, player.my_team)
    return True

def safe_build_barrier(player, ct: Controller, pos: Position) -> bool:
    if not ct.can_build_barrier(pos):
        return False
    bid = ct.build_barrier(pos)
    player.vc.add_entity(player, bid, EntityType.BARRIER, player.my_team, pos)
    player.map.on_local_build(pos, bid, EntityType.BARRIER, player.my_team)
    return True

def safe_build_harvester(player, ct: Controller, pos: Position) -> bool:
    if not ct.can_build_harvester(pos):
        return False
    bid = ct.build_harvester(pos)
    player.vc.add_entity(player, bid, EntityType.HARVESTER, player.my_team, pos)
    player.map.on_local_build(pos, bid, EntityType.HARVESTER, player.my_team)
    return True

def safe_build_launcher(player, ct: Controller, pos: Position) -> bool:
    if not ct.can_build_launcher(pos):
        return False
    bid = ct.build_launcher(pos)
    player.vc.add_entity(player, bid, EntityType.LAUNCHER, player.my_team, pos)
    player.map.on_local_build(pos, bid, EntityType.LAUNCHER, player.my_team)
    player.last_support_launcher_round = ct.get_current_round()
    return True

def safe_place_marker(player, ct: Controller, pos: Position, value: int) -> bool:
    bid = player.map.get_tile_entity_id(pos)
    if (
        bid is not None
        and player.map.get_tile_entity_team(pos) == player.my_team
        and player.map.get_tile_entity_type(pos) == EntityType.MARKER
    ):
        if ct.can_destroy(pos):
            ct.destroy(pos)
    if not ct.can_place_marker(pos):
        return False
    ct.place_marker(pos, value)
    return True
