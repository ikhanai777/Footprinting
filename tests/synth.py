"""Synthetic test images with exactly known geometry (anti-aliased by supersampling)."""

import cv2
import numpy as np

SS = 8  # supersampling factor


def canvas(w, h):
    return np.full((h * SS, w * SS), 255, np.uint8)


def finish(img):
    h, w = img.shape
    return cv2.resize(img, (w // SS, h // SS), interpolation=cv2.INTER_AREA)


def P(pts):
    """World Y-up pixel coords (x in [0,W], y in [0,H]) -> supersampled image coords."""
    return pts


def to_ss(pts, H):
    pts = np.asarray(pts, float)
    x = pts[:, 0] * SS - 0.5
    y = (H - pts[:, 1]) * SS - 0.5
    return np.column_stack([x, y])


def fill_poly(img, pts, H, color=0):
    q = (to_ss(pts, H) * 16).round().astype(np.int32)
    cv2.fillPoly(img, [q.reshape(-1, 1, 2)], color, cv2.LINE_8, 4)


def fill_circle(img, c, r, H, color=0):
    q = (to_ss([c], H)[0] * 16).round().astype(int)
    cv2.circle(img, (int(q[0]), int(q[1])), int(round(r * SS * 16)), color, -1, cv2.LINE_8, 4)


def fill_ellipse(img, c, axes, angle_deg, H, color=0):
    # angle in world (Y-up, CCW) -> image (Y-down) is negated
    q = (to_ss([c], H)[0] * 16).round().astype(int)
    ax = (int(round(axes[0] * SS * 16)), int(round(axes[1] * SS * 16)))
    cv2.ellipse(img, (int(q[0]), int(q[1])), ax, -angle_deg, 0, 360, color, -1, cv2.LINE_8, 4)


def rot_rect(cx, cy, w, h, ang_deg):
    t = np.radians(ang_deg)
    R = np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
    base = np.array([[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]])
    return base @ R.T + [cx, cy]


def slot(cx, cy, length, r, n=720):
    """Stadium: two semicircles joined by straight lines (horizontal)."""
    a = np.linspace(-np.pi / 2, np.pi / 2, n // 2)
    right = np.column_stack([cx + length / 2 + r * np.cos(a), cy + r * np.sin(a)])
    left = np.column_stack([cx - length / 2 - r * np.cos(a), cy - r * np.sin(a)])
    return np.vstack([right, left])
