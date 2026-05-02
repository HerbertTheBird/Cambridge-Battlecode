import math
from decimal import Decimal

from voronoi_core.graph.coordinate import Coordinate
from voronoi_core.numeric import is_close, is_zero, sqrt


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

    def does_intersect(self):
        i, j = self.breakpoint
        iy = i._yd
        jy = j._yd
        return not (is_close(iy, jy) and j._xd < i._xd)

    def get_intersection_x(self, l):
        i, j = self.breakpoint
        a = i._xd
        b = i._yd
        c = j._xd
        d = j._yd
        u = 2 * (b - l)
        v = 2 * (d - l)

        if is_close(b, d) or is_zero(u - v):
            return (a + c) / 2
        if is_close(b, l):
            return a
        if is_close(d, l):
            return c

        return -(sqrt(
            v * (a ** 2 * u - 2 * a * c * u + b ** 2 * (u - v) + c ** 2 * u)
            + d ** 2 * u * (v - u)
            + l ** 2 * (u - v) ** 2
        ) + a * v - c * u) / (u - v)

    def get_intersection(self, l, max_y=None):
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
        p: Coordinate = i

        # First we replace some stuff to make it easier
        a = i._xd
        b = i._yd
        c = j._xd
        d = j._yd
        u = 2 * (b - l)
        v = 2 * (d - l)

        result._xd = self.get_intersection_x(l)

        # Handle the case where the two points have the same y-coordinate (breakpoint is in the middle)
        if is_close(b, d) or is_zero(u - v):

            if c < a:
                result._yd = Coordinate._to_dec(max_y or float('inf'))
                return result

        # Handle cases where one point's y-coordinate is the same as the sweep line
        elif is_close(b, l):
            p = j

        # We have to re-evaluate this, since the point might have been changed
        a = p._xd
        b = p._yd
        x = result._xd
        u = 2 * (b - l)

        # Handle degenerate case where parabolas don't intersect
        if is_zero(u):
            result._yd = Coordinate._to_dec(float("inf"))
            return result

        # And we put everything back in y
        result._yd = 1 / u * (x ** 2 - 2 * a * x + a ** 2 + b ** 2 - l ** 2)
        return result
