# map_info.py

from __future__ import annotations

from collections import defaultdict
from typing import Dict, Optional, Set, Tuple

# Adjust this import to match your engine/package.
from cambc import Position


XY = Tuple[int, int]



def _xy(pos: Position) -> XY:
    return (pos.x, pos.y)


def _pos(x: int, y: int) -> Position:
    return Position(x, y)


def _enum_name(value) -> str:
    return getattr(value, "name", str(value)).upper()


class MapInfo:
    """
    Two-layer map knowledge.

    Ground layer:
      - EMPTY
      - ORE
      - WALL

    Building layer:
      - CORE
      - ROAD
      - HARVESTER
      - CONVEYOR
      - etc.

    Notes:
      - Builder bots are NOT tracked here.
      - Symmetry is inferred ONLY from the ground layer.
      - Core is a building-layer object with a 3x3 footprint.
      - There is only one core.
    """

    SYM_VERTICAL = "vertical"
    SYM_HORIZONTAL = "horizontal"
    SYM_ROTATIONAL = "rotational"

    GROUND_EMPTY = "EMPTY"
    GROUND_ORE = "ORE"
    GROUND_WALL = "WALL"

    def __init__(self, c):
        self.width: int = c.get_map_width()
        self.height: int = c.get_map_height()

        # ------------------------------------------------------------------
        # Ground layer
        # ------------------------------------------------------------------
        self.ground_by_xy: Dict[XY, str] = {}

        self.empty_tiles_xy: Set[XY] = set()
        self.ore_tiles_xy: Set[XY] = set()
        self.wall_tiles_xy: Set[XY] = set()

        # ------------------------------------------------------------------
        # Building layer
        # ------------------------------------------------------------------
        self.building_id_by_xy: Dict[XY, int] = {}
        self.building_type_by_xy: Dict[XY, str] = {}
        self.building_tiles_by_type: Dict[str, Set[XY]] = defaultdict(set)

        # Single core knowledge
        self.core_id: Optional[int] = None
        self.core_center_xy: Optional[XY] = None

        # Visible bookkeeping
        self.last_seen_round_by_xy: Dict[XY, int] = {}

        # ------------------------------------------------------------------
        # Symmetry
        # ------------------------------------------------------------------
        self.possible_symmetries: Set[str] = {
            self.SYM_VERTICAL,
            self.SYM_HORIZONTAL,
            self.SYM_ROTATIONAL,
        }

    # ----------------------------------------------------------------------
    # Public update
    # ----------------------------------------------------------------------

    def update(self, c) -> None:
        """
        Update known ground/building information from current vision.

        Yes: this also recomputes symmetry.
        """

        current_round = c.get_current_round()
        visible_tiles = c.get_nearby_tiles()

        # Per-update caches to avoid repeated engine calls for same building.
        building_type_cache: Dict[int, str] = {}
        building_center_cache: Dict[int, XY] = {}
        core_marked_ids: Set[int] = set()

        ground_changed = False

        last_seen_round_by_xy = self.last_seen_round_by_xy
        ground_by_xy = self.ground_by_xy
        building_id_by_xy = self.building_id_by_xy
        building_type_by_xy = self.building_type_by_xy

        get_tile_env = c.get_tile_env
        get_tile_building_id = c.get_tile_building_id
        get_entity_type = c.get_entity_type
        get_position = c.get_position

        for pos in visible_tiles:
            x = pos.x
            y = pos.y
            xy = (x, y)

            last_seen_round_by_xy[xy] = current_round

            # ----------------------------
            # Ground layer
            # ----------------------------
            env_name = _enum_name(get_tile_env(pos))
            if env_name == "ORE":
                ground_type = self.GROUND_ORE
            elif env_name == "WALL":
                ground_type = self.GROUND_WALL
            else:
                ground_type = self.GROUND_EMPTY

            if ground_by_xy.get(xy) != ground_type:
                self._set_ground(xy, ground_type)
                ground_changed = True

            # ----------------------------
            # Building layer
            # ----------------------------
            building_id = get_tile_building_id(pos)
            if building_id is None:
                self._clear_building_at(xy)
                continue

            type_name = building_type_cache.get(building_id)
            if type_name is None:
                try:
                    type_name = _enum_name(get_entity_type(building_id))
                except Exception:
                    type_name = "UNKNOWN"
                building_type_cache[building_id] = type_name

            # Fast path: tile already known as same building and type.
            if (
                building_id_by_xy.get(xy) == building_id
                and building_type_by_xy.get(xy) == type_name
            ):
                # Still ensure core footprint is known once per update.
                if type_name == "CORE" and building_id not in core_marked_ids:
                    center_xy = building_center_cache.get(building_id)
                    if center_xy is None:
                        try:
                            center = get_position(building_id)
                            center_xy = (center.x, center.y)
                            building_center_cache[building_id] = center_xy
                        except Exception:
                            center_xy = None

                    if center_xy is not None:
                        self.core_id = building_id
                        self.core_center_xy = center_xy
                        self._mark_core_3x3(center_xy, building_id)
                    core_marked_ids.add(building_id)
                continue

            self._record_visible_building_tile_cached(
                c,
                xy,
                building_id,
                type_name,
                get_position,
                building_center_cache,
                core_marked_ids,
            )

        if ground_changed:
            self._recompute_possible_symmetries()


    # ----------------------------------------------------------------------
    # Ground getters
    # ----------------------------------------------------------------------

    def get_ground_type_at(self, pos: Position) -> Optional[str]:
        return self.ground_by_xy.get(_xy(pos))

    def get_empty_tiles(self) -> Set[Position]:
        return {_pos(x, y) for (x, y) in self.empty_tiles_xy}

    def get_ore_tiles(self) -> Set[Position]:
        return {_pos(x, y) for (x, y) in self.ore_tiles_xy}

    def get_wall_tiles(self) -> Set[Position]:
        return {_pos(x, y) for (x, y) in self.wall_tiles_xy}

    def is_known_empty(self, pos: Position) -> bool:
        return _xy(pos) in self.empty_tiles_xy

    def is_known_ore(self, pos: Position) -> bool:
        return _xy(pos) in self.ore_tiles_xy

    def is_known_wall(self, pos: Position) -> bool:
        return _xy(pos) in self.wall_tiles_xy

    # ----------------------------------------------------------------------
    # Building getters
    # ----------------------------------------------------------------------

    def get_known_structure_tiles(self) -> Set[Position]:
        return {_pos(x, y) for (x, y) in self.building_id_by_xy.keys()}

    def get_building_type_at(self, pos: Position) -> Optional[str]:
        return self.building_type_by_xy.get(_xy(pos))

    def is_known_structure(self, pos: Position) -> bool:
        return _xy(pos) in self.building_id_by_xy

    def get_tiles_of_building_type(self, building_type: str) -> Set[Position]:
        building_type = building_type.upper()
        return {
            _pos(x, y)
            for (x, y) in self.building_tiles_by_type.get(building_type, set())
        }

    # ----------------------------------------------------------------------
    # Core getters
    # ----------------------------------------------------------------------

    def get_core_position(self) -> Optional[Position]:
        """
        Return the center position of the known core, or None.
        """
        if self.core_center_xy is None:
            return None
        x, y = self.core_center_xy
        return _pos(x, y)

    def get_core_tiles(self) -> Set[Position]:
        """
        Return the 3x3 footprint of the known core, or empty set if unknown.
        """
        if self.core_center_xy is None:
            return set()

        cx, cy = self.core_center_xy
        out = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                xy = (cx + dx, cy + dy)
                if self.in_bounds_xy(xy):
                    out.add(_pos(xy[0], xy[1]))
        return out

    # ----------------------------------------------------------------------
    # Avoid getter
    # ----------------------------------------------------------------------

    def get_avoid(self) -> Set[Position]:
        """
        Return:
          - all known structure tiles
          - all known wall tiles
          - plus N/S/E/W neighbors of ore tiles that currently have a known
            harvester on them
        """
        out: Set[XY] = set(self.building_id_by_xy.keys())
        out.update(self.wall_tiles_xy)

        for ore_xy in self._get_harvested_ore_tiles_xy():
            x, y = ore_xy

            nxy = (x, y - 1)
            if self.in_bounds_xy(nxy):
                out.add(nxy)

            nxy = (x, y + 1)
            if self.in_bounds_xy(nxy):
                out.add(nxy)

            nxy = (x - 1, y)
            if self.in_bounds_xy(nxy):
                out.add(nxy)

            nxy = (x + 1, y)
            if self.in_bounds_xy(nxy):
                out.add(nxy)

        return {_pos(x, y) for (x, y) in out}

    # ----------------------------------------------------------------------
    # Symmetry getters
    # ----------------------------------------------------------------------

    def get_possible_symmetries(self) -> Set[str]:
        return set(self.possible_symmetries)

    def transform_xy(self, xy: XY, symmetry: str) -> XY:
        x, y = xy

        if symmetry == self.SYM_VERTICAL:
            return (self.width - 1 - x, y)

        if symmetry == self.SYM_HORIZONTAL:
            return (x, self.height - 1 - y)

        if symmetry == self.SYM_ROTATIONAL:
            return (self.width - 1 - x, self.height - 1 - y)

        raise ValueError(f"Unknown symmetry: {symmetry}")

    def infer_symmetric_positions(
        self,
        positions: Set[Position],
        symmetry: str,
    ) -> Set[Position]:
        out = set()
        for p in positions:
            tx, ty = self.transform_xy(_xy(p), symmetry)
            out.add(_pos(tx, ty))
        return out

    # ----------------------------------------------------------------------
    # Internal: ground layer
    # ----------------------------------------------------------------------

    def _set_ground(self, xy: XY, ground_type: str) -> None:
        prev = self.ground_by_xy.get(xy)
        if prev == ground_type:
            return

        if prev == self.GROUND_EMPTY:
            self.empty_tiles_xy.discard(xy)
        elif prev == self.GROUND_ORE:
            self.ore_tiles_xy.discard(xy)
        elif prev == self.GROUND_WALL:
            self.wall_tiles_xy.discard(xy)

        self.ground_by_xy[xy] = ground_type

        if ground_type == self.GROUND_EMPTY:
            self.empty_tiles_xy.add(xy)
        elif ground_type == self.GROUND_ORE:
            self.ore_tiles_xy.add(xy)
        elif ground_type == self.GROUND_WALL:
            self.wall_tiles_xy.add(xy)
        else:
            raise ValueError(f"Invalid ground type: {ground_type}")

    # ----------------------------------------------------------------------
    # Internal: building layer
    # ----------------------------------------------------------------------

    def _clear_building_at(self, xy: XY) -> None:
        old_type = self.building_type_by_xy.pop(xy, None)
        self.building_id_by_xy.pop(xy, None)

        if old_type is not None:
            self.building_tiles_by_type[old_type].discard(xy)

    def _record_visible_building_tile(self, c, xy: XY, building_id: int) -> None:
        try:
            type_name = _enum_name(c.get_entity_type(building_id))
        except Exception:
            type_name = "UNKNOWN"

        self._record_visible_building_tile_cached(
            c,
            xy,
            building_id,
            type_name,
            c.get_position,
            {},
            set(),
        )

    def _record_visible_building_tile_cached(
        self,
        c,
        xy: XY,
        building_id: int,
        type_name: str,
        get_position_fn,
        building_center_cache: Dict[int, XY],
        core_marked_ids: Set[int],
    ) -> None:
        old_type = self.building_type_by_xy.get(xy)
        old_id = self.building_id_by_xy.get(xy)

        if old_id == building_id and old_type == type_name:
            return

        if old_type is not None:
            self.building_tiles_by_type[old_type].discard(xy)

        self.building_id_by_xy[xy] = building_id
        self.building_type_by_xy[xy] = type_name
        self.building_tiles_by_type[type_name].add(xy)

        if type_name == "CORE":
            self.core_id = building_id

            if building_id not in core_marked_ids:
                center_xy = building_center_cache.get(building_id)
                if center_xy is None:
                    try:
                        center = get_position_fn(building_id)
                        center_xy = (center.x, center.y)
                        building_center_cache[building_id] = center_xy
                    except Exception:
                        center_xy = None

                if center_xy is not None:
                    self.core_center_xy = center_xy
                    self._mark_core_3x3(center_xy, building_id)

                core_marked_ids.add(building_id)

    def _mark_core_3x3(self, center_xy: XY, core_id: int) -> None:
        cx, cy = center_xy

        min_x = max(0, cx - 1)
        max_x = min(self.width - 1, cx + 1)
        min_y = max(0, cy - 1)
        max_y = min(self.height - 1, cy + 1)

        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                xy = (x, y)

                old_type = self.building_type_by_xy.get(xy)
                if old_type is not None and old_type != "CORE":
                    self.building_tiles_by_type[old_type].discard(xy)

                self.building_id_by_xy[xy] = core_id
                self.building_type_by_xy[xy] = "CORE"
                self.building_tiles_by_type["CORE"].add(xy)

    def _get_harvested_ore_tiles_xy(self) -> Set[XY]:
        harvester_tiles = self.building_tiles_by_type.get("HARVESTER", set())
        ore_tiles_xy = self.ore_tiles_xy
        return {xy for xy in harvester_tiles if xy in ore_tiles_xy}

    # ----------------------------------------------------------------------
    # Internal: symmetry
    # ----------------------------------------------------------------------

    def _recompute_possible_symmetries(self) -> None:
        """
        Symmetry is determined ONLY from known ground layer info:
          - EMPTY
          - ORE
          - WALL

        Supported symmetries:
          - rotational
          - vertical
          - horizontal
        """
        possible = {
            self.SYM_VERTICAL,
            self.SYM_HORIZONTAL,
            self.SYM_ROTATIONAL,
        }

        width = self.width
        height = self.height
        ground_by_xy = self.ground_by_xy

        for (x, y), ground_type in ground_by_xy.items():
            if not possible:
                break

            if self.SYM_VERTICAL in possible:
                other_ground = ground_by_xy.get((width - 1 - x, y))
                if other_ground is not None and other_ground != ground_type:
                    possible.discard(self.SYM_VERTICAL)

            if self.SYM_HORIZONTAL in possible:
                other_ground = ground_by_xy.get((x, height - 1 - y))
                if other_ground is not None and other_ground != ground_type:
                    possible.discard(self.SYM_HORIZONTAL)

            if self.SYM_ROTATIONAL in possible:
                other_ground = ground_by_xy.get((width - 1 - x, height - 1 - y))
                if other_ground is not None and other_ground != ground_type:
                    possible.discard(self.SYM_ROTATIONAL)

        self.possible_symmetries = possible

    # ----------------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------------

    def in_bounds_xy(self, xy: XY) -> bool:
        x, y = xy
        return 0 <= x < self.width and 0 <= y < self.height

    def _cardinal_neighbors_xy(self, xy: XY) -> tuple[XY, XY, XY, XY]:
        x, y = xy
        return (
            (x, y - 1),  # north
            (x, y + 1),  # south
            (x - 1, y),  # west
            (x + 1, y),  # east
        )