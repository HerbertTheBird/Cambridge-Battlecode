import math

from voronoi_core.graph import Coordinate, Vertex, HalfEdge
from voronoi_core.graph.algebra import Algebra

from voronoi_core.observers.message import Message
from voronoi_core.observers.subject import Subject


class Polygon(Subject):
    def __init__(self, tuples):
        """
        A bounding polygon that will clip the edges and fit around the Voronoi diagram.

        Parameters
        ----------
        tuples: (float, float)
            x,y-coordinates of the polygon's vertices
        """

        super().__init__()
        points = [Coordinate(x, y) for x, y in tuples]
        self.points = points
        min_y = min(p._yd for p in self.points)
        min_x = min(p._xd for p in self.points)
        max_y = max(p._yd for p in self.points)
        max_x = max(p._xd for p in self.points)
        center = Coordinate((max_x + min_x) / 2, (max_y + min_y) / 2)
        self.min_y, self.min_x, self.max_y, self.max_x, self.center = min_y, min_x, max_y, max_x, center

        self.points = self._order_points(self.points)
        self.polygon_vertices = [Vertex(point._xd, point._yd) for point in self.points]

        # Pre-compute polygon edges as flat float tuples for hot inside() and
        # _get_intersection_point() loops. These avoid 4 attribute lookups per
        # edge per call and let the inner math run in pure float.
        self._edge_pts = self._build_edge_pts()
        # Sweep-line position used by _finish_edge() never changes after init.
        self._finish_sweep_line = self.min_y - abs(self.max_y)

    def _build_edge_pts(self):
        pts = self.points
        edges = []
        if not pts:
            return edges
        prev = pts[-1]
        for cur in pts:
            xi = float(prev._xd)
            yi = float(prev._yd)
            xj = float(cur._xd)
            yj = float(cur._yd)
            edges.append((xi, yi, xj, yj, xj - xi, yj - yi))
            prev = cur
        return edges

    def _order_points(self, points):
        clockwise = sorted(points, key=lambda point: (-180 - Algebra.calculate_angle(point, self.center)) % 360)
        return clockwise

    def _get_ordered_vertices(self, vertices):
        vertices = [vertex for vertex in vertices if vertex._xd is not None]
        clockwise = sorted(vertices,
                           key=lambda vertex: (-180 - Algebra.calculate_angle(vertex, self.center)) % 360)
        return clockwise

    @staticmethod
    def _get_closest_point(position, points):
        return min(points, key=lambda point: Algebra.distance(position, point))

    def finish_polygon(self, edges, existing_vertices, points):
        """
        Creates half-edges on the bounding polygon that link with Voronoi diagram's half-edges and existing vertices.

        Parameters
        ----------
        edges: list(HalfEdge)
            The list of clipped edges from the Voronoi diagram
        existing_vertices: set(Vertex)
            The list of vertices that already exists in the clipped Voronoi diagram, and vertices
        points: set(Point)
            The list of cell points

        Returns
        -------
        edges: list(HalfEdge)
            The list of all edges including the bounding polygon's edges
        vertices: list(Vertex)
            The list of all vertices including the
        """
        vertices = self._get_ordered_vertices(self.polygon_vertices)
        vertices = list(vertices) + [vertices[0]]  # <- The extra vertex added here, should be removed later
        cell = self._get_closest_point(vertices[0], points)
        previous_edge = None
        for index in range(0, len(vertices) - 1):

            # Get origin
            origin = vertices[index]
            end = vertices[index + 1]

            # If vertex is connected to other edges, update the cell
            if len(origin.connected_edges) > 0:
                cell = origin.connected_edges[0].twin.incident_point

            # Create the edge
            edge = HalfEdge(cell, origin=origin, twin=HalfEdge(None, origin=end))
            origin.connected_edges.append(edge)
            end.connected_edges.append(edge.twin)

            # Add first edge if needed
            if cell:
                cell.first_edge = cell.first_edge or edge

            # Connect edges
            if len(end.connected_edges) > 0:
                edge.set_next(end.connected_edges[0])

            # Connect to incoming edge, or previous edge
            if len(origin.connected_edges) > 0:
                origin.connected_edges[0].twin.set_next(edge)
            elif previous_edge is not None:
                previous_edge.set_next(edge)

            # Add the edge to the list
            edges.append(edge)

            # Set previous edge
            previous_edge = edge

        existing_vertices = [i for i in existing_vertices if self.inside(i)]

        return edges, vertices[:-1] + existing_vertices

    def get_coordinates(self):
        return [(i._xd, i._yd) for i in self.points]

    def finish_edges(self, edges, **kwargs):
        """
        Clip the edges to the bounding box/polygon, and remove edges and vertices that are fully outside.
        Inserts vertices at the clipped edges' endings.

        Parameters
        ----------
        edges: list(HalfEdge)
            A list of edges in the Voronoi diagram. Every edge should be presented only by one half edge.

        Returns
        -------
        clipped_edges: list(HalfEdge)
            A list of clipped edges
        """
        resulting_edges = list()
        for edge in edges:

            if edge.get_origin() is None or not self.inside(edge.get_origin()):
                self._finish_edge(edge)

            if edge.twin.get_origin() is None or not self.inside(edge.twin.get_origin()):
                self._finish_edge(edge.twin)

            if edge.get_origin() is not None and edge.twin.get_origin() is not None:
                resulting_edges.append(edge)
            else:
                edge.delete()
                edge.twin.delete()
                if self._observers:
                    self.notify_observers(Message.DEBUG, payload=f"Edges {edge} and {edge.twin} deleted!")

        return resulting_edges

    def _finish_edge(self, edge):
        # Sweep line position (cached at construction; never changes).
        sweep_line = self._finish_sweep_line
        max_y = self.max_y

        # Start should be a breakpoint
        start = edge.get_origin(y=sweep_line, max_y=max_y)

        # End should be a vertex
        end = edge.twin.get_origin(y=sweep_line, max_y=max_y)

        # Get point of intersection
        point = self._get_intersection_point(end, start)

        # Create vertex
        v = Vertex(point._xd, point._yd) if point is not None else Vertex(None, None)
        v.connected_edges.append(edge)
        edge.origin = v
        self.polygon_vertices.append(v)

        return edge

    def _on_edge(self, point):
        prev = self.points[-1]
        px = point._xd
        py = point._yd

        for cur in self.points:
            dxc = px - prev._xd
            dyc = py - prev._yd
            dx1 = cur._xd - prev._xd
            dy1 = cur._yd - prev._yd

            cross = dxc * dy1 - dyc * dx1

            if cross == 0:
                return True
            prev = cur
        return False

    def inside(self, point):
        """Tests whether a point is inside a polygon.
        Based on the Javascript implementation from https://github.com/substack/point-in-polygon

        Parameters
        ----------
        point: Point
            The point for which to check if it it is inside the polygon

        Returns
        -------
        inside: bool
            Whether the point is inside or not
        """

        x = float(point._xd)
        y = float(point._yd)
        inside = False

        for xi, yi, _xj, yj, dx, dy in self._edge_pts:
            if (yi > y) != (yj > y):
                if x < dx * (y - yi) / dy + xi:
                    inside = not inside

        return inside

    def _get_intersection_point(self, orig, end):
        if orig is None or end is None:
            return None

        # Operate in pure float so that Decimal x float arithmetic doesn't
        # raise (and so we save a function-call layer over Algebra).
        ox = float(orig._xd)
        oy = float(orig._yd)
        ex = float(end._xd)
        ey = float(end._yd)

        dx_ray = ex - ox
        dy_ray = ey - oy
        max_distance = math.hypot(dx_ray, dy_ray)
        if max_distance == 0.0:
            dir_x = dx_ray
            dir_y = dy_ray
        else:
            dir_x = dx_ray / max_distance
            dir_y = dy_ray / max_distance
        neg_dir_y = -dir_y

        best_point = None
        best_distance = None

        for p1x, p1y, _p2x, _p2y, v2x, v2y in self._edge_pts:
            denom = v2x * neg_dir_y + v2y * dir_x
            if denom == 0.0:
                continue

            v1x = ox - p1x
            v1y = oy - p1y

            t1 = (v2x * v1y - v2y * v1x) / denom
            t2 = (v1x * neg_dir_y + v1y * dir_x) / denom

            if not (t1 > 0.0 and 0.0 <= t2 <= 1.0):
                continue

            # The direction is a unit vector, so distance from orig to the
            # intersection is just |t1|, and t1 is positive here.
            if t1 <= max_distance and (best_distance is None or t1 > best_distance):
                best_point = (ox + t1 * dir_x, oy + t1 * dir_y)
                best_distance = t1

        if best_point is None:
            return None

        # Bypass Coordinate.__init__ (and its to_number() calls); we already
        # know the coordinates are valid floats.
        c = Coordinate.__new__(Coordinate)
        c._xd = best_point[0]
        c._yd = best_point[1]
        return c
