from cambc import Controller, Direction, Position, EntityType, Team

from globals import CARDINAL_DIRECTIONS, TURRET_TYPES
from helpers import is_in_vision

import map as map_mod
import vision as vc

def can_build_over_existing(pos: Position, my_pos: Position, my_team: Team, allow_launchers: bool = False) -> bool:
    """True if pos has an ally road/sentinel within action range that can be destroyed to build something.
    If enemies are visible, refuses to destroy ally sentinels."""
    if my_pos.distance_squared(pos) > 2:
        return False
    if not is_in_vision(my_pos, pos):
        return False
    if map_mod.is_wall(pos):
        return False
    bid = map_mod.get_tile_entity_id(pos)
    if bid is None:
        return True
    etype = map_mod.get_tile_entity_type(pos)
    team = map_mod.get_tile_entity_team(pos)
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

def can_build_conveyor_here(pos: Position, direction: Direction, ct: Controller, my_pos: Position, my_team: Team, allow_launchers: bool = False) -> bool:
    """True if we can build a conveyor at pos facing direction — either directly,
    or because the tile holds an ally road/sentinel we can first destroy."""
    if direction not in CARDINAL_DIRECTIONS:
        return False
    if ct.can_build_conveyor(pos, direction):
        return True
    return (can_build_over_existing(pos, my_pos, my_team, allow_launchers=allow_launchers)
            and ct.get_global_resources()[0] >= ct.get_conveyor_cost()[0])

def can_build_armoured_conveyor_here(pos: Position, direction: Direction, ct: Controller, my_pos: Position, my_team: Team, allow_launchers: bool = False) -> bool:
    """True if we can build a conveyor at pos facing direction — either directly,
    or because the tile holds an ally road/sentinel we can first destroy."""
    if direction not in CARDINAL_DIRECTIONS:
        return False
    if ct.can_build_armoured_conveyor(pos, direction):
        return True
    titanium, axionite = ct.get_global_resources()
    titanium_cost, axionite_cost = ct.get_armoured_conveyor_cost()
    return (can_build_over_existing(pos, my_pos, my_team, allow_launchers=allow_launchers)
            and titanium >= titanium_cost and axionite >= axionite_cost)


def can_build_splitter_here(pos: Position, direction: Direction, ct: Controller, my_pos: Position, my_team: Team, allow_launchers: bool = False) -> bool:
    """True if we can build a splitter at pos facing direction — either directly,
    or because the tile holds an ally road/sentinel we can first destroy."""
    if direction not in CARDINAL_DIRECTIONS:
        return False
    if ct.can_build_splitter(pos, direction):
        return True
    return (can_build_over_existing(pos, my_pos, my_team, allow_launchers=allow_launchers)
            and ct.get_global_resources()[0] >= ct.get_splitter_cost()[0])

def can_build_bridge_here(pos: Position, output: Position, ct: Controller, my_pos: Position, my_team: Team, allow_launchers: bool = False) -> bool:
    """True if we can build a bridge at pos with given output — either directly,
    or because the tile holds an ally road/sentinel we can first destroy."""
    if ct.can_build_bridge(pos, output):
        return True
    return (can_build_over_existing(pos, my_pos, my_team, allow_launchers=allow_launchers)
            and ct.get_global_resources()[0] >= ct.get_bridge_cost()[0])

def can_build_launcher_here(pos: Position, ct: Controller, my_pos: Position, my_team: Team, allow_launchers: bool = False) -> bool:
    """True if we can build a launcher at pos, possibly after destroying an allied support building."""
    if ct.get_tile_builder_bot_id(pos) is not None:
        return False
    if ct.can_build_launcher(pos):
        return True
    return (can_build_over_existing(pos, my_pos, my_team, allow_launchers=allow_launchers)
            and ct.get_global_resources()[0] >= ct.get_launcher_cost()[0])

def can_build_foundry_here(pos: Position, ct: Controller, my_pos: Position, my_team: Team) -> bool:
    """True if we can build a foundry at pos, possibly after destroying an ally road/turret."""
    if ct.get_tile_builder_bot_id(pos) is not None:
        return False
    if ct.can_build_foundry(pos):
        return True
    return can_build_over_existing(pos, my_pos, my_team)

def can_replace_with_walkable_under_builder(pos: Position, ct: Controller) -> bool:
    """True if we may destroy-and-rebuild a walkable building at pos.
    Enemy builders may remain on the tile, but allied builders other than self may not."""
    bbid = ct.get_tile_builder_bot_id(pos)
    if bbid is None or bbid == ct.get_id():
        return True
    return ct.get_team(bbid) != ct.get_team()

def safe_destroy(player, ct: Controller, pos: Position) -> bool:
    """Destroy a non-marker building at pos. Returns True if destroyed."""
    bid = map_mod.get_tile_entity_id(pos)
    if bid is None:
        return False
    etype = map_mod.get_tile_entity_type(pos)
    team = map_mod.get_tile_entity_team(pos)
    assert etype is not None
    assert team is not None
    if etype == EntityType.MARKER:
        return False
    if not ct.can_destroy(pos):
        return False
    ct.destroy(pos)
    vc.remove_entity(player, bid, etype, team, pos)
    map_mod.on_local_destroy(pos)
    return True

def safe_build_road(player, ct: Controller, pos: Position) -> bool:
    if not ct.can_build_road(pos):
        return False
    bid = ct.build_road(pos)
    vc.add_entity(player, bid, EntityType.ROAD, player.my_team, pos)
    map_mod.on_local_build(pos, bid, EntityType.ROAD, player.my_team)
    return True

def safe_build_conveyor(player, ct: Controller, pos: Position, direction) -> bool:
    if direction not in CARDINAL_DIRECTIONS:
        return False
    if not ct.can_build_conveyor(pos, direction):
        return False
    bid = ct.build_conveyor(pos, direction)
    vc.add_entity(player, bid, EntityType.CONVEYOR, player.my_team, pos)
    map_mod.on_local_build(pos, bid, EntityType.CONVEYOR, player.my_team, direction=direction)
    return True

def get_selected_conveyor_cost(player, ct: Controller) -> tuple[int, int]:
    if player.use_armoured_conveyors:
        return ct.get_armoured_conveyor_cost()
    return ct.get_conveyor_cost()

def can_build_selected_conveyor_here(player, pos: Position, direction: Direction, ct: Controller, my_pos: Position, my_team: Team, allow_launchers: bool = False) -> bool:
    if player.use_armoured_conveyors:
        return can_build_armoured_conveyor_here(
            pos, direction, ct, my_pos, my_team, allow_launchers=allow_launchers
        )
    return can_build_conveyor_here(
        pos, direction, ct, my_pos, my_team, allow_launchers=allow_launchers
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
    vc.add_entity(player, bid, EntityType.ARMOURED_CONVEYOR, player.my_team, pos)
    map_mod.on_local_build(pos, bid, EntityType.ARMOURED_CONVEYOR, player.my_team, direction=direction)
    return True

def safe_build_splitter(player, ct: Controller, pos: Position, direction) -> bool:
    if direction not in CARDINAL_DIRECTIONS:
        return False
    if not ct.can_build_splitter(pos, direction):
        return False
    bid = ct.build_splitter(pos, direction)
    vc.add_entity(player, bid, EntityType.SPLITTER, player.my_team, pos)
    map_mod.on_local_build(pos, bid, EntityType.SPLITTER, player.my_team, direction=direction)
    return True

def safe_build_bridge(player, ct: Controller, pos: Position, target: Position) -> bool:
    if not ct.can_build_bridge(pos, target):
        return False
    bid = ct.build_bridge(pos, target)
    vc.add_entity(player, bid, EntityType.BRIDGE, player.my_team, pos)
    map_mod.on_local_build(pos, bid, EntityType.BRIDGE, player.my_team, output_target=target)
    return True

def safe_build_foundry(player, ct: Controller, pos: Position) -> bool:
    if not ct.can_build_foundry(pos):
        return False
    bid = ct.build_foundry(pos)
    vc.add_entity(player, bid, EntityType.FOUNDRY, player.my_team, pos)
    map_mod.on_local_build(pos, bid, EntityType.FOUNDRY, player.my_team)
    return True

def safe_build_barrier(player, ct: Controller, pos: Position) -> bool:
    if not ct.can_build_barrier(pos):
        return False
    bid = ct.build_barrier(pos)
    vc.add_entity(player, bid, EntityType.BARRIER, player.my_team, pos)
    map_mod.on_local_build(pos, bid, EntityType.BARRIER, player.my_team)
    return True

def safe_build_harvester(player, ct: Controller, pos: Position) -> bool:
    if not ct.can_build_harvester(pos):
        return False
    bid = ct.build_harvester(pos)
    vc.add_entity(player, bid, EntityType.HARVESTER, player.my_team, pos)
    map_mod.on_local_build(pos, bid, EntityType.HARVESTER, player.my_team)
    return True

def safe_build_launcher(player, ct: Controller, pos: Position) -> bool:
    if not ct.can_build_launcher(pos):
        return False
    bid = ct.build_launcher(pos)
    vc.add_entity(player, bid, EntityType.LAUNCHER, player.my_team, pos)
    map_mod.on_local_build(pos, bid, EntityType.LAUNCHER, player.my_team)
    player.last_support_launcher_round = ct.get_current_round()
    return True

def safe_place_marker(player, ct: Controller, pos: Position, value: int) -> bool:
    bid = map_mod.get_tile_entity_id(pos)
    if (
        bid is not None
        and map_mod.get_tile_entity_team(pos) == player.my_team
        and map_mod.get_tile_entity_type(pos) == EntityType.MARKER
    ):
        if ct.can_destroy(pos):
            ct.destroy(pos)
    if not ct.can_place_marker(pos):
        return False
    ct.place_marker(pos, value)
    return True
