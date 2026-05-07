from __future__ import annotations

import argparse
import json
import os
import tkinter as tk
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

Cell = Tuple[int, int]

ENV_EMPTY = 0
ENV_WALL = 1
ENV_ORE_TITANIUM = 2
ENV_ORE_AXIONITE = 3

TEAM_A = 0

DIRECTIONS = {
    "N": (0, -1),
    "E": (1, 0),
    "S": (0, 1),
    "W": (-1, 0),
}


@dataclass(frozen=True)
class CoreInfo:
    id: int
    team: int
    center: Cell
    footprint: Tuple[Cell, ...]


@dataclass
class MapData:
    path: str
    width: int
    height: int
    rows: List[List[int]]
    cores: List[CoreInfo]


@dataclass
class Placement:
    kind: str
    direction: Optional[str] = None
    target: Optional[Cell] = None


def core_footprint(center: Cell, rows: int, cols: int) -> Tuple[Cell, ...]:
    r, c = center
    return tuple(
        (rr, cc)
        for rr in range(r - 1, r + 2)
        for cc in range(c - 1, c + 2)
        if 0 <= rr < rows and 0 <= cc < cols
    )


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


def parse_tile_row(buf: bytes) -> List[int]:
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


def parse_pos(buf: bytes) -> Cell:
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


def parse_core(buf: bytes) -> Tuple[int, int, Cell]:
    core_id = 0
    team = TEAM_A
    center = (0, 0)
    pos = 0
    while pos < len(buf):
        field_num, wire, pos = read_tag(buf, pos)
        if field_num == 1 and wire == 0:
            core_id, pos = read_varint(buf, pos)
        elif field_num == 2 and wire == 0:
            team, pos = read_varint(buf, pos)
        elif field_num == 3 and wire == 2:
            length, pos = read_varint(buf, pos)
            center = parse_pos(buf[pos : pos + length])
            pos += length
        else:
            pos = skip_field(buf, pos, wire)
    return core_id, team, center


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
            rows.append(parse_tile_row(buf[pos : pos + length]))
            pos += length
        elif field_num == 4 and wire == 2:
            length, pos = read_varint(buf, pos)
            cores.append(parse_core(buf[pos : pos + length]))
            pos += length
        else:
            pos = skip_field(buf, pos, wire)
    return width, height, rows, cores


def load_map26(path: str) -> MapData:
    with open(path, "rb") as f:
        data = f.read()

    width, height, rows, core_specs = parse_map_message(data)
    if width <= 0 or height <= 0:
        raise ValueError(f"{path} does not look like a Map message")
    if len(rows) != height or any(len(row) != width for row in rows):
        raise ValueError(f"{path} has inconsistent dimensions")

    cores: List[CoreInfo] = []
    for core_id, team, center in core_specs:
        cores.append(
            CoreInfo(
                id=core_id,
                team=team,
                center=center,
                footprint=core_footprint(center, height, width),
            )
        )

    return MapData(
        path=path,
        width=width,
        height=height,
        rows=rows,
        cores=cores,
    )


def auto_cell_size(rows: int, cols: int, max_w: int = 1280, max_h: int = 820) -> int:
    return max(8, min(28, max_w // max(cols, 1), max_h // max(rows, 1)))


class RoutePlannerApp:
    def __init__(self, root: tk.Tk, map_data: MapData, cell_size: int) -> None:
        self.root = root
        self.map_data = map_data
        self.cell_size = cell_size
        self.placements: Dict[Cell, Placement] = {}
        self.pending_bridge_source: Optional[Cell] = None
        self.core_cells = {cell for core in map_data.cores for cell in core.footprint}

        self.tool_var = tk.StringVar(value="conveyor_E")
        self.status_var = tk.StringVar(value="Choose a structure and click the map.")

        root.title(f"Route Planner - {os.path.basename(map_data.path)}")
        root.resizable(False, False)
        self.build_ui()
        self.redraw()

    def build_ui(self) -> None:
        top = tk.Frame(self.root)
        top.pack(fill="x", padx=8, pady=(8, 4))

        tools = [
            ("Harvester", "harvester"),
            ("Conv N", "conveyor_N"),
            ("Conv E", "conveyor_E"),
            ("Conv S", "conveyor_S"),
            ("Conv W", "conveyor_W"),
            ("Bridge", "bridge"),
            ("Foundry", "foundry"),
            ("Road", "road"),
            ("Erase", "erase"),
        ]
        for label, value in tools:
            tk.Radiobutton(
                top,
                text=label,
                value=value,
                variable=self.tool_var,
                indicatoron=False,
                width=9,
                command=self.cancel_bridge_if_needed,
            ).pack(side="left", padx=(0, 4))

        tk.Button(top, text="Clear", command=self.clear_plan).pack(side="left", padx=(8, 0))
        tk.Button(top, text="Save JSON", command=self.save_json).pack(side="left", padx=(4, 0))

        legend = tk.Frame(self.root)
        legend.pack(fill="x", padx=8, pady=(0, 4))
        tk.Label(
            legend,
            anchor="w",
            text="Black=wall  Brown=core  Blue=Ti ore  Orange=Ax ore  H=harvester  C=conveyor  B=bridge  F=foundry  R=road",
        ).pack(side="left")

        self.canvas = tk.Canvas(
            self.root,
            width=self.map_data.width * self.cell_size,
            height=self.map_data.height * self.cell_size,
            bg="#ffffff",
            highlightthickness=0,
        )
        self.canvas.pack(padx=8, pady=(0, 4))
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<Button-3>", self.on_right_click)

        tk.Label(self.root, textvariable=self.status_var, anchor="w").pack(
            fill="x", padx=8, pady=(0, 8)
        )

    def cancel_bridge_if_needed(self) -> None:
        if self.tool_var.get() != "bridge":
            self.pending_bridge_source = None
            self.redraw()

    def cell_from_event(self, event: tk.Event) -> Optional[Cell]:
        c = event.x // self.cell_size
        r = event.y // self.cell_size
        if 0 <= r < self.map_data.height and 0 <= c < self.map_data.width:
            return (r, c)
        return None

    def on_right_click(self, event: tk.Event) -> None:
        cell = self.cell_from_event(event)
        if cell is not None:
            self.placements.pop(cell, None)
            self.pending_bridge_source = None
            self.status_var.set(f"Erased {self.format_cell(cell)}.")
            self.redraw()

    def on_click(self, event: tk.Event) -> None:
        cell = self.cell_from_event(event)
        if cell is None:
            return

        tool = self.tool_var.get()
        if tool == "erase":
            self.placements.pop(cell, None)
            self.pending_bridge_source = None
            self.status_var.set(f"Erased {self.format_cell(cell)}.")
            self.redraw()
            return

        if tool == "bridge":
            self.place_bridge_click(cell)
            return

        placement = self.placement_for_tool(tool)
        if placement is None:
            return
        if not self.can_place(cell, placement):
            return

        self.placements[cell] = placement
        self.pending_bridge_source = None
        self.status_var.set(f"Placed {placement.kind} at {self.format_cell(cell)}.")
        self.redraw()

    def placement_for_tool(self, tool: str) -> Optional[Placement]:
        if tool.startswith("conveyor_"):
            return Placement("conveyor", direction=tool.rsplit("_", 1)[1])
        if tool in {"harvester", "foundry", "road"}:
            return Placement(tool)
        return None

    def place_bridge_click(self, cell: Cell) -> None:
        if self.pending_bridge_source is None:
            source = Placement("bridge")
            if not self.can_place(cell, source):
                return
            self.pending_bridge_source = cell
            self.status_var.set(f"Bridge source {self.format_cell(cell)}; click target within range 3.")
            self.redraw()
            return

        source_cell = self.pending_bridge_source
        if source_cell == cell:
            self.pending_bridge_source = None
            self.status_var.set("Cancelled bridge placement.")
            self.redraw()
            return

        if not self.bridge_target_in_range(source_cell, cell):
            self.status_var.set("Bridge target must be within distance squared <= 9.")
            return

        self.placements[source_cell] = Placement("bridge", target=cell)
        self.pending_bridge_source = None
        self.status_var.set(
            f"Placed bridge {self.format_cell(source_cell)} -> {self.format_cell(cell)}."
        )
        self.redraw()

    def can_place(self, cell: Cell, placement: Placement) -> bool:
        r, c = cell
        env = self.map_data.rows[r][c]
        if env == ENV_WALL:
            self.status_var.set("Cannot place on a wall.")
            return False
        if cell in self.core_cells:
            self.status_var.set("Cannot place on a core footprint.")
            return False
        if placement.kind == "harvester" and env not in {ENV_ORE_TITANIUM, ENV_ORE_AXIONITE}:
            self.status_var.set("Harvesters must be placed on titanium or axionite ore.")
            return False
        return True

    def bridge_target_in_range(self, source: Cell, target: Cell) -> bool:
        sr, sc = source
        tr, tc = target
        return (sr - tr) * (sr - tr) + (sc - tc) * (sc - tc) <= 9

    def clear_plan(self) -> None:
        self.placements.clear()
        self.pending_bridge_source = None
        self.status_var.set("Cleared plan.")
        self.redraw()

    def save_json(self) -> None:
        out_path = os.path.join(os.getcwd(), "route_plan_pong.json")
        entries = []
        for (r, c), placement in sorted(self.placements.items()):
            item = {"kind": placement.kind, "x": c, "y": r}
            if placement.direction:
                item["direction"] = placement.direction
            if placement.target:
                tr, tc = placement.target
                item["target"] = {"x": tc, "y": tr}
            entries.append(item)

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"map": self.map_data.path, "placements": entries}, f, indent=2)
            f.write("\n")

        self.status_var.set(f"Saved {len(entries)} placements to {out_path}.")

    def redraw(self) -> None:
        self.canvas.delete("all")
        for r in range(self.map_data.height):
            for c in range(self.map_data.width):
                self.draw_base_cell((r, c))

        for cell, placement in sorted(self.placements.items()):
            self.draw_placement(cell, placement)

        if self.pending_bridge_source is not None:
            self.draw_pending_bridge(self.pending_bridge_source)

    def draw_base_cell(self, cell: Cell) -> None:
        r, c = cell
        x0, y0, x1, y1 = self.cell_box(cell)
        env = self.map_data.rows[r][c]
        fill = "#ffffff"
        if env == ENV_WALL:
            fill = "#202124"
        elif env == ENV_ORE_TITANIUM:
            fill = "#b8e6ff"
        elif env == ENV_ORE_AXIONITE:
            fill = "#ffc36b"
        if cell in self.core_cells:
            fill = "#8a5a35"

        self.canvas.create_rectangle(x0, y0, x1, y1, fill=fill, outline="#d5d7dc")

    def draw_placement(self, cell: Cell, placement: Placement) -> None:
        x0, y0, x1, y1 = self.cell_box(cell, pad=3)
        cx, cy = self.cell_center(cell)

        if placement.kind == "road":
            self.canvas.create_rectangle(x0, y0, x1, y1, fill="#aeb4bd", outline="#70757d")
            self.draw_label(cx, cy, "R", "#202124")
        elif placement.kind == "foundry":
            self.canvas.create_rectangle(x0, y0, x1, y1, fill="#8f63d8", outline="#4d2f88")
            self.draw_label(cx, cy, "F", "#ffffff")
        elif placement.kind == "harvester":
            self.canvas.create_rectangle(x0, y0, x1, y1, fill="#30a46c", outline="#17633f")
            self.draw_label(cx, cy, "H", "#ffffff")
        elif placement.kind == "conveyor":
            self.canvas.create_rectangle(x0, y0, x1, y1, fill="#2bb3bd", outline="#116269")
            self.draw_conveyor_arrow(cell, placement.direction or "E")
        elif placement.kind == "bridge":
            self.canvas.create_rectangle(x0, y0, x1, y1, fill="#4068d4", outline="#193b91")
            self.draw_label(cx, cy, "B", "#ffffff")
            if placement.target is not None:
                tx, ty = self.cell_center(placement.target)
                self.canvas.create_line(cx, cy, tx, ty, fill="#193b91", width=2)
                self.canvas.create_oval(tx - 4, ty - 4, tx + 4, ty + 4, outline="#193b91", width=2)

    def draw_pending_bridge(self, cell: Cell) -> None:
        x0, y0, x1, y1 = self.cell_box(cell, pad=2)
        self.canvas.create_rectangle(x0, y0, x1, y1, outline="#193b91", width=3)

    def draw_conveyor_arrow(self, cell: Cell, direction: str) -> None:
        cx, cy = self.cell_center(cell)
        dx, dy = DIRECTIONS[direction]
        length = max(5, self.cell_size // 3)
        self.canvas.create_line(
            cx - dx * length,
            cy - dy * length,
            cx + dx * length,
            cy + dy * length,
            fill="#ffffff",
            width=2,
            arrow=tk.LAST,
        )
        self.draw_label(cx, cy + self.cell_size * 0.22, direction, "#ffffff", size_delta=-4)

    def draw_label(
        self,
        x: float,
        y: float,
        text: str,
        fill: str,
        size_delta: int = 0,
    ) -> None:
        size = max(7, self.cell_size // 2 + size_delta)
        self.canvas.create_text(x, y, text=text, fill=fill, font=("TkDefaultFont", size, "bold"))

    def cell_box(self, cell: Cell, pad: int = 0) -> Tuple[int, int, int, int]:
        r, c = cell
        x0 = c * self.cell_size + pad
        y0 = r * self.cell_size + pad
        x1 = (c + 1) * self.cell_size - pad
        y1 = (r + 1) * self.cell_size - pad
        return x0, y0, x1, y1

    def cell_center(self, cell: Cell) -> Tuple[float, float]:
        r, c = cell
        return (c + 0.5) * self.cell_size, (r + 0.5) * self.cell_size

    def format_cell(self, cell: Cell) -> str:
        r, c = cell
        return f"({c}, {r})"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manual route planner for pong.map26.")
    parser.add_argument("--map", default="maps/pong.map26", help="Map file to load.")
    parser.add_argument("--check", action="store_true", help="Load the map and print a summary.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    map_data = load_map26(args.map)
    if args.check:
        print(
            f"Loaded {args.map}: {map_data.width}x{map_data.height}, "
            f"{len(map_data.cores)} cores"
        )
        return

    root = tk.Tk()
    app = RoutePlannerApp(root, map_data, auto_cell_size(map_data.height, map_data.width))
    root.mainloop()


if __name__ == "__main__":
    main()
