---
name: img2dxf
description: Convert raster images (PNG/JPG/TIFF/BMP) into clean, editable, accurately scaled DXF drawings for AutoCAD using the locally installed img2dxf CLI. Use when the user wants an image, scan, logo, part photo or sketch turned into DXF/CAD geometry.
---

# img2dxf: image to DXF

`img2dxf` is installed in `~/img2dxf/.venv`. Run it as `~/img2dxf/.venv/bin/img2dxf`
(on Windows: `%USERPROFILE%\img2dxf\.venv\Scripts\img2dxf.exe`), or as `img2dxf` if it is on PATH.

## Procedure
1. Ask for the input image, if you don't have it, and the real size. Get one of: the scan DPI
   (`--dpi`), the real width or height of the drawn geometry (`--width` / `--height`), or units per
   pixel (`--scale`). Default units are mm (`--units`).
2. Pick the mode. Use `outline` (default) for filled shapes, parts, logos and silhouettes. Use
   `--mode centerline` for pen or line drawings where the user wants single lines.
3. For photos or uneven lighting, add `--threshold adaptive`. For noise or JPEG artifacts, add `--denoise 10`.
4. Run with `--preview`, for example
   `img2dxf part.jpg -o part.dxf --width 120 --preview`.
5. Check the report: `DXF audit: 0 errors` is required. Tell the user the entity counts, the
   scale and the max fit deviation. Give them the `.dxf` path and the `.preview.png` path.
6. If the preview shows missing detail, lower the tolerance (`-t 0.3`). If it shows too many
   small segments, raise it (`-t 0.8`) or add `--denoise`. For axis-aligned drawings, add
   `--snap-ortho 1`. If the drawing is light on dark and auto-detection fails, force
   `--foreground light`.

## Output facts
- Closed LWPOLYLINEs with true arc segments, plus CIRCLE and ELLIPSE entities.
- Layers: OUTLINE, HOLES, CENTERLINE, FILL (`--hatch`).
- `--entities primitives` writes separate LINE/ARC entities.
- DXF R2010 by default (`--dxf-version R2000..R2018`), with units set in the header.
