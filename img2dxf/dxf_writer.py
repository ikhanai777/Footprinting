"""DXF export (via ezdxf) producing clean, AutoCAD-editable drawings."""

from __future__ import annotations

import math
from dataclasses import dataclass

import ezdxf
import numpy as np
from ezdxf import units as dxf_units
from ezdxf.math import bulge_to_arc

from .fit import CircleShape, EllipseShape, PolyShape

UNIT_CODES = {
    "unitless": 0,
    "in": dxf_units.IN,
    "ft": dxf_units.FT,
    "mm": dxf_units.MM,
    "cm": dxf_units.CM,
    "m": dxf_units.M,
}

LAYERS = {
    # name: (ACI color, description)
    "OUTLINE": (7, "Outer boundaries"),
    "HOLES": (1, "Inner boundaries (holes)"),
    "CENTERLINE": (5, "Stroke centerlines"),
    "FILL": (8, "Solid fill hatches"),
}


@dataclass
class ExportItem:
    shape: object  # PolyShape | CircleShape | EllipseShape (already in drawing units)
    layer: str
    group: int = -1  # hatch group id (outer boundary index); -1 = not hatched


def transform_shape(shape, scale: float, offset: np.ndarray):
    """Scale shapes from pixel space to drawing units (bulges are scale-invariant)."""
    if isinstance(shape, PolyShape):
        return PolyShape(shape.vertices * scale + offset, shape.bulges.copy(), shape.closed, shape.max_dev * scale)
    if isinstance(shape, CircleShape):
        return CircleShape(shape.center * scale + offset, shape.radius * scale, shape.max_dev * scale)
    if isinstance(shape, EllipseShape):
        return EllipseShape(shape.center * scale + offset, shape.major_axis * scale, shape.ratio, shape.max_dev * scale)
    raise TypeError(shape)


def _r(v: float, nd: int) -> float:
    return round(float(v), nd)


def write_dxf(
    items: list[ExportItem],
    path: str,
    units: str = "mm",
    entities: str = "polyline",
    hatch: bool = False,
    version: str = "R2010",
    precision: int = 6,
) -> dict:
    doc = ezdxf.new(version, setup=True)
    doc.units = UNIT_CODES.get(units, 0)
    doc.header["$MEASUREMENT"] = 0 if units in ("in", "ft") else 1
    doc.header["$LUNITS"] = 2
    doc.header["$LUPREC"] = min(precision, 8)
    msp = doc.modelspace()

    for name, (color, desc) in LAYERS.items():
        if name not in doc.layers:
            layer = doc.layers.add(name, color=color)
            layer.description = desc

    counts = {"LWPOLYLINE": 0, "LINE": 0, "ARC": 0, "CIRCLE": 0, "ELLIPSE": 0, "HATCH": 0}
    nd = precision

    for it in items:
        attribs = {"layer": it.layer, "color": 256}  # BYLAYER
        s = it.shape
        if isinstance(s, CircleShape):
            msp.add_circle((_r(s.center[0], nd), _r(s.center[1], nd)), _r(s.radius, nd), dxfattribs=attribs)
            counts["CIRCLE"] += 1
        elif isinstance(s, EllipseShape):
            msp.add_ellipse(
                (_r(s.center[0], nd), _r(s.center[1], nd)),
                major_axis=(_r(s.major_axis[0], nd), _r(s.major_axis[1], nd)),
                ratio=float(s.ratio),
                dxfattribs=attribs,
            )
            counts["ELLIPSE"] += 1
        elif isinstance(s, PolyShape):
            if len(s.vertices) < 2:
                continue
            if entities == "polyline":
                pts = [(_r(v[0], nd), _r(v[1], nd), 0.0, 0.0, float(b)) for v, b in zip(s.vertices, s.bulges)]
                msp.add_lwpolyline(pts, format="xyseb", close=s.closed, dxfattribs=attribs)
                counts["LWPOLYLINE"] += 1
            else:
                m = len(s.vertices)
                nseg = m if s.closed else m - 1
                for k in range(nseg):
                    v0 = tuple(_r(c, nd) for c in s.vertices[k])
                    v1 = tuple(_r(c, nd) for c in s.vertices[(k + 1) % m])
                    b = float(s.bulges[k])
                    if abs(b) < 1e-12:
                        msp.add_line(v0, v1, dxfattribs=attribs)
                        counts["LINE"] += 1
                    else:
                        center, a0, a1, r = bulge_to_arc(v0, v1, b)
                        msp.add_arc(
                            (center.x, center.y), r, math.degrees(a0), math.degrees(a1), dxfattribs=attribs
                        )
                        counts["ARC"] += 1

    if hatch:
        groups: dict[int, list] = {}
        for it in items:
            if it.group >= 0:
                groups.setdefault(it.group, []).append(it.shape)
        for shapes in groups.values():
            h = msp.add_hatch(color=256, dxfattribs={"layer": "FILL"})
            h.set_solid_fill(color=256)
            h.dxf.hatch_style = 0  # normal (odd parity) - holes stay empty
            for s in shapes:
                _add_hatch_path(h, s)
            counts["HATCH"] += 1

    # Make AutoCAD open zoomed to the drawing.
    try:
        from ezdxf import zoom

        zoom.extents(msp, factor=1.05)
    except Exception:
        pass

    auditor = doc.audit()
    doc.saveas(path)
    return {"entities": counts, "audit_errors": len(auditor.errors), "audit_fixes": len(auditor.fixes)}


def _add_hatch_path(h, s) -> None:
    if isinstance(s, PolyShape) and s.closed and len(s.vertices) >= 2:
        h.paths.add_polyline_path([(float(v[0]), float(v[1]), float(b)) for v, b in zip(s.vertices, s.bulges)], is_closed=True)
    elif isinstance(s, CircleShape):
        ep = h.paths.add_edge_path()
        ep.add_arc((float(s.center[0]), float(s.center[1])), float(s.radius), 0.0, 360.0)
    elif isinstance(s, EllipseShape):
        ep = h.paths.add_edge_path()
        ep.add_ellipse(
            (float(s.center[0]), float(s.center[1])),
            (float(s.major_axis[0]), float(s.major_axis[1])),
            float(s.ratio),
            0.0,
            360.0,
        )
