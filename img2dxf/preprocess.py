"""Image loading and conversion to a continuous "foreground field".

Instead of producing a hard binary mask, the preprocessing stage produces a
float image (the *field*) together with an iso-level.  Pixels whose field value
is above the level are foreground.  Tracing the iso-contour of this continuous
field with marching squares gives sub-pixel accurate edges, which is what makes
the downstream geometry accurate (anti-aliased edges are located to a fraction
of a pixel instead of being snapped to the pixel grid).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Field:
    values: np.ndarray  # float32, H x W; foreground where values > level
    level: float
    width: int
    height: int
    dpi: tuple[float, float] | None = None

    @property
    def mask(self) -> np.ndarray:
        return self.values > self.level


def load_gray(path: str) -> tuple[np.ndarray, tuple[float, float] | None]:
    """Load any image as float32 grayscale in [0, 1] (alpha composited on white)."""
    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"Could not read image: {path}")

    if img.dtype == np.uint16:
        img = img.astype(np.float32) / 65535.0
    elif img.dtype == np.uint8:
        img = img.astype(np.float32) / 255.0
    else:
        img = img.astype(np.float32)
        rng = float(img.max() - img.min()) or 1.0
        img = (img - img.min()) / rng

    if img.ndim == 3:
        if img.shape[2] == 4:
            alpha = img[:, :, 3:4]
            img = img[:, :, :3] * alpha + (1.0 - alpha)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img

    dpi = None
    try:  # DPI metadata is optional; Pillow is only used for reading it.
        from PIL import Image

        with Image.open(path) as im:
            if "dpi" in im.info:
                d = im.info["dpi"]
                dpi = (float(d[0]), float(d[1]))
    except Exception:
        pass
    return gray.astype(np.float32), dpi


def _otsu(gray: np.ndarray) -> float:
    """Iso-level for edge tracing: midway between the ink and paper intensities.

    Otsu separates the two classes; the true edge of an anti-aliased (or
    blurred) boundary lies at 50% between the class levels, which is where the
    contour is placed.  Class medians keep this robust to noise.
    """
    u8 = np.clip(gray * 255.0 + 0.5, 0, 255).astype(np.uint8)
    t, _ = cv2.threshold(u8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    lo = u8[u8 <= t]
    hi = u8[u8 > t]
    if len(lo) == 0 or len(hi) == 0:
        return (float(t) + 0.5) / 255.0
    return 0.5 * (float(np.median(lo)) + float(np.median(hi))) / 255.0


def _background_is_dark(gray: np.ndarray, t: float) -> bool:
    """Decide polarity from the image border, which is almost always background."""
    border = np.concatenate([gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]])
    return float(np.median(border)) < t


def build_field(
    gray: np.ndarray,
    threshold: str | float = "otsu",
    foreground: str = "auto",
    blur: float = 0.8,
    denoise: float = 0.0,
    min_area: float = 16.0,
    min_hole_area: float | None = None,
    dpi: tuple[float, float] | None = None,
) -> Field:
    """Turn a grayscale image into a foreground field + iso level.

    threshold: "otsu", "adaptive" (uneven lighting: flatten the illumination
               first) or a number in 0..255.
    foreground: "auto", "dark" or "light".
    blur: Gaussian sigma (px) applied to the field; suppresses pixel staircase
          and noise while keeping straight edges unbiased.
    denoise: strength of non-local-means denoising (0 = off), for photos/scans.
    min_area / min_hole_area: despeckle - remove foreground blobs / fill holes
          smaller than this many square pixels.
    """
    h, w = gray.shape
    if denoise > 0:
        u8 = np.clip(gray * 255.0 + 0.5, 0, 255).astype(np.uint8)
        u8 = cv2.fastNlMeansDenoising(u8, None, h=float(denoise), templateWindowSize=7, searchWindowSize=21)
        gray = u8.astype(np.float32) / 255.0

    if isinstance(threshold, str) and threshold == "adaptive":
        gray = flatten_illumination(gray)
        threshold = "otsu"
    if isinstance(threshold, str) and threshold != "otsu":
        threshold = float(threshold)
    t = _otsu(gray) if threshold == "otsu" else float(threshold) / 255.0
    bg_dark = _background_is_dark(gray, t)
    fg_dark = _resolve_fg_dark(foreground, bg_dark)
    values = (1.0 - gray) if fg_dark else gray.copy()
    level = (1.0 - t) if fg_dark else t

    values = values.astype(np.float32)
    if blur and blur > 0:
        values = cv2.GaussianBlur(values, (0, 0), float(blur))

    values = _despeckle(values, level, min_area, min_hole_area if min_hole_area is not None else min_area)
    return Field(values=values, level=level, width=w, height=h, dpi=dpi)


def flatten_illumination(gray: np.ndarray, degree: int = 3, iters: int = 4) -> np.ndarray:
    """Remove smooth illumination gradients (shading, vignetting, uneven scans).

    A low-order 2-D polynomial is fitted to the pixels currently classified as
    background, the image is divided by it, and the classification is redone.
    Unlike local-mean thresholding this does not break inside large solid areas.
    """
    h, w = gray.shape
    f = max(1, int(max(h, w) / 400))
    small = cv2.resize(gray, (max(1, w // f), max(1, h // f)), interpolation=cv2.INTER_AREA)
    sh, sw = small.shape
    yy, xx = np.mgrid[0:sh, 0:sw]
    X, Y = xx.ravel() / max(sw - 1, 1) * 2 - 1, yy.ravel() / max(sh - 1, 1) * 2 - 1
    terms = [(i, j) for i in range(degree + 1) for j in range(degree + 1 - i)]
    A = np.column_stack([X**i * Y**j for i, j in terms])
    v = small.ravel().astype(np.float64)
    t = _otsu(small)
    bg_dark = _background_is_dark(small, t)
    bg = (v <= t) if bg_dark else (v > t)
    coef = None
    for _ in range(iters):
        if bg.sum() < len(terms) * 4:
            break
        coef, *_ = np.linalg.lstsq(A[bg], v[bg], rcond=None)
        surf = A @ coef
        norm = v / np.maximum(surf, 1e-3)
        t2 = _otsu(np.clip(norm, 0, 1).reshape(sh, sw).astype(np.float32))
        bg = (norm <= t2) if bg_dark else (norm > t2)
    if coef is None:
        return gray
    yy, xx = np.mgrid[0:h, 0:w]
    Xf, Yf = xx / max(w - 1, 1) * 2 - 1, yy / max(h - 1, 1) * 2 - 1
    surf = sum(c * Xf**i * Yf**j for c, (i, j) in zip(coef, terms))
    if bg_dark:  # dark background: flatten relative to the dark level instead
        return np.clip(gray - surf + float(np.median(v[bg])), 0, 1).astype(np.float32)
    return np.clip(gray / np.maximum(surf, 1e-3) * float(np.median(surf)), 0, 1).astype(np.float32)


def _resolve_fg_dark(foreground: str, bg_dark: bool) -> bool:
    if foreground == "dark":
        return True
    if foreground == "light":
        return False
    return not bg_dark


def _despeckle(values: np.ndarray, level: float, min_area: float, min_hole_area: float) -> np.ndarray:
    lo = float(min(values.min(), level - 1e-3))
    hi = float(max(values.max(), level + 1e-3))
    mask = (values > level).astype(np.uint8)

    if min_area > 0:
        n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        small = np.where(stats[1:, cv2.CC_STAT_AREA] < min_area)[0] + 1
        if len(small):
            kill = np.isin(labels, small)
            kill = cv2.dilate(kill.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
            values = values.copy()
            values[kill & (values > level - 0.5 * (level - lo))] = lo

    if min_hole_area > 0:
        inv = (values <= level).astype(np.uint8)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=4)
        border = set(np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])))
        small = [k for k in range(1, n) if stats[k, cv2.CC_STAT_AREA] < min_hole_area and k not in border]
        if small:
            fill = np.isin(labels, small)
            fill = cv2.dilate(fill.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
            values = values.copy()
            values[fill & (values < level + 0.5 * (hi - level))] = hi
    return values
