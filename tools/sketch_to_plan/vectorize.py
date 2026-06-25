"""
Sketch-to-polygon vectorizer for landscape-architecture concept sketches.

Usage:
    python vectorize.py <input.pdf|png> [--rotate 90] [--out DIR] [--min-area N] [--dpi N]

Rotation convention:
    --rotate 90  applies a 90-degree CLOCKWISE rotation using cv2.ROTATE_90_CLOCKWISE.
    --rotate 180 = 180 degrees (same both directions).
    --rotate 270 applies 90-degree COUNTER-CLOCKWISE (270 clockwise = 90 ccw).
    The test sketch "Bezirkstermin 240403 Skizze.pdf" needs --rotate 90 to be plan-upright.

Output (all in --out dir):
    polygons.json   -- detected polygon contours per class, pixel coords origin top-left
    overlay.png     -- rasterized+rotated image with coloured contour outlines drawn
    to_vwx.py       -- script that reads polygons.json and writes a build.vwxscript.py
                       (Vectorworks vs.* Python snippet, not executed here)
"""

import argparse
import json
import os
import sys

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# HSV colour ranges (OpenCV: H 0-179, S 0-255, V 0-255)
# Tuned against "Bezirkstermin 240403 Skizze.pdf" felt-tip marker sketch.
# ---------------------------------------------------------------------------
HSV_CLASSES = {
    # Stream / water — cyan-blue marker, H peaks at 97-99
    "water": {
        "ranges": [
            (np.array([88, 60, 60]), np.array([110, 255, 255])),
        ],
        "morph_open": 3,
        "morph_close": 7,
        "draw_color": (255, 100, 0),   # BGR: orange-ish for visibility on blue
        "overlay_color": (0, 180, 255),  # BGR: amber outline
    },
    # Bright zig-zag meadow — light green, H 45-85, V > 160, S moderate
    "meadow": {
        "ranges": [
            (np.array([42, 35, 160]), np.array([90, 180, 255])),
        ],
        "morph_open": 3,
        "morph_close": 9,
        "draw_color": (0, 220, 80),
        "overlay_color": (0, 255, 80),  # bright green
    },
    # Dark scribbled tree blobs — darker green, H 40-90, V < 160 OR S > 150
    "tree": {
        "ranges": [
            (np.array([40, 60, 10]), np.array([90, 255, 159])),
            # high-saturation darker green pixels
            (np.array([55, 150, 100]), np.array([90, 255, 200])),
        ],
        "morph_open": 3,
        "morph_close": 11,
        "draw_color": (0, 100, 0),
        "overlay_color": (0, 140, 0),  # dark green
    },
    # Paths — orange/tan marker, H 8-22, S > 90, V > 90
    "path": {
        "ranges": [
            (np.array([8, 90, 90]), np.array([22, 255, 255])),
        ],
        "morph_open": 3,
        "morph_close": 9,
        "draw_color": (0, 80, 200),
        "overlay_color": (0, 100, 255),  # red-orange outline
    },
    # Sand band — yellow, H 22-40, S > 30, V > 150
    "sand": {
        "ranges": [
            (np.array([20, 30, 150]), np.array([40, 210, 255])),
        ],
        "morph_open": 3,
        "morph_close": 9,
        "draw_color": (0, 200, 220),
        "overlay_color": (0, 220, 255),  # yellow-ish outline
    },
    # Building combs + outlines — dark strokes, low V and/or nearly achromatic dark
    "buildings": {
        "ranges": [
            (np.array([0,   0,  0]), np.array([179,  60,  70])),
            # also catch dark coloured ink (e.g. dark purple tones on building combs)
            (np.array([0,   0,  0]), np.array([179, 255,  50])),
        ],
        "morph_open": 1,
        "morph_close": 3,
        "draw_color": (200, 0, 200),
        "overlay_color": (255, 0, 220),  # magenta outline
    },
}

MIN_AREA_DEFAULT = 500   # px² — drop contours smaller than this
APPROX_EPSILON_FRAC = 0.01   # fraction of perimeter for approxPolyDP


def rasterize_pdf(pdf_path: str, dpi: int = 200) -> np.ndarray:
    """Return BGR ndarray of page 0 at ~dpi."""
    try:
        import fitz
    except ImportError:
        sys.exit("PyMuPDF (fitz) is required for PDF input. pip install pymupdf")
    doc = fitz.open(pdf_path)
    page = doc[0]
    scale = dpi / 72.0
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def rotate_image(img: np.ndarray, degrees: int) -> np.ndarray:
    """Rotate clockwise by degrees (90/180/270)."""
    rot_map = {
        90:  cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE,  # 270 cw = 90 ccw
    }
    if degrees == 0:
        return img
    if degrees not in rot_map:
        sys.exit(f"--rotate must be 0, 90, 180, or 270; got {degrees}")
    return cv2.rotate(img, rot_map[degrees])


def mask_for_class(hsv: np.ndarray, class_def: dict) -> np.ndarray:
    """Build a binary mask by OR-ing all HSV ranges for a class."""
    mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for lo, hi in class_def["ranges"]:
        mask |= cv2.inRange(hsv, lo, hi)
    # Morphological cleanup
    kopen = class_def.get("morph_open", 3)
    kclose = class_def.get("morph_close", 7)
    if kopen > 0:
        el = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kopen, kopen))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, el)
    if kclose > 0:
        el = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kclose, kclose))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, el)
    return mask


def contours_for_mask(mask: np.ndarray, min_area: int) -> list[list[list[int]]]:
    """Return list of simplified polygon point-lists [[x,y], ...]."""
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    polys = []
    for cnt in cnts:
        area = cv2.contourArea(cnt)
        if area < min_area:
            continue
        eps = APPROX_EPSILON_FRAC * cv2.arcLength(cnt, closed=True)
        approx = cv2.approxPolyDP(cnt, eps, closed=True)
        pts = approx.squeeze().tolist()
        if isinstance(pts[0], int):
            # single point, degenerate
            continue
        polys.append(pts)
    return polys


def vectorize(
    input_path: str,
    rotate_deg: int = 0,
    out_dir: str = "output",
    min_area: int = MIN_AREA_DEFAULT,
    dpi: int = 200,
) -> dict:
    """Run full pipeline; return the polygons dict."""
    os.makedirs(out_dir, exist_ok=True)

    # 1. Load / rasterize
    ext = os.path.splitext(input_path)[1].lower()
    if ext == ".pdf":
        img_bgr = rasterize_pdf(input_path, dpi=dpi)
    else:
        img_bgr = cv2.imread(input_path)
        if img_bgr is None:
            sys.exit(f"Cannot read image: {input_path}")

    # 2. Rotate
    img_bgr = rotate_image(img_bgr, rotate_deg)
    h, w = img_bgr.shape[:2]
    print(f"Image size after rotate: {w}x{h}")

    # 3. Convert to HSV
    img_hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)

    # 4. Segment each class
    overlay = img_bgr.copy()
    classes_out: dict[str, list] = {}

    for cls_name, cls_def in HSV_CLASSES.items():
        mask = mask_for_class(img_hsv, cls_def)
        polys = contours_for_mask(mask, min_area)
        classes_out[cls_name] = polys
        print(f"  {cls_name}: {len(polys)} polygons")

        # Draw on overlay
        raw_cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        color = cls_def["overlay_color"]
        for cnt in raw_cnts:
            if cv2.contourArea(cnt) >= min_area:
                cv2.drawContours(overlay, [cnt], -1, color, 2)

    # 5. Save outputs
    # polygons.json
    result = {
        "meta": {
            "image_size": [w, h],
            "rotation_deg": rotate_deg,
            "source": os.path.abspath(input_path),
            # To convert pixel coords to world (Y-up):
            #   world_x = px_x * scale_x
            #   world_y = (img_height - px_y) * scale_y
            # where scale_x = world_width / img_width, scale_y = world_height / img_height
            "coord_note": (
                "Pixel origin is top-left. To flip to world Y-up: "
                "world_x = px_x * (world_width/img_width), "
                "world_y = (img_height - px_y) * (world_height/img_height)"
            ),
        },
        "classes": classes_out,
    }
    json_path = os.path.join(out_dir, "polygons.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"Saved {json_path}")

    # overlay.png
    overlay_path = os.path.join(out_dir, "overlay.png")
    cv2.imwrite(overlay_path, overlay)
    print(f"Saved {overlay_path}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Sketch-to-polygon vectorizer for LA concept sketches")
    parser.add_argument("input", help="Input PDF or PNG file")
    parser.add_argument("--rotate", type=int, default=0,
                        help="Clockwise rotation in degrees: 0, 90, 180, 270")
    parser.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "output"),
                        help="Output directory (default: tools/sketch_to_plan/output/)")
    parser.add_argument("--min-area", type=int, default=MIN_AREA_DEFAULT,
                        help=f"Minimum contour area in pixels² to keep (default: {MIN_AREA_DEFAULT})")
    parser.add_argument("--dpi", type=int, default=200,
                        help="DPI for PDF rasterization (default: 200)")
    args = parser.parse_args()

    print(f"Input:    {args.input}")
    print(f"Rotate:   {args.rotate}° clockwise")
    print(f"Out dir:  {args.out}")
    print(f"Min area: {args.min_area} px²")

    vectorize(
        input_path=args.input,
        rotate_deg=args.rotate,
        out_dir=args.out,
        min_area=args.min_area,
        dpi=args.dpi,
    )
    print("Done.")


if __name__ == "__main__":
    main()
