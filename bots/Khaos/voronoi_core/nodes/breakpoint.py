import math
from decimal import Decimal

from voronoi_core.graph.coordinate import Coordinate
from voronoi_core.numeric import is_close, is_zero, sqrt, to_number


class Breakpoint:
    __slots__ = ("breakpoint", "edge")
    """
    A breakpoint between two arcs.
    """

    def __init__(self, breakpoint: tuple, edge=None):
        """
        The breakpoint is stored by an ordered tuple of sites (p_i, p_j) where p_i defines the parabola left of the
        breakpoint and p_j defines the parabola to the right. Furthermore, the internal node v has a pointer to the half
        edge in the doubly connected edge list of the Voronoi diagram. More precisely, v has a pointer to one of the
        half-edges of the edge being traced out by the breakpoint represented by v.
        :param breakpoint: A tuple of two points that caused two arcs to intersect
        """

        # The tuple of the points whose arcs intersect
        self.breakpoint = breakpoint

        # The edge this breakpoint is tracing out
        self.edge = edge

    def __repr__(self):
        return f"Breakpoint({self.breakpoint[0].name}, {self.breakpoint[1].name})"

    def tuple_name(self):
        return self.breakpoint[0].name + self.breakpoint[1].name

    def does_intersect(self, _is_close=is_close):
        i, j = self.breakpoint
        iy = i._yd
        jy = j._yd
        return not (_is_close(iy, jy) and j._xd < i._xd)

    def get_intersection_x(self, l, _is_close=is_close, _is_zero=is_zero, _sqrt=sqrt):
        i, j = self.breakpoint
        a = i._xd
        b = i._yd
        c = j._xd
        d = j._yd
        u = 2 * (b - l)
        v = 2 * (d - l)
        diff_uv = u - v

        if _is_close(b, d) or _is_zero(diff_uv):
            return (a + c) / 2
        if _is_close(b, l):
            return a
        if _is_close(d, l):
            return c

        return -(_sqrt(
            v * (a * a * u - 2 * a * c * u + b * b * diff_uv + c * c * u)
            + d * d * u * (-diff_uv)
            + l * l * diff_uv * diff_uv
        ) + a * v - c * u) / diff_uv

    def get_intersection(self, l, max_y=None,
                         _is_close=is_close, _is_zero=is_zero, _sqrt=sqrt,
                         _to_number=to_number):
        """
        Calculate the coordinates of the intersection
        Modified from https://www.cs.hmc.edu/~mbrubeck/voronoi.html

        :param max_y: Bounding box top for clipping infinite breakpoints
        :param l: (float) The position (y-coordinate) of the sweep line
        :return: (float) The coordinates of the breakpoint
        """
        # Get the points
        i, j = self.breakpoint

        # Initialize the resulting point
        result = Coordinate()

        # Inline get_intersection_x to avoid recomputing a, b, c, d, u, v.
        a = i._xd
        b = i._yd
        c = j._xd
        d = j._yd
        u = 2 * (b - l)
        v = 2 * (d - l)
        diff_uv = u - v

        bd_close = _is_close(b, d)
        uv_zero = _is_zero(diff_uv)
        bl_close = _is_close(b, l)
        dl_close = _is_close(d, l)

        if bd_close or uv_zero:
            x = (a + c) / 2
        elif bl_close:
            x = a
        elif dl_close:
            x = c
        else:
            x = -(_sqrt(
                v * (a * a * u - 2 * a * c * u + b * b * diff_uv + c * c * u)
                + d * d * u * (-diff_uv)
                + l * l * diff_uv * diff_uv
            ) + a * v - c * u) / diff_uv

        result._xd = x

        # Pick the parabola whose y-value we evaluate at x.
        p = i

        # Handle the case where the two points have the same y-coordinate (breakpoint is in the middle)
        if bd_close or uv_zero:
            if c < a:
                result._yd = _to_number(max_y or float('inf'))
                return result
        # Handle cases where one point's y-coordinate is the same as the sweep line
        elif bl_close:
            p = j

        # We have to re-evaluate this, since the point might have been changed
        a2 = p._xd
        b2 = p._yd
        u2 = 2 * (b2 - l)

        # Handle degenerate case where parabolas don't intersect
        if _is_zero(u2):
            result._yd = _to_number(float("inf"))
            return result

        # And we put everything back in y
        result._yd = (x * x - 2 * a2 * x + a2 * a2 + b2 * b2 - l * l) / u2
        return result
