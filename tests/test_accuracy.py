"""Accuracy and DXF-validity tests against synthetic images of known geometry."""

import math
import os
import subprocess
import sys

import cv2
import ezdxf
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from synth import SS, canvas, fill_circle, fill_ellipse, fill_poly, finish, rot_rect, slot  # noqa: E402

from img2dxf import Options, convert  # noqa: E402


def _rrect(x0, y0, x1, y1, r, n=90):
    out = []
    for cx, cy, a0 in [(x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)]:
        a = np.radians(np.linspace(a0, a0 + 90, n))
        out.append(np.column_stack([cx + r * np.cos(a), cy + r * np.sin(a)]))
    return np.vstack(out)


def _run(tmp_path, img, name="img.png", **kw):
    src = str(tmp_path / name)
    cv2.imwrite(src, img)
    out = str(tmp_path / (name + ".dxf"))
    res = convert(src, out, Options(**kw))
    doc = ezdxf.readfile(out)
    auditor = doc.audit()
    assert len(auditor.errors) == 0
    return res, doc


def _entities(doc, kind):
    return [e for e in doc.modelspace() if e.dxftype() == kind]


def _match_vertices(got, want, tol):
    """Every expected vertex has a produced vertex within tol (and counts match)."""
    got = np.asarray(got)
    assert len(got) == len(want), (got, want)
    for w in want:
        d = np.linalg.norm(got - np.asarray(w), axis=1).min()
        assert d <= tol, (w, got, d)


def test_circle_center_and_radius(tmp_path):
    W, H = 300, 300
    img = canvas(W, H)
    fill_circle(img, (150.3, 140.7), 100.4, H)
    _, doc = _run(tmp_path, finish(img))
    circles = _entities(doc, "CIRCLE")
    assert len(circles) == 1 and len(list(doc.modelspace())) == 1
    c = circles[0]
    assert abs(c.dxf.center.x - 150.3) < 0.1
    assert abs(c.dxf.center.y - 140.7) < 0.1
    assert abs(c.dxf.radius - 100.4) < 0.1


def test_rotated_rectangle_sharp_corners(tmp_path):
    W, H = 300, 220
    img = canvas(W, H)
    corners = rot_rect(150, 110, 180, 90, 23.0)
    fill_poly(img, corners, H)
    _, doc = _run(tmp_path, finish(img))
    polys = _entities(doc, "LWPOLYLINE")
    assert len(polys) == 1
    p = polys[0]
    assert p.closed
    pts = [(x, y) for x, y, b in p.get_points("xyb")]
    assert all(b == 0 for *_, b in p.get_points("xyb"))
    _match_vertices(pts, corners, 0.25)


def test_filleted_plate_with_holes(tmp_path):
    W, H = 340, 300
    img = canvas(W, H)
    fill_poly(img, _rrect(40, 40, 300, 260, 20), H)
    for c in [(80, 80), (260, 80), (80, 220), (260, 220)]:
        fill_circle(img, c, 10, H, 255)
    _, doc = _run(tmp_path, finish(img))

    holes = [e for e in _entities(doc, "CIRCLE") if e.dxf.layer == "HOLES"]
    assert len(holes) == 4
    for h in holes:
        assert abs(h.dxf.radius - 10) < 0.15

    outer = [e for e in _entities(doc, "LWPOLYLINE") if e.dxf.layer == "OUTLINE"]
    assert len(outer) == 1
    pts = list(outer[0].get_points("xyb"))
    assert len(pts) == 8
    bulges = sorted(abs(b) for *_, b in pts)
    assert bulges[:4] == [0, 0, 0, 0]
    for b in bulges[4:]:
        assert abs(b - math.tan(math.radians(22.5))) < 0.01  # exact 90 degree fillets
    want = [(60, 40), (280, 40), (300, 60), (300, 240), (280, 260), (60, 260), (40, 240), (40, 60)]
    _match_vertices([(x, y) for x, y, _ in pts], want, 0.3)


def test_slot_semicircles(tmp_path):
    W, H = 260, 140
    img = canvas(W, H)
    fill_poly(img, slot(130, 70, 120, 30), H)
    _, doc = _run(tmp_path, finish(img))
    (p,) = _entities(doc, "LWPOLYLINE")
    pts = list(p.get_points("xyb"))
    assert len(pts) == 4
    assert sorted(round(abs(b), 2) for *_, b in pts) == [0, 0, 1.0, 1.0]
    _match_vertices([(x, y) for x, y, _ in pts], [(70, 40), (190, 40), (190, 100), (70, 100)], 0.3)


def test_ellipse(tmp_path):
    W, H = 300, 200
    img = canvas(W, H)
    fill_ellipse(img, (150, 100), (110, 50), 30, H)
    _, doc = _run(tmp_path, finish(img))
    (e,) = _entities(doc, "ELLIPSE")
    assert abs(e.dxf.center.x - 150) < 0.15 and abs(e.dxf.center.y - 100) < 0.15
    major = np.array([e.dxf.major_axis.x, e.dxf.major_axis.y])
    assert abs(np.linalg.norm(major) - 110) < 0.2
    assert abs(e.dxf.ratio - 50 / 110) < 0.005
    ang = math.degrees(math.atan2(major[1], major[0])) % 180
    assert abs(ang - 30) < 0.3


def test_hard_edged_binary_image(tmp_path):
    """Non-anti-aliased input (pure 0/255) must still produce exact geometry."""
    W, H = 340, 300
    img = canvas(W, H)
    fill_poly(img, _rrect(40, 40, 300, 260, 20), H)
    binary = np.where(finish(img) < 128, 0, 255).astype(np.uint8)
    _, doc = _run(tmp_path, binary)
    (p,) = _entities(doc, "LWPOLYLINE")
    pts = list(p.get_points("xyb"))
    assert len(pts) == 8
    for b in sorted(abs(b) for *_, b in pts)[4:]:
        assert abs(b - math.tan(math.radians(22.5))) < 0.02


def test_light_on_dark_polarity(tmp_path):
    W, H = 200, 200
    img = canvas(W, H)
    fill_circle(img, (100, 100), 60, H)
    inverted = 255 - finish(img)
    _, doc = _run(tmp_path, inverted)
    (c,) = _entities(doc, "CIRCLE")
    assert abs(c.dxf.radius - 60) < 0.1


def test_scaling_width_and_units(tmp_path):
    W, H = 300, 220
    img = canvas(W, H)
    fill_poly(img, rot_rect(150, 110, 200, 100, 0), H)
    res, doc = _run(tmp_path, finish(img), width=50.0, units="mm", origin="geometry")
    (p,) = _entities(doc, "LWPOLYLINE")
    xy = np.array([(x, y) for x, y, _ in p.get_points("xyb")])
    assert abs((xy[:, 0].max() - xy[:, 0].min()) - 50.0) < 1e-6
    assert abs((xy[:, 1].max() - xy[:, 1].min()) - 25.0) < 0.05
    assert abs(xy.min(0)).max() < 1e-6
    assert doc.header["$INSUNITS"] == 4  # millimetres


def test_dpi_scaling(tmp_path):
    W, H = 300, 300
    img = canvas(W, H)
    fill_circle(img, (150, 150), 100, H)
    res, doc = _run(tmp_path, finish(img), dpi=254.0, units="mm")
    (c,) = _entities(doc, "CIRCLE")
    assert abs(c.dxf.radius - 10.0) < 0.01  # 100 px at 254 dpi = 10 mm


def test_primitives_are_connected(tmp_path):
    W, H = 340, 300
    img = canvas(W, H)
    fill_poly(img, _rrect(40, 40, 300, 260, 20), H)
    fill_poly(img, rot_rect(170, 150, 80, 50, 10), H, 255)
    _, doc = _run(tmp_path, finish(img), entities="primitives")
    ends = []
    for e in doc.modelspace():
        if e.dxftype() == "LINE":
            ends += [tuple(e.dxf.start)[:2], tuple(e.dxf.end)[:2]]
        elif e.dxftype() == "ARC":
            ends += [tuple(e.start_point)[:2], tuple(e.end_point)[:2]]
    assert _entities(doc, "LWPOLYLINE") == []
    assert len(_entities(doc, "ARC")) == 4 and len(_entities(doc, "LINE")) == 8
    ends = np.array(ends)
    # every endpoint coincides with exactly one other endpoint (closed chains)
    for q in ends:
        n = (np.linalg.norm(ends - q, axis=1) < 1e-5).sum()
        assert n == 2


def test_hatch_output(tmp_path):
    W, H = 200, 200
    img = canvas(W, H)
    fill_circle(img, (100, 100), 70, H)
    fill_circle(img, (100, 100), 30, H, 255)
    _, doc = _run(tmp_path, finish(img), hatch=True)
    (h,) = _entities(doc, "HATCH")
    assert len(h.paths) == 2


def _stroke(img, p0, p1, H, width=4.0):
    d = np.subtract(p1, p0) / np.linalg.norm(np.subtract(p1, p0))
    n = np.array([-d[1], d[0]]) * width / 2
    fill_poly(img, [p0 + n, p1 + n, p1 - n, p0 - n], H)


def test_centerline_t_junction(tmp_path):
    W, H = 300, 200
    img = canvas(W, H)
    _stroke(img, np.array([40.0, 60.0]), np.array([260.0, 60.0]), H)
    _stroke(img, np.array([150.0, 60.0]), np.array([150.0, 170.0]), H)
    _, doc = _run(tmp_path, finish(img), mode="centerline")
    polys = _entities(doc, "LWPOLYLINE")
    assert all(e.dxf.layer == "CENTERLINE" for e in polys)
    ends = []
    for p in polys:
        pts = [(x, y) for x, y, _ in p.get_points("xyb")]
        ends += [pts[0], pts[-1]]
        # every path is straight: 2 vertices
        assert len(pts) == 2
    ends = np.array(ends)
    junction = [q for q in ends if (np.linalg.norm(ends - q, axis=1) < 1e-6).sum() == 3]
    assert junction, ends
    assert np.linalg.norm(np.array(junction[0]) - (150, 60)) < 0.6


def test_cli(tmp_path):
    W, H = 200, 200
    img = canvas(W, H)
    fill_circle(img, (100, 100), 70, H)
    src = str(tmp_path / "c.png")
    cv2.imwrite(src, finish(img))
    out = str(tmp_path / "c.dxf")
    root = os.path.dirname(os.path.dirname(__file__))
    r = subprocess.run(
        [sys.executable, "-m", "img2dxf", src, "-o", out, "--dpi", "25.4", "--preview"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert os.path.exists(out) and os.path.exists(str(tmp_path / "c.preview.png"))
    (c,) = [e for e in ezdxf.readfile(out).modelspace() if e.dxftype() == "CIRCLE"]
    assert abs(c.dxf.radius - 70) < 0.1


@pytest.mark.parametrize("version", ["R2000", "R2004", "R2007", "R2010", "R2013", "R2018"])
def test_dxf_versions(tmp_path, version):
    W, H = 200, 200
    img = canvas(W, H)
    fill_poly(img, _rrect(30, 30, 170, 170, 15), H)
    _, doc = _run(tmp_path, finish(img), dxf_version=version)
    assert doc.dxfversion == ezdxf.const.acad_release_to_dxf_version[version]


def test_uneven_illumination(tmp_path):
    """Strong shading gradient + large solid region: no phantom contours."""
    W, H = 340, 300
    img = canvas(W, H)
    fill_poly(img, _rrect(40, 40, 300, 260, 20), H)
    fill_circle(img, (170, 150), 30, H, 255)
    im = finish(img).astype(float)
    shade = np.linspace(0.5, 1.0, W)[None, :] * np.linspace(0.8, 1.0, H)[:, None]
    shaded = np.clip(im * shade, 0, 255).astype(np.uint8)
    _, doc = _run(tmp_path, shaded, threshold="adaptive")
    assert len(list(doc.modelspace())) == 2
    (c,) = _entities(doc, "CIRCLE")
    assert abs(c.dxf.radius - 30) < 0.25
    (p,) = _entities(doc, "LWPOLYLINE")
    assert len(p) == 8
