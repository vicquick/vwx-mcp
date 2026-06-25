# sketch_to_plan — Offline Sketch Vectorizer

Converts felt-tip landscape-architecture concept sketches (PDF or PNG) into polygon
data per colour class, ready for visual QA and Vectorworks import.

## Quick start

```bash
# PDF input, rotate 90° clockwise to be plan-upright, output to default dir
python tools/sketch_to_plan/vectorize.py input.pdf --rotate 90

# PNG input, explicit output directory
python tools/sketch_to_plan/vectorize.py sketch.png --out /tmp/myout

# Also generate VW import script from the polygons
python tools/sketch_to_plan/to_vwx.py --world-w 44 --world-h 26
```

Tested with Python 3.12, opencv-python 4.10, numpy, PyMuPDF 1.x.

## Dependencies

| Package | Used for |
|---------|----------|
| `cv2` (opencv-python) | HSV segmentation, morphology, contours |
| `numpy` | Array ops |
| `fitz` (PyMuPDF) | PDF rasterization |

No extra installs needed if the above are present.

## CLI reference

### vectorize.py

```
python vectorize.py <input> [--rotate DEG] [--out DIR] [--min-area N] [--dpi N]
```

| Arg | Default | Description |
|-----|---------|-------------|
| `input` | (required) | PDF or PNG file |
| `--rotate` | 0 | Clockwise rotation in degrees: 0, 90, 180, 270 |
| `--out` | `tools/sketch_to_plan/output/` | Output directory |
| `--min-area` | 500 | Drop contours smaller than N px² |
| `--dpi` | 200 | DPI for PDF rasterization (page 1 only) |

### to_vwx.py

```
python to_vwx.py [--json polygons.json] [--world-w 44] [--world-h 26]
                  [--origin-x 0] [--origin-y 0] [--out-script build.vwxscript.py]
```

Reads `polygons.json` and writes a Vectorworks `vs.*` Python script. Paste the
generated `build.vwxscript.py` into the VW Script palette, or pass it as the
string argument to the `vwx-mcp` `execute_script` tool.

## Rotation convention

`--rotate 90` applies `cv2.ROTATE_90_CLOCKWISE`.
`--rotate 270` applies `cv2.ROTATE_90_COUNTERCLOCKWISE` (= 270° clockwise).
`--rotate 180` rotates 180°.

The test sketch `Bezirkstermin 240403 Skizze.pdf` is stored portrait (3306×2337 px
after 200 DPI rasterization) but the plan north is left. `--rotate 90` makes it
landscape / plan-upright.

## HSV colour ranges

OpenCV HSV: H 0–179, S 0–255, V 0–255.
Tuned against "Bezirkstermin 240403 Skizze.pdf" (felt-tip marker on white).

| Class | H min | H max | S min | S max | V min | V max | Notes |
|-------|-------|-------|-------|-------|-------|-------|-------|
| water | 88 | 110 | 60 | 255 | 60 | 255 | Cyan-blue stream, peaks at H=97–99 |
| meadow | 42 | 90 | 35 | 180 | 160 | 255 | Bright zig-zag marker, V>160 |
| tree | 40 | 90 | 60 | 255 | 10 | 159 | Darker scribbled blobs, V≤159 |
| tree (hi-sat) | 55 | 90 | 150 | 255 | 100 | 200 | High-sat dark-mid-V green |
| path | 8 | 22 | 90 | 255 | 90 | 255 | Orange/tan diagonal |
| sand | 20 | 40 | 30 | 210 | 150 | 255 | Yellow band along stream |
| buildings | 0 | 179 | 0 | 60 | 0 | 70 | Low-V or achromatic dark strokes |
| buildings (deep dark) | 0 | 179 | 0 | 255 | 0 | 50 | Very dark coloured ink |

Morphological ops (open then close) are applied per class with elliptical kernels:

| Class | Open kernel | Close kernel |
|-------|-------------|--------------|
| water | 3 px | 7 px |
| meadow | 3 px | 9 px |
| tree | 3 px | 11 px |
| path | 3 px | 9 px |
| sand | 3 px | 9 px |
| buildings | 1 px | 3 px |

## Output format

All files written to `--out` dir (default `tools/sketch_to_plan/output/`).

### polygons.json

```json
{
  "meta": {
    "image_size": [3306, 2337],
    "rotation_deg": 90,
    "source": "/abs/path/to/sketch.pdf",
    "coord_note": "Pixel origin is top-left. To flip to world Y-up: ..."
  },
  "classes": {
    "water": [[[x, y], [x, y], ...], ...],
    "meadow": [...],
    "tree": [...],
    "path": [...],
    "sand": [...],
    "buildings": [...]
  }
}
```

Coordinates are image pixel positions, origin top-left (OpenCV convention).

**To convert to world Y-up:**
```
world_x = px_x * (world_width / img_width)
world_y = (img_height - px_y) * (world_height / img_height)
```

Contours are simplified with `cv2.approxPolyDP` at epsilon = 1% of perimeter.

### overlay.png

The rasterized+rotated sketch with each class's raw contours drawn in colour:
- Water: amber/orange
- Meadow: bright green
- Tree: dark green
- Path: red-orange
- Sand: yellow
- Buildings: magenta

Use this as your feedback loop when tuning HSV ranges.

### build.vwxscript.py (from to_vwx.py)

Vectorworks `vs.*` Python script. For each polygon:
```python
vs.NameClass('LA-water')
h = vs.Poly(x1, y1, x2, y2, ...)
vs.SetFPat(h, 1)                          # solid fill
vs.SetFillFore(h, R*257, G*257, B*257)   # 0-255 -> 0-65535
vs.SetFillBack(h, R*257, G*257, B*257)
vs.SetClass(h, 'LA-water')
```

Coordinates are in mm (world meters × 1000), Y-up, origin at `--origin-x/y`.

## Segmentation quality (test sketch)

Run on `Bezirkstermin 240403 Skizze.pdf --rotate 90`:

| Class | Polygons | Quality |
|-------|----------|---------|
| water | 2 | **Good** — stream split into two segments by a crossing; both captured cleanly |
| meadow | 22 | **Good** — two large zig-zag areas + smaller patches; some fragmentation from zig-zag gaps is expected |
| tree | 19 | **Moderate** — darker blobs detected but small (max 2841 px²); visually correct positions |
| path | 3 | **Good** — the main diagonal tan path + two minor patches captured |
| sand | 2 | **Good** — the yellow band is cleanly split into two blobs (one either side of the stream) |
| buildings | 52 | **Good** — all U-shaped comb structures captured; high count is expected (each comb tooth = separate contour) |

**Green split assessment:** The meadow/tree split by V-threshold (V≥160 = meadow, V<160 = tree) works for this sketch. Meadow zig-zag strokes are marker-bright; tree blobs are scribbled and darker. However:
- Lighter tree tops may bleed into meadow
- Very bright spots in scribbled trees may bleed into meadow
- If the green distinction is unreliable for other sketches, merge both into a single `green` class by combining ranges H 40–90, S 35–255, V 10–255.

## Known limitations / next steps

1. **Line strokes, not filled regions**: The sketch uses open marker strokes. The detected polygons follow the stroke edges, not semantic fill regions. For planning, you'd want flood-fill or human-guided region tracing.

2. **Text / annotations**: Numbers and labels (e.g. "21.60", "29.10") fall under `buildings` (dark ink). They will appear as building polygons — filter by area or compactness if needed.

3. **Ink bleeding**: Marker bleeds create fuzzy edges. approxPolyDP simplification helps but vertex counts are still high for organic blobs.

4. **Multi-page PDFs**: Only page 0 is processed.

5. **Scale calibration**: The `to_vwx.py` world extent (default 44×26 m) must be set to match the actual plan extent. Measure from the sketch or a known reference distance.

6. **Next steps**:
   - Add `--scale-from` to pick two reference points from the image and compute real-world scale
   - Watershed / GrabCut for semantic fill regions
   - Georeferencing / CRS output for QGIS import
   - Batch mode for multiple pages / sketches
