class Event:
    __slots__ = ("_sort_key",)
    circle_event = False

    def __init__(self):
        self._sort_key = (0, 0, 0)

    @property
    def xd(self):
        return 0

    @property
    def yd(self):
        return 0

    def __lt__(self, other):
        return self._sort_key < other._sort_key

    def __eq__(self, other):
        if other is None:
            return None
        return self._sort_key == other._sort_key

    def __ne__(self, other):
        return not self.__eq__(other)
