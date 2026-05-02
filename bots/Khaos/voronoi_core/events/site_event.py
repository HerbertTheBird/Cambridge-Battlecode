from voronoi_core.graph.point import Point
from voronoi_core.events.event import Event


class SiteEvent(Event):
    # Subject inheritance was inert here -- Event.__init__ never chains via
    # super(), so Subject.__init__ was never reached and no observer attrs
    # were set. Dropping it lets us add __slots__ without a multi-inheritance
    # layout conflict (Event has __slots__).
    __slots__ = ("point",)
    circle_event = False

    def __init__(self, point: Point):
        """
        Site event
        :param point:
        """
        super().__init__()
        self.point = point
        self._sort_key = (-self.point.yd, self.point.xd, 1)

    @property
    def xd(self):
        return self.point.xd

    @property
    def yd(self):
        return self.point.yd

    def __repr__(self):
        return f"SiteEvent(x={self.point.xd}, y={self.point.yd})"
