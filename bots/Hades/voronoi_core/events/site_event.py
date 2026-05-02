from voronoi_core.graph.point import Point
from voronoi_core.events.event import Event
from voronoi_core.observers.subject import Subject


class SiteEvent(Event, Subject):
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
