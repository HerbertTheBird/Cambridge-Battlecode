from __future__ import annotations

from typing import Optional, Iterable, Set

from cambc import Controller, Direction, Position


# 8-way movement order, excluding CENTRE
DIRECTIONS = [d for d in Direction if d != Direction.CENTRE]


def _pos_key(pos: Position) -> tuple[int, int]:
    return (pos.x, pos.y)


def _dist_sq(a: Position, b: Position) -> int:
    dx = a.x - b.x
    dy = a.y - b.y
    return dx * dx + dy * dy


def _direction_order_toward(src: Position, dst: Position) -> list[Direction]:
    """Return directions ordered roughly by how well they move src toward dst."""
    dx = dst.x - src.x
    dy = dst.y - src.y

    horiz = None
    vert = None

    if dx > 0:
        horiz = Direction.EAST
    elif dx < 0:
        horiz = Direction.WEST

    if dy > 0:
        vert = Direction.SOUTH
    elif dy < 0:
        vert = Direction.NORTH

    preferred: list[Direction] = []

    # Prefer diagonal first when both components exist.
    if horiz is not None and vert is not None:
        diag_map = {
            (Direction.NORTH, Direction.EAST): Direction.NORTHEAST,
            (Direction.NORTH, Direction.WEST): Direction.NORTHWEST,
            (Direction.SOUTH, Direction.EAST): Direction.SOUTHEAST,
            (Direction.SOUTH, Direction.WEST): Direction.SOUTHWEST,
        }
        preferred.append(diag_map[(vert, horiz)])

    # Then the dominant cardinal axis, then the other one.
    if abs(dx) >= abs(dy):
        if horiz is not None:
            preferred.append(horiz)
        if vert is not None:
            preferred.append(vert)
    else:
        if vert is not None:
            preferred.append(vert)
        if horiz is not None:
            preferred.append(horiz)

    # Then all remaining directions as fallback.
    for d in DIRECTIONS:
        if d not in preferred:
            preferred.append(d)

    return preferred


def _rotate_dirs(start: Direction, follow_left: bool) -> list[Direction]:
    """Return directions in bug-follow order around a blocking face."""
    idx = DIRECTIONS.index(start)
    out = []
    for step in range(len(DIRECTIONS)):
        if follow_left:
            out.append(DIRECTIONS[(idx + step) % len(DIRECTIONS)])
        else:
            out.append(DIRECTIONS[(idx - step) % len(DIRECTIONS)])
    return out


class BugNav:
    """
    Lightweight bug-navigation helper for the current cambc API.

    Features:
    - Greedy move toward target when possible
    - Falls back to wall-following when blocked
    - Supports an avoid set of Positions it will never step onto
    - No digging / turning / legacy mechanics

    Important:
    - This only uses ct.can_move(...) / ct.move(...)
    - It does NOT build conveyors/roads automatically
    """

    def __init__(self) -> None:
        self.target: Optional[Position] = None
        self.prev_target: Optional[Position] = None

        self.follow_left: bool = False
        self.follow_right: bool = False
        self.blocked_anchor_dist: int = 10**9
        self.last_move_dir: Optional[Direction] = None

    def reset(self) -> None:
        self.follow_left = False
        self.follow_right = False
        self.blocked_anchor_dist = 10**9
        self.last_move_dir = None

    def set_target(self, target: Optional[Position]) -> None:
        self.target = target
        if (
            self.prev_target is None
            or target is None
            or _dist_sq(self.prev_target, target) > 2
        ):
            self.reset()
        self.prev_target = target

    def _normalize_avoid(
        self, avoid: Optional[Iterable[Position]]
    ) -> Set[tuple[int, int]]:
        if avoid is None:
            return set()
        return {_pos_key(p) for p in avoid}

    def _can_step(
        self,
        ct: Controller,
        direction: Direction,
        avoid: Set[tuple[int, int]],
    ) -> bool:
        nxt = ct.get_position().add(direction)
        if _pos_key(nxt) in avoid:
            return False
        return ct.can_move(direction)

    def _try_greedy(
        self,
        ct: Controller,
        target: Position,
        avoid: Set[tuple[int, int]],
    ) -> bool:
        here = ct.get_position()
        current_dist = _dist_sq(here, target)

        best_dir: Optional[Direction] = None
        best_dist = current_dist

        for d in _direction_order_toward(here, target):
            if not self._can_step(ct, d, avoid):
                continue
            nxt = here.add(d)
            d2 = _dist_sq(nxt, target)
            if d2 < best_dist:
                best_dist = d2
                best_dir = d

        if best_dir is None:
            return False

        ct.move(best_dir)
        self.last_move_dir = best_dir
        self.follow_left = False
        self.follow_right = False
        self.blocked_anchor_dist = best_dist
        return True

    def _choose_follow_side(
        self,
        ct: Controller,
        target: Position,
        avoid: Set[tuple[int, int]],
    ) -> None:
        here = ct.get_position()

        toward_dirs = _direction_order_toward(here, target)
        start = toward_dirs[0]

        left_candidates = _rotate_dirs(start, follow_left=True)
        right_candidates = _rotate_dirs(start, follow_left=False)

        left_best = 10**9
        right_best = 10**9

        for d in left_candidates:
            if self._can_step(ct, d, avoid):
                left_best = _dist_sq(here.add(d), target)
                break

        for d in right_candidates:
            if self._can_step(ct, d, avoid):
                right_best = _dist_sq(here.add(d), target)
                break

        if left_best <= right_best:
            self.follow_left = True
            self.follow_right = False
        else:
            self.follow_left = False
            self.follow_right = True

        self.blocked_anchor_dist = _dist_sq(here, target)

    def _try_bug_follow(
        self,
        ct: Controller,
        target: Position,
        avoid: Set[tuple[int, int]],
    ) -> bool:
        here = ct.get_position()
        current_dist = _dist_sq(here, target)

        if not self.follow_left and not self.follow_right:
            self._choose_follow_side(ct, target, avoid)

        toward_dirs = _direction_order_toward(here, target)
        start = toward_dirs[0]

        scan = _rotate_dirs(start, follow_left=self.follow_left)

        for d in scan:
            if not self._can_step(ct, d, avoid):
                continue

            nxt = here.add(d)
            nxt_dist = _dist_sq(nxt, target)

            # Leave wall-follow mode once we can genuinely make progress again.
            if nxt_dist < self.blocked_anchor_dist:
                self.follow_left = False
                self.follow_right = False

            ct.move(d)
            self.last_move_dir = d
            return True

        return False

    def move_toward(
        self,
        ct: Controller,
        target: Optional[Position] = None,
        avoid: Optional[Iterable[Position]] = None,
    ) -> bool:
        """
        Move one step toward target.

        Returns True if moved, False otherwise.
        """
        if target is not None:
            self.set_target(target)

        if self.target is None:
            return False

        here = ct.get_position()
        if _pos_key(here) == _pos_key(self.target):
            return False

        avoid_set = self._normalize_avoid(avoid)

        if _pos_key(self.target) in avoid_set:
            return False

        # if self._try_greedy(ct, self.target, avoid_set):
        #     return True

        return self._try_bug_follow(ct, self.target, avoid_set)