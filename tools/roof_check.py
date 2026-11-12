"""Compare the built roof meshes with the LIDAR surface: rasterise roof triangles to the 1 m grid and
report height error per building. Writes a debug image (LIDAR | model | error) for a window if asked."""
import json, os, sys
import numpy as np, tifffile, shapely
from scipy import ndimage
from shapely.geometry import Polygon
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_P = json.load(open(os.path.join(ROOT, "data", "place.json")))
E_MIN, N_MAX, SIZE = _P["tile"][0], _P["tile"][3], _P["tile"][2] - _P["tile"][0]
E0, N0 = _P["origin"]
dsm = ndimage.median_filter(tifffile.imread(os.path.join(ROOT, "data", "dsm.tif")).astype(np.float32), 3)
P = np.fromfile(os.path.join(ROOT, "game", "data", "roof_pos.bin"), np.float32).reshape(-1, 3, 3)
model = np.full((SIZE, SIZE), np.nan, np.float32)
for tri in P:
    x, y, z = tri[:, 0], tri[:, 1], tri[:, 2]          # local x (east), height, local z (south)
    c = x + SIZE / 2 - 0.5                              # pixel col coordinate of vertex
    r = z + SIZE / 2 - 0.5                              # pixel row coordinate of vertex
    c0, c1 = int(np.floor(c.min())), int(np.ceil(c.max()))
    r0, r1 = int(np.floor(r.min())), int(np.ceil(r.max()))
    if c1 < 0 or r1 < 0 or c0 >= SIZE or r0 >= SIZE:
        continue
    cc, rr = np.meshgrid(np.arange(max(c0, 0), min(c1, SIZE - 1) + 1), np.arange(max(r0, 0), min(r1, SIZE - 1) + 1))
    d = (r[1] - r[2]) * (c[0] - c[2]) + (c[2] - c[1]) * (r[0] - r[2])
    if abs(d) < 1e-9:
        continue
    l1 = ((r[1] - r[2]) * (cc - c[2]) + (c[2] - c[1]) * (rr - r[2])) / d
    l2 = ((r[2] - r[0]) * (cc - c[2]) + (c[0] - c[2]) * (rr - r[2])) / d
    l3 = 1 - l1 - l2
    m = (l1 >= -1e-6) & (l2 >= -1e-6) & (l3 >= -1e-6)
    h = l1 * y[0] + l2 * y[1] + l3 * y[2]
    cur = model[rr[m], cc[m]]
    model[rr[m], cc[m]] = np.where(np.isnan(cur), h[m], np.maximum(cur, h[m]))
foots = json.load(open(os.path.join(ROOT, "game", "data", "footprints.json")))
rows = []
for i, f in enumerate(foots):
    poly = Polygon([(x + E0, N0 - z) for x, z in f["p"]]).buffer(-0.8)
    if poly.is_empty or poly.area < 15:
        continue
    minx, miny, maxx, maxy = poly.bounds
    cc, rr = np.meshgrid(np.arange(int(minx - E_MIN), int(maxx - E_MIN) + 1), np.arange(int(N_MAX - maxy), int(N_MAX - miny) + 1))
    inside = shapely.contains_xy(poly, E_MIN + cc + 0.5, N_MAX - rr - 0.5)
    rr, cc = rr[inside], cc[inside]
    ok = (rr >= 0) & (cc >= 0) & (rr < SIZE) & (cc < SIZE)
    rr, cc = rr[ok], cc[ok]
    err = model[rr, cc] - dsm[rr, cc]
    miss = np.isnan(err).mean()
    err = err[~np.isnan(err)]
    if len(err) < 10:
        continue
    rows.append((i, f.get("n", ""), poly.area, float(np.median(np.abs(err))), float(np.mean(np.abs(err) > 1.0)), float(np.median(err)), miss))
a = np.array([r[3] for r in rows]); b = np.array([r[4] for r in rows]); w = np.array([r[2] for r in rows])
print(f"buildings {len(rows)} | area-weighted median |err| {np.average(a, weights=w):.2f} m | area >1 m off {np.average(b, weights=w) * 100:.1f}%")
print(f"median signed err (model - LIDAR) {np.median([r[5] for r in rows]):+.2f} m | roof coverage missing {np.average([r[6] for r in rows], weights=w) * 100:.1f}%")
worst = sorted(rows, key=lambda r: -r[4] * r[2])[:12]
for r in worst:
    print(f"  #{r[0]:4d} {r[1][:28]:28s} area {r[2]:6.0f}  med|err| {r[3]:.2f}  >1m {r[4] * 100:4.0f}%  bias {r[5]:+.2f}  missing {r[6] * 100:3.0f}%")
np.save(os.path.join(ROOT, "data", "roof_model_raster.npy"), model)
