import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'SequentialBridge'))
import bridge_geometry as geo  # noqa: E402


def closed(points):
    return list(zip(points, points[1:] + points[:1]))


def edge_distance(center, a, b):
    """Distance from center to the infinite line through a-b."""
    (cx, cy), (ax, ay), (bx, by) = center, a, b
    dx, dy = bx - ax, by - ay
    return abs((cx - ax) * dy - (cy - ay) * dx) / math.hypot(dx, dy)


def inside_convex(pt, poly, tol=1e-9):
    sign = 0
    for (ax, ay), (bx, by) in closed(poly):
        cross = (bx - ax) * (pt[1] - ay) - (by - ay) * (pt[0] - ax)
        if abs(cross) <= tol:
            continue
        s = 1 if cross > 0 else -1
        if sign == 0:
            sign = s
        elif s != sign:
            return False
    return True


def hexagon(across_flats, rot=0.0):
    big_r = across_flats / 2.0 / math.cos(math.pi / 6)
    return [(big_r * math.cos(rot + i * math.pi / 3), big_r * math.sin(rot + i * math.pi / 3)) for i in range(6)]


class OutlineTests(unittest.TestCase):
    def test_side_counts(self):
        self.assertEqual(len(geo.opening_outline(0, 0, 1, 1, 0, 10)), 4)
        self.assertEqual(len(geo.opening_outline(0, 0, 1, 2, 0, 10)), 4)
        self.assertEqual(len(geo.opening_outline(0, 0, 1, 3, 0, 10)), 8)
        self.assertEqual(len(geo.opening_outline(0, 0, 1, 4, 0, 10)), 16)

    def test_every_polygon_edge_is_tangent_to_hole(self):
        c, r = (3.0, -2.0), 0.3
        for level in range(2, 6):
            for theta in (0.0, 0.4, 1.3):
                poly = geo.polygon_outline(c[0], c[1], r, level, theta)
                for a, b in closed(poly):
                    self.assertAlmostEqual(edge_distance(c, a, b), r, places=9)

    def test_strip_long_sides_tangent_and_long(self):
        c, r, theta = (1.0, 2.0), 0.5, 0.7
        s = geo.strip_outline(c[0], c[1], r, theta, 20.0)
        self.assertAlmostEqual(edge_distance(c, s[0], s[1]), r)
        self.assertAlmostEqual(edge_distance(c, s[2], s[3]), r)
        self.assertAlmostEqual(math.dist(s[0], s[1]), 40.0)
        self.assertAlmostEqual(math.dist(s[1], s[2]), 2 * r)

    def test_square_aligned_with_strip(self):
        theta = 0.35
        sq = geo.polygon_outline(0, 0, 1.0, 2, theta)
        dirs = sorted((math.atan2(b[1] - a[1], b[0] - a[0]) - theta) % (math.pi / 2) for a, b in closed(sq))
        for d in dirs:
            self.assertTrue(min(d, math.pi / 2 - d) < 1e-9)

    def test_layers_nest(self):
        # each layer's opening must sit inside the one printed before it
        c, r, theta = (0.5, 0.25), 0.4, 0.9
        prev = geo.opening_outline(c[0], c[1], r, 1, theta, 50.0)
        for level in range(2, 6):
            cur = geo.opening_outline(c[0], c[1], r, level, theta, 50.0)
            for p in cur:
                self.assertTrue(inside_convex(p, prev, tol=1e-9), (level, p))
            prev = cur

    def test_new_edges_rest_on_previous_layer(self):
        # every vertex of layer k+1 lies on the boundary of layer k (so each new
        # bridge line starts and ends on printed material)
        c, r, theta = (0.0, 0.0), 1.0, 0.2
        for level in range(2, 5):
            prev = geo.polygon_outline(c[0], c[1], r, level, theta)
            cur = geo.polygon_outline(c[0], c[1], r, level + 1, theta)
            for p in cur:
                d = min(edge_distance(p, a, b) for a, b in closed(prev))
                self.assertAlmostEqual(d, 0.0, places=9)


class SpanTests(unittest.TestCase):
    def test_span_in_rectangle(self):
        rect = closed([(-5, -2), (5, -2), (5, 2), (-5, 2)])
        self.assertAlmostEqual(geo.line_span(0, 0, 0.0, rect), 10.0)
        self.assertAlmostEqual(geo.line_span(1, 0.5, math.pi / 2, rect), 4.0)

    def test_open_side_is_infinite(self):
        segs = [((-5, -2), (5, -2))]
        self.assertEqual(geo.line_span(0, 0, math.pi / 2, segs), geo.INF)

    def test_rectangle_picks_short_way(self):
        rect_pts = [(-5, -2), (5, -2), (5, 2), (-5, 2)]
        theta = geo.choose_theta(0, 0, 0.5, closed(rect_pts), [0.0, math.pi / 2])
        self.assertAlmostEqual(theta, math.pi / 2)

    def test_rectangle_does_not_go_diagonal(self):
        # a 45 degree chord across the corners of a 12 x 10 pocket is shorter than 10,
        # but bridges must stay square to the walls
        rect_pts = [(-0.6, -0.5), (0.6, -0.5), (0.6, 0.5), (-0.6, 0.5)]
        diag = max(geo.bridge_spans(0, 0, 0.3, math.pi / 4, closed(rect_pts)))
        self.assertLess(diag, 1.0)
        angles = [math.atan2(b[1] - a[1], b[0] - a[0]) for a, b in closed(rect_pts)]
        theta = geo.choose_theta(0, 0, 0.3, closed(rect_pts), angles)
        self.assertAlmostEqual(theta, math.pi / 2)

    def test_rotated_hex_snaps_to_a_flat(self):
        # In a hex pocket the shortest bridges run parallel to a flat (the chord at
        # offset r is shorter than the across-flats distance), whatever the hex rotation.
        rot = math.radians(17.0)
        pts = hexagon(1.0, rot)  # 10 mm across flats, in cm
        angles = [math.atan2(b[1] - a[1], b[0] - a[0]) for a, b in closed(pts)]
        theta = geo.choose_theta(0, 0, 0.3, closed(pts), angles)
        s1, s2 = geo.bridge_spans(0, 0, 0.3, theta, closed(pts))
        expected = (1.0 - 0.3) * 2 / math.sqrt(3)
        self.assertAlmostEqual(s1, expected, places=6)
        self.assertAlmostEqual(s2, expected, places=6)
        self.assertLess(expected, 1.0)
        self.assertTrue(any(abs(math.sin(theta - a)) < 1e-6 for a in angles))

    def test_round_pocket_has_a_default(self):
        circle = [(2 * math.cos(i * math.pi / 60), 2 * math.sin(i * math.pi / 60)) for i in range(120)]
        theta = geo.choose_theta(0, 0, 0.5, closed(circle))
        self.assertAlmostEqual(theta, 0.0)

    def test_off_centre_hole(self):
        rect = closed([(-5, -2), (5, -2), (5, 2), (-5, 2)])
        s1, s2 = geo.bridge_spans(3.0, 1.0, 0.5, math.pi / 2, rect)
        self.assertAlmostEqual(s1, 4.0)
        self.assertAlmostEqual(s2, 4.0)


if __name__ == '__main__':
    unittest.main()
