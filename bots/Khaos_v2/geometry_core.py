from __future__ import annotations

import math
from collections import deque
from typing import Dict, List, Sequence, Set, Tuple

Cell = Tuple[int, int]
Point = Tuple[float, float]
Segment = Tuple[Point, Point]
Mask = List[List[bool]]


def raster_scale_from_spacing(spacing: float) -> int:
    spacing = max(0.1, spacing)
    return max(2, min(12, int(round(4.0 / spacing))))


def rectangle_polygon(cols: int, rows: int) -> List[Point]:
    return [
        (0.0, 0.0),
        (float(cols), 0.0),
        (float(cols), float(rows)),
        (0.0, float(rows)),
    ]


def clip_polygon_against_half_plane(
    polygon: Sequence[Point],
    mid: Point,
    normal: Point,
    keep_negative_side: bool,
    eps: float = 1e-9,
) -> List[Point]:
    if not polygon:
        return []

    mx, my = mid
    nx, ny = normal

    def side_value(pt: Point) -> float:
        raw = (pt[0] - mx) * nx + (pt[1] - my) * ny
        return -raw if keep_negative_side else raw

    def intersect(a: Point, b: Point, va: float, vb: float) -> Point:
        denom = va - vb
        if abs(denom) <= eps:
            return b
        t = va / denom
        return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))

    result: List[Point] = []
    prev = polygon[-1]
    prev_value = side_value(prev)
    prev_inside = prev_value >= -eps

    for cur in polygon:
        cur_value = side_value(cur)
        cur_inside = cur_value >= -eps

        if cur_inside:
            if not prev_inside:
                result.append(intersect(prev, cur, prev_value, cur_value))
            result.append(cur)
        elif prev_inside:
            result.append(intersect(prev, cur, prev_value, cur_value))

        prev = cur
        prev_value = cur_value
        prev_inside = cur_inside

    cleaned: List[Point] = []
    for point in result:
        if not cleaned or math.hypot(point[0] - cleaned[-1][0], point[1] - cleaned[-1][1]) > eps:
            cleaned.append(point)

    if len(cleaned) > 1 and math.hypot(cleaned[0][0] - cleaned[-1][0], cleaned[0][1] - cleaned[-1][1]) <= eps:
        cleaned.pop()

    return cleaned


def _point_on_segment(pt: Point, a: Point, b: Point, eps: float = 1e-9) -> bool:
    px, py = pt
    ax, ay = a
    bx, by = b

    cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    if abs(cross) > eps:
        return False

    dot = (px - ax) * (px - bx) + (py - ay) * (py - by)
    return dot <= eps


def point_in_polygon(pt: Point, polygon: Sequence[Point], eps: float = 1e-9) -> bool:
    n = len(polygon)
    if n < 3:
        return False

    inside = False
    px, py = pt
    # Track previous vertex via locals to avoid the polygon[j] tuple unpack
    # each iteration.
    xj, yj = polygon[-1]

    for i in range(n):
        xi, yi = polygon[i]

        # _point_on_segment inlined: collinear cross + on-segment dot test.
        cross = (xj - xi) * (py - yi) - (yj - yi) * (px - xi)
        if -eps <= cross <= eps:
            dot = (px - xi) * (px - xj) + (py - yi) * (py - yj)
            if dot <= eps:
                return True

        if (yi > py) != (yj > py):
            denom = yj - yi
            if denom > eps or denom < -eps:
                x_cross = xi + (py - yi) * (xj - xi) / denom
                if x_cross >= px - eps:
                    inside = not inside

        xj, yj = xi, yi

    return inside


def _mark_horizontal_boundary(row: List[bool], scale: int, x1: float, x2: float, eps: float) -> None:
    width = len(row)
    if width == 0:
        return

    start_x, end_x = sorted((x1, x2))
    px_start = max(0, int(math.ceil(start_x * scale - 0.5 - eps * scale)))
    px_end = min(width - 1, int(math.floor(end_x * scale - 0.5 + eps * scale)))
    if px_start <= px_end:
        row[px_start:px_end + 1] = [True] * (px_end - px_start + 1)


def _mark_point_boundary(row: List[bool], scale: int, x: float, eps: float) -> None:
    px = int(round(x * scale - 0.5))
    if 0 <= px < len(row):
        center_x = (px + 0.5) / scale
        if abs(center_x - x) <= eps:
            row[px] = True


def _classify_polygon_edges(polygon: Sequence[Point], eps: float = 1e-9):
    """Split polygon edges into horizontal (dy ~ 0) and sloped lists.

    horizontal: list of (prev_x, prev_y, cur_x)
    sloped:     list of (prev_x, prev_y, dx, dy, y_min_minus_eps, y_max_plus_eps)
    Pre-computing these once avoids redoing dy / dx / min / max per scanline.
    """
    horizontal = []
    sloped = []
    prev_x, prev_y = polygon[-1]
    for cur_x, cur_y in polygon:
        dy = cur_y - prev_y
        if -eps <= dy <= eps:
            horizontal.append((prev_x, prev_y, cur_x))
        else:
            if prev_y < cur_y:
                y_lo, y_hi = prev_y, cur_y
            else:
                y_lo, y_hi = cur_y, prev_y
            sloped.append((prev_x, prev_y, cur_x - prev_x, dy, y_lo - eps, y_hi + eps))
        prev_x, prev_y = cur_x, cur_y
    return horizontal, sloped


def _fill_scanline_from_classified(row: List[bool], y: float, scale: int,
                                   horizontal, sloped, eps: float = 1e-9) -> None:
    # Horizontal edges only matter when y sits exactly on them.
    for prev_x, prev_y, cur_x in horizontal:
        if -eps <= y - prev_y <= eps:
            _mark_horizontal_boundary(row, scale, prev_x, cur_x, eps)

    crossings: List[float] = []
    crossings_append = crossings.append
    cy = y  # local-bind for the tight loop
    for prev_x, prev_y, dx, dy, y_lo, y_hi in sloped:
        if (prev_y > cy) != ((prev_y + dy) > cy):
            crossings_append(prev_x + (cy - prev_y) * dx / dy)
        if y_lo <= cy <= y_hi:
            _mark_point_boundary(row, scale, prev_x + (cy - prev_y) * dx / dy, eps)

    crossings.sort()
    inside = False
    cross_index = 0
    cross_count = len(crossings)
    inv_scale = 1.0 / scale
    half_inv = 0.5 * inv_scale
    width = len(row)

    for px in range(width):
        if row[px]:
            continue

        x = px * inv_scale + half_inv
        while cross_index < cross_count and crossings[cross_index] < x - eps:
            inside = not inside
            cross_index += 1
        row[px] = inside


def _fill_scanline_from_polygon(row: List[bool], y: float, scale: int, polygon: Sequence[Point], eps: float = 1e-9) -> None:
    """Compatibility wrapper — call sites that pre-classify should use the
    `_fill_scanline_from_classified` variant directly."""
    horizontal, sloped = _classify_polygon_edges(polygon, eps)
    _fill_scanline_from_classified(row, y, scale, horizontal, sloped, eps)


def make_mask(height: int, width: int, fill: bool = False) -> Mask:
    return [[fill] * width for _ in range(height)]


def copy_mask(mask: Mask) -> Mask:
    return [row[:] for row in mask]


def is_axis_aligned_rectangle(polygon: Sequence[Point], eps: float = 1e-9) -> bool:
    if len(polygon) != 4:
        return False

    xs = sorted({round(point[0], 9) for point in polygon})
    ys = sorted({round(point[1], 9) for point in polygon})
    if len(xs) != 2 or len(ys) != 2:
        return False

    expected = {
        (xs[0], ys[0]),
        (xs[1], ys[0]),
        (xs[1], ys[1]),
        (xs[0], ys[1]),
    }
    actual = {(round(point[0], 9), round(point[1], 9)) for point in polygon}
    return expected == actual and abs(xs[0] - xs[1]) > eps and abs(ys[0] - ys[1]) > eps


def build_analysis_mask(rows: int, cols: int, scale: int, polygon: Sequence[Point]) -> Mask:
    height = rows * scale
    width = cols * scale
    mask = make_mask(height, width, False)

    if is_axis_aligned_rectangle(polygon):
        xs = [point[0] for point in polygon]
        ys = [point[1] for point in polygon]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        px_start = max(0, int(math.ceil(min_x * scale - 0.5)))
        px_end = min(width - 1, int(math.floor(max_x * scale - 0.5)))
        py_start = max(0, int(math.ceil(min_y * scale - 0.5)))
        py_end = min(height - 1, int(math.floor(max_y * scale - 0.5)))

        if px_start <= px_end and py_start <= py_end:
            fill_count = px_end - px_start + 1
            fill_row = [True] * fill_count
            for py in range(py_start, py_end + 1):
                mask[py][px_start:px_end + 1] = fill_row
        return mask

    # Pre-classify polygon edges once; each scanline reuses these tuples.
    horizontal, sloped = _classify_polygon_edges(polygon)
    inv_scale = 1.0 / scale
    half_inv = 0.5 * inv_scale
    for py in range(height):
        y = py * inv_scale + half_inv
        _fill_scanline_from_classified(mask[py], y, scale, horizontal, sloped)

    return mask


def build_obstacle_mask(
    obstacles: Set[Cell],
    rows: int,
    cols: int,
    scale: int,
    analysis_mask: Mask,
) -> Mask:
    height = rows * scale
    width = cols * scale
    mask = make_mask(height, width, False)

    for r, c in obstacles:
        if not (0 <= r < rows and 0 <= c < cols):
            continue
        for py in range(r * scale, (r + 1) * scale):
            row = mask[py]
            analysis_row = analysis_mask[py]
            for px in range(c * scale, (c + 1) * scale):
                if 0 <= px < width and analysis_row[px]:
                    row[px] = True

    return mask


def apply_diagonal_notches(
    obstacle_mask: Mask,
    obstacles: Set[Cell],
    analysis_mask: Mask,
    scale: int,
    diagonal_gap: float,
) -> None:
    if diagonal_gap <= 0:
        return

    height = len(obstacle_mask)
    width = len(obstacle_mask[0]) if height else 0

    for r, c in obstacles:
        for dr, dc in ((1, -1), (1, 1)):
            nr, nc = r + dr, c + dc
            if (nr, nc) not in obstacles:
                continue
            if (r + dr, c) in obstacles or (r, c + dc) in obstacles:
                continue

            cx = c + (1 if dc > 0 else 0)
            cy = r + 1
            low_x = cx - diagonal_gap
            high_x = cx + diagonal_gap
            low_y = cy - diagonal_gap
            high_y = cy + diagonal_gap

            px_start = max(0, int(math.ceil(low_x * scale - 0.5)))
            px_end = min(width - 1, int(math.floor(high_x * scale - 0.5)))
            py_start = max(0, int(math.ceil(low_y * scale - 0.5)))
            py_end = min(height - 1, int(math.floor(high_y * scale - 0.5)))

            for py in range(py_start, py_end + 1):
                row = obstacle_mask[py]
                analysis_row = analysis_mask[py]
                for px in range(px_start, px_end + 1):
                    if analysis_row[px]:
                        row[px] = False


def split_obstacle_mask_by_area(obstacle_mask: Mask, min_area_pixels: int) -> Tuple[Mask, Mask]:
    height = len(obstacle_mask)
    width = len(obstacle_mask[0]) if height else 0
    visited = make_mask(height, width, False)
    kept = make_mask(height, width, False)
    discarded = make_mask(height, width, False)

    for sy in range(height):
        for sx in range(width):
            if visited[sy][sx] or not obstacle_mask[sy][sx]:
                continue

            component: List[Tuple[int, int]] = []
            queue = deque([(sy, sx)])
            visited[sy][sx] = True

            while queue:
                y, x = queue.popleft()
                component.append((y, x))
                for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                    if 0 <= ny < height and 0 <= nx < width:
                        if not visited[ny][nx] and obstacle_mask[ny][nx]:
                            visited[ny][nx] = True
                            queue.append((ny, nx))

            target = kept if len(component) >= min_area_pixels else discarded
            for y, x in component:
                target[y][x] = True

    return kept, discarded


def build_free_mask(analysis_mask: Mask, obstacle_mask: Mask) -> Mask:
    height = len(analysis_mask)
    width = len(analysis_mask[0]) if height else 0
    mask = make_mask(height, width, False)

    for y in range(height):
        out_row = mask[y]
        analysis_row = analysis_mask[y]
        obstacle_row = obstacle_mask[y]
        for x in range(width):
            out_row[x] = analysis_row[x] and not obstacle_row[x]

    return mask


def mask_to_cells(mask: Mask, scale: int) -> Set[Cell]:
    cells: Set[Cell] = set()
    for py, row in enumerate(mask):
        for px, value in enumerate(row):
            if value:
                cells.add((py // scale, px // scale))
    return cells


def cell_centers_covered_by_mask(mask: Mask, rows: int, cols: int, scale: int) -> Set[Cell]:
    covered: Set[Cell] = set()
    height = len(mask)
    width = len(mask[0]) if height else 0

    for r in range(rows):
        py = int(round((r + 0.5) * scale - 0.5))
        if not (0 <= py < height):
            continue
        row = mask[py]
        for c in range(cols):
            px = int(round((c + 0.5) * scale - 0.5))
            if 0 <= px < width and row[px]:
                covered.add((r, c))

    return covered


def boundary_segments_from_mask(mask: Mask, scale: int) -> List[Segment]:
    height = len(mask)
    width = len(mask[0]) if height else 0
    segments: List[Segment] = []
    if height == 0 or width == 0:
        return segments

    inv_scale = 1.0 / scale
    # Pre-compute the world-space coordinate of every grid line once. The inner
    # loops do many `run_start / scale` and `px / scale` divisions otherwise.
    x_coords = [round(i * inv_scale, 9) for i in range(width + 1)]
    y_coords = [round(i * inv_scale, 9) for i in range(height + 1)]

    seg_append = segments.append

    for py in range(height):
        y0 = y_coords[py]
        y1 = y_coords[py + 1]
        row = mask[py]
        row_above = mask[py - 1] if py > 0 else None
        row_below = mask[py + 1] if py + 1 < height else None

        run_start = None
        if row_above is None:
            for px in range(width):
                if row[px]:
                    if run_start is None:
                        run_start = px
                elif run_start is not None:
                    seg_append(((x_coords[run_start], y0), (x_coords[px], y0)))
                    run_start = None
        else:
            for px in range(width):
                if row[px] and not row_above[px]:
                    if run_start is None:
                        run_start = px
                elif run_start is not None:
                    seg_append(((x_coords[run_start], y0), (x_coords[px], y0)))
                    run_start = None
        if run_start is not None:
            seg_append(((x_coords[run_start], y0), (x_coords[width], y0)))

        run_start = None
        if row_below is None:
            for px in range(width):
                if row[px]:
                    if run_start is None:
                        run_start = px
                elif run_start is not None:
                    seg_append(((x_coords[run_start], y1), (x_coords[px], y1)))
                    run_start = None
        else:
            for px in range(width):
                if row[px] and not row_below[px]:
                    if run_start is None:
                        run_start = px
                elif run_start is not None:
                    seg_append(((x_coords[run_start], y1), (x_coords[px], y1)))
                    run_start = None
        if run_start is not None:
            seg_append(((x_coords[run_start], y1), (x_coords[width], y1)))

    for px in range(width):
        x0 = x_coords[px]
        x1 = x_coords[px + 1]

        run_start = None
        if px == 0:
            for py in range(height):
                if mask[py][px]:
                    if run_start is None:
                        run_start = py
                elif run_start is not None:
                    seg_append(((x0, y_coords[run_start]), (x0, y_coords[py])))
                    run_start = None
        else:
            px_minus_1 = px - 1
            for py in range(height):
                row = mask[py]
                if row[px] and not row[px_minus_1]:
                    if run_start is None:
                        run_start = py
                elif run_start is not None:
                    seg_append(((x0, y_coords[run_start]), (x0, y_coords[py])))
                    run_start = None
        if run_start is not None:
            seg_append(((x0, y_coords[run_start]), (x0, y_coords[height])))

        run_start = None
        if px == width - 1:
            for py in range(height):
                if mask[py][px]:
                    if run_start is None:
                        run_start = py
                elif run_start is not None:
                    seg_append(((x1, y_coords[run_start]), (x1, y_coords[py])))
                    run_start = None
        else:
            px_plus_1 = px + 1
            for py in range(height):
                row = mask[py]
                if row[px] and not row[px_plus_1]:
                    if run_start is None:
                        run_start = py
                elif run_start is not None:
                    seg_append(((x1, y_coords[run_start]), (x1, y_coords[py])))
                    run_start = None
        if run_start is not None:
            seg_append(((x1, y_coords[run_start]), (x1, y_coords[height])))

    return merge_axis_aligned_segments(segments)


def merge_axis_aligned_segments(segments: Sequence[Segment], eps: float = 1e-9) -> List[Segment]:
    horizontal: Dict[float, List[Tuple[float, float]]] = {}
    vertical: Dict[float, List[Tuple[float, float]]] = {}

    for start, end in segments:
        x1, y1 = start
        x2, y2 = end
        if abs(y1 - y2) <= eps:
            y = round((y1 + y2) / 2.0, 9)
            a, b = sorted((x1, x2))
            horizontal.setdefault(y, []).append((a, b))
        elif abs(x1 - x2) <= eps:
            x = round((x1 + x2) / 2.0, 9)
            a, b = sorted((y1, y2))
            vertical.setdefault(x, []).append((a, b))

    merged: List[Segment] = []

    for y, intervals in horizontal.items():
        intervals.sort()
        start, end = intervals[0]
        for cur_start, cur_end in intervals[1:]:
            if cur_start <= end + eps:
                end = max(end, cur_end)
            else:
                merged.append(((start, y), (end, y)))
                start, end = cur_start, cur_end
        merged.append(((start, y), (end, y)))

    for x, intervals in vertical.items():
        intervals.sort()
        start, end = intervals[0]
        for cur_start, cur_end in intervals[1:]:
            if cur_start <= end + eps:
                end = max(end, cur_end)
            else:
                merged.append(((x, start), (x, end)))
                start, end = cur_start, cur_end
        merged.append(((x, start), (x, end)))

    return merged


def point_to_segment_distance(pt: Point, segment: Segment) -> float:
    (x, y), ((x1, y1), (x2, y2)) = pt, segment

    if x1 == x2:
        clamped_y = min(max(y, min(y1, y2)), max(y1, y2))
        return math.hypot(x - x1, y - clamped_y)

    if y1 == y2:
        clamped_x = min(max(x, min(x1, x2)), max(x1, x2))
        return math.hypot(x - clamped_x, y - y1)

    dx = x2 - x1
    dy = y2 - y1
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return math.hypot(x - x1, y - y1)

    t = ((x - x1) * dx + (y - y1) * dy) / length_sq
    t = min(1.0, max(0.0, t))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return math.hypot(x - proj_x, y - proj_y)


def zhang_suen_thinning(mask: Mask) -> Mask:
    height = len(mask)
    width = len(mask[0]) if height else 0
    out = copy_mask(mask)

    if height < 3 or width < 3:
        return out

    changed = True
    while changed:
        changed = False

        for step in range(2):
            to_clear: List[Tuple[int, int]] = []

            for y in range(1, height - 1):
                for x in range(1, width - 1):
                    if not out[y][x]:
                        continue

                    p2 = 1 if out[y - 1][x] else 0
                    p3 = 1 if out[y - 1][x + 1] else 0
                    p4 = 1 if out[y][x + 1] else 0
                    p5 = 1 if out[y + 1][x + 1] else 0
                    p6 = 1 if out[y + 1][x] else 0
                    p7 = 1 if out[y + 1][x - 1] else 0
                    p8 = 1 if out[y][x - 1] else 0
                    p9 = 1 if out[y - 1][x - 1] else 0

                    neighbors = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9
                    if neighbors < 2 or neighbors > 6:
                        continue

                    seq = [p2, p3, p4, p5, p6, p7, p8, p9, p2]
                    transitions = sum(
                        1
                        for a, b in zip(seq, seq[1:])
                        if a == 0 and b == 1
                    )
                    if transitions != 1:
                        continue

                    if step == 0:
                        if p2 * p4 * p6 != 0 or p4 * p6 * p8 != 0:
                            continue
                    else:
                        if p2 * p4 * p8 != 0 or p2 * p6 * p8 != 0:
                            continue

                    to_clear.append((y, x))

            if to_clear:
                changed = True
                for y, x in to_clear:
                    out[y][x] = False

    return out


def mask_to_graph(mask: Mask, scale: int) -> Tuple[Dict[int, Point], Set[Tuple[int, int]]]:
    vertices: Dict[int, Point] = {}
    edges: Set[Tuple[int, int]] = set()
    pixel_to_vid: Dict[Tuple[int, int], int] = {}

    for py, row in enumerate(mask):
        for px, value in enumerate(row):
            if not value:
                continue
            vid = len(pixel_to_vid)
            pixel_to_vid[(py, px)] = vid
            vertices[vid] = ((px + 0.5) / scale, (py + 0.5) / scale)

    for (py, px), vid in pixel_to_vid.items():
        for dy, dx in ((0, 1), (1, -1), (1, 0), (1, 1)):
            other = pixel_to_vid.get((py + dy, px + dx))
            if other is None or other == vid:
                continue
            edge = (vid, other) if vid < other else (other, vid)
            edges.add(edge)

    return vertices, edges
