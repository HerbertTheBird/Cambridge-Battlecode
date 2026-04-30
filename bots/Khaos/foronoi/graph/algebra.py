import math
from foronoi.graph import Coordinate


class Algebra:
    @staticmethod
    def distance(point_a, point_b):
        x1 = point_a._xd
        x2 = point_b._xd
        y1 = point_a._yd
        y2 = point_b._yd

        return math.hypot(x2 - x1, y2 - y1)

    @staticmethod
    def magnitude(vector):
        return math.sqrt(sum(value * value for value in vector))

    @staticmethod
    def norm(vector):
        magnitude = Algebra.magnitude(vector)
        if magnitude == 0:
            return tuple(vector)
        return tuple(value / magnitude for value in vector)

    @staticmethod
    def dot(vector_a, vector_b):
        return sum(a * b for a, b in zip(vector_a, vector_b))

    @staticmethod
    def cross(vector_a, vector_b):
        return vector_a[0] * vector_b[1] - vector_a[1] * vector_b[0]

    @staticmethod
    def line_ray_intersection_point(ray_orig, ray_end, point_1, point_2):
        orig = (float(ray_orig[0]), float(ray_orig[1]))
        end = (float(ray_end[0]), float(ray_end[1]))
        direction = Algebra.norm((end[0] - orig[0], end[1] - orig[1]))
        point_1 = (float(point_1[0]), float(point_1[1]))
        point_2 = (float(point_2[0]), float(point_2[1]))

        # Ray-Line Segment Intersection Test in 2D
        # http://bit.ly/1CoxdrG
        v1 = (orig[0] - point_1[0], orig[1] - point_1[1])
        v2 = (point_2[0] - point_1[0], point_2[1] - point_1[1])
        v3 = (-direction[1], direction[0])

        denominator = Algebra.dot(v2, v3)
        if denominator == 0:
            return []

        t1 = Algebra.cross(v2, v1) / denominator
        t2 = Algebra.dot(v1, v3) / denominator

        if t1 > 0.0 and 0.0 <= t2 <= 1.0:
            return [(
                orig[0] + t1 * direction[0],
                orig[1] + t1 * direction[1],
            )]
        return []

    @staticmethod
    def get_intersection(orig: Coordinate, end: Coordinate, p1: Coordinate, p2: Coordinate):
        if not orig or not end:
            return None

        point = Algebra.line_ray_intersection_point([orig.xd, orig.yd], [end.xd, end.yd], [p1.xd, p1.yd], [p2.xd, p2.yd])

        if len(point) == 0:
            return None

        return Coordinate(point[0][0], point[0][1])

    @staticmethod
    def calculate_angle(point, center):
        dx = point._xd - center._xd
        dy = point._yd - center._yd
        return math.degrees(math.atan2(dy, dx)) % 360

    @staticmethod
    def check_clockwise(a, b, c, center):
        _ = center
        abx = b._xd - a._xd
        aby = b._yd - a._yd
        acx = c._xd - a._xd
        acy = c._yd - a._yd
        return abx * acy - aby * acx < 0


if __name__ == "__main__":
    Algebra.line_ray_intersection_point([5, 0.5], [38, 33], [10, 5], [7.5, 10])
    Algebra.line_ray_intersection_point([5, 0.5], [-28, 33], [0, 5], [2.5, 10])
