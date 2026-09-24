"""Command line interface: ``img2dxf input.png -o output.dxf``."""

from __future__ import annotations

import argparse
import os
import sys

from .converter import Options, convert


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="img2dxf",
        description="Convert raster images (PNG/JPG/BMP/TIFF...) into clean, editable DXF drawings "
        "made of true lines, arcs, circles and ellipses.",
    )
    p.add_argument("input", nargs="+", help="input image(s)")
    p.add_argument("-o", "--output", help="output .dxf (or a directory when converting several images)")

    g = p.add_argument_group("tracing")
    g.add_argument("--mode", choices=["outline", "centerline"], default="outline",
                   help="outline: boundaries of filled shapes (default); centerline: single lines along strokes")
    g.add_argument("-t", "--tolerance", type=float, default=None,
                   help="max deviation of fitted geometry from the traced edge, in pixels "
                        "(default 0.5 outline / 1.0 centerline). Smaller = more accurate, more segments")
    g.add_argument("--threshold", default="otsu",
                   help="'otsu' (default), 'adaptive' (uneven lighting / photos) or a value 0-255")
    g.add_argument("--foreground", choices=["auto", "dark", "light"], default="auto",
                   help="which side of the threshold is the drawing (default: auto from image border)")
    g.add_argument("--blur", type=float, default=0.8, help="edge smoothing sigma in px (default 0.8, 0 = off)")
    g.add_argument("--denoise", type=float, default=0.0, help="non-local-means denoise strength for photos (e.g. 10)")
    g.add_argument("--min-area", type=float, default=16.0, help="ignore specks/holes smaller than this (px^2)")
    g.add_argument("--smooth", type=float, default=1.5, help="centerline smoothing (samples)")

    g = p.add_argument_group("geometry")
    g.add_argument("--no-circles", action="store_true", help="do not emit CIRCLE entities")
    g.add_argument("--no-ellipses", action="store_true", help="do not emit ELLIPSE entities")
    g.add_argument("--no-arcs", action="store_true", help="lines only (no arc segments)")
    g.add_argument("--snap-ortho", type=float, default=0.0, metavar="DEG",
                   help="snap lines within DEG of horizontal/vertical exactly onto the axis (e.g. 1.0)")

    g = p.add_argument_group("scale & units")
    s = g.add_mutually_exclusive_group()
    s.add_argument("--scale", type=float, help="drawing units per pixel")
    s.add_argument("--dpi", type=float, help="scan resolution; 1 inch = DPI pixels")
    s.add_argument("--width", type=float, help="real width of the traced geometry (drawing units)")
    s.add_argument("--height", type=float, help="real height of the traced geometry (drawing units)")
    s.add_argument("--image-dpi", action="store_true", help="use the DPI stored in the image file")
    g.add_argument("--units", choices=["mm", "cm", "m", "in", "ft", "unitless"], default="mm")
    g.add_argument("--origin", choices=["image", "geometry"], default="image",
                   help="(0,0) at the image's bottom-left corner (default) or at the geometry's min corner")

    g = p.add_argument_group("output")
    g.add_argument("--entities", choices=["polyline", "primitives"], default="polyline",
                   help="polyline: one closed LWPOLYLINE per contour (default); "
                        "primitives: individual LINE/ARC entities")
    g.add_argument("--hatch", action="store_true", help="add solid HATCH fills for filled regions (layer FILL)")
    g.add_argument("--dxf-version", default="R2010", choices=["R2000", "R2004", "R2007", "R2010", "R2013", "R2018"])
    g.add_argument("-j", "--jobs", type=int, default=None, help="worker processes (default: all CPU cores)")
    g.add_argument("--preview", nargs="?", const="", default=None,
                   help="also write a PNG overlay of the result (default: <output>.preview.png)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    threshold: str | float = args.threshold
    if threshold not in ("otsu", "adaptive"):
        try:
            threshold = float(threshold)
        except ValueError:
            print(f"invalid --threshold {args.threshold!r}", file=sys.stderr)
            return 2

    multi = len(args.input) > 1
    if multi and args.output and not os.path.isdir(args.output):
        os.makedirs(args.output, exist_ok=True)

    rc = 0
    for inp in args.input:
        stem = os.path.splitext(os.path.basename(inp))[0]
        if multi:
            out = os.path.join(args.output or os.path.dirname(inp) or ".", stem + ".dxf")
        else:
            out = args.output or os.path.splitext(inp)[0] + ".dxf"
        preview = None
        if args.preview is not None:
            preview = args.preview if (args.preview and not multi) else os.path.splitext(out)[0] + ".preview.png"

        opts = Options(
            mode=args.mode,
            tolerance=args.tolerance,
            scale=args.scale,
            dpi=args.dpi,
            width=args.width,
            height=args.height,
            use_image_dpi=args.image_dpi,
            units=args.units,
            origin=args.origin,
            threshold=threshold,
            foreground=args.foreground,
            blur=args.blur,
            denoise=args.denoise,
            min_area=args.min_area,
            circles=not args.no_circles,
            ellipses=not args.no_ellipses,
            arcs=not args.no_arcs,
            snap_ortho=args.snap_ortho,
            smooth=args.smooth,
            entities=args.entities,
            hatch=args.hatch,
            dxf_version=args.dxf_version,
            preview=preview,
            jobs=args.jobs,
        )
        try:
            res = convert(inp, out, opts)
        except Exception as exc:  # keep going in batch mode
            print(f"error: {inp}: {exc}", file=sys.stderr)
            rc = 1
            continue
        print(res.summary())
        if preview:
            print(f"  preview: {preview}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
