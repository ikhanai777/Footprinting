"""High level image -> DXF conversion pipeline."""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

import cv2
import numpy as np

from .dxf_writer import ExportItem, transform_shape, write_dxf
from .fit import CircleShape, EllipseShape, FitOptions, PolyShape, bulge_arc, end_tangent, fit_chain
from .preprocess import build_field, load_gray
from .trace import trace_centerlines, trace_outlines

INCH_IN = {"mm": 25.4, "cm": 2.54, "m": 0.0254, "in": 1.0, "ft": 1.0 / 12.0, "unitless": 1.0}


@dataclass
class Options:
    mode: str = "outline"  # "outline" | "centerline"
    tolerance: float | None = None  # px; default 0.5 (outline) / 1.0 (centerline)
    # scaling (at most one of scale / dpi / width / height)
    scale: float | None = None  # drawing units per pixel
    dpi: float | None = None
    width: float | None = None  # target width of the traced geometry, drawing units
    height: float | None = None
    use_image_dpi: bool = False
    units: str = "mm"
    origin: str = "image"  # "image" (image bottom-left = 0,0) | "geometry" (geometry min corner = 0,0)
    # preprocessing
    threshold: str | float = "otsu"
    foreground: str = "auto"
    blur: float = 0.8
    denoise: float = 0.0
    min_area: float = 16.0
    # fitting
    circles: bool = True
    ellipses: bool = True
    arcs: bool = True
    snap_ortho: float = 0.0
    smooth: float = 1.5  # centerline smoothing sigma (samples)
    # output
    entities: str = "polyline"  # "polyline" | "primitives"
    hatch: bool = False
    dxf_version: str = "R2010"
    preview: str | None = None
    jobs: int | None = None  # worker processes for fitting (None = all cores)


@dataclass
class Result:
    output: str
    scale: float
    units: str
    shapes: int
    max_deviation_px: float
    p95_deviation_px: float
    stats: dict = field(default_factory=dict)

    def summary(self) -> str:
        e = self.stats.get("entities", {})
        ent = ", ".join(f"{k}={v}" for k, v in e.items() if v)
        return (
            f"Wrote {self.output}\n"
            f"  shapes: {self.shapes}  ({ent})\n"
            f"  scale: {self.scale:.6g} {self.units}/px\n"
            f"  fit deviation vs. traced edge: max {self.max_deviation_px:.3f} px "
            f"({self.max_deviation_px * self.scale:.4g} {self.units}), "
            f"95% {self.p95_deviation_px:.3f} px\n"
            f"  DXF audit: {self.stats.get('audit_errors', 0)} errors"
        )


def convert(input_path: str, output_path: str, opts: Options | None = None) -> Result:
    opts = opts or Options()
    gray, img_dpi = load_gray(input_path)
    fld = build_field(
        gray,
        threshold=opts.threshold,
        foreground=opts.foreground,
        blur=opts.blur,
        denoise=opts.denoise,
        min_area=opts.min_area,
        dpi=img_dpi,
    )

    tol = opts.tolerance if opts.tolerance is not None else (0.5 if opts.mode == "outline" else 1.0)
    fopts = FitOptions(
        tolerance=tol,
        detect_circles=opts.circles,
        detect_ellipses=opts.ellipses,
        arcs=opts.arcs,
        snap_ortho_deg=opts.snap_ortho,
        max_radius=5.0 * float(np.hypot(fld.width, fld.height)),
    )

    fitted = []  # (shape_px, layer, group)
    if opts.mode == "centerline":
        chains, junctions, stroke_r = trace_centerlines(fld, smooth=opts.smooth)
        # corners of a stroke centerline are rounded over about one stroke width
        fopts.corner_zone = max(fopts.corner_zone, 2.0 * stroke_r + 1.0)
        fopts.corner_limit = max(4.0 * fopts.tolerance, 3.0 * stroke_r + 2.0)
        shapes = _fit_centerlines(chains, junctions, fopts)
        fitted = [(s, "CENTERLINE", -1) for s in shapes]
    elif opts.mode == "outline":
        chains = trace_outlines(fld, min_area=opts.min_area)
        shapes = _fit_many([(ch.points, ch.closed) for ch in chains], fopts, opts.jobs)
        for idx, (ch, shape) in enumerate(zip(chains, shapes)):
            layer = "OUTLINE" if ch.depth % 2 == 0 else "HOLES"
            group = idx if ch.depth % 2 == 0 else ch.parent
            fitted.append((shape, layer, group))
    else:
        raise ValueError(f"unknown mode {opts.mode!r}")

    scale = _resolve_scale(opts, fitted, img_dpi)
    offset = _resolve_offset(opts, fitted, scale)
    items = [ExportItem(transform_shape(s, scale, offset), layer, group) for s, layer, group in fitted]

    stats = write_dxf(
        items,
        output_path,
        units=opts.units,
        entities=opts.entities,
        hatch=opts.hatch and opts.mode == "outline",
        version=opts.dxf_version,
    )

    devs = np.array([s.max_dev for s, _, _ in fitted]) if fitted else np.zeros(1)
    if opts.preview:
        render_preview(gray, [s for s, _, _ in fitted], opts.preview)

    return Result(
        output=output_path,
        scale=scale,
        units=opts.units,
        shapes=len(fitted),
        max_deviation_px=float(devs.max()),
        p95_deviation_px=float(np.percentile(devs, 95)),
        stats=stats,
    )


def _fit_one(args):
    pts, closed, fopts = args
    return fit_chain(pts, closed, fopts)


def _fit_many(chains: list, fopts: FitOptions, jobs: int | None) -> list:
    """Fit independent chains, in parallel worker processes when worthwhile."""
    jobs = (os.cpu_count() or 1) if jobs is None or jobs <= 0 else jobs
    total = sum(len(p) for p, _ in chains)
    if jobs <= 1 or len(chains) < 8 or total < 20000:
        return [fit_chain(p, c, fopts) for p, c in chains]
    # largest first for better load balance, then restore the order
    order = sorted(range(len(chains)), key=lambda k: -len(chains[k][0]))
    try:
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            res = list(ex.map(_fit_one, [(chains[k][0], chains[k][1], fopts) for k in order], chunksize=4))
    except (OSError, RuntimeError):  # no multiprocessing available: run serially
        return [fit_chain(p, c, fopts) for p, c in chains]
    out = [None] * len(chains)
    for k, r in zip(order, res):
        out[k] = r
    return out


def _fit_centerlines(chains, junctions, fopts: FitOptions) -> list:
    """Fit stroke paths, then rebuild every junction as the least-squares
    intersection of the strokes meeting there (the skeleton itself is
    distorted near junctions, so those points are not used for fitting)."""
    shapes = []
    ends: dict[int, list[tuple[int, bool]]] = {}
    for ch in chains:
        pts = ch.points
        if not ch.closed:
            # Skeleton ends are unreliable: near junctions (handled below) and
            # at free stroke ends, where they tend to hook sideways.
            tip = fopts.corner_zone
            pts = _trim_near(pts, junctions.get(ch.start_node), from_start=True, tip=tip)
            pts = _trim_near(pts, junctions.get(ch.end_node), from_start=False, tip=tip)
        shape = fit_chain(pts, ch.closed, fopts, free_ends=(True, True) if not ch.closed else (False, False))
        if isinstance(shape, PolyShape) and not shape.closed and len(shape.vertices) >= 2:
            for at_start, node, orig in ((True, ch.start_node, ch.points[0]), (False, ch.end_node, ch.points[-1])):
                if node in junctions:
                    ends.setdefault(node, []).append((len(shapes), at_start))
                else:  # free end: extend the fitted end out to where the stroke ends
                    v, d = end_tangent(shape, at_start)
                    ext = float(np.dot(orig - v, d))
                    if ext > 0:
                        shape.vertices[0 if at_start else -1] = v + ext * d
        shapes.append(shape)

    for lab, inc in ends.items():
        J = junctions[lab]
        M = np.zeros((2, 2))
        rhs = np.zeros(2)
        for si, at_start in inc:
            p, d = end_tangent(shapes[si], at_start)
            P = np.eye(2) - np.outer(d, d)
            M += P
            rhs += P @ p
        x = J.position
        if np.linalg.cond(M) < 1e6:
            cand = np.linalg.solve(M, rhs)
            if np.linalg.norm(cand - J.position) <= 2.0 * J.radius:
                x = cand
        for si, at_start in inc:
            shapes[si].vertices[0 if at_start else -1] = x
    return shapes


def _trim_near(pts: np.ndarray, J, from_start: bool, tip: float = 0.0) -> np.ndarray:
    if len(pts) < 4:
        return pts
    if J is None:  # free end: drop the last `tip` px
        if tip <= 0:
            return pts
        anchor, radius = pts[0] if from_start else pts[-1], tip
    else:
        anchor, radius = J.position, J.radius
    d = np.linalg.norm(pts - anchor, axis=1)
    far = d > radius
    if far.sum() < 3:
        return pts
    if from_start:
        return pts[int(np.argmax(far)) :]
    return pts[: len(pts) - int(np.argmax(far[::-1]))]


def _bounds(shapes) -> tuple[np.ndarray, np.ndarray]:
    pts = []
    for s in shapes:
        if isinstance(s, PolyShape):
            pts.append(sample_shape(s))
        elif isinstance(s, CircleShape):
            pts.append(np.array([s.center - s.radius, s.center + s.radius]))
        elif isinstance(s, EllipseShape):
            pts.append(sample_shape(s))
    if not pts:
        return np.zeros(2), np.ones(2)
    allp = np.vstack(pts)
    return allp.min(0), allp.max(0)


def _resolve_scale(opts: Options, fitted, img_dpi) -> float:
    given = [x is not None for x in (opts.scale, opts.dpi, opts.width, opts.height)]
    if sum(given) > 1:
        raise ValueError("use only one of scale / dpi / width / height")
    if opts.scale is not None:
        return float(opts.scale)
    dpi = opts.dpi
    if dpi is None and opts.use_image_dpi and img_dpi:
        dpi = img_dpi[0]
    if dpi is not None:
        return INCH_IN[opts.units] / float(dpi)
    if opts.width is not None or opts.height is not None:
        lo, hi = _bounds([s for s, _, _ in fitted])
        size = hi - lo
        if opts.width is not None:
            return float(opts.width) / max(size[0], 1e-9)
        return float(opts.height) / max(size[1], 1e-9)
    return 1.0


def _resolve_offset(opts: Options, fitted, scale: float) -> np.ndarray:
    if opts.origin == "geometry":
        lo, _ = _bounds([s for s, _, _ in fitted])
        return -lo * scale
    return np.zeros(2)


def sample_shape(s, step: float = 1.0) -> np.ndarray:
    """Dense points along a shape (pixel space), for previews / bounds."""
    if isinstance(s, CircleShape):
        n = max(32, int(2 * np.pi * s.radius / step))
        t = np.linspace(0, 2 * np.pi, n)
        return s.center + s.radius * np.column_stack([np.cos(t), np.sin(t)])
    if isinstance(s, EllipseShape):
        a = float(np.linalg.norm(s.major_axis))
        n = max(32, int(2 * np.pi * a / step))
        t = np.linspace(0, 2 * np.pi, n)
        u = s.major_axis
        v = np.array([-u[1], u[0]]) * s.ratio
        return s.center + np.outer(np.cos(t), u) + np.outer(np.sin(t), v)
    V, B = s.vertices, s.bulges
    m = len(V)
    out = []
    nseg = m if s.closed else m - 1
    for k in range(nseg):
        v0, v1 = V[k], V[(k + 1) % m]
        if abs(B[k]) < 1e-12:
            out.append(np.array([v0, v1]))
        else:
            c, r = bulge_arc(v0, v1, B[k])
            a0 = np.arctan2(v0[1] - c[1], v0[0] - c[0])
            sweep = 4 * np.arctan(B[k])
            n = max(4, int(abs(sweep) * r / step))
            t = a0 + np.linspace(0, sweep, n)
            out.append(c + r * np.column_stack([np.cos(t), np.sin(t)]))
    if not out:
        return V
    return np.vstack(out)


def render_preview(gray: np.ndarray, shapes, path: str, zoom: int = 2) -> None:
    """Overlay the fitted geometry (red) and vertices (blue) on the source image."""
    h, w = gray.shape
    base = cv2.cvtColor(np.clip(gray * 255, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    base = (base * 0.45 + 140).astype(np.uint8)
    img = cv2.resize(base, (w * zoom, h * zoom), interpolation=cv2.INTER_NEAREST)
    shift = 4
    f = zoom * (1 << shift)

    def to_px(p):
        return np.column_stack([p[:, 0] * f, (h - p[:, 1]) * f]).round().astype(np.int32)

    for s in shapes:
        pts = sample_shape(s, step=0.5)
        closed = not isinstance(s, PolyShape) or s.closed
        cv2.polylines(img, [to_px(pts).reshape(-1, 1, 2)], closed, (0, 0, 220), 1, cv2.LINE_AA, shift)
        if isinstance(s, PolyShape):
            for v in to_px(s.vertices):
                cv2.circle(img, tuple(int(c) for c in v), 2 * (1 << shift), (220, 90, 0), 1, cv2.LINE_AA, shift)
    cv2.imwrite(path, img)
