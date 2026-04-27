"""
Geometric Voronoi Chokepoint Detector
Based on: "Terrain Analysis in Real-Time Strategy Games" (Perkins 2010)

Fixes over original:
  - Robust geometry extraction handles Polygon / MultiPolygon / GeometryCollection
  - collect_boundary_samples correctly walks all polygon rings after notch cuts
  - prune_graph: isolated-vertex removal uses adj degree, not radius alone
  - identify_choke_points: cycle detection guards against self-loop region links
  - draw_edge_set: vertex lookup unified; no silent KeyError

New features:
  - Diagonal movement: cuts passable notches at diagonal-only obstacle corners
  - Step 6 region merging (Perkins §6) with configurable ratio thresholds
  - Second UI row for diagonal / merging parameters
"""

import argparse
import math
import time
import os
import sys
import tkinter as tk
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

from foronoi import NUMERIC_MODE_DECIMAL as FORONOI_NUMERIC_DECIMAL
from foronoi import NUMERIC_MODE_FLOAT as FORONOI_NUMERIC_FLOAT
from foronoi import Polygon as ForonoiPolygon
from foronoi import Voronoi as ForonoiVoronoi
from foronoi import set_numeric_mode as set_foronoi_numeric_mode

from chokepoint_runtime import (
    apply_diagonal_notches,
    boundary_segments_from_mask,
    build_analysis_mask,
    build_free_mask,
    build_obstacle_mask,
    cell_centers_covered_by_mask,
    clip_polygon_against_half_plane,
    point_to_segment_distance,
    raster_scale_from_spacing,
    rectangle_polygon,
    split_obstacle_mask_by_area,
)

Cell = Tuple[int, int]
VertexId = int

ENV_EMPTY = 0
ENV_WALL = 1
ENV_ORE_TITANIUM = 2
ENV_ORE_AXIONITE = 3

TEAM_A = 0
TEAM_B = 1

LOGIC_RUNTIME = "runtime"
LOGIC_OLD = "old"
RUNTIME_MATH_DECIMAL = FORONOI_NUMERIC_DECIMAL
RUNTIME_MATH_FLOAT = FORONOI_NUMERIC_FLOAT

SYMMETRY_HORIZONTAL = "horizontal"
SYMMETRY_VERTICAL = "vertical"
SYMMETRY_ROTATIONAL = "rotational"


def load_legacy_backend() -> Dict[str, object]:
    cached = getattr(load_legacy_backend, "_cache", None)
    if cached is not None:
        return cached

    try:
        import numpy as np
        from scipy.spatial import Voronoi
        from shapely.geometry import (
            GeometryCollection,
            LineString,
            MultiPolygon,
            Point,
            Polygon,
            box,
        )
        from shapely.ops import unary_union
    except ImportError as exc:
        raise RuntimeError(
            "Old chokepoint logic requires numpy, scipy, and shapely."
        ) from exc

    backend = {
        "np": np,
        "Voronoi": Voronoi,
        "GeometryCollection": GeometryCollection,
        "LineString": LineString,
        "MultiPolygon": MultiPolygon,
        "Point": Point,
        "Polygon": Polygon,
        "box": box,
        "unary_union": unary_union,
    }
    load_legacy_backend._cache = backend
    return backend


def extract_legacy_polygons(geom, backend: Dict[str, object]):
    polygon_cls = backend["Polygon"]
    multi_polygon_cls = backend["MultiPolygon"]
    geometry_collection_cls = backend["GeometryCollection"]

    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, polygon_cls):
        return [geom]
    if isinstance(geom, (multi_polygon_cls, geometry_collection_cls)):
        result = []
        for part in geom.geoms:
            result.extend(extract_legacy_polygons(part, backend))
        return result
    return []


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class GridConfig:
    rows: int = 32
    cols: int = 48
    cell_size: int = 20

    empty: str = "#ffffff"
    grid_line: str = "#d0d0d0"

    obstacle: str = "#222222"
    discarded_obstacle: str = "#9a9a9a"
    core: str = "#8b5a2b"
    titanium_ore: str = "#9ad9ff"
    axionite_ore: str = "#ff9500"

    raw_graph: str = "#d9d0ff"
    pruned_graph: str = "#7ec8ff"
    region_node: str = "#ffb347"
    choke_tile: str = "#ff0000"
    choke_geom: str = "#c40000"


@dataclass(frozen=True)
class CoreInfo:
    id: int
    team: int
    center: Cell
    footprint: FrozenSet[Cell]


@dataclass
class MapData:
    width: int
    height: int
    environment_rows: List[List[int]]
    obstacles: Set[Cell] = field(default_factory=set)
    titanium_ores: Set[Cell] = field(default_factory=set)
    axionite_ores: Set[Cell] = field(default_factory=set)
    cores: List[CoreInfo] = field(default_factory=list)
    symmetry: Optional[str] = None

@dataclass
class BlockerCandidate:
    start: VertexId
    choke: VertexId
    end: VertexId
    radius: float
    kind: str      # "wall" or "launcher"
    tile: Cell


# ---------------------------------------------------------------------------
# Map loading
# ---------------------------------------------------------------------------

def read_varint(buf: bytes, pos: int) -> Tuple[int, int]:
    result, shift = 0, 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def read_tag(buf: bytes, pos: int) -> Tuple[int, int, int]:
    tag, pos = read_varint(buf, pos)
    return tag >> 3, tag & 7, pos


def skip_field(buf: bytes, pos: int, wire: int) -> int:
    if wire == 0:
        _, pos = read_varint(buf, pos)
    elif wire == 1:
        pos += 8
    elif wire == 2:
        length, pos = read_varint(buf, pos)
        pos += length
    elif wire == 5:
        pos += 4
    else:
        raise ValueError(f"unsupported wire type {wire}")
    return pos


def parse_tile_row_message(buf: bytes) -> List[int]:
    row: List[int] = []
    pos = 0
    while pos < len(buf):
        field_num, wire, pos = read_tag(buf, pos)
        if field_num == 1 and wire == 2:
            length, pos = read_varint(buf, pos)
            end = pos + length
            while pos < end:
                value, pos = read_varint(buf, pos)
                row.append(value)
        elif field_num == 1 and wire == 0:
            value, pos = read_varint(buf, pos)
            row.append(value)
        else:
            pos = skip_field(buf, pos, wire)
    return row


def parse_pos_message(buf: bytes) -> Cell:
    x = y = 0
    pos = 0
    while pos < len(buf):
        field_num, wire, pos = read_tag(buf, pos)
        if field_num == 1 and wire == 0:
            x, pos = read_varint(buf, pos)
        elif field_num == 2 and wire == 0:
            y, pos = read_varint(buf, pos)
        else:
            pos = skip_field(buf, pos, wire)
    return (y, x)


def parse_core_position_message(buf: bytes) -> Tuple[int, int, Cell]:
    core_id = 0
    team = TEAM_A
    position = (0, 0)
    pos = 0
    while pos < len(buf):
        field_num, wire, pos = read_tag(buf, pos)
        if field_num == 1 and wire == 0:
            core_id, pos = read_varint(buf, pos)
        elif field_num == 2 and wire == 0:
            team, pos = read_varint(buf, pos)
        elif field_num == 3 and wire == 2:
            length, pos = read_varint(buf, pos)
            position = parse_pos_message(buf[pos : pos + length])
            pos += length
        else:
            pos = skip_field(buf, pos, wire)
    return (core_id, team, position)


def parse_map_message(buf: bytes) -> Tuple[int, int, List[List[int]], List[Tuple[int, int, Cell]]]:
    width = height = 0
    rows: List[List[int]] = []
    cores: List[Tuple[int, int, Cell]] = []

    pos = 0
    while pos < len(buf):
        field_num, wire, pos = read_tag(buf, pos)
        if field_num == 1 and wire == 0:
            width, pos = read_varint(buf, pos)
        elif field_num == 2 and wire == 0:
            height, pos = read_varint(buf, pos)
        elif field_num == 3 and wire == 2:
            length, pos = read_varint(buf, pos)
            rows.append(parse_tile_row_message(buf[pos : pos + length]))
            pos += length
        elif field_num == 4 and wire == 2:
            length, pos = read_varint(buf, pos)
            cores.append(parse_core_position_message(buf[pos : pos + length]))
            pos += length
        else:
            pos = skip_field(buf, pos, wire)

    return width, height, rows, cores


def map_shape_is_valid(width: int, height: int, rows: List[List[int]]) -> bool:
    return (
        width > 0
        and height > 0
        and len(rows) == height
        and all(len(row) == width for row in rows)
    )


def extract_map_from_replay(buf: bytes) -> Optional[Tuple[int, int, List[List[int]], List[Tuple[int, int, Cell]]]]:
    pos = 0
    while pos < len(buf):
        field_num, wire, pos = read_tag(buf, pos)
        if field_num == 1 and wire == 2:
            length, pos = read_varint(buf, pos)
            parsed = parse_map_message(buf[pos : pos + length])
            pos += length
            if map_shape_is_valid(parsed[0], parsed[1], parsed[2]):
                return parsed
        else:
            pos = skip_field(buf, pos, wire)
    return None


def core_footprint(center: Cell, rows: int, cols: int) -> FrozenSet[Cell]:
    # Assumes CorePosition stores the centre tile of the 3x3 core footprint.
    r, c = center
    cells = {
        (rr, cc)
        for rr in range(r - 1, r + 2)
        for cc in range(c - 1, c + 2)
        if 0 <= rr < rows and 0 <= cc < cols
    }
    return frozenset(cells)


def mirror_cell(cell: Cell, rows: int, cols: int, symmetry: str) -> Cell:
    r, c = cell
    if symmetry == SYMMETRY_VERTICAL:
        return (r, cols - 1 - c)
    if symmetry == SYMMETRY_HORIZONTAL:
        return (rows - 1 - r, c)
    if symmetry == SYMMETRY_ROTATIONAL:
        return (rows - 1 - r, cols - 1 - c)
    raise ValueError(f"unsupported symmetry {symmetry}")


def environment_rows_match_symmetry(
    environment_rows: List[List[int]],
    width: int,
    height: int,
    symmetry: str,
) -> bool:
    for r in range(height):
        for c in range(width):
            rr, cc = mirror_cell((r, c), height, width, symmetry)
            if environment_rows[r][c] != environment_rows[rr][cc]:
                return False
    return True


def cores_match_symmetry(
    cores: List[CoreInfo],
    width: int,
    height: int,
    symmetry: str,
) -> bool:
    if not cores:
        return True

    by_center: Dict[Cell, List[CoreInfo]] = defaultdict(list)
    for core in cores:
        by_center[core.center].append(core)

    for core in cores:
        mirrored_center = mirror_cell(core.center, height, width, symmetry)
        candidates = by_center.get(mirrored_center, [])
        if not candidates:
            return False

        expected_team = TEAM_B if core.team == TEAM_A else TEAM_A
        if not any(
            candidate.team == expected_team or candidate.team == core.team
            for candidate in candidates
        ):
            return False

    return True


def detect_map_symmetry(
    width: int,
    height: int,
    environment_rows: List[List[int]],
    cores: List[CoreInfo],
) -> Optional[str]:
    candidates = [
        symmetry
        for symmetry in (
            SYMMETRY_VERTICAL,
            SYMMETRY_HORIZONTAL,
            SYMMETRY_ROTATIONAL,
        )
        if environment_rows_match_symmetry(environment_rows, width, height, symmetry)
        and cores_match_symmetry(cores, width, height, symmetry)
    ]

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    core_a = next((core for core in cores if core.team == TEAM_A), None)
    core_b = next((core for core in cores if core.team == TEAM_B), None)
    preferred: List[str] = []

    if core_a is not None and core_b is not None:
        if core_a.center[0] == core_b.center[0]:
            preferred.append(SYMMETRY_VERTICAL)
        if core_a.center[1] == core_b.center[1]:
            preferred.append(SYMMETRY_HORIZONTAL)
        preferred.append(SYMMETRY_ROTATIONAL)

    for symmetry in preferred + candidates:
        if symmetry in candidates:
            return symmetry
    return candidates[0]


def build_map_data(
    width: int,
    height: int,
    environment_rows: List[List[int]],
    core_specs: List[Tuple[int, int, Cell]],
) -> MapData:
    obstacles: Set[Cell] = set()
    titanium_ores: Set[Cell] = set()
    axionite_ores: Set[Cell] = set()

    for r, row in enumerate(environment_rows):
        for c, env in enumerate(row):
            cell = (r, c)
            if env == ENV_WALL:
                obstacles.add(cell)
            elif env == ENV_ORE_TITANIUM:
                titanium_ores.add(cell)
            elif env == ENV_ORE_AXIONITE:
                axionite_ores.add(cell)

    cores = [
        CoreInfo(
            id=core_id,
            team=team,
            center=center,
            footprint=core_footprint(center, height, width),
        )
        for core_id, team, center in core_specs
    ]

    symmetry = detect_map_symmetry(width, height, environment_rows, cores)

    return MapData(
        width=width,
        height=height,
        environment_rows=environment_rows,
        obstacles=obstacles,
        titanium_ores=titanium_ores,
        axionite_ores=axionite_ores,
        cores=cores,
        symmetry=symmetry,
    )


def load_map_data(filename: str) -> MapData:
    """Parse a .map26 protobuf-encoded map file into terrain/resource/core data."""
    with open(filename, "rb") as f:
        data = f.read()

    width, height, rows, core_specs = parse_map_message(data)
    if not map_shape_is_valid(width, height, rows):
        replay_map = extract_map_from_replay(data)
        if replay_map is None:
            raise ValueError("could not decode a Map message from the file")
        width, height, rows, core_specs = replay_map

    assert map_shape_is_valid(width, height, rows), (
        f"map shape mismatch: expected {height}x{width}, "
        f"got {len(rows)} rows, widths={set(len(r) for r in rows)}"
    )
    return build_map_data(width, height, rows, core_specs)


def load_map_walls(filename: str) -> List[List[bool]]:
    """
    Parse a .map26 protobuf-encoded map file and return a 2-D boolean grid.
    True = wall / obstacle,  False = traversable.
    """
    map_data = load_map_data(filename)
    return [
        [env == ENV_WALL for env in row]
        for row in map_data.environment_rows
    ]


def walls_to_obstacles(rows: List[List[bool]]) -> Set[Cell]:
    """Convert a boolean wall grid to a set of (row, col) obstacle cells."""
    obstacles: Set[Cell] = set()
    for r, row in enumerate(rows):
        for c, is_wall in enumerate(row):
            if is_wall:
                obstacles.add((r, c))
    return obstacles


def auto_cell_size(rows: int, cols: int,
                   max_w: int = 1400, max_h: int = 860) -> int:
    """Pick the largest integer cell size that fits inside the screen budget."""
    size = min(max_w // max(cols, 1), max_h // max(rows, 1), 20)
    return max(size, 2)


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class GeometricChokepointApp:
    def __init__(self, root: tk.Tk, cfg: GridConfig,
                 initial_obstacles: Optional[Set[Cell]] = None,
                 map_data: Optional[MapData] = None,
                 logic_mode: str = LOGIC_RUNTIME,
                 runtime_math_mode: str = RUNTIME_MATH_DECIMAL) -> None:
        self.root = root
        self.cfg = cfg
        self.logic_mode = logic_mode
        self.runtime_math_mode = runtime_math_mode
        self.rounded_choke_kinds: Dict[Cell, str] = {}
        self.loaded_map_data = map_data
        self.configure_runtime_backend()

        # ---- editable raster ----
        if map_data is not None:
            self.obstacles: Set[Cell] = set(map_data.obstacles)
            self.titanium_ores: Set[Cell] = set(map_data.titanium_ores)
            self.axionite_ores: Set[Cell] = set(map_data.axionite_ores)
            self.cores: List[CoreInfo] = list(map_data.cores)
            self.source_symmetry = map_data.symmetry
        else:
            self.obstacles = set(initial_obstacles) if initial_obstacles else set()
            self.titanium_ores = set()
            self.axionite_ores = set()
            self.cores = []
            self.source_symmetry = None
        self.core_cells: Set[Cell] = {cell for core in self.cores for cell in core.footprint}
        self.painting = False
        self.paint_value = True          # True=draw, False=erase
        self.detected_symmetry: Optional[str] = self.source_symmetry

        # ---- parameter variables ----
        self.min_obstacle_area_var    = tk.StringVar(value="3")
        self.region_min_radius_var    = tk.StringVar(value="5.0")
        self.isolated_radius_var      = tk.StringVar(value="1.0")
        self.max_choke_radius_var     = tk.StringVar(value="3")
        self.simplify_eps_var         = tk.StringVar(value="0.0")
        self.sample_spacing_var       = tk.StringVar(value="1.0")
        self.show_radii_var           = tk.BooleanVar(value=True)

        # diagonal movement
        self.diagonal_movement_var    = tk.BooleanVar(value=False)
        self.diagonal_gap_var         = tk.StringVar(value="0.15")

        # region merging (Step 6)
        self.enable_merging_var       = tk.BooleanVar(value=True)
        self.merge_ratio_small_var    = tk.StringVar(value="0.7")
        self.merge_ratio_large_var    = tk.StringVar(value="0.6")
        self.merge_ratio_two_choke_var= tk.StringVar(value="0.5")

        loaded_note = (f"  Loaded {len(self.obstacles)} obstacle cells."
                       if self.obstacles else "")
        self.status_var = tk.StringVar(value=
            f"Left-drag draws obstacles.  Shift+left-drag erases.  "
            f"Enter or Analyze to run.{loaded_note}")

        # ---- geometric analysis state ----
        self.full_map_poly: List[Tuple[float, float]] = rectangle_polygon(self.cfg.cols, self.cfg.rows)
        self.analysis_poly: List[Tuple[float, float]] = list(self.full_map_poly)
        self.raster_scale = 4
        self.analysis_mask: List[List[bool]] = []
        self.kept_obstacle_mask: List[List[bool]] = []
        self.discarded_obstacle_mask: List[List[bool]] = []
        self.discarded_obstacle_cells: Set[Cell] = set()
        self.free_mask: List[List[bool]] = []
        self.free_boundary_segments: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
        self.raw_vertex_radius: Dict[VertexId, float] = {}
        self.kept_obstacle_geom = None
        self.discarded_obstacle_geom = None
        self.free_space = None

        self.raw_vertices:   Dict[VertexId, Tuple[float, float]] = {}
        self.raw_edges:      Set[Tuple[VertexId, VertexId]]      = set()

        self.pruned_vertices: Dict[VertexId, Tuple[float, float]] = {}
        self.pruned_edges:    Set[Tuple[VertexId, VertexId]]      = set()
        self.radius:          Dict[VertexId, float]               = {}

        self.region_nodes:  Set[VertexId]                                  = set()
        self.choke_nodes:   Set[VertexId]                                  = set()
        self.choke_links:   List[Tuple[VertexId, VertexId, VertexId]]      = []
        self.rounded_choke_tiles: Dict[Cell, VertexId]                     = {}

        # ---- canvas bookkeeping ----
        self.rect_ids:    Dict[Cell, int] = {}
        self.text_ids:    Dict[Cell, int] = {}
        self.overlay_ids: List[int]       = []

        self.root.title("Geometric Voronoi Chokepoints")
        self._build_ui()
        self._build_grid()
        self._bind_events()
        self.redraw()
        
    def init_headless(
        self,
        cfg: GridConfig,
        obstacles: Optional[Set[Cell]] = None,
        map_data: Optional[MapData] = None,
        logic_mode: str = LOGIC_RUNTIME,
        runtime_math_mode: str = RUNTIME_MATH_DECIMAL,
    ) -> None:
        self.cfg = cfg
        self.logic_mode = logic_mode
        self.runtime_math_mode = runtime_math_mode
        self.loaded_map_data = map_data
        self.configure_runtime_backend()
        if map_data is not None:
            self.obstacles = set(map_data.obstacles)
            self.titanium_ores = set(map_data.titanium_ores)
            self.axionite_ores = set(map_data.axionite_ores)
            self.cores = list(map_data.cores)
            self.source_symmetry = map_data.symmetry
        else:
            self.obstacles = set(obstacles) if obstacles else set()
            self.titanium_ores = set()
            self.axionite_ores = set()
            self.cores = []
            self.source_symmetry = None
        self.core_cells = {cell for core in self.cores for cell in core.footprint}
        self.detected_symmetry = self.source_symmetry

        self.full_map_poly = rectangle_polygon(cfg.cols, cfg.rows)
        self.analysis_poly = list(self.full_map_poly)
        self.raster_scale = 4

        # ---- REQUIRED STATE (must exist BEFORE clear_analysis_only) ----
        self.analysis_mask = []
        self.kept_obstacle_mask = []
        self.discarded_obstacle_mask = []
        self.discarded_obstacle_cells = set()
        self.free_mask = []
        self.free_boundary_segments = []
        self.raw_vertex_radius = {}
        self.kept_obstacle_geom = None
        self.discarded_obstacle_geom = None
        self.free_space = None

        self.raw_vertices = {}
        self.raw_edges = set()

        self.pruned_vertices = {}
        self.pruned_edges = set()
        self.radius = {}

        self.region_nodes = set()
        self.choke_nodes = set()
        self.choke_links = []
        self.rounded_choke_tiles = {}
        self.rounded_choke_kinds = {}

        # UI-related stubs (safe in headless)
        self.canvas = None
        self.overlay_ids = []
        self.text_ids = {}

        # now safe
        self.clear_analysis_only()

    def configure_runtime_backend(self) -> None:
        if self.logic_mode == LOGIC_RUNTIME:
            set_foronoi_numeric_mode(self.runtime_math_mode)
        else:
            set_foronoi_numeric_mode(RUNTIME_MATH_DECIMAL)

    # ===================================================================== #
    #  UI                                                                    #
    # ===================================================================== #

    def _build_ui(self) -> None:
        def lbl_entry(parent, label: str, var, width: int = 5) -> None:
            tk.Label(parent, text=label).pack(side="left", padx=(10, 2))
            tk.Entry(parent, textvariable=var, width=width).pack(side="left")

        # --- row 1: core analysis parameters ---
        row1 = tk.Frame(self.root)
        row1.pack(fill="x", padx=8, pady=(8, 2))

        tk.Button(row1, text="Analyze", command=self.analyze).pack(side="left")
        tk.Button(row1, text="Clear",   command=self.clear_all).pack(side="left", padx=(6, 0))

        lbl_entry(row1, "Min obstacle area", self.min_obstacle_area_var, 4)
        lbl_entry(row1, "Region min radius", self.region_min_radius_var)
        lbl_entry(row1, "Isolated radius",   self.isolated_radius_var)
        lbl_entry(row1, "Max choke radius",  self.max_choke_radius_var)
        lbl_entry(row1, "Simplify eps",      self.simplify_eps_var)
        lbl_entry(row1, "Sample spacing",    self.sample_spacing_var, 4)
        tk.Checkbutton(row1, text="Show radii", variable=self.show_radii_var,
                       command=self.redraw).pack(side="left", padx=(12, 0))

        # --- row 2: diagonal movement + region merging ---
        row2 = tk.Frame(self.root)
        row2.pack(fill="x", padx=8, pady=(0, 2))

        tk.Checkbutton(row2, text="Diagonal movement",
                       variable=self.diagonal_movement_var).pack(side="left")
        lbl_entry(row2, "Diag gap", self.diagonal_gap_var, 5)

        tk.Label(row2, text="  |").pack(side="left")

        tk.Checkbutton(row2, text="Merge regions (Step 6)",
                       variable=self.enable_merging_var).pack(side="left", padx=(8, 0))
        lbl_entry(row2, "Ratio small",  self.merge_ratio_small_var)
        lbl_entry(row2, "Ratio large",  self.merge_ratio_large_var)
        lbl_entry(row2, "Ratio 2-choke", self.merge_ratio_two_choke_var)

        # --- legend ---
        legend = tk.Frame(self.root)
        legend.pack(fill="x", padx=8, pady=(0, 4))
        tk.Label(legend, anchor="w", text=(
            "Brown=core  Light blue=titanium  Orange=axionite  "
            "Black=kept obstacle  Gray=discarded  Lavender=raw Voronoi  "
            "Blue=pruned graph  Orange node=region node  Red=choke tile"
        )).pack(side="left")

        # --- canvas ---
        self.canvas = tk.Canvas(
            self.root,
            width=self.cfg.cols * self.cfg.cell_size,
            height=self.cfg.rows * self.cfg.cell_size,
            bg=self.cfg.empty,
            highlightthickness=0,
        )
        self.canvas.pack(padx=8, pady=(0, 4))

        # --- status bar ---
        tk.Label(self.root, textvariable=self.status_var,
                 anchor="w").pack(fill="x", padx=8, pady=(0, 8))

    def _build_grid(self) -> None:
        for r in range(self.cfg.rows):
            for c in range(self.cfg.cols):
                x0 = c * self.cfg.cell_size
                y0 = r * self.cfg.cell_size
                rect = self.canvas.create_rectangle(
                    x0, y0,
                    x0 + self.cfg.cell_size, y0 + self.cfg.cell_size,
                    fill=self.cfg.empty, outline=self.cfg.grid_line,
                )
                self.rect_ids[(r, c)] = rect

    def _bind_events(self) -> None:
        self.canvas.bind("<Button-1>",        self.on_left_down)
        self.canvas.bind("<B1-Motion>",       self.on_left_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouse_up)
        self.root.bind("<Return>",            lambda _e: self.analyze())
        self.root.bind("c",                   lambda _e: self.clear_all())

    # ===================================================================== #
    #  Input handlers                                                        #
    # ===================================================================== #

    def shift_held(self, event: tk.Event) -> bool:
        return bool(event.state & 0x0001)

    def cell_from_event(self, event: tk.Event) -> Optional[Cell]:
        c = event.x // self.cfg.cell_size
        r = event.y // self.cfg.cell_size
        if 0 <= r < self.cfg.rows and 0 <= c < self.cfg.cols:
            return (r, c)
        return None

    def on_left_down(self, event: tk.Event) -> None:
        self.painting    = True
        self.paint_value = not self.shift_held(event)
        self.paint_cell_from_event(event)

    def on_left_drag(self, event: tk.Event) -> None:
        if self.painting:
            self.paint_value = not self.shift_held(event)
            self.paint_cell_from_event(event)

    def on_mouse_up(self, _event: tk.Event) -> None:
        self.painting = False

    def paint_cell_from_event(self, event: tk.Event) -> None:
        cell = self.cell_from_event(event)
        if cell is None:
            return
        if self.paint_value:
            self.obstacles.add(cell)
        else:
            self.obstacles.discard(cell)
        self.clear_analysis_only()
        self.redraw()
        self.status_var.set(
            f"Obstacle cells: {len(self.obstacles)}.  "
            "Press Analyze (or Enter) to compute the graph.")

    # ===================================================================== #
    #  State management                                                      #
    # ===================================================================== #

    def clear_analysis_only(self) -> None:
        self.analysis_poly = list(self.full_map_poly)
        self.analysis_mask = []
        self.kept_obstacle_mask = []
        self.discarded_obstacle_mask = []
        self.discarded_obstacle_cells.clear()
        self.free_mask = []
        self.free_boundary_segments = []
        self.raw_vertex_radius.clear()
        self.kept_obstacle_geom = None
        self.discarded_obstacle_geom = None
        self.free_space = None

        self.raw_vertices.clear();  self.raw_edges.clear()
        self.pruned_vertices.clear(); self.pruned_edges.clear()
        self.radius.clear()

        self.region_nodes.clear()
        self.choke_nodes.clear()
        self.choke_links.clear()
        self.rounded_choke_tiles.clear()
        self.rounded_choke_kinds.clear()

    def clear_all(self) -> None:
        self.obstacles.clear()
        self.clear_analysis_only()
        self.redraw()
        self.status_var.set(
            "Cleared.  Left-drag draws obstacles.  Shift+left-drag erases.")

    # ===================================================================== #
    #  Parameter parsing                                                     #
    # ===================================================================== #
    
    def blocker_kind_for_radius(self, r: float) -> Optional[str]:
        """
        Map geometric choke width -> usable game blocker.

        Tune these thresholds based on gameplay feel.
        """
        WALL_MAX = 0.8        # narrow corridor
        LAUNCHER_MAX = 1.8    # medium corridor

        if r <= WALL_MAX:
            return "wall"
        if r <= LAUNCHER_MAX:
            return "launcher"
        return None


    def blocker_footprint(self, tile: Cell, kind: str) -> Set[Cell]:
        """
        Returns grid cells occupied by the blocker.
        """
        r, c = tile
        radius = 0 if kind == "wall" else 1

        cells = set()
        for rr in range(r - radius, r + radius + 1):
            for cc in range(c - radius, c + radius + 1):
                if 0 <= rr < self.cfg.rows and 0 <= cc < self.cfg.cols:
                    cells.add((rr, cc))
        return cells


    def simplify_choke_points_for_game(self) -> None:
        """
        Converts geometric chokepoints into:
        - wall (1x1)
        - launcher (3x3)

        Then removes overlapping / clustered choke points.
        """

        candidates: List[BlockerCandidate] = []

        # ---- convert to candidates ----
        for start, choke, end in self.choke_links:
            r = self.radius.get(choke, 0.0)

            kind = self.blocker_kind_for_radius(r)
            if kind is None:
                continue

            tile = self._round_point_to_tile(self.pruned_vertices[choke])
            if tile is None:
                continue

            candidates.append(BlockerCandidate(start, choke, end, r, kind, tile))

        if not candidates:
            self.choke_nodes.clear()
            self.choke_links.clear()
            self.rounded_choke_tiles.clear()
            self.rounded_choke_kinds.clear()
            return

        # ---- union-find clustering (overlapping footprints) ----
        parent = list(range(len(candidates)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        # overlap detection via occupied cells
        owner = {}
        for i, cand in enumerate(candidates):
            for cell in self.blocker_footprint(cand.tile, cand.kind):
                if cell in owner:
                    union(i, owner[cell])
                else:
                    owner[cell] = i

        # group clusters
        groups = defaultdict(list)
        for i in range(len(candidates)):
            groups[find(i)].append(i)

        # ---- pick best per cluster ----
        kept_links = []
        kept_nodes = set()
        kept_tiles = {}
        kept_kinds = {}

        for group in groups.values():
            # prefer:
            # 1. smallest radius (tightest choke)
            # 2. wall over launcher
            best = min(
                group,
                key=lambda i: (
                    candidates[i].radius,
                    0 if candidates[i].kind == "wall" else 1,
                ),
            )

            cand = candidates[best]

            kept_links.append((cand.start, cand.choke, cand.end))
            kept_nodes.add(cand.choke)

            prev = kept_tiles.get(cand.tile)
            if prev is None or self.radius[cand.choke] < self.radius[prev]:
                kept_tiles[cand.tile] = cand.choke
                kept_kinds[cand.tile] = cand.kind

        # ---- apply ----
        self.choke_links = kept_links
        self.choke_nodes = kept_nodes
        self.rounded_choke_tiles = kept_tiles
        self.rounded_choke_kinds = kept_kinds

    def parse_int(self, value: str, default: int) -> int:
        try:
            return max(0, int(value))
        except ValueError:
            return default

    def parse_float(self, value: str, default: float) -> float:
        try:
            return max(0.0, float(value))
        except ValueError:
            return default

    def current_environment_rows(self) -> List[List[int]]:
        rows = [
            [ENV_EMPTY for _ in range(self.cfg.cols)]
            for _ in range(self.cfg.rows)
        ]

        for r, c in self.titanium_ores:
            if 0 <= r < self.cfg.rows and 0 <= c < self.cfg.cols:
                rows[r][c] = ENV_ORE_TITANIUM

        for r, c in self.axionite_ores:
            if 0 <= r < self.cfg.rows and 0 <= c < self.cfg.cols:
                rows[r][c] = ENV_ORE_AXIONITE

        for r, c in self.obstacles:
            if 0 <= r < self.cfg.rows and 0 <= c < self.cfg.cols:
                rows[r][c] = ENV_WALL

        return rows

    def team_core(self, team: int) -> Optional[CoreInfo]:
        return next((core for core in self.cores if core.team == team), None)

    def anchor_core(self) -> Optional[CoreInfo]:
        return self.team_core(TEAM_A) or (self.cores[0] if self.cores else None)

    def opponent_core(self, anchor: CoreInfo) -> Optional[CoreInfo]:
        for core in self.cores:
            if core.id != anchor.id:
                return core
        return None

    def detect_current_symmetry(self) -> Optional[str]:
        symmetry = detect_map_symmetry(
            self.cfg.cols,
            self.cfg.rows,
            self.current_environment_rows(),
            self.cores,
        )
        self.detected_symmetry = symmetry
        return symmetry

    def core_center_world(self, core: CoreInfo) -> Tuple[float, float]:
        r, c = core.center
        return (c + 0.5, r + 0.5)

    def compute_analysis_polygon(self, symmetry: Optional[str]) -> List[Tuple[float, float]]:
        if symmetry is None:
            return list(self.full_map_poly)

        anchor = self.anchor_core()
        if anchor is None:
            return list(self.full_map_poly)

        anchor_x, anchor_y = self.core_center_world(anchor)

        if symmetry == SYMMETRY_VERTICAL:
            mid_x = self.cfg.cols / 2.0
            if anchor_x <= mid_x:
                return [(0.0, 0.0), (mid_x, 0.0), (mid_x, float(self.cfg.rows)), (0.0, float(self.cfg.rows))]
            return [(mid_x, 0.0), (float(self.cfg.cols), 0.0), (float(self.cfg.cols), float(self.cfg.rows)), (mid_x, float(self.cfg.rows))]

        if symmetry == SYMMETRY_HORIZONTAL:
            mid_y = self.cfg.rows / 2.0
            if anchor_y <= mid_y:
                return [(0.0, 0.0), (float(self.cfg.cols), 0.0), (float(self.cfg.cols), mid_y), (0.0, mid_y)]
            return [(0.0, mid_y), (float(self.cfg.cols), mid_y), (float(self.cfg.cols), float(self.cfg.rows)), (0.0, float(self.cfg.rows))]

        if symmetry == SYMMETRY_ROTATIONAL:
            other = self.opponent_core(anchor)
            if other is None:
                return list(self.full_map_poly)

            other_x, other_y = self.core_center_world(other)
            mid = ((anchor_x + other_x) / 2.0, (anchor_y + other_y) / 2.0)
            normal = (other_x - anchor_x, other_y - anchor_y)
            return clip_polygon_against_half_plane(
                self.full_map_poly,
                mid=mid,
                normal=normal,
                keep_negative_side=True,
            )

        return list(self.full_map_poly)

    def mirror_point(self, pt: Tuple[float, float], symmetry: str) -> Tuple[float, float]:
        x, y = pt
        if symmetry == SYMMETRY_VERTICAL:
            return (self.cfg.cols - x, y)
        if symmetry == SYMMETRY_HORIZONTAL:
            return (x, self.cfg.rows - y)
        if symmetry == SYMMETRY_ROTATIONAL:
            return (self.cfg.cols - x, self.cfg.rows - y)
        raise ValueError(f"unsupported symmetry {symmetry}")

    def mirror_graph_state(
        self,
        vertices: Dict[VertexId, Tuple[float, float]],
        edges: Set[Tuple[VertexId, VertexId]],
        radius_map: Optional[Dict[VertexId, float]] = None,
    ) -> Tuple[
        Dict[VertexId, Tuple[float, float]],
        Set[Tuple[VertexId, VertexId]],
        Dict[VertexId, float],
        Dict[VertexId, VertexId],
        Dict[VertexId, VertexId],
    ]:
        if self.detected_symmetry is None or not vertices:
            return (
                dict(vertices),
                set(edges),
                dict(radius_map) if radius_map is not None else {},
                {vid: vid for vid in vertices},
                {vid: vid for vid in vertices},
            )

        key_to_vid: Dict[Tuple[float, float], VertexId] = {}
        new_vertices: Dict[VertexId, Tuple[float, float]] = {}
        new_radius: Dict[VertexId, float] = {}

        def add_vertex(pt: Tuple[float, float], src_vid: Optional[VertexId]) -> VertexId:
            key = self.quantize_point(pt[0], pt[1])
            vid = key_to_vid.get(key)
            if vid is None:
                vid = len(key_to_vid)
                key_to_vid[key] = vid
                new_vertices[vid] = key
            if radius_map is not None and src_vid is not None:
                new_radius[vid] = max(new_radius.get(vid, 0.0), radius_map.get(src_vid, 0.0))
            return vid

        original_map: Dict[VertexId, VertexId] = {}
        mirrored_map: Dict[VertexId, VertexId] = {}

        for old_vid in sorted(vertices):
            original_map[old_vid] = add_vertex(vertices[old_vid], old_vid)
        for old_vid in sorted(vertices):
            mirrored_pt = self.mirror_point(vertices[old_vid], self.detected_symmetry)
            mirrored_map[old_vid] = add_vertex(mirrored_pt, old_vid)

        new_edges: Set[Tuple[VertexId, VertexId]] = set()
        for a, b in edges:
            for aa, bb in (
                (original_map[a], original_map[b]),
                (mirrored_map[a], mirrored_map[b]),
            ):
                if aa == bb:
                    continue
                edge = (aa, bb) if aa < bb else (bb, aa)
                new_edges.add(edge)

        return new_vertices, new_edges, new_radius, original_map, mirrored_map

    def mirror_analysis_results(self) -> None:
        if self.detected_symmetry is None:
            return

        self.raw_vertices, self.raw_edges, _, _, _ = self.mirror_graph_state(
            self.raw_vertices,
            self.raw_edges,
        )

        (
            self.pruned_vertices,
            self.pruned_edges,
            mirrored_radius,
            original_vids,
            mirrored_vids,
        ) = self.mirror_graph_state(
            self.pruned_vertices,
            self.pruned_edges,
            self.radius,
        )
        self.radius = mirrored_radius

        self.region_nodes = {
            original_vids[vid]
            for vid in self.region_nodes
            if vid in original_vids
        } | {
            mirrored_vids[vid]
            for vid in self.region_nodes
            if vid in mirrored_vids
        }

        self.choke_nodes = {
            original_vids[vid]
            for vid in self.choke_nodes
            if vid in original_vids
        } | {
            mirrored_vids[vid]
            for vid in self.choke_nodes
            if vid in mirrored_vids
        }

        mirrored_links: Set[Tuple[VertexId, VertexId, VertexId]] = set()
        for start, choke, end in self.choke_links:
            if start in original_vids and choke in original_vids and end in original_vids:
                mirrored_links.add((
                    original_vids[start],
                    original_vids[choke],
                    original_vids[end],
                ))
            if start in mirrored_vids and choke in mirrored_vids and end in mirrored_vids:
                mirrored_links.add((
                    mirrored_vids[start],
                    mirrored_vids[choke],
                    mirrored_vids[end],
                ))
        self.choke_links = sorted(mirrored_links)

        new_tiles: Dict[Cell, VertexId] = {}
        new_kinds: Dict[Cell, str] = {}
        for tile, vid in list(self.rounded_choke_tiles.items()):
            kind = self.rounded_choke_kinds.get(tile, "wall")
            for mapped_tile, mapped_vid in (
                (tile, original_vids.get(vid)),
                (mirror_cell(tile, self.cfg.rows, self.cfg.cols, self.detected_symmetry),
                 mirrored_vids.get(vid)),
            ):
                if mapped_vid is None:
                    continue
                prev_vid = new_tiles.get(mapped_tile)
                if prev_vid is None or self.radius[mapped_vid] < self.radius[prev_vid]:
                    new_tiles[mapped_tile] = mapped_vid
                    new_kinds[mapped_tile] = kind

        self.rounded_choke_tiles = new_tiles
        self.rounded_choke_kinds = new_kinds

    # ===================================================================== #
    #  Step 1 – Build obstacle geometry                                      #
    # ===================================================================== #

    def build_obstacle_geometry(
        self,
        min_area: int,
        simplify_eps: float,
        diagonal_movement: bool = False,
        diagonal_gap: float = 0.15,
    ) -> bool:
        if self.logic_mode == LOGIC_OLD:
            return self.build_obstacle_geometry_legacy(
                min_area=min_area,
                simplify_eps=simplify_eps,
                diagonal_movement=diagonal_movement,
                diagonal_gap=diagonal_gap,
            )

        """
        Rasterize the current obstacle set into a local mask representation.
        This keeps the chokepoint pipeline self-contained and feeds a vendored
        pure-Python Voronoi implementation.
        """
        if not self.obstacles:
            return False

        _ = simplify_eps  # Preserved for UI compatibility.

        scale = self.raster_scale
        self.analysis_mask = build_analysis_mask(
            self.cfg.rows,
            self.cfg.cols,
            scale,
            self.analysis_poly,
        )
        obstacle_mask = build_obstacle_mask(
            self.obstacles,
            self.cfg.rows,
            self.cfg.cols,
            scale,
            self.analysis_mask,
        )

        if diagonal_movement and diagonal_gap > 0:
            apply_diagonal_notches(
                obstacle_mask,
                self.obstacles,
                self.analysis_mask,
                scale,
                diagonal_gap,
            )

        min_area_pixels = max(1, int(math.ceil(min_area * scale * scale)))
        (
            self.kept_obstacle_mask,
            self.discarded_obstacle_mask,
        ) = split_obstacle_mask_by_area(obstacle_mask, min_area_pixels)

        self.discarded_obstacle_cells = cell_centers_covered_by_mask(
            self.discarded_obstacle_mask,
            self.cfg.rows,
            self.cfg.cols,
            scale,
        )
        self.free_mask = build_free_mask(self.analysis_mask, self.kept_obstacle_mask)
        self.free_boundary_segments = boundary_segments_from_mask(self.free_mask, scale)

        return any(any(row) for row in self.kept_obstacle_mask)

    def build_obstacle_geometry_legacy(
        self,
        min_area: int,
        simplify_eps: float,
        diagonal_movement: bool = False,
        diagonal_gap: float = 0.15,
    ) -> bool:
        backend = load_legacy_backend()
        box = backend["box"]
        unary_union = backend["unary_union"]
        polygon_cls = backend["Polygon"]

        if not self.obstacles:
            return False

        analysis_geom = polygon_cls(self.analysis_poly)
        squares = [box(c, r, c + 1, r + 1) for (r, c) in self.obstacles]
        unioned = unary_union(squares).intersection(analysis_geom)

        if diagonal_movement and diagonal_gap > 0:
            notch_polys = []

            for (r, c) in self.obstacles:
                for dr, dc in [(1, -1), (1, 1)]:
                    nr, nc = r + dr, c + dc
                    if (nr, nc) not in self.obstacles:
                        continue
                    if (r + dr, c) in self.obstacles or (r, c + dc) in self.obstacles:
                        continue
                    cx = c + (1 if dc > 0 else 0)
                    cy = r + 1
                    notch_polys.append(
                        box(
                            cx - diagonal_gap,
                            cy - diagonal_gap,
                            cx + diagonal_gap,
                            cy + diagonal_gap,
                        )
                    )

            if notch_polys:
                unioned = unioned.difference(unary_union(notch_polys))

        all_polys = extract_legacy_polygons(unioned, backend)
        kept = []
        discarded = []

        for poly in all_polys:
            if poly.area >= min_area:
                if simplify_eps > 0:
                    poly = poly.simplify(simplify_eps, preserve_topology=True)
                kept.append(poly)
            else:
                discarded.append(poly)

        self.kept_obstacle_geom = unary_union(kept) if kept else None
        self.discarded_obstacle_geom = unary_union(discarded) if discarded else None

        if self.kept_obstacle_geom is None:
            self.free_space = analysis_geom
            return False

        self.free_space = analysis_geom.difference(self.kept_obstacle_geom)
        return True

    # ===================================================================== #
    #  Step 2 – Compute Voronoi graph                                        #
    # ===================================================================== #

    def sample_segment(
        self, start: Tuple[float, float], end: Tuple[float, float], spacing: float
    ) -> List[Tuple[float, float]]:
        x1, y1 = start
        x2, y2 = end
        out: List[Tuple[float, float]] = []
        seg_len = math.hypot(x2 - x1, y2 - y1)
        steps = max(1, int(math.ceil(seg_len / spacing)))
        for k in range(steps + 1):
            t = k / steps
            out.append((
                round(x1 + t * (x2 - x1), 4),
                round(y1 + t * (y2 - y1), 4),
            ))
        return out

    def collect_boundary_samples(self, spacing: float) -> List[Tuple[float, float]]:
        if self.logic_mode == LOGIC_OLD:
            return self.collect_boundary_samples_legacy(spacing)

        samples: Set[Tuple[float, float]] = set()
        spacing = max(spacing, 1.0 / max(self.raster_scale, 1))

        for start, end in self.free_boundary_segments:
            for point in self.sample_segment(start, end, spacing):
                samples.add(point)

        return sorted(samples)

    def sample_linestring_legacy(self, line, spacing: float) -> List[Tuple[float, float]]:
        coords = list(line.coords)
        out: List[Tuple[float, float]] = []
        for i in range(len(coords) - 1):
            x1, y1 = coords[i]
            x2, y2 = coords[i + 1]
            seg_len = math.hypot(x2 - x1, y2 - y1)
            steps = max(1, int(math.ceil(seg_len / spacing)))
            for k in range(steps + 1):
                t = k / steps
                out.append((
                    round(x1 + t * (x2 - x1), 4),
                    round(y1 + t * (y2 - y1), 4),
                ))
        return out

    def sample_ring_legacy(self, ring, spacing: float, out: Set[Tuple[float, float]]) -> None:
        backend = load_legacy_backend()
        line_string_cls = backend["LineString"]
        for point in self.sample_linestring_legacy(line_string_cls(ring.coords), spacing):
            out.add(point)

    def collect_boundary_samples_legacy(self, spacing: float) -> List[Tuple[float, float]]:
        backend = load_legacy_backend()
        np = backend["np"]

        samples: Set[Tuple[float, float]] = set()
        border = backend["Polygon"](self.analysis_poly).boundary
        rings = [border] if border.geom_type == "LineString" else list(border.geoms)
        for ring in rings:
            for point in self.sample_linestring_legacy(ring, spacing):
                samples.add(point)

        if self.kept_obstacle_geom is not None:
            for poly in extract_legacy_polygons(self.kept_obstacle_geom, backend):
                self.sample_ring_legacy(poly.exterior, spacing, samples)
                for hole in poly.interiors:
                    self.sample_ring_legacy(hole, spacing, samples)

        margin = 2 * max(self.cfg.rows, self.cfg.cols)
        for gx, gy in [
            (-margin, -margin),
            (-margin, self.cfg.rows + margin),
            (self.cfg.cols + margin, -margin),
            (self.cfg.cols + margin, self.cfg.rows + margin),
        ]:
            samples.add((gx, gy))

        return [tuple(point) for point in np.array(sorted(samples), dtype=float)]

    def quantize_point(self, x: float, y: float,
                       digits: int = 4) -> Tuple[float, float]:
        return (round(x, digits), round(y, digits))

    def point_is_inside_free_space(self, pt: Tuple[float, float]) -> bool:
        if not self.free_mask:
            return False

        x, y = pt
        height = len(self.free_mask)
        width = len(self.free_mask[0]) if height else 0
        if width == 0 or height == 0:
            return False
        if x < 0.0 or y < 0.0 or x > self.cfg.cols or y > self.cfg.rows:
            return False

        px = min(width - 1, max(0, int(math.floor(x * self.raster_scale))))
        py = min(height - 1, max(0, int(math.floor(y * self.raster_scale))))
        return self.free_mask[py][px]

    def segment_is_inside_free_space(
        self, p1: Tuple[float, float], p2: Tuple[float, float]
    ) -> bool:
        if self.logic_mode == LOGIC_OLD:
            return self.segment_is_inside_free_space_legacy(p1, p2)

        seg_len = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        if seg_len < 1e-6:
            return False

        steps = max(5, int(math.ceil(seg_len * self.raster_scale * 2)))
        for k in range(1, steps):
            t = k / steps
            pt = (
                p1[0] + t * (p2[0] - p1[0]),
                p1[1] + t * (p2[1] - p1[1]),
            )
            if not self.point_is_inside_free_space(pt):
                return False
        return True

    def segment_is_inside_free_space_legacy(
        self, p1: Tuple[float, float], p2: Tuple[float, float]
    ) -> bool:
        backend = load_legacy_backend()
        line_string_cls = backend["LineString"]
        seg = line_string_cls([p1, p2])
        if seg.length < 1e-6:
            return False
        for t in (0.1, 0.3, 0.5, 0.7, 0.9):
            pt = seg.interpolate(t, normalized=True)
            if not self.free_space.covers(pt):
                return False
        return True

    def compute_geometric_voronoi_graph(self, spacing: float) -> None:
        if self.logic_mode == LOGIC_OLD:
            self.compute_geometric_voronoi_graph_legacy(spacing)
            return

        self.configure_runtime_backend()
        self.raw_vertices.clear()
        self.raw_edges.clear()
        self.raw_vertex_radius.clear()

        points = self.collect_boundary_samples(spacing)
        if len(points) < 4 or len(self.analysis_poly) < 3:
            return

        vor = ForonoiVoronoi(ForonoiPolygon(self.analysis_poly))
        vor.create_diagram(points=points)
        key_to_vid: Dict[Tuple[float, float], VertexId] = {}

        def get_vid(pt: Tuple[float, float]) -> VertexId:
            key = self.quantize_point(pt[0], pt[1])
            if key not in key_to_vid:
                vid = len(key_to_vid)
                key_to_vid[key] = vid
                self.raw_vertices[vid] = key
                self.raw_vertex_radius[vid] = self.compute_radius(key)
            return key_to_vid[key]

        for edge in vor.edges:
            if edge.twin is None or edge.twin.incident_point is None:
                continue

            origin = edge.get_origin()
            target = edge.twin.get_origin()
            if origin is None or target is None:
                continue

            p1 = (float(origin.x), float(origin.y))
            p2 = (float(target.x), float(target.y))

            if not self.segment_is_inside_free_space(p1, p2):
                continue

            v1 = get_vid(p1)
            v2 = get_vid(p2)
            if v1 == v2:
                continue

            edge_key = (v1, v2) if v1 < v2 else (v2, v1)
            self.raw_edges.add(edge_key)

    def compute_geometric_voronoi_graph_legacy(self, spacing: float) -> None:
        backend = load_legacy_backend()
        voronoi_cls = backend["Voronoi"]

        self.raw_vertices.clear()
        self.raw_edges.clear()

        points = self.collect_boundary_samples_legacy(spacing)
        if len(points) < 4:
            return

        vor = voronoi_cls(points)
        key_to_vid: Dict[Tuple[float, float], VertexId] = {}

        def get_vid(pt: Tuple[float, float]) -> VertexId:
            key = self.quantize_point(pt[0], pt[1])
            if key not in key_to_vid:
                vid = len(key_to_vid)
                key_to_vid[key] = vid
                self.raw_vertices[vid] = key
            return key_to_vid[key]

        for ridge in vor.ridge_vertices:
            if len(ridge) != 2:
                continue
            a, b = ridge
            if a == -1 or b == -1:
                continue

            p1 = tuple(vor.vertices[a])
            p2 = tuple(vor.vertices[b])

            if not self.segment_is_inside_free_space_legacy(p1, p2):
                continue

            v1 = get_vid(p1)
            v2 = get_vid(p2)
            if v1 == v2:
                continue

            edge = (v1, v2) if v1 < v2 else (v2, v1)
            self.raw_edges.add(edge)

    # ===================================================================== #
    #  Step 3 – Prune Voronoi diagram                                       #
    # ===================================================================== #

    def build_adjacency(
        self, edges: Set[Tuple[VertexId, VertexId]]
    ) -> Dict[VertexId, Set[VertexId]]:
        adj: Dict[VertexId, Set[VertexId]] = defaultdict(set)
        for a, b in edges:
            adj[a].add(b)
            adj[b].add(a)
        return adj

    def compute_radius(self, pt: Tuple[float, float]) -> float:
        if self.logic_mode == LOGIC_OLD:
            return self.compute_radius_legacy(pt)

        segments = self.free_boundary_segments
        if not segments:
            return 0.0

        x, y = pt
        best = float("inf")
        for (x1, y1), (x2, y2) in segments:
            if x1 == x2:
                low_y = y1 if y1 <= y2 else y2
                high_y = y2 if y2 >= y1 else y1
                clamped_y = low_y if y <= low_y else high_y if y >= high_y else y
                dist = math.hypot(x - x1, y - clamped_y)
            elif y1 == y2:
                low_x = x1 if x1 <= x2 else x2
                high_x = x2 if x2 >= x1 else x1
                clamped_x = low_x if x <= low_x else high_x if x >= high_x else x
                dist = math.hypot(x - clamped_x, y - y1)
            else:
                dist = point_to_segment_distance(pt, ((x1, y1), (x2, y2)))

            if dist < best:
                best = dist

        return best

    def compute_radius_legacy(self, pt: Tuple[float, float]) -> float:
        backend = load_legacy_backend()
        point_cls = backend["Point"]
        return point_cls(pt[0], pt[1]).distance(self.free_space.boundary)

    def prune_graph(self, isolated_radius_threshold: float) -> None:
        active_edges: Set[Tuple[VertexId, VertexId]] = set(self.raw_edges)
        active_vertices: Set[VertexId] = set()
        for a, b in active_edges:
            active_vertices.add(a)
            active_vertices.add(b)

        self.radius = {
            vid: self.raw_vertex_radius.get(vid, self.compute_radius(self.raw_vertices[vid]))
            for vid in active_vertices
        }

        adj = self.build_adjacency(active_edges)

        # Iteratively remove leaves whose radius < parent's radius
        leaves: deque = deque(
            v for v in active_vertices if len(adj[v]) == 1
        )

        while leaves:
            leaf = leaves.popleft()
            if leaf not in active_vertices:
                continue
            if len(adj[leaf]) != 1:
                continue

            parent = next(iter(adj[leaf]))

            # Only prune if the leaf is "less open" than its parent
            if self.radius[leaf] < self.radius[parent]:
                edge = (leaf, parent) if leaf < parent else (parent, leaf)
                active_edges.discard(edge)
                adj[parent].discard(leaf)
                adj[leaf].discard(parent)
                active_vertices.discard(leaf)

                if len(adj[parent]) == 1:
                    leaves.append(parent)

        # Remove isolated vertices (degree 0) below the radius threshold
        for v in list(active_vertices):
            if len(adj[v]) == 0 and self.radius[v] < isolated_radius_threshold:
                active_vertices.discard(v)

        self.pruned_edges = {
            e for e in active_edges
            if e[0] in active_vertices and e[1] in active_vertices
        }
        self.pruned_vertices = {
            v: self.raw_vertices[v] for v in active_vertices
        }
        self.radius = {v: self.radius[v] for v in active_vertices}

    # ===================================================================== #
    #  Step 4 – Identify region nodes                                       #
    # ===================================================================== #

    def chebyshev(self, v1: VertexId, v2: VertexId) -> float:
        x1, y1 = self.pruned_vertices[v1]
        x2, y2 = self.pruned_vertices[v2]
        return max(abs(x2 - x1), abs(y2 - y1))

    def is_locally_maximal(self, vid: VertexId) -> bool:
        """Return True if no other pruned vertex within Chebyshev radius[vid]
        has a radius >= radius[vid]."""
        r_a = self.radius[vid]
        for other, pt in self.pruned_vertices.items():
            if other == vid:
                continue
            if self.chebyshev(vid, other) <= r_a and self.radius[other] >= r_a:
                return False
        return True

    def identify_region_nodes(self, region_min_radius: float) -> None:
        self.region_nodes.clear()
        adj = self.build_adjacency(self.pruned_edges)

        for vid in self.pruned_vertices:
            degree = len(adj.get(vid, set()))
            # All non-degree-2 nodes are region nodes (intersections, endpoints…)
            if degree != 2:
                self.region_nodes.add(vid)
                continue
            # Degree-2 nodes are region nodes only if locally maximal and wide enough
            if (self.radius[vid] >= region_min_radius
                    and self.is_locally_maximal(vid)):
                self.region_nodes.add(vid)

    # ===================================================================== #
    #  Step 5 – Identify choke-point nodes                                  #
    # ===================================================================== #

    def identify_choke_points(self, max_choke_radius: float) -> None:
        self.choke_nodes.clear()
        self.choke_links.clear()
        self.rounded_choke_tiles.clear()

        if not self.region_nodes:
            return

        adj = self.build_adjacency(self.pruned_edges)
        visited_edges: Set[Tuple[VertexId, VertexId]] = set()

        for start in sorted(self.region_nodes):
            for nb in sorted(adj.get(start, set())):
                canonical = (start, nb) if start < nb else (nb, start)
                if canonical in visited_edges:
                    continue

                # Walk the chain from start through nb until the next region node
                path: List[VertexId] = [start]
                prev, cur = start, nb
                visited_edges.add(canonical)
                seen: Set[VertexId] = {start}
                valid = True

                while True:
                    path.append(cur)

                    # Cycle detected – discard this path
                    if cur in seen:
                        valid = False
                        break
                    seen.add(cur)

                    # Reached another region node → this is the end of the segment
                    if cur in self.region_nodes and cur != start:
                        break

                    nexts = [x for x in adj.get(cur, set()) if x != prev]
                    if not nexts:
                        break       # dead-end: not ending at a region node

                    nxt = nexts[0]
                    e2 = (cur, nxt) if cur < nxt else (nxt, cur)
                    visited_edges.add(e2)
                    prev, cur = cur, nxt

                if not valid or len(path) < 2:
                    continue

                end = path[-1]
                # Must end at a *different* region node
                if end not in self.region_nodes or end == start:
                    continue

                # The choke-point node is the vertex with the smallest radius
                choke = min(path, key=lambda v: (self.radius[v], v))
                if self.radius[choke] > max_choke_radius:
                    continue

                self.choke_nodes.add(choke)
                self.choke_links.append((start, choke, end))

                tile = self._round_point_to_tile(self.pruned_vertices[choke])
                if tile is None:
                    continue
                prev_choice = self.rounded_choke_tiles.get(tile)
                if prev_choice is None or self.radius[choke] < self.radius[prev_choice]:
                    self.rounded_choke_tiles[tile] = choke

    # ===================================================================== #
    #  Step 6 – Merge adjacent regions (Perkins §6)                         #
    # ===================================================================== #

    def merge_adjacent_regions(
        self,
        ratio_small:     float = 0.90,
        ratio_large:     float = 0.85,
        ratio_two_choke: float = 0.70,
    ) -> None:
        """
        Remove choke points that are not significant enough to warrant a
        region boundary.  Two regions are merged when:
          1.  choke_radius > ratio_small  * min(region_radii)  OR
              choke_radius > ratio_large  * max(region_radii)
          2.  One of the two regions has exactly 2 choke points and the
              larger of those choke radii > ratio_two_choke * that region's
              node radius.
        Choke points are examined in decreasing radius order.
        """
        if not self.choke_links:
            return

        # Union-Find over region nodes
        parent: Dict[VertexId, VertexId] = {v: v for v in self.region_nodes}

        def find(v: VertexId) -> VertexId:
            root = v
            while parent[root] != root:
                root = parent[root]
            # Path compression
            while parent[v] != root:
                parent[v], v = root, parent[v]
            return root

        def union(a: VertexId, b: VertexId) -> None:
            ra, rb = find(a), find(b)
            if ra == rb:
                return
            # Keep the larger-radius node as representative
            if self.radius.get(ra, 0.0) >= self.radius.get(rb, 0.0):
                parent[rb] = ra
            else:
                parent[ra] = rb

        # Sort choke links by decreasing choke-point radius
        indexed = sorted(
            enumerate(self.choke_links),
            key=lambda x: -self.radius.get(x[1][1], 0.0),
        )

        removed_indices: Set[int] = set()

        for idx, (start, choke, end) in indexed:
            r_start = find(start)
            r_end   = find(end)

            # Already in the same component (earlier merge)
            if r_start == r_end:
                removed_indices.add(idx)
                continue

            choke_r   = self.radius.get(choke, 0.0)
            rad_s     = self.radius.get(r_start, 0.0)
            rad_e     = self.radius.get(r_end,   0.0)
            smaller_r = min(rad_s, rad_e)
            larger_r  = max(rad_s, rad_e)

            # Criterion 1
            should_merge = (
                choke_r > ratio_small * smaller_r
                or choke_r > ratio_large * larger_r
            )

            # Criterion 2 – one region has exactly 2 active choke points
            if not should_merge:
                # Build per-component choke list (only for still-active links)
                comp_chokes: Dict[VertexId, List[float]] = defaultdict(list)
                for i, (s, c, e) in enumerate(self.choke_links):
                    if i in removed_indices:
                        continue
                    rs, re = find(s), find(e)
                    if rs == re:
                        continue
                    cr = self.radius.get(c, 0.0)
                    comp_chokes[rs].append(cr)
                    comp_chokes[re].append(cr)

                for region_rep in (r_start, r_end):
                    rc_list = comp_chokes.get(region_rep, [])
                    if len(rc_list) == 2:
                        max_choke_r = max(rc_list)
                        region_r    = self.radius.get(region_rep, 0.0)
                        if region_r > 0 and max_choke_r > ratio_two_choke * region_r:
                            should_merge = True
                            break

            if should_merge:
                union(r_start, r_end)
                removed_indices.add(idx)

        # Apply removals ------------------------------------------------
        removed_vids: Set[VertexId] = set()
        kept_links: List[Tuple[VertexId, VertexId, VertexId]] = []

        for i, link in enumerate(self.choke_links):
            if i in removed_indices:
                removed_vids.add(link[1])   # choke vertex
            else:
                kept_links.append(link)

        self.choke_links  = kept_links
        self.choke_nodes -= removed_vids
        self.rounded_choke_tiles = {
            tile: vid
            for tile, vid in self.rounded_choke_tiles.items()
            if vid not in removed_vids
        }

    # ===================================================================== #
    #  Analysis pipeline                                                     #
    # ===================================================================== #

    def analyze(self) -> None:
        if not self.obstacles:
            self.status_var.set("Draw at least one obstacle cell first.")
            return

        # Read parameters
        min_area          = self.parse_int  (self.min_obstacle_area_var.get(),    1)
        region_min_radius = self.parse_float(self.region_min_radius_var.get(),    2.0)
        isolated_radius   = self.parse_float(self.isolated_radius_var.get(),      1.0)
        max_choke_radius  = self.parse_float(self.max_choke_radius_var.get(),  9999.0)
        simplify_eps      = self.parse_float(self.simplify_eps_var.get(),         0.0)
        sample_spacing    = max(0.1, self.parse_float(self.sample_spacing_var.get(), 0.5))
        diagonal_movement = self.diagonal_movement_var.get()
        diagonal_gap      = self.parse_float(self.diagonal_gap_var.get(),         0.15)
        enable_merging    = self.enable_merging_var.get()
        ratio_small       = self.parse_float(self.merge_ratio_small_var.get(),    0.90)
        ratio_large       = self.parse_float(self.merge_ratio_large_var.get(),    0.85)
        ratio_two_choke   = self.parse_float(self.merge_ratio_two_choke_var.get(),0.70)

        self.clear_analysis_only()
        self.detect_current_symmetry()
        self.analysis_poly = self.compute_analysis_polygon(self.detected_symmetry)
        if self.logic_mode == LOGIC_RUNTIME:
            self.configure_runtime_backend()
            self.raster_scale = raster_scale_from_spacing(sample_spacing)

        timings = {}
        total_start = time.perf_counter()

        # Step 1
        t0 = time.perf_counter()
        ok = self.build_obstacle_geometry(
            min_area=min_area,
            simplify_eps=simplify_eps,
            diagonal_movement=diagonal_movement,
            diagonal_gap=diagonal_gap,
        )
        timings["build_obstacle_geometry"] = time.perf_counter() - t0

        if not ok:
            self.redraw()
            self.status_var.set("All obstacles were discarded by the area threshold.")
            return

        # Step 2
        t0 = time.perf_counter()
        self.compute_geometric_voronoi_graph(sample_spacing)
        timings["compute_voronoi"] = time.perf_counter() - t0

        # Step 3
        t0 = time.perf_counter()
        self.prune_graph(isolated_radius)
        timings["prune_graph"] = time.perf_counter() - t0

        # Step 4
        t0 = time.perf_counter()
        self.identify_region_nodes(region_min_radius)
        timings["identify_regions"] = time.perf_counter() - t0

        # Step 5
        t0 = time.perf_counter()
        self.identify_choke_points(max_choke_radius)
        timings["identify_chokes"] = time.perf_counter() - t0

        # Step 6
        if enable_merging:
            t0 = time.perf_counter()
            self.merge_adjacent_regions(ratio_small, ratio_large, ratio_two_choke)
            timings["merge_regions"] = time.perf_counter() - t0

        # Final simplification
        t0 = time.perf_counter()
        self.simplify_choke_points_for_game()
        timings["simplify_for_game"] = time.perf_counter() - t0

        # Symmetry mirror
        t0 = time.perf_counter()
        self.mirror_analysis_results()
        timings["mirror_results"] = time.perf_counter() - t0

        total_elapsed = time.perf_counter() - total_start
        timings["TOTAL"] = total_elapsed

        self.redraw()

        # Console timing log
        print("\n=== Chokepoint Detection Timing ===")
        for name, elapsed in timings.items():
            print(f"{name:24s}: {elapsed*1000:8.2f} ms")

        diag_note = " (diagonal)" if diagonal_movement else ""
        symmetry_note = f", symmetry: {self.detected_symmetry}" if self.detected_symmetry else ""

        if self.logic_mode == LOGIC_RUNTIME:
            logic_note = f", logic: {self.logic_mode}/{self.runtime_math_mode}"
        else:
            logic_note = f", logic: {self.logic_mode}"
        self.status_var.set(
            f"Raw verts: {len(self.raw_vertices)}  "
            f"Pruned verts: {len(self.pruned_vertices)}  "
            f"Chokes: {len(self.choke_nodes)}  "
            f"Time: {total_elapsed*1000:.1f} ms{diag_note}{symmetry_note}{logic_note}"
        )
    
    # ===================================================================== #
    #  Geometry helpers                                                      #
    # ===================================================================== #

    def _round_point_to_tile(self, pt: Tuple[float, float]) -> Optional[Cell]:
        """Map a continuous (x, y) world point to the nearest grid cell."""
        x, y = pt
        c = int(round(x - 0.5))
        r = int(round(y - 0.5))
        if 0 <= r < self.cfg.rows and 0 <= c < self.cfg.cols:
            return (r, c)
        return None

    # ===================================================================== #
    #  Drawing                                                               #
    # ===================================================================== #

    def color_for_cell(self, cell: Cell) -> str:
        if cell in self.rounded_choke_tiles:
            return self.cfg.choke_tile

        if cell in self.core_cells:
            return self.cfg.core

        if cell in self.obstacles:
            if self.logic_mode == LOGIC_OLD and self.discarded_obstacle_geom is not None:
                backend = load_legacy_backend()
                point_cls = backend["Point"]
                r, c = cell
                if self.discarded_obstacle_geom.covers(point_cls(c + 0.5, r + 0.5)):
                    return self.cfg.discarded_obstacle
            if cell in self.discarded_obstacle_cells:
                return self.cfg.discarded_obstacle
            return self.cfg.obstacle

        if cell in self.titanium_ores:
            return self.cfg.titanium_ore

        if cell in self.axionite_ores:
            return self.cfg.axionite_ore

        return self.cfg.empty

    def cell_center_px(self, cell: Cell) -> Tuple[float, float]:
        r, c = cell
        return ((c + 0.5) * self.cfg.cell_size,
                (r + 0.5) * self.cfg.cell_size)

    def world_to_canvas(self, pt: Tuple[float, float]) -> Tuple[float, float]:
        x, y = pt
        return (x * self.cfg.cell_size, y * self.cfg.cell_size)

    def clear_overlays(self) -> None:
        for item_id in self.overlay_ids:
            self.canvas.delete(item_id)
        self.overlay_ids.clear()

    def draw_edge_set(
        self,
        edges:  Set[Tuple[VertexId, VertexId]],
        vertex_map: Dict[VertexId, Tuple[float, float]],
        color: str,
        width: int,
    ) -> None:
        for a, b in edges:
            pa = vertex_map.get(a)
            pb = vertex_map.get(b)
            if pa is None or pb is None:
                continue
            x1, y1 = self.world_to_canvas(pa)
            x2, y2 = self.world_to_canvas(pb)
            item = self.canvas.create_line(
                x1, y1, x2, y2, fill=color, width=width)
            self.overlay_ids.append(item)

    def draw_region_nodes(self) -> None:
        rad = max(3, self.cfg.cell_size // 5)
        for vid in self.region_nodes:
            x, y = self.world_to_canvas(self.pruned_vertices[vid])
            item = self.canvas.create_oval(
                x - rad, y - rad, x + rad, y + rad,
                outline="", fill=self.cfg.region_node)
            self.overlay_ids.append(item)

    def draw_choke_geometry(self) -> None:
        for vid in self.choke_nodes:
            if vid not in self.pruned_vertices:
                continue

            pt = self.pruned_vertices[vid]
            tile = self._round_point_to_tile(pt)
            if tile is None:
                continue

            kind = self.rounded_choke_kinds.get(tile, "wall")
            x, y = self.world_to_canvas(pt)

            if kind == "wall":
                r = max(4, self.cfg.cell_size // 4)
                item = self.canvas.create_rectangle(
                    x - r + 3, y - r + 3, x + r - 3, y + r - 3,
                    fill="#ff0000",
                    outline="#ff0000"
                )
                self.overlay_ids.append(item)

            else:  # launcher
                r_tile, c_tile = tile

                # surrounding 3x3 footprint as translucent-style outlines
                for rr in range(r_tile - 1, r_tile + 2):
                    for cc in range(c_tile - 1, c_tile + 2):
                        if 0 <= rr < self.cfg.rows and 0 <= cc < self.cfg.cols:
                            x0 = cc * self.cfg.cell_size
                            y0 = rr * self.cfg.cell_size
                            x1 = x0 + self.cfg.cell_size
                            y1 = y0 + self.cfg.cell_size

                            if (rr, cc) == tile:
                                # center tile solid
                                rect = self.canvas.create_rectangle(
                                    x0 + 3, y0 + 3, x1 - 3, y1 - 3,
                                    fill="#0044ff",
                                    outline="#0044ff"
                                )
                            else:
                                # surrounding tiles only outlined
                                rect = self.canvas.create_rectangle(
                                    x0 +3, y0 + 3,
                                    x1 - 3, y1 - 3,
                                    outline="#4a90ff",
                                    width=3
                                )

                            self.overlay_ids.append(rect)
                        
    def redraw(self) -> None:
        # Cell colours
        for cell, rect_id in self.rect_ids.items():
            self.canvas.itemconfig(rect_id, fill=self.color_for_cell(cell))

        self.clear_overlays()

        if self.raw_edges:
            # After symmetry mirroring, raw/pruned graphs no longer share a
            # common vertex-id namespace, so raw edges must use raw_vertices.
            self.draw_edge_set(self.raw_edges, self.raw_vertices, self.cfg.raw_graph, 1)
        if self.pruned_edges:
            self.draw_edge_set(self.pruned_edges, self.pruned_vertices,
                               self.cfg.pruned_graph, 2)
        if self.region_nodes:
            self.draw_region_nodes()
        if self.choke_nodes:
            self.draw_choke_geometry()

        # Radius text labels on choke tiles
        show = self.show_radii_var.get()
        # Remove stale text items
        for cell in list(self.text_ids):
            if not show or cell not in self.rounded_choke_tiles:
                self.canvas.delete(self.text_ids.pop(cell))

        if show:
            font_size = max(7, self.cfg.cell_size // 3)
            for cell, vid in self.rounded_choke_tiles.items():
                x, y = self.cell_center_px(cell)
                text = f"{self.radius[vid]:.1f}"
                text_id = self.text_ids.get(cell)
                if text_id is None:
                    text_id = self.canvas.create_text(
                        x, y, text=text,
                        font=("TkDefaultFont", font_size, "bold"),
                        fill="#650000",
                    )
                    self.text_ids[cell] = text_id
                else:
                    self.canvas.coords(text_id, x, y)
                    self.canvas.itemconfig(text_id, text=text)


# =========================================================================== #
#  Entry point                                                                 #
# =========================================================================== #

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Geometric Voronoi chokepoint detector",
    )
    parser.add_argument(
        "--logic",
        choices=(LOGIC_RUNTIME, LOGIC_OLD),
        default=LOGIC_RUNTIME,
        help="Choose the chokepoint analysis backend (default: runtime).",
    )
    parser.add_argument(
        "--runtime-math",
        choices=(RUNTIME_MATH_DECIMAL, RUNTIME_MATH_FLOAT),
        default=RUNTIME_MATH_DECIMAL,
        help="Choose the runtime foronoi numeric mode (default: decimal).",
    )
    parser.add_argument(
        "--map", metavar="MAP_NAME",
        help="Load a single map from maps/<MAP_NAME>.map26 and open UI.",
    )
    parser.add_argument(
        "--maps-dir", metavar="DIR", default="maps",
        help="Directory that contains .map26 files (default: ./maps).",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Run headless benchmark over all maps and print averaged timings.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.logic == LOGIC_OLD:
        load_legacy_backend()
        set_foronoi_numeric_mode(RUNTIME_MATH_DECIMAL)
    else:
        set_foronoi_numeric_mode(args.runtime_math)

    # --- BATCH MODE ---
    if args.all:
        run_all_maps(
            args.maps_dir,
            logic_mode=args.logic,
            runtime_math_mode=args.runtime_math,
        )
        return

    cfg              = GridConfig()
    initial_obstacles: Optional[Set[Cell]] = None
    map_data: Optional[MapData] = None
    title_suffix     = ""

    if args.map:
        map_path = os.path.join(args.maps_dir, f"{args.map}.map26")

        if not os.path.isfile(map_path):
            # Friendly error: list available maps so the user knows what's there.
            available = []
            if os.path.isdir(args.maps_dir):
                available = [
                    os.path.splitext(f)[0]
                    for f in sorted(os.listdir(args.maps_dir))
                    if f.endswith(".map26")
                ]
            msg = f"Map file not found: {map_path}"
            if available:
                msg += f"\nAvailable maps in '{args.maps_dir}':\n  " + "\n  ".join(available)
            else:
                msg += f"\nNo .map26 files found in '{args.maps_dir}'."
            print(msg, file=sys.stderr)
            sys.exit(1)

        print(f"Loading map: {map_path}")
        map_data = load_map_data(map_path)

        height = map_data.height
        width  = map_data.width
        if height == 0 or width == 0:
            print("Error: map is empty.", file=sys.stderr)
            sys.exit(1)

        cell_size = auto_cell_size(height, width)
        cfg = GridConfig(rows=height, cols=width, cell_size=cell_size)

        initial_obstacles = set(map_data.obstacles)
        title_suffix      = f" — {args.map}  ({width}×{height})"
        print(f"  Dimensions : {width} cols × {height} rows")
        print(f"  Cell size  : {cell_size} px")
        print(f"  Obstacles  : {len(initial_obstacles)} cells")
        print(f"  Titanium   : {len(map_data.titanium_ores)} cells")
        print(f"  Axionite   : {len(map_data.axionite_ores)} cells")
        print(f"  Cores      : {len(map_data.cores)}")
        print(f"  Symmetry   : {map_data.symmetry or 'none detected'}")
        print(f"  Logic      : {args.logic}")
        if args.logic == LOGIC_RUNTIME:
            print(f"  Runtime math: {args.runtime_math}")

    root = tk.Tk()
    app  = GeometricChokepointApp(
        root,
        cfg,
        initial_obstacles=initial_obstacles,
        map_data=map_data,
        logic_mode=args.logic,
        runtime_math_mode=args.runtime_math,
    )
    if args.logic == LOGIC_RUNTIME:
        backend_tag = f"{args.logic}/{args.runtime_math}"
    else:
        backend_tag = args.logic
    root.title(f"Geometric Voronoi Chokepoints{title_suffix} [{backend_tag}]")
    root.resizable(False, False)
    if map_data is not None:
        root.after(0, app.analyze)
    root.mainloop()

def run_analysis_headless(
    cfg: GridConfig,
    map_data: MapData,
    logic_mode: str = LOGIC_RUNTIME,
    runtime_math_mode: str = RUNTIME_MATH_DECIMAL,
) -> dict:
    if logic_mode == LOGIC_OLD:
        load_legacy_backend()
        set_foronoi_numeric_mode(RUNTIME_MATH_DECIMAL)
    else:
        set_foronoi_numeric_mode(runtime_math_mode)

    app = GeometricChokepointApp.__new__(GeometricChokepointApp)
    app.init_headless(
        cfg,
        map_data=map_data,
        logic_mode=logic_mode,
        runtime_math_mode=runtime_math_mode,
    )

    # ---------------- params (match analyze()) ----------------
    min_area          = 2
    region_min_radius = 2.0
    isolated_radius   = 1.0
    max_choke_radius  = 9999.0
    simplify_eps      = 0.0
    sample_spacing    = 1.0

    enable_merging    = True
    ratio_small       = 0.90
    ratio_large       = 0.85
    ratio_two_choke   = 0.70

    app.clear_analysis_only()
    app.detect_current_symmetry()
    app.analysis_poly = app.compute_analysis_polygon(app.detected_symmetry)
    if logic_mode == LOGIC_RUNTIME:
        app.configure_runtime_backend()
        app.raster_scale = raster_scale_from_spacing(sample_spacing)

    timings = {}
    total_start = time.perf_counter()

    # ---------------- Step 1 ----------------
    t0 = time.perf_counter()
    ok = app.build_obstacle_geometry(
        min_area=min_area,
        simplify_eps=simplify_eps,
        diagonal_movement=False,
        diagonal_gap=0.15,
    )
    timings["build_obstacle_geometry"] = (time.perf_counter() - t0)
    if not ok:
        timings["TOTAL"] = (time.perf_counter() - total_start)
        return timings

    # ---------------- Step 2 ----------------
    t0 = time.perf_counter()
    app.compute_geometric_voronoi_graph(sample_spacing)
    timings["compute_voronoi"] = (time.perf_counter() - t0)

    # ---------------- Step 3 ----------------
    t0 = time.perf_counter()
    app.prune_graph(isolated_radius)
    timings["prune_graph"] = (time.perf_counter() - t0)

    # ---------------- Step 4 ----------------
    t0 = time.perf_counter()
    app.identify_region_nodes(region_min_radius)
    timings["identify_regions"] = (time.perf_counter() - t0)

    # ---------------- Step 5 ----------------
    t0 = time.perf_counter()
    app.identify_choke_points(max_choke_radius)
    timings["identify_chokes"] = (time.perf_counter() - t0)

    # ---------------- Step 6 (optional merge) ----------------
    if enable_merging:
        t0 = time.perf_counter()
        app.merge_adjacent_regions(ratio_small, ratio_large, ratio_two_choke)
        timings["merge_regions"] = (time.perf_counter() - t0)

    # ---------------- final simplification ----------------
    t0 = time.perf_counter()
    app.simplify_choke_points_for_game()
    timings["simplify_for_game"] = (time.perf_counter() - t0)

    # ---------------- symmetry mirror ----------------
    t0 = time.perf_counter()
    app.mirror_analysis_results()
    timings["mirror_results"] = (time.perf_counter() - t0)

    # ---------------- TOTAL ----------------
    timings["TOTAL"] = (time.perf_counter() - total_start)

    return timings

def run_all_maps(
    maps_dir: str,
    logic_mode: str = LOGIC_RUNTIME,
    runtime_math_mode: str = RUNTIME_MATH_DECIMAL,
):
    files = [
        f for f in os.listdir(maps_dir)
        if f.endswith(".map26")
    ]

    if not files:
        print("No maps found.")
        return

    totals = defaultdict(float)
    counts = defaultdict(int)

    for f in files:
        path = os.path.join(maps_dir, f)
        print(f"Running: {f}")

        map_data = load_map_data(path)
        h, w = map_data.height, map_data.width
        cfg = GridConfig(rows=h, cols=w, cell_size=10)

        timings = run_analysis_headless(
            cfg,
            map_data,
            logic_mode=logic_mode,
            runtime_math_mode=runtime_math_mode,
        )

        for k, v in timings.items():
            totals[k] += v
            counts[k] += 1

    if logic_mode == LOGIC_RUNTIME:
        label = f"{logic_mode}/{runtime_math_mode}"
    else:
        label = logic_mode
    print(f"\n=== AVERAGED TIMINGS (ms) [{label}] ===\n")

    for k in totals.keys():
        avg_ms = (totals[k] / counts[k]) * 1000
        print(f"{k:<24} {avg_ms:>8.2f} ms")

if __name__ == "__main__":
    main()
