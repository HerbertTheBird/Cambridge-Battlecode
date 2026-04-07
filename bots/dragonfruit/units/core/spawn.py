from cambc import Direction, Position

from globals import DIRECTIONS

def dir_distance(a, b):
    ia = DIRECTIONS.index(a)
    ib = DIRECTIONS.index(b)
    diff = abs(ia - ib)
    return min(diff, 8 - diff)

def get_ray_endpoint(start: Position, direction: Direction, width: int, height: int) -> Position:
    dx, dy = direction.delta()
    x, y = start.x, start.y

    while True:
        nx, ny = x + dx, y + dy
        if nx < 0 or nx >= width or ny < 0 or ny >= height:
            return Position(x, y)
        x, y = nx, ny

def get_valid_directions(ct, core_pos, width, height):
    valid = []
    for d in DIRECTIONS:
        endpoint = get_ray_endpoint(core_pos, d, width, height)
        if not ct.is_in_vision(endpoint):
            valid.append((d, endpoint))
    return valid

def pick_three_directions(core_pos, width, height, valid_dirs):
    if len(valid_dirs) <= 3:
        return valid_dirs

    center = Position(width // 2, height // 2)
    half_w, half_h = width // 2, height // 2
    max_dist_sq = half_w * half_w + half_h * half_h

    best_triplet = (valid_dirs[0], valid_dirs[1], valid_dirs[2])
    best_score = -1

    for i in range(len(valid_dirs)):
        for j in range(i + 1, len(valid_dirs)):
            for k in range(j + 1, len(valid_dirs)):
                sep01 = dir_distance(valid_dirs[i][0], valid_dirs[j][0])
                sep02 = dir_distance(valid_dirs[i][0], valid_dirs[k][0])
                sep12 = dir_distance(valid_dirs[j][0], valid_dirs[k][0])

                # product of pairwise separations: rewards balanced spread
                # e.g. (3,3,2)->18 beats "T" shape (2,2,4)->16
                spread = sep01 * sep02 * sep12

                # center closeness: best of the 3 endpoints (0 to 1)
                best_closeness = max(
                    1.0 - valid_dirs[i][1].distance_squared(center) / max_dist_sq,
                    1.0 - valid_dirs[j][1].distance_squared(center) / max_dist_sq,
                    1.0 - valid_dirs[k][1].distance_squared(center) / max_dist_sq,
                )

                # spread ranges 0-64 (max 4*4*4), closeness 0-1
                score = spread * 10 + best_closeness * 30

                if score > best_score:
                    best_score = score
                    best_triplet = (valid_dirs[i], valid_dirs[j], valid_dirs[k])

    return list(best_triplet)

def prioritize_direction(directions: list[Direction], preferred_dir: Direction) -> list[Direction]:
    """Move preferred_dir to the front, adding it if needed."""
    ordered = [d for d in directions if d != preferred_dir]
    return [preferred_dir, *ordered][:3]
