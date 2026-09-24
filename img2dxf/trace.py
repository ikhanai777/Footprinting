"""Tracing: sub-pixel outlines and skeleton centerlines.

All returned coordinates are in *pixel units with Y pointing up* (CAD
convention).  The image occupies the rectangle [0, W] x [0, H], so pixel
(row r, col c) covers x in [c, c+1] and y in [H-r-1, H-r].
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter1d, map_coordinates
from skimage import measure
from skimage.morphology import skeletonize

from .preprocess import Field


@dataclass
class Contour:
    points: np.ndarray  # (N, 2) float64, Y-up px; closed contours do not repeat the first point
    closed: bool
    area: float = 0.0
    depth: int = 0
    parent: int = -1
    children: list[int] = dc_field(default_factory=list)
    start_node: int = -1  # centerline mode: junction ids at the path ends (-1 = free end)
    end_node: int = -1


@dataclass
class Junction:
    position: np.ndarray  # Y-up px
    degree: int
    radius: float  # region around the junction where the skeleton is unreliable


def _to_world(rc: np.ndarray, height: int, offset: float) -> np.ndarray:
    """(row, col) sample coordinates -> Y-up (x, y) with pixel-corner origin."""
    x = rc[:, 1] - offset + 0.5
    y = height - (rc[:, 0] - offset + 0.5)
    return np.column_stack([x, y]).astype(np.float64)


def _dedupe(pts: np.ndarray, closed: bool, eps: float = 1e-6) -> np.ndarray:
    if len(pts) < 2:
        return pts
    keep = np.ones(len(pts), bool)
    keep[1:] = np.linalg.norm(np.diff(pts, axis=0), axis=1) > eps
    pts = pts[keep]
    if closed and len(pts) > 1 and np.linalg.norm(pts[0] - pts[-1]) <= eps:
        pts = pts[:-1]
    return pts


def signed_area(pts: np.ndarray) -> float:
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def trace_outlines(field: Field, min_area: float = 16.0) -> list[Contour]:
    """Sub-pixel boundary contours of all foreground regions, with nesting."""
    pad = 2
    lo = float(min(field.values.min(), field.level - 1.0))
    padded = np.pad(field.values, pad, mode="constant", constant_values=lo)
    raw = measure.find_contours(padded, field.level)

    contours: list[Contour] = []
    for rc in raw:
        pts = _to_world(rc, field.height, pad)
        pts = _dedupe(pts, closed=True)
        if len(pts) < 3:
            continue
        a = signed_area(pts)
        if abs(a) < min_area:
            continue
        contours.append(Contour(points=pts, closed=True, area=abs(a)))

    _compute_nesting(contours)
    return contours


def _compute_nesting(contours: list[Contour]) -> None:
    """Assign parent/depth by geometric containment (smallest enclosing contour)."""
    order = sorted(range(len(contours)), key=lambda k: contours[k].area)
    boxes = [(c.points.min(0), c.points.max(0)) for c in contours]
    polys = [c.points.astype(np.float32).reshape(-1, 1, 2) for c in contours]

    for idx_pos, k in enumerate(order):
        probe = contours[k].points[0]
        lo_k, hi_k = boxes[k]
        for m in order[idx_pos + 1 :]:  # only larger contours can contain k
            lo_m, hi_m = boxes[m]
            if np.any(lo_k < lo_m) or np.any(hi_k > hi_m):
                continue
            if cv2.pointPolygonTest(polys[m], (float(probe[0]), float(probe[1])), False) > 0:
                contours[k].parent = m
                contours[m].children.append(k)
                break

    def depth(k: int) -> int:
        d = 0
        while contours[k].parent >= 0:
            k = contours[k].parent
            d += 1
        return d

    for k, c in enumerate(contours):
        c.depth = depth(k)
    # Orientation convention: outer boundaries CCW, holes CW.
    for c in contours:
        ccw = signed_area(c.points) > 0
        if ccw != (c.depth % 2 == 0):
            c.points = c.points[::-1].copy()


# ----------------------------------------------------------------------------
# Centerline (skeleton) tracing
# ----------------------------------------------------------------------------

_NB = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def trace_centerlines(
    field: Field, smooth: float = 1.5, prune: float | None = None
) -> tuple[list[Contour], dict[int, Junction], float]:
    """Single-line centerlines of strokes (for line drawings / sketches).

    Returns the paths, the junctions (degree >= 3 nodes) they meet at, and the
    median stroke half-width in px.
    """
    mask = field.mask
    skel = skeletonize(mask)
    skel = np.pad(skel, 1).astype(bool)
    h = field.height

    nbcount = cv2.filter2D(skel.astype(np.uint8), -1, np.ones((3, 3), np.float32), borderType=cv2.BORDER_CONSTANT)
    nbcount = nbcount.astype(np.int32) - skel.astype(np.int32)
    node = skel & (nbcount != 2)
    n_lab, node_lab, _, cxy = cv2.connectedComponentsWithStats(node.astype(np.uint8), connectivity=8)
    centroids = cxy[:, ::-1].astype(np.float64)  # (row, col)

    visited = np.zeros_like(skel)
    paths: list[tuple[list, int, int]] = []  # (list of (r, c)), start node label, end node label

    def walk(start_lab: int, r0: int, c0: int, prev: tuple[int, int]):
        pts = [tuple(centroids[start_lab]), (r0, c0)]
        visited[r0, c0] = True
        cur = (r0, c0)
        steps = 0
        while True:
            steps += 1
            nxt = None
            node_hits = []
            for dr, dc in _NB:
                q = (cur[0] + dr, cur[1] + dc)
                if q == prev or not skel[q]:
                    continue
                if node[q]:
                    node_hits.append(node_lab[q])
                elif not visited[q]:
                    nxt = q
                    break
            if nxt is not None:
                visited[nxt] = True
                pts.append(nxt)
                prev, cur = cur, nxt
                continue
            if node_hits:
                end = node_hits[0]
                if steps < 3:
                    others = [l for l in node_hits if l != start_lab]
                    if others:
                        end = others[0]
                pts.append(tuple(centroids[end]))
                return pts, end
            return pts, -1

    node_pixels = np.argwhere(node)
    for r, c in node_pixels:
        lab = node_lab[r, c]
        for dr, dc in _NB:
            q = (r + dr, c + dc)
            if skel[q] and not node[q] and not visited[q]:
                pts, end = walk(lab, q[0], q[1], (r, c))
                paths.append((pts, lab, end))

    # Node clusters that touch another node cluster directly (short bridges)
    # need no path; clusters are 8-connected so this cannot happen.

    # Isolated loops (no nodes at all)
    closed_loops = []
    for r, c in np.argwhere(skel & ~node & ~visited):
        if visited[r, c]:
            continue
        loop = [(r, c)]
        visited[r, c] = True
        cur, prev = (r, c), None
        while True:
            nxt = None
            for dr, dc in _NB:
                q = (cur[0] + dr, cur[1] + dc)
                if q != prev and skel[q] and not visited[q]:
                    nxt = q
                    break
            if nxt is None:
                break
            visited[nxt] = True
            loop.append(nxt)
            prev, cur = cur, nxt
        if len(loop) >= 8:
            closed_loops.append(loop)

    # --- prune short spurs (skeleton artifacts at stroke ends/corners)
    degree = np.zeros(n_lab, int)
    for _, a, b in paths:
        degree[a] += 1
        if b >= 0:
            degree[b] += 1
    dist = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5)
    radii = dist[skel[1:-1, 1:-1]]
    stroke_r = float(np.median(radii)) if len(radii) else 1.0
    if prune is None:
        # spurs at a corner of angle a are ~ r / sin(a / 2) long
        prune = 3.5 * stroke_r + 2.0
    kept = []
    for p in paths:
        pts, a, b = p
        length = _poly_len(np.asarray(pts, float))
        is_spur = (degree[a] == 1 and b >= 0 and degree[b] >= 3) or (b >= 0 and degree[b] == 1 and degree[a] >= 3)
        if is_spur and length < prune:
            degree[a] -= 1
            if b >= 0:
                degree[b] -= 1
            continue
        kept.append(p)
    paths = kept

    # --- join paths through nodes that now have exactly two incident ends
    paths, loops2 = _join_through_degree2(paths)

    degree = np.zeros(n_lab, int)
    for _, a, b in paths:
        degree[a] += 1
        if b >= 0:
            degree[b] += 1
    junctions: dict[int, Junction] = {}
    for lab in range(1, n_lab):
        if degree[lab] >= 3:
            pos = _to_world(centroids[lab][None, :], h, 1)[0]
            junctions[lab] = Junction(pos, int(degree[lab]), 2.0 * stroke_r + 1.5)

    out: list[Contour] = []
    for pts, a, b in paths:
        arr = np.asarray(pts, float)
        arr = _dedupe(_to_world(arr, h, 1), closed=False)
        if len(arr) < 2:
            continue
        arr = _smooth(arr, smooth, closed=False)
        arr = _recenter(arr, field, stroke_r, closed=False)
        c = Contour(points=_smooth(arr, 1.0, closed=False), closed=False)
        c.start_node = a if a in junctions else -1
        c.end_node = b if b in junctions else -1
        out.append(c)
    for loop in closed_loops + loops2:
        arr = np.asarray(loop, float)
        arr = _dedupe(_to_world(arr, h, 1), closed=True)
        if len(arr) < 4:
            continue
        arr = _recenter(_smooth(arr, smooth, closed=True), field, stroke_r, closed=True)
        out.append(Contour(points=_smooth(arr, 1.0, closed=True), closed=True))
    return out, junctions, stroke_r


def _recenter(pts: np.ndarray, field: Field, stroke_r: float, closed: bool) -> np.ndarray:
    """Move centerline points to the exact middle of the stroke.

    A skeleton lies on pixel centers, which is up to half a pixel off for
    even stroke widths.  For each point, the stroke profile is sampled along
    the local normal from the continuous field; the point is moved to the
    midpoint of the two sub-pixel edge crossings.  Points whose profile does
    not look like a normal stroke cross-section (junctions, ends) are kept.
    """
    n = len(pts)
    if n < 3:
        return pts
    tang = np.gradient(pts, axis=0)
    if closed:
        tang = np.roll(pts, -1, axis=0) - np.roll(pts, 1, axis=0)
    norm = np.linalg.norm(tang, axis=1, keepdims=True)
    tang = tang / np.maximum(norm, 1e-12)
    nrm = np.column_stack([-tang[:, 1], tang[:, 0]])

    reach = 2.0 * stroke_r + 3.0
    dt = 0.25
    t = np.arange(-reach, reach + dt / 2, dt)
    c0 = len(t) // 2
    samp = pts[:, None, :] + t[None, :, None] * nrm[:, None, :]
    cols = samp[..., 0] - 0.5
    rows = field.height - samp[..., 1] - 0.5
    v = map_coordinates(field.values, [rows.ravel(), cols.ravel()], order=1, mode="nearest").reshape(rows.shape)
    lvl = field.level
    inside = v > lvl

    ok = inside[:, c0].copy()
    left_out = ~inside[:, :c0]
    has_l = left_out.any(axis=1)
    i = c0 - 1 - np.argmax(left_out[:, ::-1], axis=1)  # last outside sample left of center
    right_out = ~inside[:, c0 + 1 :]
    has_r = right_out.any(axis=1)
    j = c0 + 1 + np.argmax(right_out, axis=1)  # first outside sample right of center
    ok &= has_l & has_r

    rows_idx = np.arange(n)
    i1 = np.minimum(i + 1, len(t) - 1)
    j0 = np.maximum(j - 1, 0)
    vi, vi1 = v[rows_idx, i], v[rows_idx, i1]
    vj0, vj = v[rows_idx, j0], v[rows_idx, j]
    tl = t[i] + dt * (lvl - vi) / np.where(np.abs(vi1 - vi) > 1e-12, vi1 - vi, 1.0)
    tr = t[j0] + dt * (vj0 - lvl) / np.where(np.abs(vj0 - vj) > 1e-12, vj0 - vj, 1.0)
    width = tr - tl
    ok &= np.abs(width - 2.0 * stroke_r) <= stroke_r + 1.5
    shift = np.where(ok, 0.5 * (tl + tr), 0.0)
    shift = np.clip(shift, -1.0, 1.0)
    out = pts + shift[:, None] * nrm
    if not closed:
        out[0], out[-1] = pts[0], pts[-1]
    return out


def _poly_len(p: np.ndarray) -> float:
    return float(np.linalg.norm(np.diff(p, axis=0), axis=1).sum()) if len(p) > 1 else 0.0


def _join_through_degree2(paths):
    paths = [[list(p[0]), p[1], p[2]] for p in paths]
    loops = []
    changed = True
    while changed:
        changed = False
        ends: dict[int, list[tuple[int, int]]] = {}
        for i, (pts, a, b) in enumerate(paths):
            if pts is None:
                continue
            ends.setdefault(a, []).append((i, 0))
            if b >= 0:
                ends.setdefault(b, []).append((i, 1))
        for lab, inc in ends.items():
            if len(inc) != 2:
                continue
            (i, ei), (j, ej) = inc
            if i == j:  # a path whose both ends meet at this node -> closed loop
                loops.append(paths[i][0][:-1])
                paths[i][0] = None
                changed = True
                break
            pi, pj = paths[i], paths[j]
            seq_i = pi[0] if ei == 1 else pi[0][::-1]
            end_i_other = pi[1] if ei == 1 else pi[2]
            seq_j = pj[0] if ej == 0 else pj[0][::-1]
            end_j_other = pj[2] if ej == 0 else pj[1]
            paths[i] = [seq_i + seq_j[1:], end_i_other, end_j_other]
            paths[j][0] = None
            changed = True
            break
        paths = [p for p in paths if p[0] is not None]
    return [(p[0], p[1], p[2]) for p in paths], loops


def _smooth(pts: np.ndarray, sigma: float, closed: bool) -> np.ndarray:
    if sigma <= 0 or len(pts) < 5:
        return pts
    if closed:
        return gaussian_filter1d(pts, sigma, axis=0, mode="wrap")
    sm = gaussian_filter1d(pts, sigma, axis=0, mode="nearest")
    sm[0], sm[-1] = pts[0], pts[-1]  # endpoints are shared with other paths: keep exact
    return sm
