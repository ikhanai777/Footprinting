"""Primitive fitting: turn dense sub-pixel point chains into CAD geometry.

Each traced chain is converted into one of:

* a full CIRCLE,
* a full ELLIPSE,
* a polyline made of straight LINE segments and circular ARC segments
  (represented as vertices + DXF bulge values, i.e. a clean LWPOLYLINE).

Pipeline for a chain:
  1. greedy segmentation - from the current point take the longest run that
     fits a line (total least squares) or a circular arc (geometric fit)
     within the tolerance; prefer lines when both are comparable;
  2. merge adjacent segments that together still fit a single primitive;
  3. collapse tiny corner-rounding segments (blur/antialias artifacts) between
     two segments whose intersection reconstructs the sharp corner;
  4. place vertices at the *intersection* of neighbouring fitted primitives
     (sharp corners are recovered exactly) or at the projection onto them for
     tangent (smooth) joins;
  5. convert arcs to bulges using the fitted radius.
All computations are in pixel units (Y-up); scaling happens at export time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
from scipy.optimize import minimize_scalar
import numpy as np


# ----------------------------------------------------------------------------
# Output shapes
# ----------------------------------------------------------------------------


@dataclass
class PolyShape:
    vertices: np.ndarray  # (M, 2)
    bulges: np.ndarray  # (M,) bulge of segment starting at vertex k
    closed: bool
    max_dev: float = 0.0


@dataclass
class CircleShape:
    center: np.ndarray
    radius: float
    max_dev: float = 0.0


@dataclass
class EllipseShape:
    center: np.ndarray
    major_axis: np.ndarray  # vector from center to the end of the major axis
    ratio: float  # minor / major
    max_dev: float = 0.0


@dataclass
class FitOptions:
    tolerance: float = 0.5  # max deviation (px) of a primitive from the traced points
    detect_circles: bool = True
    detect_ellipses: bool = True
    arcs: bool = True
    snap_ortho_deg: float = 0.0  # snap near horizontal/vertical lines (0 = off)
    corner_limit: float = 0.0  # max corner reconstruction distance, px (0 = auto)
    corner_zone: float = 2.0  # radius (px) around a corner affected by blur/anti-aliasing
    max_radius: float = 1e5
    min_arc_points: int = 6
    max_arc_sweep_deg: float = 300.0


# ----------------------------------------------------------------------------
# Elementary fits
# ----------------------------------------------------------------------------


def fit_line(p: np.ndarray):
    """Total least squares line -> (centroid, unit direction, max |distance|)."""
    c = p.mean(axis=0)
    q = p - c
    sxx = float(np.dot(q[:, 0], q[:, 0]))
    syy = float(np.dot(q[:, 1], q[:, 1]))
    sxy = float(np.dot(q[:, 0], q[:, 1]))
    th = 0.5 * math.atan2(2.0 * sxy, sxx - syy)  # principal axis (closed form)
    d = np.array([math.cos(th), math.sin(th)])
    n = np.array([-d[1], d[0]])
    err = float(np.abs(q @ n).max()) if len(p) else 0.0
    return c, d, err


def fit_circle(p: np.ndarray, iters: int = 8, reject_above: float | None = None, accept_below: float | None = None):
    """Geometric circle fit (Kasa init + Gauss-Newton). Returns (center, r, max err) or None.

    reject_above: skip the refinement when the algebraic fit is already this far
    off (the caller would reject it anyway) - a large speed-up in searches.
    """
    if len(p) < 3:
        return None
    m = p.mean(axis=0)
    q = p - m
    x, y = q[:, 0], q[:, 1]
    A = np.column_stack([x, y, np.ones_like(x)])
    b = x * x + y * y
    try:
        sol = np.linalg.solve(A.T @ A, A.T @ b)
    except np.linalg.LinAlgError:
        return None
    cx, cy = sol[0] / 2, sol[1] / 2
    r2 = sol[2] + cx * cx + cy * cy
    if not np.isfinite(r2) or r2 <= 0:
        return None
    r = math.sqrt(r2)
    if reject_above is not None or accept_below is not None:
        err0 = float(np.abs(np.hypot(x - cx, y - cy) - r).max())
        if (reject_above is not None and err0 > reject_above) or (accept_below is not None and err0 <= accept_below):
            return np.array([cx, cy]) + m, r, err0
    n = float(len(x))
    for _ in range(iters):
        dx, dy = x - cx, y - cy
        d = np.hypot(dx, dy)
        if d.min() < 1e-12:
            break
        ux, uy = dx / d, dy / d
        res = d - r
        # normal equations of the Jacobian [-ux, -uy, -1]
        sxx, syy, sxy = ux @ ux, uy @ uy, ux @ uy
        sx, sy = ux.sum(), uy.sum()
        JTJ = np.array([[sxx, sxy, sx], [sxy, syy, sy], [sx, sy, n]])
        JTr = -np.array([ux @ res, uy @ res, res.sum()])
        try:
            delta = np.linalg.solve(JTJ, -JTr)
        except np.linalg.LinAlgError:
            break
        cx, cy, r = cx + delta[0], cy + delta[1], r + delta[2]
        if abs(delta).max() < 1e-7 * max(1.0, abs(r)):
            break
    if not np.isfinite(r) or r <= 0:
        return None
    err = float(np.abs(np.hypot(x - cx, y - cy) - r).max())
    return np.array([cx, cy]) + m, float(r), err


def _angles(p: np.ndarray, c: np.ndarray) -> np.ndarray:
    return np.unwrap(np.arctan2(p[:, 1] - c[1], p[:, 0] - c[0]))


# ----------------------------------------------------------------------------
# Segments
# ----------------------------------------------------------------------------


@dataclass
class _Seg:
    kind: str  # "line" | "arc"
    pts: np.ndarray
    c: np.ndarray | None = None  # line centroid / arc center
    d: np.ndarray | None = None  # line direction
    r: float = 0.0
    ccw: bool = True
    sweep: float = 0.0


class _Fitter:
    def __init__(self, opts: FitOptions):
        self.o = opts
        self.tol = opts.tolerance
        self.max_sweep = math.radians(opts.max_arc_sweep_deg)

    # -- predicates -------------------------------------------------------
    def line(self, p: np.ndarray) -> _Seg | None:
        c, d, err = fit_line(p)
        if err > self.tol:
            return None
        if np.dot(p[-1] - p[0], d) < 0:
            d = -d
        return _Seg("line", p, c=c, d=d)

    def arc(self, p: np.ndarray, precise: bool = False) -> _Seg | None:
        if not self.o.arcs or len(p) < self.o.min_arc_points:
            return None
        f = fit_circle(p, reject_above=4.0 * self.tol, accept_below=None if precise else 0.8 * self.tol)
        if f is None:
            return None
        c, r, err = f
        if err > self.tol or r > self.o.max_radius or r < 0.5 * self.tol:
            return None
        th = _angles(p, c)
        dth = np.diff(th)
        total = th[-1] - th[0]
        if abs(total) < 1e-9 or abs(total) > self.max_sweep:
            return None
        s = 1.0 if total > 0 else -1.0
        # angular progress must be monotone (no zig-zag / backtracking)
        if np.any(dth * s < -max(self.tol / r, 1e-3)):
            return None
        return _Seg("arc", p, c=c, r=r, ccw=total > 0, sweep=abs(total))

    def curved_enough(self, s: _Seg) -> bool:
        """An arc that is almost straight is a line (typically plus a blurred
        corner that it swallowed); rejecting it lets the corner be rebuilt."""
        sagitta = s.r * (1.0 - math.cos(min(s.sweep, math.pi) / 2.0))
        return sagitta >= 1.2 * self.tol

    def refit(self, p: np.ndarray, kind: str) -> _Seg | None:
        return self.line(p) if kind == "line" else self.arc(p)

    # -- greedy segmentation ---------------------------------------------
    def _extend(self, pts: np.ndarray, i: int, fn, first: int) -> tuple[int, _Seg | None]:
        n = len(pts)
        best_j, best = i, None
        step = first
        hi = None
        while True:
            j = min(i + step, n - 1)
            s = fn(pts[i : j + 1])
            if s is not None:
                best_j, best = j, s
                if j == n - 1:
                    return best_j, best
                step *= 2
            else:
                hi = j
                break
        lo = best_j
        while hi - lo > 1:
            mid = (lo + hi) // 2
            s = fn(pts[i : mid + 1])
            if s is not None:
                lo, best = mid, s
            else:
                hi = mid
        return lo, best

    def _line_from(self, pts: np.ndarray, i: int) -> tuple[int, _Seg]:
        j, s = self._extend(pts, i, self.line, 1)
        if s is None:  # two points always define a line
            j, s = i + 1, _two_point_line(pts[i : i + 2])
        return j, s

    def segment(self, pts: np.ndarray) -> list[_Seg]:
        n = len(pts)
        segs: list[_Seg] = []
        cz = self.o.corner_zone
        i = 0
        while i < n - 1:
            jl, sl = self._line_from(pts, i)
            # A blurred corner apex can stop a line early: also try starting the
            # line a little later; the skipped points become a tiny segment that
            # is collapsed into a sharp corner afterwards.
            skip_k = i
            if cz > 0:
                k = i + 1
                while k < n - 2 and np.linalg.norm(pts[k] - pts[i]) <= cz:
                    jk, sk = self._line_from(pts, k)
                    if jk > jl + max(2, int(0.25 * (jl - i))) and jk > skip_k:
                        skip_j, skip_s, skip_k = jk, sk, k
                    k += 1
            ja, sa = (i, None)
            if self.o.arcs and n - i >= self.o.min_arc_points:
                ja, sa = self._extend(pts, i, self.arc, self.o.min_arc_points - 1)
                if sa is not None and not self.curved_enough(sa):
                    ja, sa = i, None
            best_line_j = skip_j if skip_k > i else jl
            if sa is not None and ja > best_line_j + max(2, int(0.15 * (best_line_j - i))):
                segs.append(self.arc(sa.pts, precise=True) or sa)
                i = ja
            elif skip_k > i:
                segs.append(_two_point_line(pts[i : skip_k + 1]))
                segs.append(skip_s)
                i = skip_j
            else:
                segs.append(sl)
                i = jl
        return segs

    def refine_lines(self, segs: list[_Seg]) -> None:
        """Refit long lines without the points near their ends, which are
        contaminated by corner rounding (blur / anti-aliasing)."""
        cz = self.o.corner_zone
        if cz <= 0:
            return
        for s in segs:
            if s.kind != "line" or len(s.pts) < 8:
                continue
            a, b = s.pts[0], s.pts[-1]
            keep = (np.linalg.norm(s.pts - a, axis=1) > cz) & (np.linalg.norm(s.pts - b, axis=1) > cz)
            if keep.sum() >= max(5, len(s.pts) // 3):
                c, d, _ = fit_line(s.pts[keep])
                if np.dot(d, s.d) < 0:
                    d = -d
                s.c, s.d = c, d

    # -- merging --------------------------------------------------------------
    def merge(self, segs: list[_Seg], closed: bool) -> list[_Seg]:
        changed = True
        while changed and len(segs) > 1:
            changed = False
            k = 0
            while k < len(segs) - 1:
                a, b = segs[k], segs[k + 1]
                m = self._try_merge(a, b)
                if m is not None:
                    segs[k : k + 2] = [m]
                    changed = True
                else:
                    k += 1
            if closed and len(segs) > 2:
                m = self._try_merge(segs[-1], segs[0])
                if m is not None:
                    segs = [m] + segs[1:-1]
                    changed = True
        return segs

    def _try_merge(self, a: _Seg, b: _Seg) -> _Seg | None:
        p = np.vstack([a.pts, b.pts[1:]])
        kinds = [a.kind] if a.kind == b.kind else []
        if a.kind != b.kind:
            kinds = ["line", "arc"]
        for kind in kinds:
            s = self.refit(p, kind)
            if s is not None and (kind == "line" or self.curved_enough(s)):
                return s
        return None


def _two_point_line(p: np.ndarray) -> _Seg:
    return _Seg("line", p, c=p.mean(0), d=_unit(p[-1] - p[0]))


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else np.array([1.0, 0.0])


def _cross(a, b) -> float:
    return float(a[0] * b[1] - a[1] * b[0])


# ----------------------------------------------------------------------------
# Junctions (vertex placement)
# ----------------------------------------------------------------------------


def _project(s: _Seg, p: np.ndarray) -> np.ndarray:
    if s.kind == "line":
        return s.c + np.dot(p - s.c, s.d) * s.d
    v = p - s.c
    return s.c + s.r * _unit(v)


def _intersections(a: _Seg, b: _Seg) -> list[tuple[np.ndarray, float]]:
    """Intersection points and the sine of the crossing angle."""
    if a.kind == "line" and b.kind == "line":
        den = _cross(a.d, b.d)
        if abs(den) < 1e-12:
            return []
        t = _cross(b.c - a.c, b.d) / den
        return [(a.c + t * a.d, abs(den))]
    if a.kind == "arc" and b.kind == "line":
        return _intersections(b, a)
    if a.kind == "line" and b.kind == "arc":
        f = a.c - b.c
        bb = np.dot(f, a.d)
        cc = np.dot(f, f) - b.r * b.r
        disc = bb * bb - cc
        if disc < 0:
            return []
        out = []
        for t in (-bb - math.sqrt(disc), -bb + math.sqrt(disc)):
            x = a.c + t * a.d
            u = (x - b.c) / b.r
            out.append((x, abs(float(np.dot(a.d, u)))))
        return out
    # arc - arc
    dvec = b.c - a.c
    dist = float(np.linalg.norm(dvec))
    if dist < 1e-12 or dist > a.r + b.r or dist < abs(a.r - b.r):
        return []
    aa = (a.r * a.r - b.r * b.r + dist * dist) / (2 * dist)
    hh = math.sqrt(max(a.r * a.r - aa * aa, 0.0))
    e = dvec / dist
    base = a.c + aa * e
    perp = np.array([-e[1], e[0]])
    out = []
    for sgn in (-1, 1):
        x = base + sgn * hh * perp
        u1, u2 = (x - a.c) / a.r, (x - b.c) / b.r
        out.append((x, abs(_cross(u1, u2))))
    return out


_SIN_TANGENT = math.sin(math.radians(8.0))


def _junction(a: _Seg, b: _Seg, p: np.ndarray, limit: float, tol: float) -> tuple[np.ndarray, bool]:
    """Vertex between consecutive segments. Returns (point, is_sharp_corner)."""
    best = None
    for x, s in _intersections(a, b):
        if s < _SIN_TANGENT:
            continue
        dd = float(np.linalg.norm(x - p))
        if dd <= limit and (best is None or dd < best[0]):
            best = (dd, x)
    if best is not None:
        return best[1], True
    # Tangent / smooth join: use the exact tangency point. Along a tangent join
    # the data break point can slide by about sqrt(2 r tol) either way.
    if a.kind == "line" and b.kind == "line":
        return 0.5 * (_project(a, p) + _project(b, p)), False
    if a.kind == "line" or b.kind == "line":
        ln, arc = (a, b) if a.kind == "line" else (b, a)
        foot = _project(ln, arc.c)  # tangent point of a line touching the circle
        slide = limit + 2.0 * math.sqrt(2.0 * arc.r * tol)
        gap = abs(float(np.linalg.norm(foot - arc.c)) - arc.r)
        if gap <= 2.0 * tol and np.linalg.norm(foot - p) <= slide:
            return foot, False
        return _project(ln, p), False
    u = _unit(b.c - a.c)
    cands = [a.c + a.r * u, a.c - a.r * u]  # external / internal tangency
    x = min(cands, key=lambda q: float(np.linalg.norm(q - p)))
    slide = limit + 2.0 * math.sqrt(2.0 * min(a.r, b.r) * tol)
    if np.linalg.norm(x - _project(b, x)) <= 2.0 * tol and np.linalg.norm(x - p) <= slide:
        return 0.5 * (x + _project(b, x)), False
    return 0.5 * (_project(a, p) + _project(b, p)), False


def _arc_bulge(v0: np.ndarray, v1: np.ndarray, s: _Seg) -> float:
    """Bulge of the arc through the fixed end vertices that best fits the data.

    Every arc through v0 and v1 has its center on the chord's perpendicular
    bisector, so this is a 1-D least-squares problem in the center offset h.
    (Re-using the free-fit radius instead is ill-conditioned near semicircles.)
    """
    d = v1 - v0
    chord = float(np.linalg.norm(d))
    if chord < 1e-12:
        return 0.0
    mid = 0.5 * (v0 + v1)
    n = np.array([-d[1], d[0]]) / chord
    half2 = 0.25 * chord * chord
    p = s.pts

    def cost(h: float) -> float:
        ctr = mid + h * n
        return float(((np.linalg.norm(p - ctr, axis=1) - math.sqrt(half2 + h * h)) ** 2).sum())

    h0 = float(np.dot(s.c - mid, n))
    span = max(s.r, chord)
    res = minimize_scalar(cost, bounds=(h0 - span, h0 + span), method="bounded", options={"xatol": 1e-7 * span})
    h = float(res.x) if res.success and cost(res.x) <= cost(h0) else h0
    ctr = mid + h * n
    a0 = math.atan2(v0[1] - ctr[1], v0[0] - ctr[0])
    a1 = math.atan2(v1[1] - ctr[1], v1[0] - ctr[0])
    if s.ccw:
        sweep = (a1 - a0) % (2.0 * math.pi)
    else:
        sweep = -((a0 - a1) % (2.0 * math.pi))
    return math.tan(sweep / 4.0)


def bulge_arc(v0: np.ndarray, v1: np.ndarray, bulge: float):
    """Center and radius of a bulge arc."""
    chord = v1 - v0
    c = float(np.linalg.norm(chord))
    theta = 4.0 * math.atan(bulge)
    r = c / (2.0 * math.sin(abs(theta) / 2.0))
    mid = (v0 + v1) / 2.0
    n = np.array([-chord[1], chord[0]]) / c  # left normal
    # CCW arcs have their center left of the chord (right for |theta| > pi,
    # where cos(theta/2) turns negative); CW arcs mirror that.
    h = r * math.cos(theta / 2.0)
    center = mid + n * h * (1.0 if bulge > 0 else -1.0)
    return center, abs(r)


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------


def _turning(pts: np.ndarray, k: int = 3) -> np.ndarray:
    a = np.roll(pts, k, axis=0)
    b = np.roll(pts, -k, axis=0)
    v1 = pts - a
    v2 = b - pts
    ang = np.arctan2(v1[:, 0] * v2[:, 1] - v1[:, 1] * v2[:, 0], (v1 * v2).sum(1))
    return np.abs(ang)


def _start_index(pts: np.ndarray) -> int:
    """Where to open a closed contour: the middle of its longest straight run
    (a split line is re-merged exactly), else its sharpest corner."""
    n = len(pts)
    k = 5 if n > 40 else 2
    straight = _turning(pts, k) < 0.08
    if straight.all():
        return 0
    if straight.any():
        # rotate so that index 0 is not straight, then find the longest run
        off = int(np.argmin(straight))
        s = np.roll(straight, -off)
        best_len, best_mid, run = 0, 0, 0
        for i, v in enumerate(s):
            run = run + 1 if v else 0
            if run > best_len:
                best_len, best_mid = run, i - run // 2
        if best_len >= 10:
            return (best_mid + off) % n
    return int(np.argmax(_turning(pts)))


def fit_chain(points: np.ndarray, closed: bool, opts: FitOptions, free_ends: tuple[bool, bool] = (False, False)):
    """Fit a traced chain; returns CircleShape, EllipseShape or PolyShape.

    free_ends: for open chains, whether each end vertex may be moved onto the
    fitted geometry (True) or must stay exactly at the chain's end point.
    """
    tol = opts.tolerance
    pts = np.asarray(points, float)
    if closed and len(pts) >= 8:
        full = _try_full_curves(pts, opts)
        if full is not None:
            return full

    if len(pts) < 3:
        return PolyShape(pts.copy(), np.zeros(len(pts)), closed)

    fitter = _Fitter(opts)
    if closed:
        start = _start_index(pts)
        pts = np.roll(pts, -start, axis=0)
        work = np.vstack([pts, pts[:1]])
    else:
        work = pts

    segs = fitter.segment(work)
    segs = fitter.merge(segs, closed)
    fitter.refine_lines(segs)
    _snap(segs, opts)

    limit = opts.corner_limit if opts.corner_limit > 0 else max(4.0 * tol, 3.0)
    segs = _collapse_tiny(segs, closed, limit, tol)

    # Lloyd-style refinement: place vertices, hand each point to the segment on
    # its side of the vertex, refit every primitive on its core points (away
    # from the corners), and repeat.
    verts = _vertices(segs, work, closed, limit, tol, free_ends)
    best = _finish(segs, verts, closed, tol, limit, pts)
    for _ in range(3):
        _reassign(segs, verts, closed)
        _refit(segs, verts, closed, opts)
        verts = _vertices(segs, work, closed, limit, tol, free_ends)
        cand = _finish(segs, verts, closed, tol, limit, pts)
        # never let a refinement step make the result worse
        if cand.max_dev <= best.max_dev + 0.05 * tol or cand.max_dev <= tol:
            best = cand
        else:
            break
    return best


def _finish(segs, verts, closed, tol, limit, pts) -> PolyShape:
    V, B = _emit(segs, verts, closed, tol, limit)
    V, B = _drop_degenerate(V, B, closed)
    V, B = _drop_collinear(V, B, closed, tol)
    shape = PolyShape(V, B, closed)
    shape.max_dev = _poly_deviation(shape, pts)
    return shape


def _snap(segs: list[_Seg], opts: FitOptions) -> None:
    if opts.snap_ortho_deg <= 0:
        return
    lim = math.radians(opts.snap_ortho_deg)
    for s in segs:
        if s.kind != "line":
            continue
        ang = math.atan2(s.d[1], s.d[0])
        q = round(ang / (math.pi / 2)) * (math.pi / 2)
        if abs(ang - q) <= lim:
            s.d = np.array([math.cos(q), math.sin(q)])
            s.d[np.abs(s.d) < 1e-15] = 0.0


def _vertices(segs, work, closed, limit, tol, free=(False, False)) -> list[np.ndarray]:
    m = len(segs)
    if closed:
        return [_junction(segs[k - 1], segs[k], segs[k].pts[0], limit, tol)[0] for k in range(m)]
    out = [_project(segs[0], work[0]) if free[0] else work[0]]
    for k in range(1, m):
        out.append(_junction(segs[k - 1], segs[k], segs[k].pts[0], limit, tol)[0])
    out.append(_project(segs[-1], work[-1]) if free[1] else work[-1])
    return out


def _reassign(segs: list[_Seg], verts, closed: bool) -> None:
    """Move each break point to the traced point nearest its vertex."""
    m = len(segs)
    for k in range(m) if closed else range(1, m):
        if m < 2:
            return
        a, b = segs[k - 1], segs[k]
        if a is b:
            continue
        joined = np.vstack([a.pts, b.pts[1:]])
        la = len(a.pts)
        # search a window around the current break (not across the far ends)
        lo = max(1, la - 1 - max(2, (la - 1) // 2))
        hi = min(len(joined) - 2, la - 1 + max(2, (len(b.pts) - 1) // 2))
        if hi < lo:
            continue
        win = joined[lo : hi + 1]
        split = lo + int(np.argmin(np.linalg.norm(win - verts[k], axis=1)))
        a.pts, b.pts = joined[: split + 1], joined[split:]


def _core(s: _Seg, v0: np.ndarray, v1: np.ndarray, zone: float, min_pts: int) -> np.ndarray:
    p = s.pts
    keep = (np.linalg.norm(p - v0, axis=1) > zone) & (np.linalg.norm(p - v1, axis=1) > zone)
    return p[keep] if keep.sum() >= min_pts else p


def _refit(segs: list[_Seg], verts, closed: bool, opts: FitOptions) -> None:
    m = len(segs)
    zone = max(opts.corner_zone, opts.tolerance)
    for k, s in enumerate(segs):
        v0, v1 = verts[k], verts[(k + 1) % len(verts)] if closed else verts[k + 1]
        if s.kind == "line":
            core = _core(s, v0, v1, zone, 3)
            if len(core) >= 2:
                c, d, _ = fit_line(core)
                if np.dot(d, s.d) < 0:
                    d = -d
                s.c, s.d = c, d
        else:
            core = _core(s, v0, v1, zone, 6)
            if len(core) < 5:
                continue
            nbrs = []
            if closed or k > 0:
                nbrs.append(segs[k - 1])
            if closed or k < m - 1:
                nbrs.append(segs[(k + 1) % m])
            tangent = [L for L in nbrs if L.kind == "line" and L is not s and _is_tangent(L, s, opts.tolerance)]
            free = fit_circle(core)
            f = _fit_tangent_circle(core, s, tangent) if tangent else free
            if tangent and f is not None and free is not None:
                # keep the tangency only if the data agrees with it
                rms_c = _circle_rms(core, f[0], f[1])
                rms_f = _circle_rms(core, free[0], free[1])
                if rms_c > rms_f + 0.25 * opts.tolerance:
                    f = free
            if f is not None and 0.5 * s.r < f[1] < 2.0 * s.r:
                s.c, s.r = f[0], f[1]
    _snap(segs, opts)


def _circle_rms(p: np.ndarray, c: np.ndarray, r: float) -> float:
    return float(np.sqrt(np.mean((np.linalg.norm(p - c, axis=1) - r) ** 2)))


def _is_tangent(line: _Seg, arc: _Seg, tol: float) -> bool:
    n = np.array([-line.d[1], line.d[0]])
    return abs(abs(float(np.dot(arc.c - line.c, n))) - arc.r) <= 2.0 * tol


def _fit_tangent_circle(p: np.ndarray, s: _Seg, lines: list[_Seg]):
    """Circle fit constrained to stay tangent to the given lines (fillets)."""
    if not lines:
        return fit_circle(p)
    # unit normals of each line, pointing towards the arc center
    cons = []
    for L in lines:
        n = np.array([-L.d[1], L.d[0]])
        if np.dot(s.c - L.c, n) < 0:
            n = -n
        cons.append((L.c, n))

    def resid(C, r):
        return np.linalg.norm(p - C, axis=1) - r

    if len(cons) == 2 and abs(_cross(cons[0][1], cons[1][1])) > 1e-3:
        # center(r) = C0 + r * D solves n_i . (C - c_i) = r for both lines
        A = np.array([cons[0][1], cons[1][1]])
        b = np.array([np.dot(cons[0][1], cons[0][0]), np.dot(cons[1][1], cons[1][0])])
        Ainv = np.linalg.inv(A)
        C0, D = Ainv @ b, Ainv @ np.ones(2)
        res = minimize_scalar(
            lambda r: float((resid(C0 + r * D, r) ** 2).sum()),
            bounds=(0.5 * s.r, 2.0 * s.r),
            method="bounded",
            options={"xatol": 1e-7 * s.r},
        )
        r = float(res.x)
        return C0 + r * D, r
    # one tangent line (or two parallel ones): center = c + t d + r n
    c, n = cons[0]
    d = np.array([n[1], -n[0]])
    t0 = float(np.dot(s.c - c, d))
    from scipy.optimize import least_squares

    sol = least_squares(lambda x: resid(c + x[0] * d + x[1] * n, x[1]), x0=[t0, s.r], method="lm")
    t, r = sol.x
    if not np.isfinite(r) or r <= 0:
        return None
    return c + t * d + r * n, float(r)


def _emit(segs, verts, closed, tol, limit):
    """Vertices + bulges; any segment that does not match its own data within
    tolerance is replaced by a tolerance-bounded polyline (safety net)."""
    m = len(segs)
    V, B = [], []
    for k, s in enumerate(segs):
        v0 = verts[k]
        v1 = verts[(k + 1) % m] if closed else verts[k + 1]
        b = _arc_bulge(v0, v1, s) if s.kind == "arc" else 0.0
        if b and abs(b) * 0.5 * float(np.linalg.norm(v1 - v0)) < 0.25 * tol:
            b = 0.0  # sagitta below tolerance: it is a straight segment
        core = _core(s, v0, v1, limit, 3)
        if len(core) and abs(b) > 0:
            dev = float(_arc_dist(core, v0, v1, b).max())
        elif len(core):
            dev = float(_seg_dist(core, v0, v1).max())
        else:
            dev = 0.0
        if s.kind == "arc" and (dev > 2.0 * tol or abs(4.0 * math.atan(b)) > math.radians(340)):
            # try the segment as a straight line first
            ldev = float(_seg_dist(core, v0, v1).max()) if len(core) else 0.0
            if ldev <= 2.0 * tol:
                b, dev = 0.0, ldev
        if dev > 2.0 * tol:
            inner = _dp_fixed(s.pts, v0, v1, tol)
            V.append(v0)
            B.append(0.0)
            for q in inner:
                V.append(q)
                B.append(0.0)
            continue
        V.append(v0)
        B.append(b)
    if not closed:
        V.append(verts[-1])
        B.append(0.0)
    return np.asarray(V, float), np.asarray(B, float)


def _dp_fixed(p: np.ndarray, v0: np.ndarray, v1: np.ndarray, tol: float) -> list[np.ndarray]:
    """Douglas-Peucker interior vertices between fixed end vertices."""
    q = np.vstack([v0, p[1:-1], v1]) if len(p) > 2 else np.vstack([v0, v1])
    keep = np.zeros(len(q), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(q) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        d = _seg_dist(q[i + 1 : j], q[i], q[j])
        k = int(np.argmax(d))
        if d[k] > tol:
            keep[i + 1 + k] = True
            stack += [(i, i + 1 + k), (i + 1 + k, j)]
    idx = np.nonzero(keep)[0][1:-1]
    return [q[i] for i in idx]


def _collapse_tiny(segs: list[_Seg], closed: bool, limit: float, tol: float) -> list[_Seg]:
    """Remove short segments produced by rounded (blurred) corners."""
    changed = True
    while changed:
        changed = False
        m = len(segs)
        if m < (4 if closed else 3):
            break
        for k in range(m):
            if not closed and (k == 0 or k == m - 1):
                continue
            s = segs[k]
            seglen = float(np.linalg.norm(np.diff(s.pts, axis=0), axis=1).sum())
            if seglen > limit:
                continue
            a, b = segs[k - 1], segs[(k + 1) % m]
            if a is b:
                continue
            mid = s.pts[len(s.pts) // 2]
            x, sharp = _junction(a, b, mid, limit, tol)
            if not sharp:
                continue
            h = len(s.pts) // 2
            a.pts = np.vstack([a.pts, s.pts[1 : h + 1]])
            b.pts = np.vstack([s.pts[h:-1], b.pts]) if h < len(s.pts) - 1 else b.pts
            segs = segs[:k] + segs[k + 1 :]
            changed = True
            break
    return segs


def _drop_degenerate(V, B, closed):
    """Remove zero-length segments (coincident consecutive vertices)."""
    m = len(V)
    keep = np.ones(m, bool)
    for k in range(m):
        if not closed and k == m - 1:
            break
        if np.linalg.norm(V[(k + 1) % m] - V[k]) < 1e-9 and keep.sum() > 2:
            keep[k] = False
    return V[keep], B[keep]


def _drop_collinear(V, B, closed, tol):
    """Remove vertices that sit on a straight line between their neighbours."""
    changed = True
    while changed and len(V) > (3 if closed else 2):
        changed = False
        m = len(V)
        for k in range(m) if closed else range(1, m - 1):
            if B[k - 1] != 0.0 or B[k] != 0.0:
                continue
            a, v, b = V[k - 1], V[k], V[(k + 1) % m]
            if _seg_dist(v[None, :], a, b)[0] <= 0.25 * tol:
                V = np.delete(V, k, axis=0)
                B = np.delete(B, k)
                changed = True
                break
    return V, B


def shape_distance(shape: PolyShape, p: np.ndarray) -> np.ndarray:
    """Distance from each point to the nearest segment of a polyline shape."""
    V, B = shape.vertices, shape.bulges
    m = len(V)
    best = np.full(len(p), np.inf)
    for k in range(m if shape.closed else m - 1):
        v0, v1 = V[k], V[(k + 1) % m]
        d = _seg_dist(p, v0, v1) if abs(B[k]) < 1e-12 else _arc_dist(p, v0, v1, B[k])
        best = np.minimum(best, d)
    return best


def _poly_deviation(shape: PolyShape, p: np.ndarray) -> float:
    if len(p) == 0 or len(shape.vertices) < 2:
        return 0.0
    return float(shape_distance(shape, p).max())


def _arc_dist(p: np.ndarray, v0: np.ndarray, v1: np.ndarray, bulge: float) -> np.ndarray:
    c, r = bulge_arc(v0, v1, bulge)
    sweep = 4.0 * math.atan(bulge)
    a0 = math.atan2(v0[1] - c[1], v0[0] - c[0])
    ang = np.arctan2(p[:, 1] - c[1], p[:, 0] - c[0])
    rel = (ang - a0) % (2 * math.pi) if sweep > 0 else (a0 - ang) % (2 * math.pi)
    inside = rel <= abs(sweep)
    d_circ = np.abs(np.linalg.norm(p - c, axis=1) - r)
    d_end = np.minimum(np.linalg.norm(p - v0, axis=1), np.linalg.norm(p - v1, axis=1))
    return np.where(inside, d_circ, d_end)


def _seg_dist(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ab = b - a
    L2 = float(ab @ ab)
    if L2 < 1e-18:
        return np.linalg.norm(p - a, axis=1)
    t = np.clip(((p - a) @ ab) / L2, 0.0, 1.0)
    proj = a + t[:, None] * ab
    return np.linalg.norm(p - proj, axis=1)


def _try_full_curves(pts: np.ndarray, opts: FitOptions):
    tol = opts.tolerance
    if opts.detect_circles:
        f = fit_circle(pts)
        if f is not None:
            c, r, err = f
            if err <= tol and r <= opts.max_radius:
                th = _angles(pts, c)
                if abs(th[-1] - th[0]) > 1.8 * math.pi:  # really goes all the way round
                    return CircleShape(c, r, err)
    if opts.detect_ellipses and len(pts) >= 10:
        try:
            (cx, cy), (w, h), ang = cv2.fitEllipseDirect(pts.astype(np.float32))
        except cv2.error:
            return None
        if not all(np.isfinite([cx, cy, w, h, ang])) or min(w, h) <= 0:
            return None
        # refine in float64 from the robust estimate, then verify
        a, b = w / 2.0, h / 2.0
        t = math.radians(ang)
        R = np.array([[math.cos(t), math.sin(t)], [-math.sin(t), math.cos(t)]])
        q = (pts - np.array([cx, cy])) @ R.T
        rho = np.sqrt((q[:, 0] / a) ** 2 + (q[:, 1] / b) ** 2)
        radial = np.linalg.norm(q, axis=1) * np.abs(1.0 - 1.0 / np.maximum(rho, 1e-12))
        err = float(radial.max())
        if err <= tol and max(a, b) <= opts.max_radius:
            if a >= b:
                major = np.array([math.cos(t), math.sin(t)]) * a
                ratio = b / a
            else:
                major = np.array([-math.sin(t), math.cos(t)]) * b
                ratio = a / b
            if ratio > 0.999:  # effectively a circle that failed the circle test
                return None
            return EllipseShape(np.array([cx, cy]), major, ratio, err)
    return None


def end_tangent(shape: PolyShape, at_start: bool) -> tuple[np.ndarray, np.ndarray]:
    """End vertex of an open polyline and the unit direction pointing *into* it
    from the path (i.e. the direction in which the path leaves that end)."""
    V, B = shape.vertices, shape.bulges
    if at_start:
        v0, v1, b = V[0], V[1], B[0]
        half = 2.0 * math.atan(b)  # half the included angle
        chord = _unit(v1 - v0)
        rot = -half  # tangent at the start of a bulge arc
        t = np.array([chord[0] * math.cos(rot) - chord[1] * math.sin(rot), chord[0] * math.sin(rot) + chord[1] * math.cos(rot)])
        return v0, -t
    v0, v1, b = V[-2], V[-1], B[-2]
    half = 2.0 * math.atan(b)
    chord = _unit(v1 - v0)
    rot = half
    t = np.array([chord[0] * math.cos(rot) - chord[1] * math.sin(rot), chord[0] * math.sin(rot) + chord[1] * math.cos(rot)])
    return v1, t
