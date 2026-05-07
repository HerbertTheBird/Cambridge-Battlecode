import math
from decimal import *

from voronoi_core.events.event import Event
from voronoi_core.graph.coordinate import Coordinate
from voronoi_core.nodes.leaf_node import LeafNode
from voronoi_core.nodes.arc import Arc
from voronoi_core.numeric import is_zero, sqrt


class CircleEvent(Event):
    __slots__ = ("center", "radius", "arc_pointer", "is_valid", "point_triple", "arc_triple")
    circle_event = True

    def __init__(self, center: Coordinate, radius: Decimal, arc_node: LeafNode, point_triple=None, arc_triple=None):
        """
        Circle event.

        :param arc_node: Pointer to the node in the beach line tree that holds the arc that will disappear
        :param point_triple: The tuple of points that caused the event
        """
        super().__init__()
        self.center = center
        self.radius = radius
        self.arc_pointer = arc_node
        self.is_valid = True
        self.point_triple = point_triple
        self.arc_triple = arc_triple
        self._sort_key = (-(self.center._yd - self.radius), self.center._xd, 0)

    def __repr__(self):
        return f"CircleEvent({self.point_triple}, y-radius={self.center.yd - self.radius:.2f}, y={self.center.yd:.2f}, radius={self.radius:.2f})"

    @property
    def xd(self):
        return self.center.xd

    @property
    def yd(self):
        return self.center.yd - self.radius

    def get_triangle(self):
        return (
            (self.point_triple[0].xd, self.point_triple[0].yd),
            (self.point_triple[1].xd, self.point_triple[1].yd),
            (self.point_triple[2].xd, self.point_triple[2].yd),
        )

    def remove(self):
        self.is_valid = False
        return self

    @staticmethod
    def create_circle_event(left_node: LeafNode, middle_node: LeafNode, right_node: LeafNode, sweep_line) -> "CircleEvent":
        """
        Checks if the breakpoints converge, and inserts circle event if required.
        :param sweep_line: Y-coordinate of the sweep line
        :param left_node: The node that represents the arc on the left
        :param middle_node: The node that represents the arc on the middle
        :param right_node: The node that represents the arc on the right
        :return: The circle event or None if no circle event needs to be inserted
        """

        # Check if any of the nodes is None
        if left_node is None or right_node is None or middle_node is None:
            return None

        # Get arcs from the nodes
        left_arc: Arc = left_node.data
        middle_arc: Arc = middle_node.data
        right_arc: Arc = right_node.data

        # Get the points from the arcs
        a, b, c = left_arc.origin, middle_arc.origin, right_arc.origin

        # Check if we can create a circle event
        circle = CircleEvent.create_circle(a, b, c)
        if circle:
            x, y, radius = circle

            # Skip Coordinate.__init__ + the two to_number() calls; we already
            # have the coordinates as the right numeric type from create_circle.
            center = Coordinate.__new__(Coordinate)
            center._xd = x
            center._yd = y

            return CircleEvent(center=center, radius=radius, arc_node=middle_node, point_triple=(a, b, c),
                               arc_triple=(left_arc, middle_arc, right_arc))

        return None

    @staticmethod
    def create_circle(a, b, c):

        # Algorithm from O'Rourke 2ed p. 189
        ax = a._xd
        ay = a._yd
        bx = b._xd
        by = b._yd
        cx = c._xd
        cy = c._yd

        A = bx - ax
        B = by - ay
        C = cx - ax
        D = cy - ay

        # G = 2 * ((bx-ax)*(cy-by) - (by-ay)*(cx-bx))
        # Using cy-by = D - B and cx-bx = C - A:
        #   = 2 * (A*(D-B) - B*(C-A)) = 2 * (A*D - B*C)
        G = 2 * (A * D - B * C)

        if is_zero(G):
            # Points are all on one line (collinear), so no circle can be made
            return False

        E = A * (ax + bx) + B * (ay + by)
        F = C * (ax + cx) + D * (ay + cy)

        # Center and radius of the circle
        x = (D * E - B * F) / G
        y = (A * F - C * E) / G

        rdx = ax - x
        rdy = ay - y
        radius = sqrt(rdx * rdx + rdy * rdy)

        return x, y, radius
