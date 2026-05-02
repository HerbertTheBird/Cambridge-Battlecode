from __future__ import annotations

import math
import sys
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from cambc import Controller, EntityType, Position

import map_info
import pathing
from geometry_core import (
    apply_diagonal_notches,
    boundary_segments_from_mask,
    build_analysis_mask,
    build_free_mask,
    build_obstacle_mask,
    clip_polygon_against_half_plane,
    is_axis_aligned_rectangle,
    point_in_polygon,
    raster_scale_from_spacing,
    rectangle_polygon,
    split_obstacle_mask_by_area,
)
from voronoi_core import NUMERIC_MODE_FLOAT, Polygon as ForonoiPolygon, Voronoi as ForonoiVoronoi, set_numeric_mode
from voronoi_core.events.circle_event import CircleEvent
from voronoi_core.events.site_event import SiteEvent
from voronoi_core.graph.point import Point as ForonoiPoint
from log import CHOKEPOINT_DRAW_DEBUG

Cell = Tuple[int, int]
Point = Tuple[float, float]
Mask = List[List[bool]]
VertexId = int

ENV_EMPTY = 0
ENV_WALL = 1
ENV_ORE_TITANIUM = 2
ENV_ORE_AXIONITE = 3

TEAM_A = 0
TEAM_B = 1

SYMMETRY_HORIZONTAL = "horizontal"
SYMMETRY_VERTICAL = "vertical"
SYMMETRY_ROTATIONAL = "rotational"

STAGE_WAITING = "waiting"
STAGE_GEOMETRY = "geometry"
STAGE_VORONOI_INIT = "voronoi_init"
STAGE_VORONOI_SWEEP = "voronoi_sweep"
STAGE_VORONOI_FINISH = "voronoi_finish"
STAGE_VORONOI_EXTRACT = "voronoi_extract"
STAGE_PRUNE = "prune"
STAGE_REGIONS = "regions"
STAGE_CHOKES = "chokes"
STAGE_MERGE = "merge"
STAGE_SIMPLIFY = "simplify"
STAGE_MIRROR = "mirror"
STAGE_DONE = "done"
STAGE_FAILED = "failed"

CHOKEPOINT_ENABLE = True
CHOKEPOINT_START_ROUND = 100
CHOKEPOINT_REQUIRE_FULL_ANALYSIS_SEEN = False
CHOKEPOINT_MIN_SEEN_MAP_FRACTION = 0.10
CHOKEPOINT_SAMPLE_SPACING = 1.5
CHOKEPOINT_MIN_OBSTACLE_AREA = 3
CHOKEPOINT_REGION_MIN_RADIUS = 5.0
CHOKEPOINT_ISOLATED_RADIUS = 1.0
CHOKEPOINT_MAX_CHOKE_RADIUS = 3.0
CHOKEPOINT_DIAGONAL_MOVEMENT = False
CHOKEPOINT_DIAGONAL_GAP = 0.15
CHOKEPOINT_ENABLE_MERGING = True
CHOKEPOINT_MERGE_RATIO_SMALL = 0.7
CHOKEPOINT_MERGE_RATIO_LARGE = 0.6
CHOKEPOINT_MERGE_RATIO_TWO = 0.5
CHOKEPOINT_CPU_BUDGET_US = 1850
CHOKEPOINT_MIN_HEADROOM_US = 100
CHOKEPOINT_MAX_STAGES_PER_TICK = 16
CHOKEPOINT_MAX_SWEEP_EVENTS_PER_TICK = 64
CHOKEPOINT_MAX_FINISH_EDGES_PER_TICK = 48
CHOKEPOINT_MAX_EXTRACT_EDGES_PER_TICK = 192
CHOKEPOINT_BUDGET_CHECK_INTERVAL = 8
CHOKEPOINT_DEBUG_MAX_LIVE_VORONOI_EDGES = 96
CHOKEPOINT_DEBUG_MAX_GRAPH_EDGES = 160
CHOKEPOINT_DEBUG_MAX_SITE_GUIDES = 24
CHOKEPOINT_DEBUG_SWEEP_TICK_SPACING = 3
CHOKEPOINT_DEBUG_PRINTS = False
CHOKEPOINT_DEBUG_INTERVAL_ROUNDS = 25

BLOCKER_WALL = "wall"
BLOCKER_LAUNCHER = "launcher"

_VISIBLE_DISPOSABLE_TYPES = frozenset({
    EntityType.ROAD,
    EntityType.MARKER,
    EntityType.CONVEYOR,
    EntityType.ARMOURED_CONVEYOR,
    EntityType.BRIDGE,
    EntityType.SPLITTER,
})


@dataclass(frozen=True)
class CoreInfo:
    id: int
    team: int
    center: Cell


@dataclass
class GridConfig:
    rows: int
    cols: int


@dataclass
class BlockerCandidate:
    start: VertexId
    choke: VertexId
    end: VertexId
    radius: float
    kind: str
    tile: Cell


@dataclass
class AnalyzerState:
    cfg: GridConfig
    detected_symmetry: Optional[str]
    obstacles: Set[Cell]
    cores: List[CoreInfo]
    analysis_poly: List[Point]
    analysis_tile_mask: int
    raster_scale: int
    stage: str = STAGE_WAITING
    failed_reason: Optional[str] = None

    analysis_mask: Mask = field(default_factory=list)
    kept_obstacle_mask: Mask = field(default_factory=list)
    free_mask: Mask = field(default_factory=list)
    # Cached free_mask dimensions so segment_is_inside_free_space doesn't
    # call len() on every Voronoi edge.
    free_height: int = 0
    free_width: int = 0
    free_boundary_segments: List[Tuple[Point, Point]] = field(default_factory=list)
    free_boundary_verticals: List[Tuple[float, float, float]] = field(default_factory=list)
    free_boundary_horizontals: List[Tuple[float, float, float]] = field(default_factory=list)

    raw_vertex_radius: Dict[VertexId, float] = field(default_factory=dict)
    raw_vertices: Dict[VertexId, Point] = field(default_factory=dict)
    raw_edges: Set[Tuple[VertexId, VertexId]] = field(default_factory=set)
    raw_vertex_ids_by_point: Dict[Tuple[int, int], VertexId] = field(default_factory=dict)

    pruned_vertices: Dict[VertexId, Point] = field(default_factory=dict)
    pruned_edges: Set[Tuple[VertexId, VertexId]] = field(default_factory=set)
    pruned_adj: Dict[VertexId, Set[VertexId]] = field(default_factory=dict)
    radius: Dict[VertexId, float] = field(default_factory=dict)

    region_nodes: Set[VertexId] = field(default_factory=set)
    choke_nodes: Set[VertexId] = field(default_factory=set)
    choke_links: List[Tuple[VertexId, VertexId, VertexId]] = field(default_factory=list)
    rounded_choke_tiles: Dict[Cell, VertexId] = field(default_factory=dict)
    rounded_choke_kinds: Dict[Cell, str] = field(default_factory=dict)
    blocker_kind_by_index: Dict[int, str] = field(default_factory=dict)

    voronoi: Optional[ForonoiVoronoi] = None
    voronoi_site_index: int = 0
    voronoi_finish_cursor: int = 0
    voronoi_finished_edges: List = field(default_factory=list)
    voronoi_edge_cursor: int = 0
    processed_events: int = 0

    def prepare_free_boundary_segments(self) -> None:
        verticals: List[Tuple[float, float, float]] = []
        horizontals: List[Tuple[float, float, float]] = []

        for (x1, y1), (x2, y2) in self.free_boundary_segments:
            if x1 == x2:
                low_y = y1 if y1 <= y2 else y2
                high_y = y2 if y2 >= y1 else y1
                verticals.append((x1, low_y, high_y))
            else:
                low_x = x1 if x1 <= x2 else x2
                high_x = x2 if x2 >= x1 else x1
                horizontals.append((y1, low_x, high_x))

        self.free_boundary_verticals = verticals
        self.free_boundary_horizontals = horizontals

    def collect_boundary_samples(self, spacing: float) -> List[Point]:
        samples: Set[Tuple[int, int]] = set()
        samples_add = samples.add
        spacing = max(spacing, 1.0 / max(self.raster_scale, 1))
        for start, end in self.free_boundary_segments:
            x1, y1 = start
            x2, y2 = end
            dx = x2 - x1
            dy = y2 - y1
            seg_len = math.hypot(dx, dy)
            steps = max(1, int(math.ceil(seg_len / spacing)))
            inv_steps = 1.0 / steps
            for k in range(steps + 1):
                t = k * inv_steps
                # quantize_key inlined.
                samples_add((int((x1 + t * dx) * 10000.0 + 0.5),
                             int((y1 + t * dy) * 10000.0 + 0.5)))
        return [(x * 0.0001, y * 0.0001) for x, y in samples]

    def quantize_key(self, x: float, y: float) -> Tuple[int, int]:
        return (int(x * 10000.0 + 0.5), int(y * 10000.0 + 0.5))

    def segment_is_inside_free_space(self, p1: Point, p2: Point) -> bool:
        free_mask = self.free_mask
        height = self.free_height
        width = self.free_width
        if width == 0 or height == 0 or not free_mask:
            return False

        cfg = self.cfg
        cols = cfg.cols
        rows = cfg.rows
        scale = self.raster_scale

        p1x = p1[0]
        p1y = p1[1]
        p2x = p2[0]
        p2y = p2[1]
        if p1x < 0.0 or p1y < 0.0 or p2x < 0.0 or p2y < 0.0:
            return False
        if p1x > cols or p2x > cols or p1y > rows or p2y > rows:
            return False

        width_m1 = width - 1
        height_m1 = height - 1
        ix = int(p1x * scale)
        iy = int(p1y * scale)
        x1 = ix if ix < width_m1 else width_m1
        y1 = iy if iy < height_m1 else height_m1
        ix = int(p2x * scale)
        iy = int(p2y * scale)
        x2 = ix if ix < width_m1 else width_m1
        y2 = iy if iy < height_m1 else height_m1

        dx = x2 - x1
        dy = y2 - y1
        adx = -dx if dx < 0 else dx
        ady = -dy if dy < 0 else dy
        steps = adx if adx > ady else ady
        if steps == 0:
            return False

        # Integer DDA over the raster mask avoids allocating sampled float points
        # for every Voronoi edge.
        last_x = x1
        last_y = y1
        for k in range(1, steps):
            px = x1 + (dx * k) // steps
            py = y1 + (dy * k) // steps
            if px == last_x and py == last_y:
                continue
            last_x = px
            last_y = py
            if not free_mask[py][px]:
                return False
        return True

    def compute_radius(self, pt: Point) -> float:
        segments = self.free_boundary_segments
        if not segments:
            return 0.0
        x, y = pt
        best_sq = float("inf")

        for x1, low_y, high_y in self.free_boundary_verticals:
            dx = x - x1
            if y <= low_y:
                dy = low_y - y
            elif y >= high_y:
                dy = y - high_y
            else:
                dy = 0.0
            dist_sq = dx * dx + dy * dy
            if dist_sq < best_sq:
                if dist_sq == 0.0:
                    return 0.0
                best_sq = dist_sq

        for y1, low_x, high_x in self.free_boundary_horizontals:
            dy = y - y1
            if x <= low_x:
                dx = low_x - x
            elif x >= high_x:
                dx = x - high_x
            else:
                dx = 0.0
            dist_sq = dx * dx + dy * dy
            if dist_sq < best_sq:
                if dist_sq == 0.0:
                    return 0.0
                best_sq = dist_sq

        return math.sqrt(best_sq)

    def prune_graph(self, isolated_radius_threshold: float) -> None:
        active_edges: Set[Tuple[VertexId, VertexId]] = self.raw_edges
        adj: Dict[VertexId, Set[VertexId]] = defaultdict(set)
        for a, b in active_edges:
            adj[a].add(b)
            adj[b].add(a)
        active_vertices: Set[VertexId] = set(adj.keys())

        radius: Dict[VertexId, float] = {}
        raw_radius = self.raw_vertex_radius
        raw_vertices = self.raw_vertices
        for vid in active_vertices:
            r = raw_radius.get(vid)
            if r is None:
                r = self.compute_radius(raw_vertices[vid])
            radius[vid] = r
        self.radius = radius
        leaves: deque[int] = deque(v for v in active_vertices if len(adj[v]) == 1)
        keep_pruned_edges = CHOKEPOINT_DRAW_DEBUG or CHOKEPOINT_DEBUG_PRINTS

        while leaves:
            leaf = leaves.popleft()
            if leaf not in active_vertices:
                continue
            if len(adj[leaf]) != 1:
                continue

            parent = next(iter(adj[leaf]))
            if self.radius[leaf] < self.radius[parent]:
                if keep_pruned_edges:
                    edge = (leaf, parent) if leaf < parent else (parent, leaf)
                    active_edges.discard(edge)
                adj[parent].discard(leaf)
                adj[leaf].discard(parent)
                active_vertices.discard(leaf)
                if len(adj[parent]) == 1:
                    leaves.append(parent)

        for v in list(active_vertices):
            if len(adj[v]) == 0 and self.radius[v] < isolated_radius_threshold:
                active_vertices.discard(v)

        if keep_pruned_edges:
            self.pruned_edges = {
                e for e in active_edges
                if e[0] in active_vertices and e[1] in active_vertices
            }
        else:
            self.pruned_edges.clear()
        self.pruned_vertices = {
            v: self.raw_vertices[v] for v in active_vertices
        }
        self.pruned_adj = {
            v: {nb for nb in adj.get(v, set()) if nb in active_vertices}
            for v in active_vertices
        }
        self.radius = {v: self.radius[v] for v in active_vertices}

    def identify_region_nodes(self, region_min_radius: float) -> None:
        self.region_nodes.clear()
        adj = self.pruned_adj
        vertices = self.pruned_vertices
        radius = self.radius
        cell_size = max(1.0, region_min_radius)
        grid: Dict[Tuple[int, int], List[VertexId]] = defaultdict(list)
        for vid, (x, y) in vertices.items():
            grid[(int(x // cell_size), int(y // cell_size))].append(vid)

        def is_locally_maximal_fast(vid: VertexId) -> bool:
            x, y = vertices[vid]
            r_a = radius[vid]
            cx = int(x // cell_size)
            cy = int(y // cell_size)
            span = int(math.ceil(r_a / cell_size))
            for gy in range(cy - span, cy + span + 1):
                for gx in range(cx - span, cx + span + 1):
                    for other in grid.get((gx, gy), ()):
                        if other == vid:
                            continue
                        ox, oy = vertices[other]
                        if max(abs(ox - x), abs(oy - y)) <= r_a and radius[other] >= r_a:
                            return False
            return True

        for vid in vertices:
            degree = len(adj.get(vid, set()))
            if degree != 2:
                self.region_nodes.add(vid)
                continue
            if radius[vid] >= region_min_radius and is_locally_maximal_fast(vid):
                self.region_nodes.add(vid)

    def _round_point_to_tile(self, pt: Point) -> Optional[Cell]:
        x, y = pt
        c = int(round(x - 0.5))
        r = int(round(y - 0.5))
        if 0 <= r < self.cfg.rows and 0 <= c < self.cfg.cols:
            return (r, c)
        return None

    def identify_choke_points(self, max_choke_radius: float) -> None:
        self.choke_nodes.clear()
        self.choke_links.clear()
        self.rounded_choke_tiles.clear()
        if not self.region_nodes:
            return

        adj = self.pruned_adj
        visited_edges: Set[Tuple[VertexId, VertexId]] = set()

        for start in sorted(self.region_nodes):
            for nb in sorted(adj.get(start, set())):
                canonical = (start, nb) if start < nb else (nb, start)
                if canonical in visited_edges:
                    continue

                path: List[VertexId] = [start]
                prev, cur = start, nb
                visited_edges.add(canonical)
                seen: Set[VertexId] = {start}
                valid = True

                while True:
                    path.append(cur)
                    if cur in seen:
                        valid = False
                        break
                    seen.add(cur)
                    if cur in self.region_nodes and cur != start:
                        break
                    nxt = None
                    for maybe_next in adj.get(cur, set()):
                        if maybe_next != prev:
                            nxt = maybe_next
                            break
                    if nxt is None:
                        break
                    e2 = (cur, nxt) if cur < nxt else (nxt, cur)
                    visited_edges.add(e2)
                    prev, cur = cur, nxt

                if not valid or len(path) < 2:
                    continue

                end = path[-1]
                if end not in self.region_nodes or end == start:
                    continue

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

    def merge_adjacent_regions(
        self,
        ratio_small: float,
        ratio_large: float,
        ratio_two_choke: float,
    ) -> None:
        if not self.choke_links:
            return

        parent: Dict[VertexId, VertexId] = {v: v for v in self.region_nodes}

        def find(v: VertexId) -> VertexId:
            root = v
            while parent[root] != root:
                root = parent[root]
            while parent[v] != root:
                parent[v], v = root, parent[v]
            return root

        def union(a: VertexId, b: VertexId) -> None:
            ra, rb = find(a), find(b)
            if ra == rb:
                return
            if self.radius.get(ra, 0.0) >= self.radius.get(rb, 0.0):
                parent[rb] = ra
            else:
                parent[ra] = rb

        indexed = sorted(
            enumerate(self.choke_links),
            key=lambda x: -self.radius.get(x[1][1], 0.0),
        )

        removed_indices: Set[int] = set()

        for idx, (start, choke, end) in indexed:
            r_start = find(start)
            r_end = find(end)
            if r_start == r_end:
                removed_indices.add(idx)
                continue

            choke_r = self.radius.get(choke, 0.0)
            rad_s = self.radius.get(r_start, 0.0)
            rad_e = self.radius.get(r_end, 0.0)
            smaller_r = min(rad_s, rad_e)
            larger_r = max(rad_s, rad_e)

            should_merge = (
                choke_r > ratio_small * smaller_r
                or choke_r > ratio_large * larger_r
            )

            if not should_merge:
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
                        region_r = self.radius.get(region_rep, 0.0)
                        if region_r > 0 and max_choke_r > ratio_two_choke * region_r:
                            should_merge = True
                            break

            if should_merge:
                union(r_start, r_end)
                removed_indices.add(idx)

        removed_vids: Set[VertexId] = set()
        kept_links: List[Tuple[VertexId, VertexId, VertexId]] = []
        for i, link in enumerate(self.choke_links):
            if i in removed_indices:
                removed_vids.add(link[1])
            else:
                kept_links.append(link)

        self.choke_links = kept_links
        self.choke_nodes -= removed_vids
        self.rounded_choke_tiles = {
            tile: vid
            for tile, vid in self.rounded_choke_tiles.items()
            if vid not in removed_vids
        }

    def blocker_kind_for_radius(self, r: float) -> Optional[str]:
        if r <= 0.8:
            return BLOCKER_WALL
        if r <= 1.8:
            return BLOCKER_LAUNCHER
        return None

    def blocker_footprint(self, tile: Cell, kind: str) -> Set[Cell]:
        r, c = tile
        radius = 0 if kind == BLOCKER_WALL else 1
        cells = set()
        for rr in range(r - radius, r + radius + 1):
            for cc in range(c - radius, c + radius + 1):
                if 0 <= rr < self.cfg.rows and 0 <= cc < self.cfg.cols:
                    cells.add((rr, cc))
        return cells

    def simplify_choke_points_for_game(self) -> None:
        candidates: List[BlockerCandidate] = []
        for start, choke, end in self.choke_links:
            radius = self.radius.get(choke, 0.0)
            kind = self.blocker_kind_for_radius(radius)
            if kind is None:
                continue
            tile = self._round_point_to_tile(self.pruned_vertices[choke])
            if tile is None:
                continue
            candidates.append(BlockerCandidate(start, choke, end, radius, kind, tile))

        if not candidates:
            self.choke_nodes.clear()
            self.choke_links.clear()
            self.rounded_choke_tiles.clear()
            self.rounded_choke_kinds.clear()
            self.blocker_kind_by_index.clear()
            return

        parent = list(range(len(candidates)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        owner: Dict[Cell, int] = {}
        for i, candidate in enumerate(candidates):
            for cell in self.blocker_footprint(candidate.tile, candidate.kind):
                if cell in owner:
                    union(i, owner[cell])
                else:
                    owner[cell] = i

        groups: Dict[int, List[int]] = defaultdict(list)
        for i in range(len(candidates)):
            groups[find(i)].append(i)

        kept_links: List[Tuple[VertexId, VertexId, VertexId]] = []
        kept_nodes: Set[VertexId] = set()
        kept_tiles: Dict[Cell, VertexId] = {}
        kept_kinds: Dict[Cell, str] = {}

        for group in groups.values():
            best = min(
                group,
                key=lambda i: (
                    candidates[i].radius,
                    0 if candidates[i].kind == BLOCKER_WALL else 1,
                ),
            )
            cand = candidates[best]
            kept_links.append((cand.start, cand.choke, cand.end))
            kept_nodes.add(cand.choke)
            prev = kept_tiles.get(cand.tile)
            if prev is None or self.radius[cand.choke] < self.radius[prev]:
                kept_tiles[cand.tile] = cand.choke
                kept_kinds[cand.tile] = cand.kind

        self.choke_links = kept_links
        self.choke_nodes = kept_nodes
        self.rounded_choke_tiles = kept_tiles
        self.rounded_choke_kinds = kept_kinds
        self.blocker_kind_by_index = {
            _cell_to_index(cell[0], cell[1], self.cfg.cols): kind
            for cell, kind in kept_kinds.items()
        }

    def mirror_point(self, pt: Point, symmetry: str) -> Point:
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
        vertices: Dict[VertexId, Point],
        edges: Set[Tuple[VertexId, VertexId]],
        radius_map: Optional[Dict[VertexId, float]] = None,
    ) -> Tuple[
        Dict[VertexId, Point],
        Set[Tuple[VertexId, VertexId]],
        Dict[VertexId, float],
        Dict[VertexId, VertexId],
        Dict[VertexId, VertexId],
    ]:
        if self.detected_symmetry is None or not vertices:
            identity = {vid: vid for vid in vertices}
            return (
                dict(vertices),
                set(edges),
                dict(radius_map) if radius_map is not None else {},
                identity,
                identity,
            )

        key_to_vid: Dict[Tuple[int, int], VertexId] = {}
        new_vertices: Dict[VertexId, Point] = {}
        new_radius: Dict[VertexId, float] = {}

        def add_vertex(pt: Point, src_vid: Optional[VertexId]) -> VertexId:
            key = self.quantize_key(pt[0], pt[1])
            vid = key_to_vid.get(key)
            if vid is None:
                vid = len(key_to_vid)
                key_to_vid[key] = vid
                new_vertices[vid] = (key[0] * 0.0001, key[1] * 0.0001)
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
            kind = self.rounded_choke_kinds.get(tile, BLOCKER_WALL)
            for mapped_tile, mapped_vid in (
                (tile, original_vids.get(vid)),
                (mirror_cell(tile, self.cfg.rows, self.cfg.cols, self.detected_symmetry), mirrored_vids.get(vid)),
            ):
                if mapped_vid is None:
                    continue
                prev_vid = new_tiles.get(mapped_tile)
                if prev_vid is None or self.radius[mapped_vid] < self.radius[prev_vid]:
                    new_tiles[mapped_tile] = mapped_vid
                    new_kinds[mapped_tile] = kind

        self.rounded_choke_tiles = new_tiles
        self.rounded_choke_kinds = new_kinds
        self.blocker_kind_by_index = {
            _cell_to_index(cell[0], cell[1], self.cfg.cols): kind
            for cell, kind in new_kinds.items()
        }

    def mirror_blocker_targets_only(self) -> None:
        final_targets: Dict[int, str] = {}

        def add_target(cell: Cell, kind: str) -> None:
            r, c = cell
            if not (0 <= r < self.cfg.rows and 0 <= c < self.cfg.cols):
                return
            idx = _cell_to_index(r, c, self.cfg.cols)
            existing = final_targets.get(idx)
            if existing is None or kind == BLOCKER_WALL:
                final_targets[idx] = kind

        for tile, kind in self.rounded_choke_kinds.items():
            add_target(tile, kind)
            if self.detected_symmetry is not None:
                add_target(
                    mirror_cell(tile, self.cfg.rows, self.cfg.cols, self.detected_symmetry),
                    kind,
                )

        self.blocker_kind_by_index = final_targets

    def release_pruned_inputs(self) -> None:
        self.analysis_mask = []
        self.kept_obstacle_mask = []
        self.free_mask = []
        self.free_boundary_segments = []
        self.free_boundary_verticals = []
        self.free_boundary_horizontals = []
        self.obstacles.clear()
        self.raw_vertex_radius.clear()
        self.raw_vertices.clear()
        self.raw_edges.clear()
        self.raw_vertex_ids_by_point.clear()

    def release_final_analysis_state(self) -> None:
        self.release_pruned_inputs()
        self.cores = []
        self.analysis_poly = []
        self.analysis_tile_mask = 0
        self.pruned_vertices.clear()
        self.pruned_edges.clear()
        self.pruned_adj.clear()
        self.radius.clear()
        self.region_nodes.clear()
        self.choke_nodes.clear()
        self.choke_links.clear()
        self.rounded_choke_tiles.clear()
        self.rounded_choke_kinds.clear()
        self.voronoi = None
        self.voronoi_finish_cursor = 0
        self.voronoi_finished_edges = []
        self.voronoi_edge_cursor = 0

    def build_obstacle_geometry(self) -> bool:
        if not self.obstacles:
            return False

        scale = self.raster_scale
        if not CHOKEPOINT_DIAGONAL_MOVEMENT:
            min_area_cells = max(1, int(math.ceil(CHOKEPOINT_MIN_OBSTACLE_AREA)))
            kept_obstacles = self.filter_obstacle_cells_by_area(min_area_cells)
            if not kept_obstacles:
                return False
            self.obstacles = kept_obstacles
            self.analysis_mask = build_analysis_mask(
                self.cfg.rows,
                self.cfg.cols,
                scale,
                self.analysis_poly,
            )
            self.kept_obstacle_mask = build_obstacle_mask(
                kept_obstacles,
                self.cfg.rows,
                self.cfg.cols,
                scale,
                self.analysis_mask,
            )
            self.free_mask = build_free_mask(self.analysis_mask, self.kept_obstacle_mask)
            self.free_height = len(self.free_mask)
            self.free_width = len(self.free_mask[0]) if self.free_height else 0
            self.free_boundary_segments = boundary_segments_from_mask(self.free_mask, scale)
            self.prepare_free_boundary_segments()
            return True

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

        if CHOKEPOINT_DIAGONAL_MOVEMENT and CHOKEPOINT_DIAGONAL_GAP > 0:
            apply_diagonal_notches(
                obstacle_mask,
                self.obstacles,
                self.analysis_mask,
                scale,
                CHOKEPOINT_DIAGONAL_GAP,
            )

        min_area_pixels = max(1, int(math.ceil(CHOKEPOINT_MIN_OBSTACLE_AREA * scale * scale)))
        self.kept_obstacle_mask, _ = split_obstacle_mask_by_area(
            obstacle_mask,
            min_area_pixels,
        )
        self.free_mask = build_free_mask(self.analysis_mask, self.kept_obstacle_mask)
        self.free_height = len(self.free_mask)
        self.free_width = len(self.free_mask[0]) if self.free_height else 0
        self.free_boundary_segments = boundary_segments_from_mask(self.free_mask, scale)
        self.prepare_free_boundary_segments()
        return any(any(row) for row in self.kept_obstacle_mask)

    def filter_obstacle_cells_by_area(self, min_area_cells: int) -> Set[Cell]:
        remaining = set(self.obstacles)
        kept: Set[Cell] = set()

        while remaining:
            start = remaining.pop()
            queue = [start]
            cursor = 0
            while cursor < len(queue):
                r, c = queue[cursor]
                cursor += 1
                for neighbor in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        queue.append(neighbor)

            if len(queue) >= min_area_cells:
                kept.update(queue)

        return kept

    def site_radius_for_sites(self, x: float, y: float, site_a, site_b) -> Optional[float]:
        # Direct ._xd/._yd access skips the .xd/.yd property descriptors.
        # In float numeric mode (the only mode used here) these are already
        # plain floats, so no float() cast is needed.
        if site_a is not None:
            dx = x - site_a._xd
            dy = y - site_a._yd
            best_sq = dx * dx + dy * dy
            if site_b is not None:
                dx = x - site_b._xd
                dy = y - site_b._yd
                dist_sq = dx * dx + dy * dy
                if dist_sq < best_sq:
                    best_sq = dist_sq
            return math.sqrt(best_sq)
        if site_b is not None:
            dx = x - site_b._xd
            dy = y - site_b._yd
            return math.sqrt(dx * dx + dy * dy)
        return None

    def get_or_create_raw_vid(self, pt: Point, radius: Optional[float] = None) -> VertexId:
        # Inline quantize_key — saves one bound-method dispatch per call,
        # which adds up across ~15k Voronoi-edge endpoints per analysis.
        key = (int(pt[0] * 10000.0 + 0.5), int(pt[1] * 10000.0 + 0.5))
        ids = self.raw_vertex_ids_by_point
        vid = ids.get(key)
        if vid is not None:
            if radius is not None:
                radii = self.raw_vertex_radius
                existing = radii.get(vid)
                if existing is None or radius < existing:
                    radii[vid] = radius
            return vid
        vid = len(ids)
        ids[key] = vid
        self.raw_vertices[vid] = (key[0] * 0.0001, key[1] * 0.0001)
        if radius is not None:
            self.raw_vertex_radius[vid] = radius
        return vid

    def step_sweep_event(self) -> bool:
        if self.voronoi is None:
            return False
        while not self.voronoi.event_queue.empty():
            event = self.voronoi.event_queue.get()
            if event.circle_event:
                if not event.is_valid:
                    continue
                self.voronoi.sweep_line = event.yd
                self.voronoi.handle_circle_event(event)
            elif isinstance(event, SiteEvent):
                event.point.name = self.voronoi_site_index
                self.voronoi_site_index += 1
                self.voronoi.sweep_line = event.yd
                self.voronoi.handle_site_event(event)
            else:
                continue
            self.voronoi.event = event
            self.processed_events += 1
            return True
        return False

    def finish_voronoi(self) -> None:
        if self.voronoi is None:
            return
        self.voronoi.edges = self.voronoi.bounding_poly.finish_edges(
            edges=self.voronoi.edges,
            vertices=self.voronoi._vertices,
            points=self.voronoi.sites,
            event_queue=self.voronoi.event_queue,
        )
        self.voronoi.edges, self.voronoi._vertices = self.voronoi.bounding_poly.finish_polygon(
            self.voronoi.edges,
            self.voronoi._vertices,
            self.voronoi.sites,
        )
        if self.voronoi.remove_zero_length_edges:
            self.voronoi.clean_up_zero_length_edges()

    def step_finish_voronoi_edges(self, max_edges: int) -> bool:
        if self.voronoi is None:
            return True

        edges = self.voronoi.edges
        poly = self.voronoi.bounding_poly
        processed = 0

        while self.voronoi_finish_cursor < len(edges) and processed < max_edges:
            edge = edges[self.voronoi_finish_cursor]
            self.voronoi_finish_cursor += 1
            processed += 1

            twin = edge.twin
            if twin is None:
                continue

            origin = edge.get_origin()
            if origin is None or not poly.inside(origin):
                poly._finish_edge(edge)
                origin = edge.get_origin()

            target = twin.get_origin()
            if target is None or not poly.inside(target):
                poly._finish_edge(twin)
                target = twin.get_origin()

            if origin is not None and target is not None:
                self.voronoi_finished_edges.append(edge)
            else:
                edge.delete()
                twin.delete()

        return self.voronoi_finish_cursor >= len(edges)

    def finish_voronoi_polygon(self) -> None:
        if self.voronoi is None:
            return
        # We only consume clipped Voronoi edges; building the full polygon DCEL
        # is useful for cell areas but costs a large single-turn spike here.
        self.voronoi.edges = self.voronoi_finished_edges
        self.voronoi_finished_edges = []


_state: Optional[AnalyzerState] = None
_target_mask: int = 0
_completed_target_mask: int = 0
_abandoned_target_mask: int = 0
_last_visibility_sync_round: int = -1
_debug_last_by_key: Dict[str, int] = {}


def _debug(controller: Optional[Controller], message: str, key: Optional[str] = None, interval: int = 0) -> None:
    if not CHOKEPOINT_DEBUG_PRINTS:
        return

    current_round = map_info._rc.get_current_round() if map_info._width else 0
    if key is not None and interval > 0:
        last_round = _debug_last_by_key.get(key)
        if last_round is not None and current_round - last_round < interval:
            return
        _debug_last_by_key[key] = current_round

    unit_id = "?"
    if controller is not None:
        try:
            unit_id = str(controller.get_id())
        except Exception:
            unit_id = "?"
    print(f"[Hades chokepoint r={current_round} id={unit_id}] {message}", file=sys.stderr)


def debug(controller: Optional[Controller], message: str, key: Optional[str] = None, interval: int = 0) -> None:
    _debug(controller, message, key=key, interval=interval)


def _cell_to_index(r: int, c: int, width: int) -> int:
    return c + r * width


def _refresh_target_mask(state: Optional[AnalyzerState]) -> None:
    global _target_mask
    mask = 0
    if state is not None:
        for idx in state.blocker_kind_by_index:
            mask |= 1 << idx
    _target_mask = mask


def _index_to_position(idx: int, width: int) -> Position:
    return Position(idx % width, idx // width)


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
        if not any(candidate.team == expected_team or candidate.team == core.team for candidate in candidates):
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

    my_core = next((core for core in cores if core.team == map_info._my_team_idx), None)
    other_core = next((core for core in cores if core.team != map_info._my_team_idx), None)
    preferred: List[str] = []

    if my_core is not None and other_core is not None:
        if my_core.center[0] == other_core.center[0]:
            preferred.append(SYMMETRY_VERTICAL)
        if my_core.center[1] == other_core.center[1]:
            preferred.append(SYMMETRY_HORIZONTAL)
        preferred.append(SYMMETRY_ROTATIONAL)

    for symmetry in preferred + candidates:
        if symmetry in candidates:
            return symmetry
    return candidates[0]


def _current_environment_rows() -> List[List[int]]:
    width = map_info._width
    height = map_info._height
    rows = [[ENV_EMPTY for _ in range(width)] for _ in range(height)]
    walls = map_info._bm_env[map_info._IDX_ENV_WALL]
    ti = map_info._bm_env[map_info._IDX_ENV_ORE_TI]
    ax = map_info._bm_env[map_info._IDX_ENV_ORE_AX]
    for r in range(height):
        row = rows[r]
        base = r * width
        for c in range(width):
            bit = 1 << (base + c)
            if walls & bit:
                row[c] = ENV_WALL
            elif ti & bit:
                row[c] = ENV_ORE_TITANIUM
            elif ax & bit:
                row[c] = ENV_ORE_AXIONITE
    return rows


def _current_symmetry(environment_rows: List[List[int]], cores: List[CoreInfo]) -> Optional[str]:
    solved = _current_symmetry_from_flags()
    if solved is not None:
        return solved
    if map_info._bm_seen == map_info._board_mask:
        return detect_map_symmetry(map_info._width, map_info._height, environment_rows, cores)
    return None


def _current_symmetry_from_flags() -> Optional[str]:
    if not map_info._solved_sym:
        return None
    if map_info._hor_sym:
        return SYMMETRY_VERTICAL
    if map_info._ver_sym:
        return SYMMETRY_HORIZONTAL
    if map_info._rot_sym:
        return SYMMETRY_ROTATIONAL
    return None


def _compute_analysis_polygon(cfg: GridConfig, symmetry: Optional[str], anchor: CoreInfo, other: Optional[CoreInfo]) -> List[Point]:
    full_map_poly = rectangle_polygon(cfg.cols, cfg.rows)
    if symmetry is None:
        return list(full_map_poly)

    anchor_x = anchor.center[1] + 0.5
    anchor_y = anchor.center[0] + 0.5

    if symmetry == SYMMETRY_VERTICAL:
        mid_x = cfg.cols / 2.0
        if anchor_x <= mid_x:
            return [(0.0, 0.0), (mid_x, 0.0), (mid_x, float(cfg.rows)), (0.0, float(cfg.rows))]
        return [(mid_x, 0.0), (float(cfg.cols), 0.0), (float(cfg.cols), float(cfg.rows)), (mid_x, float(cfg.rows))]

    if symmetry == SYMMETRY_HORIZONTAL:
        mid_y = cfg.rows / 2.0
        if anchor_y <= mid_y:
            return [(0.0, 0.0), (float(cfg.cols), 0.0), (float(cfg.cols), mid_y), (0.0, mid_y)]
        return [(0.0, mid_y), (float(cfg.cols), mid_y), (float(cfg.cols), float(cfg.rows)), (0.0, float(cfg.rows))]

    if symmetry == SYMMETRY_ROTATIONAL and other is not None:
        other_x = other.center[1] + 0.5
        other_y = other.center[0] + 0.5
        mid = ((anchor_x + other_x) / 2.0, (anchor_y + other_y) / 2.0)
        normal = (other_x - anchor_x, other_y - anchor_y)
        return clip_polygon_against_half_plane(
            full_map_poly,
            mid=mid,
            normal=normal,
            keep_negative_side=True,
        )

    return list(full_map_poly)


def _analysis_polygon_mask(width: int, height: int, polygon: Sequence[Point]) -> int:
    if is_axis_aligned_rectangle(polygon):
        xs = [p[0] for p in polygon]
        ys = [p[1] for p in polygon]
        min_c = max(0, int(math.floor(min(xs))))
        max_c = min(width - 1, int(math.ceil(max(xs)) - 1))
        min_r = max(0, int(math.floor(min(ys))))
        max_r = min(height - 1, int(math.ceil(max(ys)) - 1))
        mask = 0
        row_bits = ((1 << (max_c - min_c + 1)) - 1) << min_c if max_c >= min_c else 0
        for r in range(min_r, max_r + 1):
            mask |= row_bits << (r * width)
        return mask

    mask = 0
    for r in range(height):
        for c in range(width):
            if point_in_polygon((c + 0.5, r + 0.5), polygon):
                mask |= 1 << (c + r * width)
    return mask


def _snapshot_state() -> Optional[AnalyzerState]:
    my_core_pos = map_info._my_core
    other_core_pos = map_info._their_core or map_info._predicted_enemy_core
    if my_core_pos is None or other_core_pos is None:
        return None

    width = map_info._width
    height = map_info._height
    cfg = GridConfig(rows=height, cols=width)
    cores = [
        CoreInfo(id=0, team=map_info._my_team_idx, center=(my_core_pos.y, my_core_pos.x)),
        CoreInfo(id=1, team=1 - map_info._my_team_idx, center=(other_core_pos.y, other_core_pos.x)),
    ]
    symmetry = _current_symmetry_from_flags()
    if symmetry is None and map_info._bm_seen == map_info._board_mask:
        symmetry = detect_map_symmetry(width, height, _current_environment_rows(), cores)
    anchor = cores[0]
    other = cores[1]
    analysis_poly = _compute_analysis_polygon(cfg, symmetry, anchor, other)
    analysis_tile_mask = _analysis_polygon_mask(width, height, analysis_poly)

    obstacles: Set[Cell] = set()
    walls = map_info._bm_env[map_info._IDX_ENV_WALL] & analysis_tile_mask
    while walls:
        lsb = walls & -walls
        idx = lsb.bit_length() - 1
        obstacles.add((idx // width, idx % width))
        walls ^= lsb

    return AnalyzerState(
        cfg=cfg,
        detected_symmetry=symmetry,
        obstacles=obstacles,
        cores=cores,
        analysis_poly=analysis_poly,
        analysis_tile_mask=analysis_tile_mask,
        raster_scale=raster_scale_from_spacing(CHOKEPOINT_SAMPLE_SPACING),
    )


def _start_block_key(reason: str) -> str:
    if reason.startswith("round "):
        return "round"
    if reason.startswith("analysis_area_unseen"):
        return "analysis_area_unseen"
    return reason.split(":", 1)[0]


def _snapshot_unseen_analysis_tiles(snapshot: AnalyzerState) -> int:
    return (snapshot.analysis_tile_mask & ~map_info._bm_seen).bit_count()


def _minimum_seen_map_tiles() -> int:
    return max(1, int(math.ceil(map_info._board_mask.bit_count() * CHOKEPOINT_MIN_SEEN_MAP_FRACTION)))


def _start_readiness() -> Tuple[Optional[str], Optional[AnalyzerState]]:
    if not CHOKEPOINT_ENABLE:
        return "disabled", None
    if map_info._width == 0 or map_info._height == 0:
        return "map_info_uninitialized", None
    if map_info._my_core is None:
        return "missing_my_core", None
    if map_info._their_core is None and map_info._predicted_enemy_core is None:
        return "missing_enemy_core", None

    current_round = map_info._rc.get_current_round()
    if current_round < CHOKEPOINT_START_ROUND:
        return f"round {current_round} < start {CHOKEPOINT_START_ROUND}", None

    seen_count = map_info._bm_seen.bit_count()
    board_count = map_info._board_mask.bit_count()
    min_seen = _minimum_seen_map_tiles()
    if seen_count < min_seen:
        return (
            f"map_area_seen {seen_count}/{board_count} tiles; "
            f"min={min_seen} ({CHOKEPOINT_MIN_SEEN_MAP_FRACTION:.0%})"
        ), None

    if _current_symmetry_from_flags() is None and map_info._bm_seen != map_info._board_mask:
        return "symmetry_unknown", None

    snapshot = _snapshot_state()
    if snapshot is None:
        return "snapshot_unavailable", None

    if snapshot.detected_symmetry is None:
        return "symmetry_unknown", None

    unseen_count = _snapshot_unseen_analysis_tiles(snapshot)
    if unseen_count and CHOKEPOINT_REQUIRE_FULL_ANALYSIS_SEEN:
        return f"analysis_area_unseen {unseen_count} tiles; full_map_required", None

    return None, snapshot


def _start_block_reason() -> Optional[str]:
    return _start_readiness()[0]


def _should_start() -> bool:
    return _start_block_reason() is None


def _ensure_state(controller: Optional[Controller] = None) -> Optional[AnalyzerState]:
    global _state
    if _state is None:
        reason, snapshot = _start_readiness()
        if reason is not None:
            if CHOKEPOINT_DEBUG_PRINTS:
                _debug(
                    controller,
                    f"waiting to start: {reason}; seen={map_info._bm_seen.bit_count()}/{map_info._board_mask.bit_count()} "
                    f"sym={map_info._solved_sym}",
                    key=f"start_wait:{_start_block_key(reason)}",
                    interval=CHOKEPOINT_DEBUG_INTERVAL_ROUNDS,
                )
            return None
        _state = snapshot
        if _state is not None:
            if CHOKEPOINT_DEBUG_PRINTS:
                unseen_count = _snapshot_unseen_analysis_tiles(_state)
                _debug(
                    controller,
                    f"started analyzer: map={_state.cfg.cols}x{_state.cfg.rows} "
                    f"obstacles={len(_state.obstacles)} symmetry={_state.detected_symmetry} "
                    f"analysis_tiles={_state.analysis_tile_mask.bit_count()} unseen_analysis={unseen_count} "
                    f"seen={map_info._bm_seen.bit_count()}/{map_info._board_mask.bit_count()} "
                    f"min_seen_fraction={CHOKEPOINT_MIN_SEEN_MAP_FRACTION:.0%} "
                    f"require_full_seen={CHOKEPOINT_REQUIRE_FULL_ANALYSIS_SEEN}",
                )
    return _state


def _step_geometry(state: AnalyzerState) -> None:
    ok = state.build_obstacle_geometry()
    if not ok:
        state.stage = STAGE_DONE
        state.blocker_kind_by_index.clear()
        _refresh_target_mask(state)
        if not CHOKEPOINT_DRAW_DEBUG:
            state.release_final_analysis_state()
        return
    state.stage = STAGE_VORONOI_INIT


def _step_voronoi_init(state: AnalyzerState) -> None:
    set_numeric_mode(NUMERIC_MODE_FLOAT)
    state.raw_vertex_ids_by_point.clear()
    state.raw_vertices.clear()
    state.raw_edges.clear()
    state.raw_vertex_radius.clear()
    points = state.collect_boundary_samples(CHOKEPOINT_SAMPLE_SPACING)
    if len(points) < 4 or len(state.analysis_poly) < 3:
        state.stage = STAGE_DONE
        state.blocker_kind_by_index.clear()
        _refresh_target_mask(state)
        if not CHOKEPOINT_DRAW_DEBUG:
            state.release_final_analysis_state()
        return
    state.voronoi = ForonoiVoronoi(ForonoiPolygon(state.analysis_poly))
    state.voronoi.initialize([ForonoiPoint(x, y) for x, y in points])
    if not CHOKEPOINT_DRAW_DEBUG:
        state.analysis_poly = []
        state.cores = []
        state.analysis_tile_mask = 0
    state.voronoi_site_index = 0
    state.processed_events = 0
    state.stage = STAGE_VORONOI_SWEEP


def _step_voronoi_sweep(state: AnalyzerState, controller: Controller) -> None:
    if state.voronoi is None:
        state.stage = STAGE_FAILED
        state.failed_reason = "missing_voronoi"
        return
    processed = 0
    while processed < CHOKEPOINT_MAX_SWEEP_EVENTS_PER_TICK:
        if processed % CHOKEPOINT_BUDGET_CHECK_INTERVAL == 0 and not _budget_remaining(controller):
            return
        if not state.step_sweep_event():
            state.voronoi_finish_cursor = 0
            state.voronoi_finished_edges = []
            state.stage = STAGE_VORONOI_FINISH
            return
        processed += 1


def _step_voronoi_finish(state: AnalyzerState, controller: Controller) -> None:
    if state.voronoi is None:
        state.stage = STAGE_FAILED
        state.failed_reason = "missing_voronoi"
        return
    if not _budget_remaining(controller):
        return
    if not state.step_finish_voronoi_edges(CHOKEPOINT_MAX_FINISH_EDGES_PER_TICK):
        return
    if not _budget_remaining(controller):
        return
    state.finish_voronoi_polygon()
    state.voronoi_edge_cursor = 0
    state.stage = STAGE_VORONOI_EXTRACT


def _step_voronoi_extract(state: AnalyzerState, controller: Controller) -> None:
    if state.voronoi is None:
        state.stage = STAGE_FAILED
        state.failed_reason = "missing_voronoi"
        return

    edges = state.voronoi.edges
    processed = 0
    while state.voronoi_edge_cursor < len(edges) and processed < CHOKEPOINT_MAX_EXTRACT_EDGES_PER_TICK:
        if processed % CHOKEPOINT_BUDGET_CHECK_INTERVAL == 0 and not _budget_remaining(controller):
            return

        edge = edges[state.voronoi_edge_cursor]
        state.voronoi_edge_cursor += 1
        processed += 1

        twin = edge.twin
        if twin is None or twin.incident_point is None:
            continue
        site_a = edge.incident_point
        site_b = twin.incident_point
        origin = edge.get_origin()
        target = twin.get_origin()
        if origin is None or target is None:
            continue

        p1 = (origin.x, origin.y)
        p2 = (target.x, target.y)
        if not state.segment_is_inside_free_space(p1, p2):
            continue

        v1 = state.get_or_create_raw_vid(p1, state.site_radius_for_sites(p1[0], p1[1], site_a, site_b))
        v2 = state.get_or_create_raw_vid(p2, state.site_radius_for_sites(p2[0], p2[1], site_a, site_b))
        if v1 == v2:
            continue

        edge_key = (v1, v2) if v1 < v2 else (v2, v1)
        state.raw_edges.add(edge_key)

    if state.voronoi_edge_cursor >= len(edges):
        state.voronoi = None
        state.stage = STAGE_PRUNE


def _step_prune(state: AnalyzerState) -> None:
    state.prune_graph(CHOKEPOINT_ISOLATED_RADIUS)
    if not CHOKEPOINT_DRAW_DEBUG:
        state.release_pruned_inputs()
    state.stage = STAGE_REGIONS


def _step_regions(state: AnalyzerState) -> None:
    state.identify_region_nodes(CHOKEPOINT_REGION_MIN_RADIUS)
    state.stage = STAGE_CHOKES


def _step_chokes(state: AnalyzerState) -> None:
    state.identify_choke_points(CHOKEPOINT_MAX_CHOKE_RADIUS)
    state.stage = STAGE_MERGE if CHOKEPOINT_ENABLE_MERGING else STAGE_SIMPLIFY


def _step_merge(state: AnalyzerState) -> None:
    state.merge_adjacent_regions(
        CHOKEPOINT_MERGE_RATIO_SMALL,
        CHOKEPOINT_MERGE_RATIO_LARGE,
        CHOKEPOINT_MERGE_RATIO_TWO,
    )
    state.stage = STAGE_SIMPLIFY


def _step_simplify(state: AnalyzerState) -> None:
    state.simplify_choke_points_for_game()
    state.stage = STAGE_MIRROR


def _step_mirror(state: AnalyzerState) -> None:
    if CHOKEPOINT_DRAW_DEBUG:
        state.mirror_analysis_results()
    else:
        state.mirror_blocker_targets_only()
        state.release_final_analysis_state()
    _refresh_target_mask(state)
    state.stage = STAGE_DONE


def _sync_visible_targets() -> None:
    global _last_visibility_sync_round
    if _state is None or _state.stage != STAGE_DONE:
        return
    current_round = map_info._rc.get_current_round()
    if current_round == _last_visibility_sync_round:
        return
    _last_visibility_sync_round = current_round

    global _completed_target_mask, _abandoned_target_mask
    mask = _target_mask & ~(_completed_target_mask | _abandoned_target_mask) & map_info._bm_seen_observed
    while mask:
        bit = mask & -mask
        idx = bit.bit_length() - 1
        mask ^= bit

        etype = map_info.type_at(idx % map_info._width, idx // map_info._width)
        team = map_info.team_at(idx % map_info._width, idx // map_info._width)
        if etype is None:
            continue

        if team == map_info._my_team and (
            etype == EntityType.BARRIER
            or etype == EntityType.LAUNCHER
        ):
            _completed_target_mask |= bit
            continue

        if etype in _VISIBLE_DISPOSABLE_TYPES:
            continue

        _abandoned_target_mask |= bit


def analysis_complete() -> bool:
    return _state is not None and _state.stage == STAGE_DONE


def blocker_kind_at(pos: Position) -> Optional[str]:
    if not analysis_complete():
        return None
    idx = pos.x + pos.y * map_info._width
    return _state.blocker_kind_by_index.get(idx)


def mark_completed(pos: Position) -> None:
    global _completed_target_mask
    _completed_target_mask |= 1 << (pos.x + pos.y * map_info._width)


def abandon_target(pos: Position) -> None:
    global _abandoned_target_mask
    _abandoned_target_mask |= 1 << (pos.x + pos.y * map_info._width)


def claim_targets() -> int:
    if not analysis_complete():
        return 0
    _sync_visible_targets()
    mask = _target_mask & ~(_completed_target_mask | _abandoned_target_mask)
    if not mask:
        return 0
    my_mask = 1 << (map_info._my_pos.x + map_info._my_pos.y * map_info._width)
    return pathing.voronoi_claim(my_mask, map_info._bm_friendly_bots, mask)


def claim_targets_near(pos: Position, chebyshev_radius: int) -> int:
    if not analysis_complete():
        return 0
    _sync_visible_targets()
    mask = _target_mask & ~(_completed_target_mask | _abandoned_target_mask)
    if not mask:
        return 0

    local = 1 << (pos.x + pos.y * map_info._width)
    for _ in range(chebyshev_radius):
        local = map_info.expand_chebyshev(local)
    mask &= local
    if not mask:
        return 0

    my_mask = 1 << (pos.x + pos.y * map_info._width)
    return pathing.voronoi_claim(my_mask, map_info._bm_friendly_bots, mask)


def _stage_debug_summary(state: AnalyzerState) -> str:
    vor_edges = len(state.voronoi.edges) if state.voronoi is not None else 0
    return (
        f"stage={state.stage} raw={len(state.raw_vertices)}/{len(state.raw_edges)} "
        f"pruned={len(state.pruned_vertices)}/{len(state.pruned_edges)} "
        f"regions={len(state.region_nodes)} chokes={len(state.choke_nodes)} "
        f"blockers={len(state.blocker_kind_by_index)} sweep_events={state.processed_events} "
        f"finish_cursor={state.voronoi_finish_cursor}/{vor_edges} "
        f"edge_cursor={state.voronoi_edge_cursor}/{vor_edges}"
    )


def _budget_remaining(controller: Controller) -> bool:
    return controller.get_cpu_time_elapsed() < CHOKEPOINT_CPU_BUDGET_US - CHOKEPOINT_MIN_HEADROOM_US


def _xy_to_debug_position(state: AnalyzerState, x: float, y: float) -> Optional[Position]:
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    c = min(state.cfg.cols - 1, max(0, int(math.floor(x))))
    r = min(state.cfg.rows - 1, max(0, int(math.floor(y))))
    return Position(c, r)


def _draw_xy_dot(controller: Controller, state: AnalyzerState, pt: Point, red: int, green: int, blue: int) -> None:
    pos = _xy_to_debug_position(state, pt[0], pt[1])
    if pos is not None:
        controller.draw_indicator_dot(pos, red, green, blue)


def _draw_xy_line(
    controller: Controller,
    state: AnalyzerState,
    start: Point,
    end: Point,
    red: int,
    green: int,
    blue: int,
) -> None:
    p1 = _xy_to_debug_position(state, start[0], start[1])
    p2 = _xy_to_debug_position(state, end[0], end[1])
    if p1 is None or p2 is None:
        return
    if p1 == p2:
        controller.draw_indicator_dot(p1, red, green, blue)
    else:
        controller.draw_indicator_line(p1, p2, red, green, blue)


def _draw_polygon_outline(controller: Controller, state: AnalyzerState, poly: Sequence[Point], red: int, green: int, blue: int) -> None:
    if len(poly) < 2:
        return
    for i, start in enumerate(poly):
        _draw_xy_line(controller, state, start, poly[(i + 1) % len(poly)], red, green, blue)


def _draw_edge_set(
    controller: Controller,
    state: AnalyzerState,
    vertices: Dict[VertexId, Point],
    edges: Set[Tuple[VertexId, VertexId]],
    red: int,
    green: int,
    blue: int,
    limit: int,
) -> None:
    for drawn, (a, b) in enumerate(edges):
        if drawn >= limit:
            break
        p1 = vertices.get(a)
        p2 = vertices.get(b)
        if p1 is None or p2 is None:
            continue
        _draw_xy_line(controller, state, p1, p2, red, green, blue)


def _draw_voronoi_edge(controller: Controller, state: AnalyzerState, edge, sweep_line: float) -> None:
    if edge is None or edge.twin is None or getattr(edge, "removed", False):
        return
    try:
        origin = edge.get_origin(sweep_line, state.cfg.rows)
        target = edge.twin.get_origin(sweep_line, state.cfg.rows)
    except Exception:
        return
    if origin is None or target is None:
        return
    _draw_xy_line(
        controller,
        state,
        (float(origin.xd), float(origin.yd)),
        (float(target.xd), float(target.yd)),
        0,
        255,
        120,
    )


def _draw_voronoi_construction_overlay(controller: Controller, state: AnalyzerState) -> None:
    voronoi = state.voronoi
    if voronoi is None:
        return

    sweep_line = float(voronoi.sweep_line)
    if not math.isfinite(sweep_line):
        return

    min_x = min(x for x, _ in state.analysis_poly)
    max_x = max(x for x, _ in state.analysis_poly)
    sweep_y = min(float(state.cfg.rows), max(0.0, sweep_line))
    _draw_xy_line(controller, state, (min_x, sweep_y), (max_x, sweep_y), 255, 0, 255)

    sweep_row = min(state.cfg.rows - 1, max(0, int(math.floor(sweep_y))))
    start_c = min(state.cfg.cols - 1, max(0, int(math.floor(min_x))))
    end_c = min(state.cfg.cols - 1, max(0, int(math.ceil(max_x))))
    tick_to = min(state.cfg.rows - 1, sweep_row + 1)
    for c in range(start_c, end_c + 1, max(1, CHOKEPOINT_DEBUG_SWEEP_TICK_SPACING)):
        controller.draw_indicator_line(Position(c, sweep_row), Position(c, tick_to), 255, 90, 255)

    sites = list(voronoi.sites or [])
    if sites:
        stride = max(1, len(sites) // max(1, CHOKEPOINT_DEBUG_MAX_SITE_GUIDES))
        drawn = 0
        for site in sites:
            if drawn >= CHOKEPOINT_DEBUG_MAX_SITE_GUIDES:
                break
            site_name = getattr(site, "name", None)
            if site_name is None and drawn % stride != 0:
                continue
            sx = float(site.xd)
            sy = float(site.yd)
            if site_name is None:
                _draw_xy_dot(controller, state, (sx, sy), 90, 90, 90)
            else:
                _draw_xy_line(controller, state, (sx, sy), (sx, sweep_y), 80, 180, 255)
                _draw_xy_dot(controller, state, (sx, sy), 0, 255, 0)
            drawn += 1

    live_edges = list(voronoi.edges)
    if live_edges:
        for edge in live_edges[-CHOKEPOINT_DEBUG_MAX_LIVE_VORONOI_EDGES:]:
            _draw_voronoi_edge(controller, state, edge, sweep_line)

    event = voronoi.event
    if isinstance(event, SiteEvent):
        pt = (float(event.point.xd), float(event.point.yd))
        _draw_xy_line(controller, state, pt, (pt[0], sweep_y), 255, 255, 0)
        _draw_xy_dot(controller, state, pt, 255, 255, 0)
    elif isinstance(event, CircleEvent):
        center = (float(event.center.xd), float(event.center.yd))
        _draw_xy_line(controller, state, center, (center[0], sweep_y), 255, 80, 0)
        _draw_xy_dot(controller, state, center, 255, 80, 0)
        points = list(event.point_triple or [])
        for i, point in enumerate(points):
            pt = (float(point.xd), float(point.yd))
            _draw_xy_dot(controller, state, pt, 255, 120, 0)
            if points:
                nxt = points[(i + 1) % len(points)]
                _draw_xy_line(controller, state, pt, (float(nxt.xd), float(nxt.yd)), 255, 120, 0)


def _draw_debug_overlay(controller: Controller, state: AnalyzerState) -> None:
    if not CHOKEPOINT_DRAW_DEBUG:
        return

    _draw_polygon_outline(controller, state, state.analysis_poly, 120, 120, 120)
    _draw_voronoi_construction_overlay(controller, state)

    if state.pruned_edges:
        _draw_edge_set(
            controller,
            state,
            state.pruned_vertices,
            state.pruned_edges,
            60,
            255,
            60,
            CHOKEPOINT_DEBUG_MAX_GRAPH_EDGES,
        )
    elif state.raw_edges:
        _draw_edge_set(
            controller,
            state,
            state.raw_vertices,
            state.raw_edges,
            90,
            140,
            255,
            CHOKEPOINT_DEBUG_MAX_GRAPH_EDGES,
        )

    for start, choke, end in state.choke_links:
        p_start = state.pruned_vertices.get(start)
        p_choke = state.pruned_vertices.get(choke)
        p_end = state.pruned_vertices.get(end)
        if p_start is None or p_choke is None or p_end is None:
            continue
        _draw_xy_line(controller, state, p_start, p_choke, 255, 255, 0)
        _draw_xy_line(controller, state, p_choke, p_end, 255, 255, 0)

    for vid in state.region_nodes:
        pt = state.pruned_vertices.get(vid)
        if pt is None:
            continue
        tile = state._round_point_to_tile(pt)
        if tile is None:
            continue
        r, c = tile
        controller.draw_indicator_dot(Position(c, r), 255, 170, 0)

    for (r, c), vid in state.rounded_choke_tiles.items():
        pos = Position(c, r)
        kind = state.rounded_choke_kinds.get((r, c), BLOCKER_WALL)
        if kind == BLOCKER_LAUNCHER:
            for rr in range(r - 1, r + 2):
                for cc in range(c - 1, c + 2):
                    if 0 <= rr < state.cfg.rows and 0 <= cc < state.cfg.cols:
                        footprint_pos = Position(cc, rr)
                        controller.draw_indicator_dot(footprint_pos, 0, 255, 255)
                        if rr != r or cc != c:
                            controller.draw_indicator_line(pos, footprint_pos, 0, 255, 255)
                            controller.draw_indicator_line(footprint_pos, pos, 0, 90, 255)
            for cc in range(c - 1, c + 2):
                if 0 <= cc < state.cfg.cols and 0 <= r - 1 < state.cfg.rows and 0 <= r + 1 < state.cfg.rows:
                    controller.draw_indicator_line(Position(cc, r - 1), Position(cc, r + 1), 0, 90, 255)
            for rr in range(r - 1, r + 2):
                if 0 <= rr < state.cfg.rows and 0 <= c - 1 < state.cfg.cols and 0 <= c + 1 < state.cfg.cols:
                    controller.draw_indicator_line(Position(c - 1, rr), Position(c + 1, rr), 0, 90, 255)
            controller.draw_indicator_dot(pos, 0, 70, 255)
            controller.draw_indicator_dot(pos, 255, 255, 255)
        else:
            controller.draw_indicator_dot(pos, 255, 0, 0)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx = c + dx
                ny = r + dy
                if 0 <= nx < state.cfg.cols and 0 <= ny < state.cfg.rows:
                    controller.draw_indicator_line(pos, Position(nx, ny), 255, 0, 0)


def post_turn(controller: Controller) -> None:
    if controller.get_entity_type() != EntityType.BUILDER_BOT:
        return
    if controller.get_cpu_time_elapsed() >= CHOKEPOINT_CPU_BUDGET_US:
        if CHOKEPOINT_DEBUG_PRINTS:
            _debug(
                controller,
                f"skip post_turn: cpu={controller.get_cpu_time_elapsed()}us "
                f"{_stage_debug_summary(_state) if _state is not None else 'state=none'}",
                key="skip_cpu",
                interval=CHOKEPOINT_DEBUG_INTERVAL_ROUNDS,
            )
        if CHOKEPOINT_DRAW_DEBUG and _state is not None:
            _draw_debug_overlay(controller, _state)
        return

    state = _ensure_state(controller)
    if state is None:
        return
    if state.stage in (STAGE_DONE, STAGE_FAILED):
        _sync_visible_targets()
        if CHOKEPOINT_DEBUG_PRINTS:
            _debug(
                controller,
                f"idle terminal: {_stage_debug_summary(state)} completed={_completed_target_mask.bit_count()} abandoned={_abandoned_target_mask.bit_count()}",
                key=f"terminal:{state.stage}",
                interval=CHOKEPOINT_DEBUG_INTERVAL_ROUNDS,
            )
        if CHOKEPOINT_DRAW_DEBUG:
            _draw_debug_overlay(controller, state)
        return
    if state.stage == STAGE_WAITING:
        state.stage = STAGE_GEOMETRY
        if CHOKEPOINT_DEBUG_PRINTS:
            _debug(controller, f"stage {STAGE_WAITING} -> {STAGE_GEOMETRY}; {_stage_debug_summary(state)}")

    steps = 0
    while steps < CHOKEPOINT_MAX_STAGES_PER_TICK and state.stage not in (STAGE_DONE, STAGE_FAILED):
        stage_before = state.stage
        if not _budget_remaining(controller):
            if CHOKEPOINT_DEBUG_PRINTS:
                _debug(
                    controller,
                    f"skip stage: budget exhausted before work; {_stage_debug_summary(state)} "
                    f"cpu={controller.get_cpu_time_elapsed()}us",
                    key=f"skip_budget:{state.stage}",
                    interval=CHOKEPOINT_DEBUG_INTERVAL_ROUNDS,
                )
            break

        if state.stage == STAGE_GEOMETRY:
            _step_geometry(state)
        elif state.stage == STAGE_VORONOI_INIT:
            _step_voronoi_init(state)
        elif state.stage == STAGE_VORONOI_SWEEP:
            _step_voronoi_sweep(state, controller)
        elif state.stage == STAGE_VORONOI_FINISH:
            _step_voronoi_finish(state, controller)
        elif state.stage == STAGE_VORONOI_EXTRACT:
            _step_voronoi_extract(state, controller)
        elif state.stage == STAGE_PRUNE:
            _step_prune(state)
        elif state.stage == STAGE_REGIONS:
            _step_regions(state)
        elif state.stage == STAGE_CHOKES:
            _step_chokes(state)
        elif state.stage == STAGE_MERGE:
            _step_merge(state)
        elif state.stage == STAGE_SIMPLIFY:
            _step_simplify(state)
        elif state.stage == STAGE_MIRROR:
            _step_mirror(state)
        else:
            break

        steps += 1
        if CHOKEPOINT_DEBUG_PRINTS:
            if state.stage == stage_before:
                if state.stage in (STAGE_VORONOI_SWEEP, STAGE_VORONOI_EXTRACT):
                    _debug(
                        controller,
                        f"progress: {_stage_debug_summary(state)} cpu={controller.get_cpu_time_elapsed()}us",
                        key=f"progress:{state.stage}",
                        interval=CHOKEPOINT_DEBUG_INTERVAL_ROUNDS,
                    )
            else:
                _debug(
                    controller,
                    f"stage {stage_before} -> {state.stage}; {_stage_debug_summary(state)} "
                    f"cpu={controller.get_cpu_time_elapsed()}us",
                )

        if state.stage == stage_before and state.stage in (STAGE_VORONOI_SWEEP, STAGE_VORONOI_FINISH, STAGE_VORONOI_EXTRACT):
            break

    if state.stage == STAGE_DONE:
        _sync_visible_targets()
        if CHOKEPOINT_DEBUG_PRINTS:
            _debug(
                controller,
                f"analysis complete: {_stage_debug_summary(state)} completed={_completed_target_mask.bit_count()} abandoned={_abandoned_target_mask.bit_count()}",
                key="analysis_complete",
                interval=999999,
            )
    elif state.stage == STAGE_FAILED and CHOKEPOINT_DEBUG_PRINTS:
        _debug(controller, f"analysis failed: reason={state.failed_reason}; {_stage_debug_summary(state)}")
    if CHOKEPOINT_DRAW_DEBUG:
        _draw_debug_overlay(controller, state)
