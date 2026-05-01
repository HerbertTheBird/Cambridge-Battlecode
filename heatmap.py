#!/usr/bin/env python3
"""
Heatmap Renderer — visualize where each unit type spent time over a replay.

For each turn, accumulates +1 in a 2D grid at every entity's current position
(per category). Outputs ASCII heatmaps to stdout (always) and optionally a PNG
or CSV per category if --png/--csv is given.

Categories rendered: BUILDER_BOT, GUNNER, SENTINEL, BREACH, LAUNCHER,
HARVESTER, FOUNDRY, CONVEYOR, BARRIER. Walls and ore are also overlaid in the
ASCII view.

Usage:
    python heatmap.py replay.replay26 --team A
    python heatmap.py replay.replay26 --team A --png heatmaps/ --category BUILDER_BOT
    python heatmap.py replay.replay26 --team A --turns 1-500
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "bots" / "_debug_wrapper"))
from replay_parser import (
    parse_replay, GameReplay, PlaceEntity, RemoveEntity, MoveBuilderBot, Pos,
)


CATEGORIES = (
    "BUILDER_BOT", "GUNNER", "SENTINEL", "BREACH", "LAUNCHER",
    "HARVESTER", "FOUNDRY", "CONVEYOR", "BARRIER",
)

ENV_WALL = 1
ENV_ORE_TI = 2
ENV_ORE_AX = 3


def build_heatmaps(replay: GameReplay, team: int, turn_lo: int, turn_hi: int) -> dict[str, list[list[int]]]:
    """For each category, build a width-x-height grid of accumulated occupancy counts."""
    w, h = replay.map.width, replay.map.height
    grids: dict[str, list[list[int]]] = {
        c: [[0] * w for _ in range(h)] for c in CATEGORIES
    }
    ents: dict[int, tuple[int, str, Pos, bool]] = {}   # id -> (team, etype, pos, alive)

    for i, turn in enumerate(replay.turns):
        turn_num = i + 1
        for upd in turn.updates:
            if isinstance(upd, PlaceEntity):
                e = upd.entity
                if e.entity_type == "MARKER":
                    continue
                ents[e.id] = (e.team, e.entity_type, e.pos, True)
            elif isinstance(upd, MoveBuilderBot):
                rec = ents.get(upd.id)
                if rec:
                    ents[upd.id] = (rec[0], rec[1], upd.to, rec[3])
            elif isinstance(upd, RemoveEntity):
                rec = ents.get(upd.id)
                if rec:
                    ents[upd.id] = (rec[0], rec[1], rec[2], False)

        if turn_lo <= turn_num <= turn_hi:
            for et, etype, pos, alive in ents.values():
                if not alive or et != team or etype not in grids:
                    continue
                if 0 <= pos.x < w and 0 <= pos.y < h:
                    grids[etype][pos.y][pos.x] += 1

    return grids


# ── ASCII rendering ──────────────────────────────────────────────────────────

ASCII_RAMP = " .:-=+*#%@"


def render_ascii(grid: list[list[int]], terrain: list[list[int]], cores: list, w: int, h: int, title: str) -> str:
    flat = [v for row in grid for v in row]
    mx = max(flat) if flat else 0
    lines = [f"  {title} (max cell={mx})"]
    col_label = "  " + "".join(str(x % 10) for x in range(w))
    lines.append(col_label)
    lines.append("  " + "+" + "-" * w + "+")
    for y in range(h):
        row = []
        for x in range(w):
            t = terrain[y][x] if y < len(terrain) and x < len(terrain[y]) else 0
            v = grid[y][x]
            # Cores override
            on_core = any(abs(x - c.pos.x) <= 1 and abs(y - c.pos.y) <= 1 for c in cores)
            if t == ENV_WALL:
                ch = "#"
            elif on_core:
                ch = "C"
            elif v == 0:
                if t == ENV_ORE_TI:
                    ch = "t"
                elif t == ENV_ORE_AX:
                    ch = "a"
                else:
                    ch = "."
            else:
                # Logarithmic scale into ASCII_RAMP[1:]
                idx = 1 + min(len(ASCII_RAMP) - 2, int((len(ASCII_RAMP) - 2) * v / mx)) if mx > 0 else 1
                ch = ASCII_RAMP[idx]
            row.append(ch)
        lines.append(f"{y:2d}|" + "".join(row) + "|")
    lines.append("  " + "+" + "-" * w + "+")
    lines.append("  legend: # wall, C core, t/a ore (ti/ax), . empty unvisited, " + "/".join(ASCII_RAMP[1:]) + " low->high occupancy")
    return "\n".join(lines)


# ── PNG rendering (optional, requires PIL/Pillow) ──────────────────────────────

def write_png(grid: list[list[int]], terrain: list[list[int]], cores: list, w: int, h: int, out_path: Path) -> None:
    try:
        from PIL import Image
    except ImportError:
        print(f"  Skipping PNG (Pillow not installed): {out_path}", file=sys.stderr)
        return

    # Render each cell as one pixel; scale up by 8x for visibility.
    scale = 8
    img = Image.new("RGB", (w, h), (255, 255, 255))
    pixels = img.load()
    flat = [v for row in grid for v in row]
    mx = max(flat) if flat else 0

    for y in range(h):
        for x in range(w):
            t = terrain[y][x] if y < len(terrain) and x < len(terrain[y]) else 0
            v = grid[y][x]
            on_core = any(abs(x - c.pos.x) <= 1 and abs(y - c.pos.y) <= 1 for c in cores)
            if t == ENV_WALL:
                pixels[x, y] = (50, 50, 50)
            elif on_core:
                pixels[x, y] = (100, 200, 255)
            elif v == 0:
                if t == ENV_ORE_TI:
                    pixels[x, y] = (200, 200, 100)
                elif t == ENV_ORE_AX:
                    pixels[x, y] = (180, 100, 200)
                else:
                    pixels[x, y] = (240, 240, 240)
            else:
                # Heat ramp: white -> yellow -> red
                norm = v / mx if mx else 0
                r = 255
                g = int(255 * (1 - norm))
                b = int(50 * (1 - norm))
                pixels[x, y] = (r, g, b)

    img = img.resize((w * scale, h * scale), Image.NEAREST)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)


def write_csv(grid: list[list[int]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        for row in grid:
            f.write(",".join(str(v) for v in row) + "\n")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Render unit-occupancy heatmaps from a replay.")
    parser.add_argument("replay", type=Path)
    parser.add_argument("--team", choices=["A", "B"], default="A")
    parser.add_argument("--turns", default=None, help="Turn range, e.g. '1-500' (default: all)")
    parser.add_argument("--category", choices=list(CATEGORIES) + ["ALL"], default="ALL",
                        help="Render only this category (default ALL).")
    parser.add_argument("--png", type=Path, default=None,
                        help="Output dir for per-category PNG heatmaps (requires Pillow).")
    parser.add_argument("--csv", type=Path, default=None,
                        help="Output dir for per-category CSV grids.")
    parser.add_argument("--no-ascii", action="store_true", help="Suppress ASCII output.")
    args = parser.parse_args()

    if not args.replay.exists():
        print(f"Replay not found: {args.replay}", file=sys.stderr)
        return 1

    replay = parse_replay(str(args.replay))
    n = len(replay.turns)
    if args.turns:
        if "-" in args.turns:
            lo_s, hi_s = args.turns.split("-", 1)
            turn_lo, turn_hi = int(lo_s), int(hi_s)
        else:
            turn_lo = turn_hi = int(args.turns)
    else:
        turn_lo, turn_hi = 1, n

    team = 0 if args.team == "A" else 1
    print(f"Building heatmaps for team {args.team} from turns {turn_lo}..{turn_hi} ({n} total)...")
    grids = build_heatmaps(replay, team=team, turn_lo=turn_lo, turn_hi=turn_hi)

    cats_to_render = [args.category] if args.category != "ALL" else list(CATEGORIES)

    for cat in cats_to_render:
        grid = grids[cat]
        total = sum(v for row in grid for v in row)
        if total == 0:
            if not args.no_ascii:
                print(f"\n  {cat}: no occupancy")
            continue
        if not args.no_ascii:
            print()
            print(render_ascii(grid, replay.map.terrain, replay.map.cores,
                               replay.map.width, replay.map.height, f"{cat} (total={total})"))
        if args.png:
            write_png(grid, replay.map.terrain, replay.map.cores,
                      replay.map.width, replay.map.height, args.png / f"{cat.lower()}_team{args.team}.png")
        if args.csv:
            write_csv(grid, args.csv / f"{cat.lower()}_team{args.team}.csv")

    if args.png:
        print(f"\n  PNGs written to {args.png}/")
    if args.csv:
        print(f"  CSVs written to {args.csv}/")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
