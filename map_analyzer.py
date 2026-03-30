#!/usr/bin/env python3
"""
Map Analyzer — parse .map26 files and extract strategic intel.

Outputs: dimensions, ore positions, symmetry type, core positions,
distances to nearest ore, chokepoint analysis, open-area scoring.

Usage:
    python map_analyzer.py maps/arena.map26
    python map_analyzer.py maps/           # analyze all maps in directory
    python map_analyzer.py maps/ --sort ore_titanium  # sort by titanium count
    python map_analyzer.py maps/arena.map26 --ascii   # print ASCII map
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path


# ── Protobuf primitives (same as replay_parser) ─────────────────────────────

def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = data[pos]; pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def _signed32(v: int) -> int:
    if v >= (1 << 31):
        v -= (1 << 32)
    return v


def _parse_fields(data: bytes) -> dict[int, list]:
    fields: dict[int, list] = {}
    pos, end = 0, len(data)
    while pos < end:
        tag, pos = _read_varint(data, pos)
        field_num = tag >> 3
        wire_type = tag & 0x7
        if wire_type == 0:
            val, pos = _read_varint(data, pos)
        elif wire_type == 2:
            length, pos = _read_varint(data, pos)
            val = data[pos:pos + length]; pos += length
        elif wire_type == 1:
            val = data[pos:pos + 8]; pos += 8
        elif wire_type == 5:
            val = data[pos:pos + 4]; pos += 4
        else:
            break
        fields.setdefault(field_num, []).append(val)
    return fields


# ── Environment tile types ───────────────────────────────────────────────────

ENV_EMPTY = 0
ENV_WALL = 1
ENV_ORE_TITANIUM = 2
ENV_ORE_AXIONITE = 3

ENV_NAMES = {
    ENV_EMPTY: "empty",
    ENV_WALL: "wall",
    ENV_ORE_TITANIUM: "titanium",
    ENV_ORE_AXIONITE: "axionite",
}

ENV_CHARS = {
    ENV_EMPTY: ".",
    ENV_WALL: "#",
    ENV_ORE_TITANIUM: "T",
    ENV_ORE_AXIONITE: "A",
}


# ── Map data structures ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Pos:
    x: int
    y: int

    def dist_sq(self, other: Pos) -> int:
        dx = self.x - other.x
        dy = self.y - other.y
        return dx * dx + dy * dy

    def dist(self, other: Pos) -> float:
        return math.sqrt(self.dist_sq(other))

    def manhattan(self, other: Pos) -> int:
        return abs(self.x - other.x) + abs(self.y - other.y)


@dataclass
class CoreInfo:
    team: int   # 0=A, 1=B
    pos: Pos


@dataclass
class MapData:
    name: str
    width: int
    height: int
    terrain: list[list[int]]   # terrain[y][x]
    cores: list[CoreInfo]


@dataclass
class MapAnalysis:
    name: str
    width: int
    height: int
    total_tiles: int
    wall_count: int
    empty_count: int
    titanium_ore_count: int
    axionite_ore_count: int
    wall_pct: float
    openness: float  # fraction of non-wall tiles

    symmetry: str  # "horizontal", "vertical", "rotational", "unknown"

    core_a: Pos | None
    core_b: Pos | None
    core_distance: float | None
    core_manhattan: int | None

    # Ore positions
    titanium_positions: list[Pos]
    axionite_positions: list[Pos]

    # Distance from each core to nearest ore (BFS on passable tiles)
    a_nearest_titanium_bfs: int | None
    a_nearest_axionite_bfs: int | None
    b_nearest_titanium_bfs: int | None
    b_nearest_axionite_bfs: int | None

    # Chokepoint score (lower = more choked)
    chokepoint_score: float

    # Ore clustering: avg distance between ore tiles of same type
    titanium_clustering: float | None
    axionite_clustering: float | None

    # Quadrant ore distribution (for assessing balance)
    ore_by_quadrant: dict[str, dict[str, int]]


# ── Parsing ──────────────────────────────────────────────────────────────────

def parse_map(path: Path) -> MapData:
    data = path.read_bytes()
    f = _parse_fields(data)

    width = f.get(1, [0])[0]
    height = f.get(2, [0])[0]

    terrain: list[list[int]] = []
    for row_data in f.get(3, []):
        rf = _parse_fields(row_data)
        tiles = list(rf[1][0]) if 1 in rf else []
        terrain.append(tiles)

    cores: list[CoreInfo] = []
    for cp in f.get(4, []):
        cf = _parse_fields(cp)
        core_team = cf.get(2, [0])[0]
        if 3 in cf:
            pf = _parse_fields(cf[3][0])
            cx = _signed32(pf[1][0]) if 1 in pf else 0
            cy = _signed32(pf[2][0]) if 2 in pf else 0
        else:
            cx, cy = 0, 0
        cores.append(CoreInfo(team=core_team, pos=Pos(cx, cy)))

    return MapData(name=path.stem, width=width, height=height, terrain=terrain, cores=cores)


# ── Analysis ─────────────────────────────────────────────────────────────────

DIRS_8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def _bfs_distances(terrain: list[list[int]], w: int, h: int, start: Pos) -> dict[tuple[int, int], int]:
    """BFS from start over non-wall tiles. Returns {(x,y): distance}."""
    dist: dict[tuple[int, int], int] = {}
    if start.x < 0 or start.x >= w or start.y < 0 or start.y >= h:
        return dist
    if terrain[start.y][start.x] == ENV_WALL:
        return dist

    q: deque[tuple[int, int, int]] = deque()
    q.append((start.x, start.y, 0))
    dist[(start.x, start.y)] = 0

    while q:
        x, y, d = q.popleft()
        for dx, dy in DIRS_8:
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in dist:
                if terrain[ny][nx] != ENV_WALL:
                    dist[(nx, ny)] = d + 1
                    q.append((nx, ny, d + 1))
    return dist


def _detect_symmetry(terrain: list[list[int]], w: int, h: int) -> str:
    """Detect map symmetry type."""
    # Horizontal reflection (left-right)
    horiz = True
    for y in range(h):
        for x in range(w // 2 + 1):
            mx = w - 1 - x
            if terrain[y][x] != terrain[y][mx]:
                horiz = False
                break
        if not horiz:
            break

    # Vertical reflection (top-bottom)
    vert = True
    for y in range(h // 2 + 1):
        my = h - 1 - y
        for x in range(w):
            if terrain[y][x] != terrain[my][x]:
                vert = False
                break
        if not vert:
            break

    # 180-degree rotational symmetry
    rot = True
    for y in range(h):
        for x in range(w):
            rx, ry = w - 1 - x, h - 1 - y
            if terrain[y][x] != terrain[ry][rx]:
                rot = False
                break
        if not rot:
            break

    if horiz and vert:
        return "horizontal+vertical"
    if horiz:
        return "horizontal"
    if vert:
        return "vertical"
    if rot:
        return "rotational"
    return "unknown"


def _chokepoint_score(terrain: list[list[int]], w: int, h: int) -> float:
    """Score how open the map is. Higher = more open.
    Measures: for each non-wall tile, count non-wall neighbors (0-8).
    Average that ratio. More open maps score higher."""
    total = 0
    count = 0
    for y in range(h):
        for x in range(w):
            if terrain[y][x] == ENV_WALL:
                continue
            neighbors = 0
            for dx, dy in DIRS_8:
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and terrain[ny][nx] != ENV_WALL:
                    neighbors += 1
            total += neighbors
            count += 1
    return total / (count * 8) if count else 0.0


def _ore_clustering(positions: list[Pos]) -> float | None:
    """Average pairwise distance between ore tiles. Lower = more clustered."""
    if len(positions) < 2:
        return None
    total = 0.0
    pairs = 0
    for i, a in enumerate(positions):
        for b in positions[i + 1:]:
            total += a.dist(b)
            pairs += 1
    return total / pairs if pairs else None


def _ore_by_quadrant(positions: list[Pos], w: int, h: int) -> dict[str, int]:
    mx, my = w / 2, h / 2
    quads = {"NW": 0, "NE": 0, "SW": 0, "SE": 0}
    for p in positions:
        qx = "W" if p.x < mx else "E"
        qy = "N" if p.y < my else "S"
        quads[qy + qx] += 1
    return quads


def analyze_map(md: MapData) -> MapAnalysis:
    w, h = md.width, md.height
    terrain = md.terrain

    wall_count = empty_count = ti_count = ax_count = 0
    ti_positions: list[Pos] = []
    ax_positions: list[Pos] = []

    for y in range(h):
        for x in range(w):
            t = terrain[y][x] if y < len(terrain) and x < len(terrain[y]) else ENV_EMPTY
            if t == ENV_WALL:
                wall_count += 1
            elif t == ENV_ORE_TITANIUM:
                ti_count += 1
                ti_positions.append(Pos(x, y))
            elif t == ENV_ORE_AXIONITE:
                ax_count += 1
                ax_positions.append(Pos(x, y))
            else:
                empty_count += 1

    total = w * h

    core_a = core_b = None
    for c in md.cores:
        if c.team == 0:
            core_a = c.pos
        else:
            core_b = c.pos

    core_dist = core_a.dist(core_b) if core_a and core_b else None
    core_manh = core_a.manhattan(core_b) if core_a and core_b else None

    # BFS distances from cores
    a_bfs = _bfs_distances(terrain, w, h, core_a) if core_a else {}
    b_bfs = _bfs_distances(terrain, w, h, core_b) if core_b else {}

    def nearest_bfs(bfs_dist: dict, positions: list[Pos]) -> int | None:
        best = None
        for p in positions:
            d = bfs_dist.get((p.x, p.y))
            if d is not None and (best is None or d < best):
                best = d
        return best

    symmetry = _detect_symmetry(terrain, w, h)
    choke = _chokepoint_score(terrain, w, h)

    all_ore = ti_positions + ax_positions
    ore_quads = {
        "titanium": _ore_by_quadrant(ti_positions, w, h),
        "axionite": _ore_by_quadrant(ax_positions, w, h),
        "all": _ore_by_quadrant(all_ore, w, h),
    }

    return MapAnalysis(
        name=md.name,
        width=w, height=h,
        total_tiles=total,
        wall_count=wall_count,
        empty_count=empty_count,
        titanium_ore_count=ti_count,
        axionite_ore_count=ax_count,
        wall_pct=wall_count / total * 100 if total else 0,
        openness=(total - wall_count) / total if total else 0,
        symmetry=symmetry,
        core_a=core_a,
        core_b=core_b,
        core_distance=core_dist,
        core_manhattan=core_manh,
        a_nearest_titanium_bfs=nearest_bfs(a_bfs, ti_positions),
        a_nearest_axionite_bfs=nearest_bfs(a_bfs, ax_positions),
        b_nearest_titanium_bfs=nearest_bfs(b_bfs, ti_positions),
        b_nearest_axionite_bfs=nearest_bfs(b_bfs, ax_positions),
        chokepoint_score=choke,
        titanium_clustering=_ore_clustering(ti_positions),
        axionite_clustering=_ore_clustering(ax_positions),
        titanium_positions=ti_positions,
        axionite_positions=ax_positions,
        ore_by_quadrant=ore_quads,
    )


# ── Output ───────────────────────────────────────────────────────────────────

def print_ascii_map(md: MapData, analysis: MapAnalysis) -> None:
    """Print an ASCII representation of the map with legend."""
    w, h = md.width, md.height
    terrain = md.terrain

    # Header with column numbers (ones digit)
    col_label = "".join(str(x % 10) for x in range(w))
    print(f"    {col_label}")
    print(f"   +{'-' * w}+")

    for y in range(h):
        row = []
        for x in range(w):
            t = terrain[y][x] if y < len(terrain) and x < len(terrain[y]) else ENV_EMPTY
            # Check if this is a core tile (3x3 around core center)
            is_core = False
            for c in md.cores:
                if abs(x - c.pos.x) <= 1 and abs(y - c.pos.y) <= 1:
                    is_core = True
                    ch = "a" if c.team == 0 else "b"
                    break
            if is_core:
                row.append(ch)
            else:
                row.append(ENV_CHARS.get(t, "?"))
        print(f"{y:3d}|{''.join(row)}|")

    print(f"   +{'-' * w}+")
    print(f"Legend: .=empty  #=wall  T=titanium  A=axionite  a=core_A  b=core_B")


def print_analysis(a: MapAnalysis, show_ascii: bool = False, md: MapData | None = None) -> None:
    print(f"\n{'=' * 60}")
    print(f"  Map: {a.name}  ({a.width}x{a.height} = {a.total_tiles} tiles)")
    print(f"{'=' * 60}")

    if show_ascii and md:
        print()
        print_ascii_map(md, a)

    print(f"\n  Symmetry:       {a.symmetry}")
    print(f"  Walls:          {a.wall_count} ({a.wall_pct:.1f}%)")
    print(f"  Openness:       {a.openness:.2f}")
    print(f"  Chokepoint:     {a.chokepoint_score:.3f}  (1.0=fully open, 0=fully walled)")

    print(f"\n  Titanium ore:   {a.titanium_ore_count}")
    print(f"  Axionite ore:   {a.axionite_ore_count}")
    print(f"  Total ore:      {a.titanium_ore_count + a.axionite_ore_count}")

    if a.titanium_clustering is not None:
        print(f"  Ti clustering:  {a.titanium_clustering:.1f} avg pairwise dist")
    if a.axionite_clustering is not None:
        print(f"  Ax clustering:  {a.axionite_clustering:.1f} avg pairwise dist")

    if a.core_a and a.core_b:
        print(f"\n  Core A:         ({a.core_a.x}, {a.core_a.y})")
        print(f"  Core B:         ({a.core_b.x}, {a.core_b.y})")
        print(f"  Core distance:  {a.core_distance:.1f} euclidean / {a.core_manhattan} manhattan")

    print(f"\n  BFS from Core A:")
    print(f"    Nearest Ti:   {a.a_nearest_titanium_bfs or 'N/A'} steps")
    print(f"    Nearest Ax:   {a.a_nearest_axionite_bfs or 'N/A'} steps")
    print(f"  BFS from Core B:")
    print(f"    Nearest Ti:   {a.b_nearest_titanium_bfs or 'N/A'} steps")
    print(f"    Nearest Ax:   {a.b_nearest_axionite_bfs or 'N/A'} steps")

    print(f"\n  Ore by quadrant:")
    for kind, quads in a.ore_by_quadrant.items():
        qs = " ".join(f"{k}={v}" for k, v in quads.items())
        print(f"    {kind:10s}: {qs}")


def print_summary_table(analyses: list[MapAnalysis], sort_key: str | None = None) -> None:
    if sort_key:
        key_map = {
            "name": lambda a: a.name,
            "size": lambda a: a.total_tiles,
            "walls": lambda a: a.wall_pct,
            "openness": lambda a: -a.openness,
            "ore_titanium": lambda a: -a.titanium_ore_count,
            "ore_axionite": lambda a: -a.axionite_ore_count,
            "ore_total": lambda a: -(a.titanium_ore_count + a.axionite_ore_count),
            "chokepoint": lambda a: a.chokepoint_score,
            "core_dist": lambda a: -(a.core_distance or 0),
        }
        if sort_key in key_map:
            analyses = sorted(analyses, key=key_map[sort_key])

    headers = ["Map", "Size", "Sym", "Walls%", "Open", "Choke", "Ti", "Ax", "CoreDist", "NearTi", "NearAx"]
    rows: list[list[str]] = []
    for a in analyses:
        rows.append([
            a.name,
            f"{a.width}x{a.height}",
            a.symmetry[:4],
            f"{a.wall_pct:.0f}%",
            f"{a.openness:.2f}",
            f"{a.chokepoint_score:.3f}",
            str(a.titanium_ore_count),
            str(a.axionite_ore_count),
            f"{a.core_distance:.0f}" if a.core_distance else "-",
            str(a.a_nearest_titanium_bfs) if a.a_nearest_titanium_bfs else "-",
            str(a.a_nearest_axionite_bfs) if a.a_nearest_axionite_bfs else "-",
        ])

    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    div = sum(widths) + len(headers) - 1
    print(f"\n{'=' * max(80, div)}")
    print("Map Analysis Summary")
    print(f"{'=' * max(80, div)}")
    print(" ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("-" * max(80, div))
    for row in rows:
        print(" ".join(v.ljust(w) for v, w in zip(row, widths)))
    print()


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze Cambridge Battlecode .map26 files.")
    parser.add_argument("path", help="Path to a .map26 file or directory of maps.")
    parser.add_argument("--ascii", action="store_true", help="Print ASCII map visualization.")
    parser.add_argument(
        "--sort",
        choices=["name", "size", "walls", "openness", "ore_titanium", "ore_axionite", "ore_total", "chokepoint", "core_dist"],
        help="Sort summary table by this key.",
    )
    parser.add_argument("--summary-only", action="store_true", help="Only print summary table, skip individual reports.")
    args = parser.parse_args()

    target = Path(args.path)
    if target.is_file():
        map_files = [target]
    elif target.is_dir():
        map_files = sorted(target.glob("*.map26"))
        if not map_files:
            print(f"No .map26 files found in {target}", file=sys.stderr)
            return 1
    else:
        print(f"Path not found: {target}", file=sys.stderr)
        return 1

    analyses: list[MapAnalysis] = []
    map_datas: list[MapData] = []
    for mf in map_files:
        md = parse_map(mf)
        map_datas.append(md)
        analyses.append(analyze_map(md))

    if not args.summary_only:
        for md, a in zip(map_datas, analyses):
            print_analysis(a, show_ascii=args.ascii, md=md)

    if len(analyses) > 1:
        print_summary_table(analyses, sort_key=args.sort)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
