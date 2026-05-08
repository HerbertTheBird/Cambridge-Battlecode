# mirror.py
# Wraps a Controller so team B sees the map as if it were team A
# (vertical mirror: x -> width - 1 - x). Bot scripts written for team A
# then work unchanged for team B.

from cambc import *


_DIR_FLIP = {
    Direction.NORTH: Direction.NORTH,
    Direction.SOUTH: Direction.SOUTH,
    Direction.EAST: Direction.WEST,
    Direction.WEST: Direction.EAST,
    Direction.NORTHEAST: Direction.NORTHWEST,
    Direction.NORTHWEST: Direction.NORTHEAST,
    Direction.SOUTHEAST: Direction.SOUTHWEST,
    Direction.SOUTHWEST: Direction.SOUTHEAST,
    Direction.CENTRE: Direction.CENTRE,
}


class MirrorController:
    def __init__(self, c):
        self._c = c
        self._w = c.get_map_width()

    def _mp(self, p):
        if p is None:
            return None
        return Position(self._w - 1 - p.x, p.y)

    def _md(self, d):
        if d is None:
            return None
        return _DIR_FLIP[d]

    # Tile queries
    def get_tile_env(self, pos):
        return self._c.get_tile_env(self._mp(pos))

    def get_tile_building_id(self, pos):
        return self._c.get_tile_building_id(self._mp(pos))

    def get_tile_builder_bot_id(self, pos):
        return self._c.get_tile_builder_bot_id(self._mp(pos))

    def is_tile_empty(self, pos):
        return self._c.is_tile_empty(self._mp(pos))

    def is_tile_passable(self, pos):
        return self._c.is_tile_passable(self._mp(pos))

    def is_in_vision(self, pos):
        return self._c.is_in_vision(self._mp(pos))

    # Position-returning queries
    def get_position(self, id=None):
        if id is None:
            return self._mp(self._c.get_position())
        return self._mp(self._c.get_position(id))

    def get_gunner_target(self):
        t = self._c.get_gunner_target()
        return self._mp(t) if t is not None else None

    def get_bridge_target(self, id):
        return self._mp(self._c.get_bridge_target(id))

    def get_attackable_tiles(self):
        return [self._mp(p) for p in self._c.get_attackable_tiles()]

    def get_attackable_tiles_from(self, position, direction, turret_type):
        tiles = self._c.get_attackable_tiles_from(
            self._mp(position), self._md(direction), turret_type
        )
        return [self._mp(p) for p in tiles]

    def get_nearby_tiles(self, dist_sq=None):
        if dist_sq is None:
            tiles = self._c.get_nearby_tiles()
        else:
            tiles = self._c.get_nearby_tiles(dist_sq)
        return [self._mp(p) for p in tiles]

    # Direction-returning
    def get_direction(self, id=None):
        if id is None:
            return self._md(self._c.get_direction())
        return self._md(self._c.get_direction(id))

    # Movement
    def move(self, direction):
        return self._c.move(self._md(direction))

    def can_move(self, direction):
        return self._c.can_move(self._md(direction))

    # Directional buildings
    def build_conveyor(self, pos, direction):
        return self._c.build_conveyor(self._mp(pos), self._md(direction))

    def can_build_conveyor(self, pos, direction):
        return self._c.can_build_conveyor(self._mp(pos), self._md(direction))

    def build_splitter(self, pos, direction):
        return self._c.build_splitter(self._mp(pos), self._md(direction))

    def can_build_splitter(self, pos, direction):
        return self._c.can_build_splitter(self._mp(pos), self._md(direction))

    def build_armoured_conveyor(self, pos, direction):
        return self._c.build_armoured_conveyor(self._mp(pos), self._md(direction))

    def can_build_armoured_conveyor(self, pos, direction):
        return self._c.can_build_armoured_conveyor(self._mp(pos), self._md(direction))

    def build_gunner(self, pos, direction):
        return self._c.build_gunner(self._mp(pos), self._md(direction))

    def can_build_gunner(self, pos, direction):
        return self._c.can_build_gunner(self._mp(pos), self._md(direction))

    def build_sentinel(self, pos, direction):
        return self._c.build_sentinel(self._mp(pos), self._md(direction))

    def can_build_sentinel(self, pos, direction):
        return self._c.can_build_sentinel(self._mp(pos), self._md(direction))

    def build_breach(self, pos, direction):
        return self._c.build_breach(self._mp(pos), self._md(direction))

    def can_build_breach(self, pos, direction):
        return self._c.can_build_breach(self._mp(pos), self._md(direction))

    # Bridge
    def build_bridge(self, pos, target):
        return self._c.build_bridge(self._mp(pos), self._mp(target))

    def can_build_bridge(self, pos, target):
        return self._c.can_build_bridge(self._mp(pos), self._mp(target))

    # Non-directional buildings
    def build_harvester(self, pos):
        return self._c.build_harvester(self._mp(pos))

    def can_build_harvester(self, pos):
        return self._c.can_build_harvester(self._mp(pos))

    def build_road(self, pos):
        return self._c.build_road(self._mp(pos))

    def can_build_road(self, pos):
        return self._c.can_build_road(self._mp(pos))

    def build_barrier(self, pos):
        return self._c.build_barrier(self._mp(pos))

    def can_build_barrier(self, pos):
        return self._c.can_build_barrier(self._mp(pos))

    def build_foundry(self, pos):
        return self._c.build_foundry(self._mp(pos))

    def can_build_foundry(self, pos):
        return self._c.can_build_foundry(self._mp(pos))

    def build_launcher(self, pos):
        return self._c.build_launcher(self._mp(pos))

    def can_build_launcher(self, pos):
        return self._c.can_build_launcher(self._mp(pos))

    # Generic build
    def build(self, entity_type, position, extra=None):
        position = self._mp(position)
        if isinstance(extra, Direction):
            extra = self._md(extra)
        elif isinstance(extra, Position):
            extra = self._mp(extra)
        return self._c.build(entity_type, position, extra)

    def can_build(self, entity_type, position, extra=None):
        position = self._mp(position)
        if isinstance(extra, Direction):
            extra = self._md(extra)
        elif isinstance(extra, Position):
            extra = self._mp(extra)
        return self._c.can_build(entity_type, position, extra)

    # Heal & destroy
    def heal(self, pos):
        return self._c.heal(self._mp(pos))

    def can_heal(self, pos):
        return self._c.can_heal(self._mp(pos))

    def destroy(self, pos):
        return self._c.destroy(self._mp(pos))

    def can_destroy(self, pos):
        return self._c.can_destroy(self._mp(pos))

    # Markers
    def place_marker(self, pos, value):
        return self._c.place_marker(self._mp(pos), value)

    def can_place_marker(self, pos):
        return self._c.can_place_marker(self._mp(pos))

    # Combat
    def fire(self, target):
        return self._c.fire(self._mp(target))

    def can_fire(self, target):
        return self._c.can_fire(self._mp(target))

    def can_fire_from(self, position, direction, turret_type, target):
        return self._c.can_fire_from(
            self._mp(position), self._md(direction), turret_type, self._mp(target)
        )

    def can_rotate(self, direction):
        return self._c.can_rotate(self._md(direction))

    def rotate(self, direction):
        return self._c.rotate(self._md(direction))

    def launch(self, bot_pos, target):
        return self._c.launch(self._mp(bot_pos), self._mp(target))

    def can_launch(self, bot_pos, target):
        return self._c.can_launch(self._mp(bot_pos), self._mp(target))

    # Core
    def spawn_builder(self, pos):
        return self._c.spawn_builder(self._mp(pos))

    def can_spawn(self, pos):
        return self._c.can_spawn(self._mp(pos))

    # Debug indicators
    def draw_indicator_line(self, pos_a, pos_b, r, g, b):
        return self._c.draw_indicator_line(self._mp(pos_a), self._mp(pos_b), r, g, b)

    def draw_indicator_dot(self, pos, r, g, b):
        return self._c.draw_indicator_dot(self._mp(pos), r, g, b)

    # Everything else passes through unchanged.
    def __getattr__(self, name):
        return getattr(self._c, name)
