#!/usr/bin/env python3
"""
Taunt-style terrain analysis GUI.

This version is designed to visibly respond every time you press Run Analysis.

It does these steps:

1. Reads a paintable terrain grid.
2. Finds walkable connected components.
3. Splits those components into areas by height and buildability.
4. Detects resource clusters.
5. Partitions each resource area so every resource cluster gets its own region.
6. Draws colored regions and red borders between them.

Controls:
- Left-click / drag to paint.
- Select paint mode from the right panel.
- Press Run Analysis to compute regions.
"""

import math
import tkinter as tk
from collections import deque
from dataclasses import dataclass, field
from tkinter import ttk


Cell = tuple[int, int]


@dataclass
class TerrainCell:
    walkable: bool = True
    buildable: bool = True
    height: int = 0
    resource: bool = False


@dataclass
class Area:
    id: int
    cells: set[Cell]
    component_id: int
    height: int
    buildable: bool
    resource_clusters: list[set[Cell]] = field(default_factory=list)
    regions: list[set[Cell]] = field(default_factory=list)


class TerrainAnalyzer:
    def __init__(self, grid: list[list[TerrainCell]]):
        self.grid = grid
        self.h = len(grid)
        self.w = len(grid[0]) if self.h else 0

    def analyze(self) -> list[Area]:
        component_of = self.connected_walkable_components()
        areas = self.split_into_areas(component_of)

        for area in areas:
            if area.buildable:
                area.resource_clusters = self.find_resource_clusters(area.cells)

            if len(area.resource_clusters) > 1:
                area.regions = self.make_nearest_cluster_regions(area)
            else:
                area.regions = [set(area.cells)]

        return areas

    def neighbors4(self, cell: Cell):
        x, y = cell

        possible = [
            (x + 1, y),
            (x - 1, y),
            (x, y + 1),
            (x, y - 1),
        ]

        for nx, ny in possible:
            if 0 <= nx < self.w and 0 <= ny < self.h:
                yield nx, ny

    def connected_walkable_components(self) -> dict[Cell, int]:
        seen = set()
        component_of = {}
        component_id = 0

        for y in range(self.h):
            for x in range(self.w):
                start = (x, y)

                if start in seen:
                    continue

                if not self.grid[y][x].walkable:
                    continue

                component_id += 1

                queue = deque([start])
                seen.add(start)
                component_of[start] = component_id

                while queue:
                    current = queue.popleft()

                    for neighbor in self.neighbors4(current):
                        nx, ny = neighbor

                        if neighbor in seen:
                            continue

                        if not self.grid[ny][nx].walkable:
                            continue

                        seen.add(neighbor)
                        component_of[neighbor] = component_id
                        queue.append(neighbor)

        return component_of

    def split_into_areas(self, component_of: dict[Cell, int]) -> list[Area]:
        """
        Split walkable terrain into areas.

        Cells are in the same area only if they share:
        - walkable component,
        - height,
        - buildability.
        """
        seen = set()
        areas = []

        for y in range(self.h):
            for x in range(self.w):
                start = (x, y)

                if start in seen:
                    continue

                if start not in component_of:
                    continue

                start_cell = self.grid[y][x]

                area_key = (
                    component_of[start],
                    start_cell.height,
                    start_cell.buildable,
                )

                queue = deque([start])
                seen.add(start)
                cells = {start}

                while queue:
                    current = queue.popleft()

                    for neighbor in self.neighbors4(current):
                        nx, ny = neighbor

                        if neighbor in seen:
                            continue

                        if neighbor not in component_of:
                            continue

                        neighbor_cell = self.grid[ny][nx]

                        neighbor_key = (
                            component_of[neighbor],
                            neighbor_cell.height,
                            neighbor_cell.buildable,
                        )

                        if neighbor_key != area_key:
                            continue

                        seen.add(neighbor)
                        cells.add(neighbor)
                        queue.append(neighbor)

                area = Area(
                    id=len(areas) + 1,
                    cells=cells,
                    component_id=area_key[0],
                    height=area_key[1],
                    buildable=area_key[2],
                )

                areas.append(area)

        return areas

    def find_resource_clusters(
        self,
        area_cells: set[Cell],
        cluster_radius: int = 4,
    ) -> list[set[Cell]]:
        """
        Find groups of nearby resource cells.
        """
        resources = set()

        for x, y in area_cells:
            if self.grid[y][x].resource:
                resources.add((x, y))

        clusters = []
        seen = set()

        for resource in resources:
            if resource in seen:
                continue

            cluster = {resource}
            seen.add(resource)
            queue = deque([resource])

            while queue:
                current = queue.popleft()
                cx, cy = current

                for other in resources:
                    if other in seen:
                        continue

                    ox, oy = other
                    distance = abs(cx - ox) + abs(cy - oy)

                    if distance <= cluster_radius:
                        seen.add(other)
                        cluster.add(other)
                        queue.append(other)

            clusters.append(cluster)

        return clusters

    def make_nearest_cluster_regions(self, area: Area) -> list[set[Cell]]:
        """
        Partition the area by assigning every cell to the nearest resource cluster.

        This makes the analysis always visible, unlike a strict separator solver
        that might fail to find clean straight-line cuts.
        """
        centers = []

        for cluster in area.resource_clusters:
            center_x = sum(x for x, _ in cluster) / len(cluster)
            center_y = sum(y for _, y in cluster) / len(cluster)
            centers.append((center_x, center_y))

        regions = [set() for _ in centers]

        for cell in area.cells:
            x, y = cell

            best_index = 0
            best_distance = None

            for index, center in enumerate(centers):
                center_x, center_y = center

                distance = abs(x - center_x) + abs(y - center_y)

                if best_distance is None or distance < best_distance:
                    best_distance = distance
                    best_index = index

            regions[best_index].add(cell)

        return regions


class TerrainGUI(tk.Tk):
    CELL_SIZE = 16
    GRID_W = 48
    GRID_H = 32

    REGION_COLORS = [
        "#f6c7c7",
        "#c7dcf6",
        "#d1f6c7",
        "#f3e6aa",
        "#dfc7f6",
        "#c7f0f6",
        "#f6d9c7",
        "#d8d8d8",
        "#c7f6de",
        "#f6c7e8",
    ]

    def __init__(self):
        super().__init__()

        self.title("Taunt-style Terrain Analysis")
        self.resizable(False, False)

        self.grid = []
        self.areas = []

        self.paint_mode = tk.StringVar(value="wall")
        self.show_regions = tk.BooleanVar(value=True)

        self.build_gui()
        self.make_sample_map()
        self.draw()

    def build_gui(self):
        main = ttk.Frame(self, padding=8)
        main.grid(row=0, column=0)

        self.canvas = tk.Canvas(
            main,
            width=self.GRID_W * self.CELL_SIZE,
            height=self.GRID_H * self.CELL_SIZE,
            bg="white",
            highlightthickness=1,
            highlightbackground="#888888",
        )

        self.canvas.grid(row=0, column=0, rowspan=2)

        self.canvas.bind("<Button-1>", self.paint)
        self.canvas.bind("<B1-Motion>", self.paint)

        panel = ttk.Frame(main, padding=(10, 0, 0, 0))
        panel.grid(row=0, column=1, sticky="nw")

        ttk.Label(panel, text="Paint mode").grid(row=0, column=0, sticky="w")

        modes = [
            ("Wall / obstacle", "wall"),
            ("Low buildable", "low"),
            ("Mid buildable", "mid"),
            ("High buildable", "high"),
            ("Unbuildable", "unbuildable"),
            ("Resource", "resource"),
            ("Erase resource", "erase_resource"),
        ]

        for row, pair in enumerate(modes, start=1):
            label, value = pair

            ttk.Radiobutton(
                panel,
                text=label,
                variable=self.paint_mode,
                value=value,
            ).grid(row=row, column=0, sticky="w")

        ttk.Separator(panel).grid(row=9, column=0, sticky="ew", pady=8)

        ttk.Checkbutton(
            panel,
            text="Show region tint",
            variable=self.show_regions,
            command=self.draw,
        ).grid(row=10, column=0, sticky="w", pady=(0, 8))

        ttk.Button(
            panel,
            text="Run analysis",
            command=self.run_analysis,
        ).grid(row=11, column=0, sticky="ew", pady=2)

        ttk.Button(
            panel,
            text="Sample map",
            command=self.load_sample,
        ).grid(row=12, column=0, sticky="ew", pady=2)

        ttk.Button(
            panel,
            text="Clear map",
            command=self.clear_map,
        ).grid(row=13, column=0, sticky="ew", pady=2)

        ttk.Separator(panel).grid(row=14, column=0, sticky="ew", pady=8)

        legend = (
            "Legend:\n"
            "Black = wall\n"
            "Green = low buildable\n"
            "Olive = mid buildable\n"
            "Dark green = high buildable\n"
            "Blue = unbuildable\n"
            "Yellow = resource\n"
            "Red = region border"
        )

        ttk.Label(
            panel,
            text=legend,
            justify="left",
        ).grid(row=15, column=0, sticky="w")

        self.status = tk.StringVar(
            value="Sample map loaded. Press Run analysis."
        )

        ttk.Label(
            main,
            textvariable=self.status,
            wraplength=self.GRID_W * self.CELL_SIZE,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

    def make_empty_grid(self):
        self.grid = []

        for _y in range(self.GRID_H):
            row = []

            for _x in range(self.GRID_W):
                row.append(TerrainCell())

            self.grid.append(row)

    def make_sample_map(self):
        self.make_empty_grid()

        # Border walls.
        for x in range(self.GRID_W):
            self.grid[0][x].walkable = False
            self.grid[self.GRID_H - 1][x].walkable = False

        for y in range(self.GRID_H):
            self.grid[y][0].walkable = False
            self.grid[y][self.GRID_W - 1].walkable = False

        # Vertical unbuildable strip.
        for y in range(8, 24):
            for x in range(22, 26):
                self.grid[y][x].buildable = False

        # Gap in the strip.
        for y in range(14, 18):
            for x in range(22, 26):
                self.grid[y][x].buildable = True

        # A raised plateau.
        for y in range(4, 13):
            for x in range(31, 44):
                self.grid[y][x].height = 1

        # Obstacles.
        for x in range(6, 18):
            self.grid[10][x].walkable = False
            self.grid[10][x].resource = False

        for x in range(30, 42):
            self.grid[22][x].walkable = False
            self.grid[22][x].resource = False

        for y in range(20, 28):
            self.grid[y][13].walkable = False
            self.grid[y][13].resource = False

        # Resource clusters.
        clusters = [
            [(7, 5), (8, 5), (7, 6), (8, 6)],
            [(39, 6), (40, 6), (39, 7), (40, 7)],
            [(8, 26), (9, 26), (8, 27), (9, 27)],
            [(38, 26), (39, 26), (38, 27), (39, 27)],
        ]

        for cluster in clusters:
            for x, y in cluster:
                self.grid[y][x].walkable = True
                self.grid[y][x].buildable = True
                self.grid[y][x].resource = True

    def load_sample(self):
        self.make_sample_map()
        self.areas = []
        self.status.set("Sample map loaded. Press Run analysis.")
        self.draw()

    def clear_map(self):
        self.make_empty_grid()
        self.areas = []
        self.status.set("Map cleared.")
        self.draw()

    def paint(self, event):
        x = event.x // self.CELL_SIZE
        y = event.y // self.CELL_SIZE

        if not (0 <= x < self.GRID_W and 0 <= y < self.GRID_H):
            return

        cell = self.grid[y][x]
        mode = self.paint_mode.get()

        if mode == "wall":
            cell.walkable = False
            cell.resource = False

        elif mode == "low":
            cell.walkable = True
            cell.buildable = True
            cell.height = 0

        elif mode == "mid":
            cell.walkable = True
            cell.buildable = True
            cell.height = 1

        elif mode == "high":
            cell.walkable = True
            cell.buildable = True
            cell.height = 2

        elif mode == "unbuildable":
            cell.walkable = True
            cell.buildable = False
            cell.resource = False

        elif mode == "resource":
            if cell.walkable and cell.buildable:
                cell.resource = True

        elif mode == "erase_resource":
            cell.resource = False

        self.areas = []
        self.status.set("Map changed. Press Run analysis.")
        self.draw()

    def run_analysis(self):
        analyzer = TerrainAnalyzer(self.grid)
        self.areas = analyzer.analyze()

        area_count = len(self.areas)
        cluster_count = sum(len(area.resource_clusters) for area in self.areas)

        split_area_count = 0

        for area in self.areas:
            if len(area.regions) > 1:
                split_area_count += 1

        self.status.set(
            f"Analysis complete: "
            f"{area_count} areas, "
            f"{cluster_count} resource clusters, "
            f"{split_area_count} partitioned areas."
        )

        self.draw()

    def base_color(self, cell: TerrainCell) -> str:
        if not cell.walkable:
            return "#202020"

        if not cell.buildable:
            return "#7da1c4"

        if cell.height == 0:
            return "#c9d88a"

        if cell.height == 1:
            return "#aeb971"

        return "#8ba06b"

    def make_region_color_map(self) -> dict[Cell, str]:
        color_by_cell = {}

        if not self.show_regions.get():
            return color_by_cell

        color_index = 0

        for area in self.areas:
            if len(area.regions) <= 1:
                continue

            for region in area.regions:
                color = self.REGION_COLORS[color_index % len(self.REGION_COLORS)]
                color_index += 1

                for cell in region:
                    color_by_cell[cell] = color

        return color_by_cell

    def make_region_id_map(self) -> dict[Cell, tuple[int, int]]:
        """
        Returns:
            cell -> (area_id, region_index)
        """
        region_id_by_cell = {}

        for area in self.areas:
            if len(area.regions) <= 1:
                continue

            for region_index, region in enumerate(area.regions):
                for cell in region:
                    region_id_by_cell[cell] = (area.id, region_index)

        return region_id_by_cell

    def draw(self):
        self.canvas.delete("all")

        color_by_cell = self.make_region_color_map()

        for y in range(self.GRID_H):
            for x in range(self.GRID_W):
                cell = self.grid[y][x]

                fill = color_by_cell.get((x, y), self.base_color(cell))

                x0 = x * self.CELL_SIZE
                y0 = y * self.CELL_SIZE
                x1 = x0 + self.CELL_SIZE
                y1 = y0 + self.CELL_SIZE

                self.canvas.create_rectangle(
                    x0,
                    y0,
                    x1,
                    y1,
                    fill=fill,
                    outline="#555555",
                    width=1,
                )

                if cell.resource:
                    padding = 3

                    self.canvas.create_oval(
                        x0 + padding,
                        y0 + padding,
                        x1 - padding,
                        y1 - padding,
                        fill="#ffd447",
                        outline="#8a6d00",
                        width=1,
                    )

        self.draw_region_borders()

        self.canvas.create_text(
            8,
            8,
            anchor="nw",
            fill="#111111",
            text="Yellow = resources    Red = computed region borders",
            font=("TkDefaultFont", 10, "bold"),
        )

    def draw_region_borders(self):
        region_id_by_cell = self.make_region_id_map()

        if not region_id_by_cell:
            return

        for y in range(self.GRID_H):
            for x in range(self.GRID_W):
                here = region_id_by_cell.get((x, y))

                if here is None:
                    continue

                right = region_id_by_cell.get((x + 1, y))
                down = region_id_by_cell.get((x, y + 1))

                x0 = x * self.CELL_SIZE
                y0 = y * self.CELL_SIZE
                x1 = x0 + self.CELL_SIZE
                y1 = y0 + self.CELL_SIZE

                if right is not None and right != here:
                    self.canvas.create_line(
                        x1,
                        y0,
                        x1,
                        y1,
                        fill="#e02b2b",
                        width=2,
                    )

                if down is not None and down != here:
                    self.canvas.create_line(
                        x0,
                        y1,
                        x1,
                        y1,
                        fill="#e02b2b",
                        width=2,
                    )


def main():
    app = TerrainGUI()
    app.mainloop()


if __name__ == "__main__":
    main()