"""Pure 2D geometry for sequential bridging. No Fusion imports, so it can be tested anywhere.

Everything works in the plane of the selected face. Levels are counted in print order:
level 1 is the first bridge layer (a slot as wide as the hole, spanning the pocket),
level 2 leaves a square, level 3 an octagon, and so on (2**level sides), each one
circumscribed about the hole so its new edges rest on the layer printed before it.
"""
import math

_EPS = 1e-9
INF = float('inf')


def strip_outline(cx, cy, r, theta, half_len):
    """Rectangle of width 2r centred on the hole, running along direction theta."""
    ux, uy = math.cos(theta), math.sin(theta)
    vx, vy = -uy, ux
    return [
        (cx - half_len * ux - r * vx, cy - half_len * uy - r * vy),
        (cx + half_len * ux - r * vx, cy + half_len * uy - r * vy),
        (cx + half_len * ux + r * vx, cy + half_len * uy + r * vy),
        (cx - half_len * ux + r * vx, cy - half_len * uy + r * vy),
    ]


def polygon_outline(cx, cy, r, level, theta):
    """Regular polygon with 2**level sides circumscribed about the hole (level >= 2).

    Two of its sides are parallel to direction theta, so the level-2 square lines up
    with the level-1 slot.
    """
    sides = 2 ** level
    step = 2.0 * math.pi / sides
    big_r = r / math.cos(math.pi / sides)
    return [
        (cx + big_r * math.cos(theta + step / 2.0 + j * step),
         cy + big_r * math.sin(theta + step / 2.0 + j * step))
        for j in range(sides)
    ]


def opening_outline(cx, cy, r, level, theta, half_len):
    """Outline of the opening left in bridge layer `level` (1-based, print order)."""
    if level < 1:
        raise ValueError('level must be >= 1')
    if level == 1:
        return strip_outline(cx, cy, r, theta, half_len)
    return polygon_outline(cx, cy, r, level, theta)


def line_span(px, py, theta, segments):
    """Length of the stretch of the line through (px, py) along theta that lies between
    the nearest boundary segments on either side. INF if a side is open."""
    ux, uy = math.cos(theta), math.sin(theta)
    t_pos, t_neg = INF, -INF
    for (ax, ay), (bx, by) in segments:
        dx, dy = bx - ax, by - ay
        den = ux * dy - uy * dx
        if abs(den) < _EPS:
            continue
        wx, wy = ax - px, ay - py
        t = (wx * dy - wy * dx) / den
        s = (wx * uy - wy * ux) / den
        if s < -1e-7 or s > 1.0 + 1e-7:
            continue
        if t > _EPS:
            t_pos = min(t_pos, t)
        elif t < -_EPS:
            t_neg = max(t_neg, t)
    if t_pos == INF or t_neg == -INF:
        return INF
    return t_pos - t_neg


def bridge_spans(cx, cy, r, theta, segments):
    """Spans of the two first-layer bridges (the lines tangent to the hole)."""
    vx, vy = -math.sin(theta), math.cos(theta)
    return (
        line_span(cx + r * vx, cy + r * vy, theta, segments),
        line_span(cx - r * vx, cy - r * vy, theta, segments),
    )


def candidate_angles(edge_angles, grid_deg=15):
    """Directions worth trying: along and across every straight wall.

    Bridges stay square to the pocket walls even where a diagonal across a corner would
    be slightly shorter. A coarse grid is used only when the pocket has no straight wall.
    """
    raw = []
    for a in edge_angles:
        raw.append(a)
        raw.append(a + math.pi / 2.0)
    if not raw:
        raw.extend(math.radians(d) for d in range(0, 180, grid_deg))
    out = []
    for a in raw:
        a = a % math.pi
        if a > math.pi - 1e-7:
            a = 0.0
        if not any(abs(a - b) < 1e-7 for b in out):
            out.append(a)
    return out


def choose_theta(cx, cy, r, segments, edge_angles=()):
    """Pick the first-bridge direction that gives the shortest bridges across the pocket."""
    best, best_key = None, None
    for a in candidate_angles(edge_angles):
        s1, s2 = bridge_spans(cx, cy, r, a, segments)
        key = (round(max(s1, s2), 6), round(s1 + s2, 6))
        if best_key is None or key < best_key:
            best, best_key = a, key
    return best if best is not None else 0.0
