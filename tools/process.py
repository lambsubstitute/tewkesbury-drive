"""Turn OSM + EA LIDAR + Historic England data into game-ready Tewkesbury map files.

Local coords: x = Easting - E0 (east +), z = N0 - Northing (south +), y = metres above OD.
Town tile covers E 387800..390300, N 231500..234000 => x,z in [-1250, 1250].

  process.py                   the town tile -> game/data/
  process.py --region longdon  the countryside cells set up by tools/region.py -> game/data/longdon/
                               (same local origin, so both load into one world)
"""
import json, math, os, sys, hashlib
import numpy as np
import tifffile
import logging
logging.getLogger("tifffile").setLevel(logging.ERROR)   # EA GeoTIFF nodata tag warning
import pyproj
from pyproj import Transformer
from scipy import ndimage
from scipy.spatial import Delaunay
import shapely
from shapely.geometry import Polygon, MultiPolygon, LineString, box, Point
from shapely.ops import unary_union
import shapely.ops
from PIL import Image, ImageDraw, ImageFilter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import buildgeo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
REGION = sys.argv[sys.argv.index("--region") + 1] if "--region" in sys.argv else None
E0, N0 = 389050.0, 232750.0          # local origin: centre of the town tile, shared by every region
if REGION:
    # a set of 500 m cells (tools/region.py). TILE = the ground this run owns; the LIDAR raster is one
    # square mosaic covering every cell plus a margin, so buildings and roads on cell edges come out whole.
    RDATA = os.path.join(DATA, REGION)
    REG = json.load(open(os.path.join(RDATA, "region.json")))
    CELLS, CS, PAD = REG["cells"], REG["cell"], REG["pad"]
    OUT = os.path.join(ROOT, "game", "data", REGION)
    TILE = shapely.union_all([box(e_, n_, e_ + CS, n_ + CS) for e_, n_ in CELLS])
    E_MIN = min(c[0] for c in CELLS) - PAD
    N_MAX = max(c[1] for c in CELLS) + CS + PAD
    SIZE = max(max(c[0] for c in CELLS) + CS + PAD - E_MIN, N_MAX - (min(c[1] for c in CELLS) - PAD))
    RURAL = True                     # no generated lamps; pavements only where OSM maps them
    TOWN_BOX = box(*REG["town"])
else:
    RDATA = DATA
    OUT = os.path.join(ROOT, "game", "data")
    E_MIN, N_MAX, SIZE = 387800, 234000, 2500
    TILE = box(E_MIN, N_MAX - SIZE, E_MIN + SIZE, N_MAX)
    RURAL = False
RAST = box(E_MIN, N_MAX - SIZE, E_MIN + SIZE, N_MAX)
shapely.prepare(TILE)
os.makedirs(OUT, exist_ok=True)


def in_tile(E, N):
    return bool(shapely.contains_xy(TILE, E, N))

pyproj.network.set_network_enabled(True)  # OSTN15 grid for metre-accurate OSM->BNG
TF = Transformer.from_crs(4326, 27700, always_xy=True)


def h32(s):
    return int(hashlib.md5(str(s).encode()).hexdigest()[:8], 16)


def rnd(seed, i=0):
    return (h32(f"{seed}:{i}") % 100000) / 100000.0


# ---------------------------------------------------------------- rasters
print("loading lidar")


def mosaic(kind, optional=False):
    """Region raster: every cell's 1 m LIDAR (with its margin) placed into one square grid. Pixels no cell
    covers take the 10 m horizon terrain (bare ground), so filters near the edge see sensible values.
    optional: cells without this layer are left NaN."""
    A = np.full((SIZE, SIZE), np.nan, np.float32)
    for e_, n_ in CELLS:
        if optional and not os.path.exists(os.path.join(RDATA, "lidar", f"{e_}_{n_}_{kind}.tif")):
            continue
        a = tifffile.imread(os.path.join(RDATA, "lidar", f"{e_}_{n_}_{kind}.tif")).astype(np.float32)
        a[(a < -100) | (a > 2000)] = np.nan
        r0, c0 = N_MAX - (n_ + CS + PAD), (e_ - PAD) - E_MIN
        blk = A[r0:r0 + a.shape[0], c0:c0 + a.shape[1]]
        np.copyto(blk, a[:blk.shape[0], :blk.shape[1]], where=~np.isnan(a[:blk.shape[0], :blk.shape[1]]))
    if optional:
        print(" ", kind, "mosaic: cells with data", sum(os.path.exists(os.path.join(RDATA, "lidar", f"{e_}_{n_}_{kind}.tif")) for e_, n_ in CELLS))
        return A
    hp_ = os.path.join(RDATA, "horizon_dtm.npy")
    hz = np.load(hp_) if os.path.exists(hp_) else np.full((10, 10), np.nanmedian(A), np.float32)
    hz = np.where(np.isnan(hz), np.nanmedian(hz), hz)
    miss = np.isnan(A)
    rr, cc = np.nonzero(miss)
    HE0, HN1 = 380500, 240000                       # horizon box west / north edge (tools/region.py)
    A[rr, cc] = ndimage.map_coordinates(hz, [(HN1 - (N_MAX - rr - 0.5)) / 10 - 0.5, ((E_MIN + cc + 0.5) - HE0) / 10 - 0.5],
                                        order=1, mode="nearest")
    print(" ", kind, "mosaic", A.shape, "filled from horizon", int(miss.sum()))
    return A


if REGION:
    dtm = mosaic("dtm")
    dsm = np.maximum(mosaic("dsm"), dtm)
else:
    dtm = tifffile.imread(os.path.join(DATA, "dtm.tif")).astype(np.float32)
    dsm = tifffile.imread(os.path.join(DATA, "dsm.tif")).astype(np.float32)
dsm_s = ndimage.median_filter(dsm, size=3)


def pix_rc(E, N):
    return (N_MAX - N) - 0.5, (E - E_MIN) - 0.5


def sample(grid, E, N):
    r, c = pix_rc(np.asarray(E), np.asarray(N))
    return ndimage.map_coordinates(grid, [np.atleast_1d(r), np.atleast_1d(c)], order=1, mode="nearest")


# ---------------------------------------------------------------- OSM
print("loading osm")
osm = json.load(open(os.path.join(RDATA, "osm.json")))["elements"]


def proj(geom):
    lons = [p["lon"] for p in geom]
    lats = [p["lat"] for p in geom]
    E, N = TF.transform(lons, lats)
    return list(zip(E, N))


def assemble_rings(lines):
    lines = [list(l) for l in lines if len(l) >= 2]
    rings = []
    while lines:
        cur = lines.pop(0)
        changed = True
        while cur[0] != cur[-1] and changed:
            changed = False
            for i, l in enumerate(lines):
                if l[0] == cur[-1]:
                    cur += l[1:]
                elif l[-1] == cur[-1]:
                    cur += l[::-1][1:]
                elif l[-1] == cur[0]:
                    cur = l + cur[1:]
                elif l[0] == cur[0]:
                    cur = l[::-1] + cur[1:]
                else:
                    continue
                lines.pop(i)
                changed = True
                break
        if len(cur) >= 4 and cur[0] == cur[-1]:
            rings.append(cur)
    return rings


def element_polygon(e):
    try:
        if e["type"] == "way":
            g = e.get("geometry")
            if not g or len(g) < 4 or (g[0]["lat"], g[0]["lon"]) != (g[-1]["lat"], g[-1]["lon"]):
                return None
            p = Polygon(proj(g))
        elif e["type"] == "relation":
            outers = [proj(m["geometry"]) for m in e.get("members", []) if m.get("role") == "outer" and m.get("geometry")]
            inners = [proj(m["geometry"]) for m in e.get("members", []) if m.get("role") == "inner" and m.get("geometry")]
            outers = [[tuple(map(lambda v: round(v, 3), pt)) for pt in l] for l in outers]
            inners = [[tuple(map(lambda v: round(v, 3), pt)) for pt in l] for l in inners]
            orings = assemble_rings(outers)
            irings = assemble_rings(inners)
            if not orings:
                return None
            polys = []
            ipolys = [Polygon(r) for r in irings]
            for r in orings:
                op = Polygon(r)
                holes = [ip.exterior.coords for ip in ipolys if op.contains(ip.representative_point())]
                polys.append(Polygon(r, holes))
            p = unary_union(polys)
        else:
            return None
        if not p.is_valid:
            p = p.buffer(0)
        if p.is_empty:
            return None
        return p
    except Exception as ex:
        return None


def polys_of(g):
    if g is None or g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    if isinstance(g, MultiPolygon):
        return list(g.geoms)
    return [x for x in getattr(g, "geoms", []) if isinstance(x, Polygon)]


# ---------------------------------------------------------------- building outlines
BLDG = []   # (key, OSM-style tags, BNG polygon) for every building outline
for e in osm:
    t = e.get("tags", {})
    if "building" in t and t.get("building") not in ("no", "roof", "canopy", "carport"):
        g = element_polygon(e)
        if g is not None:
            BLDG.append((f"{e['type']}{e['id']}", t, g))
if REGION and os.path.exists(os.path.join(RDATA, "os_buildings.json")):
    # OSM maps only a handful of rural buildings; Ordnance Survey OpenMap Local (OGL) has every one.
    # OS outlines fill in wherever OSM has nothing overlapping.
    _osm_tree = shapely.STRtree([g for _, _, g in BLDG]) if BLDG else None
    _n_os = 0
    for b in json.load(open(os.path.join(RDATA, "os_buildings.json"))):
        try:
            p = Polygon(b["p"], b["h"]).buffer(0)
        except Exception:
            continue
        if p.is_empty or not p.intersects(RAST) or p.area < 6:
            continue
        if _osm_tree is not None and any(p.intersection(BLDG[i][2]).area > 0.3 * min(p.area, BLDG[i][2].area)
                                         for i in _osm_tree.query(p)):
            continue
        t = {"building": "church" if b.get("cls") == "Place Of Worship" else "yes"}
        if b.get("n"):
            t["name"] = b["n"]
        BLDG.append((f"os{b['id']}", t, p))
        _n_os += 1
    print("OS OpenMap Local buildings added:", _n_os, "OSM buildings:", len(BLDG) - _n_os)

# ---------------------------------------------------------------- Historic England
print("loading listed buildings")
he = json.load(open(os.path.join(RDATA, "lb_polys.json")))["features"]
he_polys = []
for f in he:
    rings = f["geometry"].get("rings", [])
    if not rings:
        continue
    try:
        p = Polygon(rings[0], rings[1:]).buffer(0)
        he_polys.append((p, f["attributes"]))
    except Exception:
        pass
he_tree = shapely.STRtree([p for p, _ in he_polys])

# ---------------------------------------------------------------- buildings
print("buildings")
STYLE_PLAIN, STYLE_TIMBER, STYLE_STONE, STYLE_SHED, STYLE_BRICK, STYLE_ASHLAR = 0, 1, 2, 3, 4, 5
NSTYLES = 6
walls = {s: {"pos": [], "uv": [], "col": []} for s in range(NSTYLES)}

# Wall materials observed in Commons photos of listed buildings (see data/materials_by_photo.txt)
PHOTO_MAT = {}
_idx = {str(r["n"]): r["le"] for r in json.load(open(os.path.join(DATA, "photo_index.json")))}
for line in open(os.path.join(DATA, "materials_by_photo.txt")):
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    n, code = line.split()
    kind, _, hexc = code.partition(":")
    col = tuple(int(hexc[i:i + 2], 16) for i in (0, 2, 4)) if hexc else None
    PHOTO_MAT[str(_idx[n])] = (kind, col)
if REGION and os.path.exists(os.path.join(RDATA, "materials_by_photo.txt")):
    for line in open(os.path.join(RDATA, "materials_by_photo.txt")):   # "<list entry> <code>"
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        le_, code = line.split()
        kind, _, hexc = code.partition(":")
        PHOTO_MAT[le_] = (kind, tuple(int(hexc[i:i + 2], 16) for i in (0, 2, 4)) if hexc else None)
BRICK_TINTS = [(255, 255, 255), (255, 238, 225), (238, 224, 218), (255, 246, 236), (226, 214, 210)]


def photo_style(code, key):
    k, col = code
    if k == "B":
        return STYLE_BRICK, BRICK_TINTS[h32(key) % len(BRICK_TINTS)]
    if k == "W":
        return STYLE_PLAIN, (240, 238, 232)
    if k == "C":
        return STYLE_PLAIN, col
    if k == "T":
        return STYLE_TIMBER, col or (242, 238, 228)
    return STYLE_ASHLAR, col or (205, 190, 158)


# facade index: listing polygons (grown 1.5 m to catch the wall line) that have a photo material
# Many NHLE "polygons" are ~3 m2 marker squares inside the building: treat those as points.
FACADE, MARKERS = [], []
for hp, attr in he_polys:
    code = PHOTO_MAT.get(str(attr["ListEntry"]))
    if code and code[0] != "X":
        st = photo_style(code, attr["ListEntry"])
        if hp.area < 20:
            MARKERS.append((hp.centroid, st, attr))
        else:
            FACADE.append((hp.buffer(1.5), st, attr))
FACADE_TREE = shapely.STRtree([f[0] for f in FACADE])
MARKER_TREE = shapely.STRtree([m[0] for m in MARKERS])
print("facade polygons", len(FACADE), "markers", len(MARKERS))


ORPHANS = {}  # osm element key -> markers whose listing sits just outside that building's OSM outline


def link_orphans():
    blds = [(k_, g_) for k_, _, g_ in BLDG]
    tree = shapely.STRtree([g for _, g in blds])
    claimed = set()
    for m in MARKERS:
        for i in tree.query(m[0].buffer(1.0), predicate="intersects"):
            claimed.add(blds[i][0])
    n = 0
    for m in MARKERS:
        if len(tree.query(m[0].buffer(1.0), predicate="intersects")):
            continue
        i = tree.nearest(m[0])
        key, g = blds[i]
        if g.distance(m[0]) <= 16 and key not in claimed:
            ORPHANS.setdefault(key, []).append(m)
            n += 1
    print("orphan listings snapped to nearby buildings:", n)


link_orphans()


def markers_in(poly, key=None):
    return [MARKERS[i] for i in MARKER_TREE.query(poly.buffer(1.0), predicate="intersects")] + ORPHANS.get(key, [])


def facade_at(x, y):
    hits = FACADE_TREE.query(Point(x, y), predicate="intersects")
    if len(hits) == 0:
        return None
    best = min(hits, key=lambda i: FACADE[i][0].area)  # smallest = most specific listing
    return FACADE[best]
roof_pos, roof_col, roof_idx = [], [], []
foot_out = []
bmask = Image.new("L", (SIZE, SIZE), 0)
bdraw = ImageDraw.Draw(bmask)

BRICKS = [(150, 72, 52), (165, 85, 60), (140, 66, 50), (175, 100, 72), (128, 62, 48)]
RENDERS = [(232, 226, 210), (240, 236, 224), (222, 210, 186), (214, 200, 170), (236, 228, 200), (200, 210, 205), (230, 214, 200)]
LIMESTONE = (205, 190, 158)
ROOF_CLAY = [(140, 70, 52), (122, 64, 50), (150, 82, 60), (110, 60, 48)]
ROOF_SLATE = [(70, 74, 82), (84, 86, 92), (62, 64, 70)]
ROOF_CONC = [(105, 80, 68), (92, 88, 84), (118, 96, 80)]


def to_local(E, N):
    return E - E0, N0 - N


def pix_mask(poly):
    minx, miny, maxx, maxy = poly.bounds
    c0, c1 = int(math.floor(minx - E_MIN)), int(math.ceil(maxx - E_MIN))
    r0, r1 = int(math.floor(N_MAX - maxy)), int(math.ceil(N_MAX - miny))
    c0, r0 = max(c0, 0), max(r0, 0)
    c1, r1 = min(c1, SIZE), min(r1, SIZE)
    if c1 <= c0 or r1 <= r0:
        return None
    cc, rr = np.meshgrid(np.arange(c0, c1), np.arange(r0, r1))
    Ex = E_MIN + cc + 0.5
    Ny = N_MAX - rr - 0.5
    inside = shapely.contains_xy(poly, Ex, Ny)
    return rr[inside], cc[inside], Ex[inside], Ny[inside]


def carve_open_ground(poly):
    """Remove parts of an OSM outline where LIDAR shows open ground (courtyards, yards, gaps between
    houses that OSM drew as one block). Returns the trimmed polygon (or the original if trimming
    would remove most of it, e.g. a building LIDAR predates)."""
    pm = pix_mask(poly)
    if pm is None or len(pm[0]) < 20:
        return poly, 0.0
    rows, cols = pm[0], pm[1]
    r0, c0 = rows.min(), cols.min()
    grid = np.zeros((rows.max() - r0 + 1, cols.max() - c0 + 1), bool)
    grid[rows - r0, cols - c0] = (dsm_s[rows, cols] - dtm[rows, cols]) < 1.8
    grid = ndimage.binary_opening(grid, np.ones((3, 3), bool))
    comp, n = ndimage.label(grid)
    holes = []
    for ci in range(1, n + 1):
        rr, cc = np.nonzero(comp == ci)
        if len(rr) < 12:
            continue
        x0 = E_MIN + cc + c0
        y1 = N_MAX - (rr + r0)
        holes.append(shapely.union_all(shapely.box(x0, y1 - 1, x0 + 1, y1)))
    if not holes:
        return poly, 0.0
    open_ = shapely.union_all(holes).buffer(0.35, join_style="mitre").buffer(-0.35, join_style="mitre").simplify(0.4)
    trimmed = poly.difference(open_)
    if trimmed.is_empty or trimmed.area < 0.4 * poly.area:
        return poly, 0.0
    return trimmed, poly.area - trimmed.area


def densify_ring(coords, step=1.0):
    out = []
    for (x0, y0), (x1, y1) in zip(coords[:-1], coords[1:]):
        L = math.hypot(x1 - x0, y1 - y0)
        n = max(1, int(L / step))
        for k in range(n):
            t = k / n
            out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    return out


from scipy.spatial import cKDTree


def fit_planes(X, Y, H, seed):
    rng = np.random.default_rng(seed % (2 ** 32))
    N = len(X)
    A = np.column_stack([X, Y, np.ones(N)])
    remaining = np.ones(N, bool)
    planes = []
    maxk = min(10, 2 + N // 50)
    minin = max(8, int(0.04 * N))
    for _ in range(maxk):
        idx = np.nonzero(remaining)[0]
        if len(idx) < minin:
            break
        T = 200
        tri = rng.choice(idx, size=(T, 3))
        P = A[tri]
        hh = H[tri]
        ok = np.abs(np.linalg.det(P)) > 1e-3
        if not ok.any():
            break
        sol = np.zeros((T, 3))
        sol[ok] = np.linalg.solve(P[ok], hh[ok][..., None])[..., 0]
        ok &= np.hypot(sol[:, 0], sol[:, 1]) < 1.8   # reject > ~60 deg (walls)
        res = np.abs(A[idx] @ sol.T - H[idx][:, None])
        cnt = (res < 0.3).sum(0)
        cnt[~ok] = -1
        b = int(np.argmax(cnt))
        if cnt[b] < minin:
            break
        inl = idx[res[:, b] < 0.3]
        p = sol[b]
        for _ in range(2):
            p, *_ = np.linalg.lstsq(A[inl], H[inl], rcond=None)
            inl = idx[np.abs(A[idx] @ p - H[idx]) < 0.3]
            if len(inl) < 3:
                break
        if len(inl) < minin or np.hypot(p[0], p[1]) > 1.8:
            break
        planes.append(p)
        remaining[inl] = False
    return np.array(planes)


def roof_model(ipm, base, cx, cy, bid):
    rows, cols, Ex, Ny = ipm
    X, Y = Ex - cx, Ny - cy
    H = dsm_s[rows, cols] - base
    planes = fit_planes(X, Y, H, h32(bid))
    if len(planes) == 0:
        return None
    hmax = float(np.percentile(H, 99)) + 0.3
    r0, c0 = rows.min(), cols.min()
    hgt, wid = rows.max() - r0 + 1, cols.max() - c0 + 1
    K = len(planes)
    mask = np.zeros((hgt, wid), np.float32)
    mask[rows - r0, cols - c0] = 1
    stack = np.zeros((K, hgt, wid), np.float32)
    for k, (a, b, c) in enumerate(planes):
        img = np.zeros((hgt, wid), np.float32)
        img[rows - r0, cols - c0] = np.abs(a * X + b * Y + c - H)
        num = ndimage.uniform_filter(img, 3)
        den = ndimage.uniform_filter(mask, 3)
        stack[k] = num / np.maximum(den, 1e-6)
    lab_img = np.argmin(stack, axis=0)
    lab = lab_img[rows - r0, cols - c0]
    pa = planes[lab]
    ph = pa[:, 0] * X + pa[:, 1] * Y + pa[:, 2]
    good = np.mean(np.abs(ph - H) < 0.5)
    if good < 0.55:
        return None
    lo = 2.3
    ph = np.clip(ph, lo, hmax)
    # keep big coherent features the planes miss (towers, spires, taller wings): 3x3+ blobs off by >1.5 m
    off = np.zeros((hgt, wid), bool)
    off[rows - r0, cols - c0] = np.abs(ph - H) > 1.5
    off = ndimage.binary_opening(off, np.ones((3, 3), bool))
    comp, ncomp = ndimage.label(off)
    feat = np.zeros(len(rows), bool)
    cpix = comp[rows - r0, cols - c0]
    for ci in range(1, ncomp + 1):
        m = cpix == ci
        if m.sum() < 9:
            continue
        top = float(np.percentile(H[m], 70))
        if top > np.median(ph[m]) + 1.5:
            ph[m] = top          # flat-topped block with near-vertical sides (towers)
            feat |= m & (top > np.median(ph) + 6)
    # ridge / valley points between neighbouring pixels of different planes
    L = np.full((hgt, wid), -1)
    L[rows - r0, cols - c0] = lab
    rx, rh = [], []
    for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
        A0 = L[max(0, -dr):hgt - max(0, dr), max(0, -dc):wid - max(0, dc)]
        A1 = L[max(0, dr):, max(0, dc):][:A0.shape[0], :A0.shape[1]]
        if dc < 0:
            A0 = L[0:hgt - dr, 1:]
            A1 = L[dr:, :wid - 1]
        rr, cc = np.nonzero((A0 >= 0) & (A1 >= 0) & (A0 != A1))
        for r_, c_ in zip(rr, cc):
            if dc < 0:
                R0, C0, R1, C1 = r_, c_ + 1, r_ + dr, c_
            else:
                R0, C0 = r_ + max(0, -dr), c_ + max(0, -dc)
                R1, C1 = R0 + dr, C0 + dc
            i, j = L[R0, C0], L[R1, C1]
            p0 = np.array([E_MIN + c0 + C0 + 0.5 - cx, N_MAX - (r0 + R0) - 0.5 - cy])
            p1 = np.array([E_MIN + c0 + C1 + 0.5 - cx, N_MAX - (r0 + R1) - 0.5 - cy])
            f = lambda p, k: planes[k][0] * p[0] + planes[k][1] * p[1] + planes[k][2]
            d0, d1 = f(p0, i) - f(p0, j), f(p1, i) - f(p1, j)
            if d0 * d1 <= 0 and d0 != d1:
                t = d0 / (d0 - d1)
                q = p0 + t * (p1 - p0)
                rx.append((q[0] + cx, q[1] + cy))
                rh.append(min(max(f(q, i), lo), hmax))
    tree = cKDTree(np.column_stack([X, Y]))

    def height_at(xy):
        _, nn = tree.query(np.column_stack([xy[:, 0] - cx, xy[:, 1] - cy]))
        pk = planes[lab[nn]]
        return np.clip(pk[:, 0] * (xy[:, 0] - cx) + pk[:, 1] * (xy[:, 1] - cy) + pk[:, 2], lo, hmax)

    ridge_xy = np.array(rx).reshape(-1, 2)
    return np.column_stack([Ex, Ny]), ph, ridge_xy, np.array(rh), height_at, feat


# ---- context for building geometry: all footprints, drivable roads, shops
print("context")
_all = []
for _k, t, g in BLDG:
    _all += [p for p in polys_of(g.intersection(RAST if REGION else TILE)) if p.area >= 6]
ALL_B = shapely.union_all(_all)
shapely.prepare(ALL_B)

_DRIVE_W = {"motorway": 11, "trunk": 9, "primary": 7.5, "secondary": 7, "tertiary": 6.5, "unclassified": 5.5,
            "residential": 5.5, "living_street": 5, "pedestrian": 6, "primary_link": 5.5, "secondary_link": 5,
            "tertiary_link": 5, "trunk_link": 6, "road": 5, "service": 3.6}
if RURAL:
    # untagged country roads are narrower than town streets: B-roads ~6 m, lanes single-track with passing places
    _DRIVE_W.update({"primary": 7.0, "secondary": 6.2, "tertiary": 5.4, "unclassified": 4.2, "residential": 4.8, "service": 3.0})


def road_width(t):
    """carriageway width (m) used for rendering, pavements, kerbs and collisions alike"""
    hw = t.get("highway")
    w = _DRIVE_W.get(hw, 5.0)
    try:
        if "width" in t:
            return max(2.5, min(14, float(str(t["width"]).split()[0])))
        if "lanes" in t and hw != "service":
            w = max(w, float(t["lanes"]) * 3.2)
    except ValueError:
        pass
    if t.get("oneway") == "yes" and hw in ("primary", "secondary", "tertiary") and "lanes" not in t:
        w *= 0.75
    return w


_rl, _rw = [], []
for e in osm:
    t = e.get("tags", {})
    if e["type"] == "way" and t.get("highway") in _DRIVE_W and e.get("geometry"):
        _rl.append(LineString(proj(e["geometry"])))
        _rw.append(road_width(t))
ROAD_TREE = shapely.STRtree(_rl)




def road_near(x, y):
    pt = Point(x, y)
    i = ROAD_TREE.nearest(pt)
    if i is None:
        return None
    ln = _rl[i]
    q = ln.interpolate(ln.project(pt))
    return pt.distance(q), _rw[i], (q.x, q.y)


street_osm = json.load(open(os.path.join(RDATA, "osm_street.json")))["elements"]
SHOP_AMEN = {"cafe", "restaurant", "pub", "fast_food", "bank", "pharmacy", "post_office", "bar", "veterinary",
             "dentist", "doctors", "bureau_de_change", "ice_cream", "library", "estate_agent"}
SHOPS = []
for e in street_osm:
    t = e.get("tags", {})
    if e["type"] != "node":
        continue
    if "shop" in t or t.get("amenity") in SHOP_AMEN or "office" in t or "craft" in t or t.get("tourism") in ("hotel", "guest_house"):
        E, N = TF.transform(e["lon"], e["lat"])
        SHOPS.append((Point(E, N), t.get("name"), t))
SHOP_TREE = shapely.STRtree([s_[0] for s_ in SHOPS])
SHOP_PAINT = [(30, 52, 40), (28, 36, 62), (24, 24, 26), (96, 24, 32), (60, 66, 70), (226, 220, 200), (40, 70, 90), (112, 80, 40)]
# real shopfront colours read from Mapillary photos (data/shopfronts_from_photos.txt)
_hex = lambda h: tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
PHOTO_SHOP = {}
_fn = os.path.join(DATA, "shopfronts_from_photos.txt")
if os.path.exists(_fn):
    _names = sorted({n_ for _, n_, _ in SHOPS if n_})
    for line in open(_fn):
        if not line.strip() or line.startswith("#"):
            continue
        parts = [x_.strip() for x_ in line.split("|")]
        k = parts[0].lower()
        m = [n_ for n_ in _names if n_.lower() == k] or [n_ for n_ in _names if n_.lower().startswith(k) or k in n_.lower()]
        if len(m) == 1:
            PHOTO_SHOP[m[0]] = (_hex(parts[1]), _hex(parts[2]) if len(parts) > 2 and parts[2] else _hex(parts[1]))
    print("shopfront colours from photos:", len(PHOTO_SHOP))
signs_out = []
shopfront_out = []
# Roof heights read from DSM_B. In the country many cottages stand under trees, and the LIDAR first return
# records the canopy instead of the roof. The last return passes through foliage to the roof (or ground)
# beneath, so where first - last > 1 m the roof height comes from the last return instead.
DSM_B = dsm_s
VEG = None
if RURAL and any(os.path.exists(os.path.join(RDATA, "lidar", f"{e_}_{n_}_lz.tif")) for e_, n_ in CELLS):
    _lz = mosaic("lz", optional=True)
    _lz = ndimage.median_filter(np.where(np.isnan(_lz), dsm, np.maximum(_lz, dtm)), size=3)   # no data: no foliage test
    VEG = ndimage.uniform_filter(((dsm - _lz) > 1.0).astype(np.float32), 3) > 0.5
    DSM_B = np.where(VEG, _lz, dsm_s)
    del _lz


def roof_under_trees(poly, pm, base, bid):
    """Share of a building's roof hidden by foliage (roof heights there already come from the last return).
    A small building whose roof still reads over 12 m (dense evergreen canopy that even the last return
    did not get through) gets a 40 degree gable along its long axis at a cottage eave height instead.
    Returns minus the share when the gable was used."""
    rows, cols = pm[0], pm[1]
    frac = float(VEG[rows, cols].mean()) if len(rows) else 0.0
    h = DSM_B[rows, cols] - base
    if not (len(h) and np.percentile(h, 90) > 12 and poly.area < 400):
        return frac
    rect = poly.minimum_rotated_rectangle
    co = np.array(rect.exterior.coords)[:4]
    e1, e2 = co[1] - co[0], co[2] - co[1]
    ax_, w_ = (e1, np.linalg.norm(e2)) if np.linalg.norm(e1) >= np.linalg.norm(e2) else (e2, np.linalg.norm(e1))
    u = ax_ / np.linalg.norm(ax_)
    perp = np.array([-u[1], u[0]])
    c = np.array(rect.centroid.coords[0])
    E, N = E_MIN + cols + 0.5, N_MAX - rows - 0.5
    d = np.abs((E - c[0]) * perp[0] + (N - c[1]) * perp[1])
    small = poly.area < 40
    pitch = math.tan(math.radians(25 if small else 40))
    DSM_B[rows, cols] = base + (2.4 if small else 4.8) + np.clip(w_ / 2 - d, 0, None) * pitch
    return -max(frac, 0.01)


ctx = {"E_MIN": E_MIN, "N_MAX": N_MAX, "E0": E0, "N0": N0, "dsm_s": DSM_B, "dtm": dtm, "sample": sample,
       "ALL_B": ALL_B, "road_near": road_near, "B_LIST": _all, "B_TREE": shapely.STRtree(_all)}
sink = buildgeo.Sink(NSTYLES)


CHAINS = ("betfred", "card factory", "ladbrokes", "poundland", "savers", "peacocks", "shoe zone", "subway", "kfc",
          "papa john", "holland & barrett", "tsb", "lloyds", "boots", "superdrug", "costa", "greggs", "specsavers",
          "scrivens", "coffee#1", "age uk", "tgjones", "m&co", "timpson", "edinburgh woollen mill", "domino",
          "sue ryder", "cancer research", "british heart", "oxfam", "salvation army", "st. richard", "blue cross",
          "coral", "william hill", "halifax", "hsbc", "nationwide", "santander", "co-op", "tesco", "sainsbury",
          "iceland", "vodafone", "cex", "ee", "post office", "card rack", "tewkesbury phones")
AWNING_SHOPS = {"butcher", "greengrocer", "bakery", "deli", "florist", "fishmonger", "confectionery", "farm"}
AWNING_AMEN = {"cafe", "ice_cream"}
# awnings seen in the Mapillary photos: (kind, canvas colour)
SEEN_AWNINGS = {"The Card Rack": ("plain", "#b3202a"), "Halfords Traditional Butchers": ("stripe", "#26386e"),
                "Chocolat": ("stripe", "#2f5a3a")}
PUB_PAINT = [(28, 40, 32), (60, 22, 28), (24, 24, 26), (30, 36, 52)]
TRAD_PAINT = [(30, 52, 40), (28, 36, 62), (24, 24, 26), (96, 24, 32), (60, 66, 70), (226, 220, 200), (40, 70, 90),
              (112, 80, 40), (238, 236, 228), (70, 90, 70)]
MODERN_PAINT = [(40, 44, 50), (30, 50, 110), (180, 30, 40), (20, 110, 90), (60, 60, 64), (230, 230, 228)]


def shop_style(name, tags, old_building):
    n = (name or "").lower()
    amen = tags.get("amenity")
    if amen in ("pub", "bar"):
        return "pub"
    if any(c in n for c in CHAINS) or amen == "bank" or tags.get("shop") in ("mobile_phone", "supermarket", "convenience"):
        return "modern"
    return "trad" if old_building or h32(n) % 10 < 6 else "modern"


def shop_units(pieces, shops_here, bid, old_building, hs):
    """Group street-facing 1 m wall pieces into shop units (one per shop, contiguous along one wall)."""
    named = shops_here
    assign = []
    for (x0, y0, x1, y1, nx, ny, ei, ri) in pieces:
        mp = Point((x0 + x1) / 2, (y0 + y1) / 2)
        k = -1
        if named:
            k = min(range(len(named)), key=lambda k_: named[k_][0].distance(mp))
            if named[k][0].distance(mp) > 30:
                k = -1
        assign.append(k)
    runs = []
    for pc, k in zip(pieces, assign):
        x0, y0, x1, y1, nx, ny, ei, ri = pc
        cur = runs[-1] if runs else None
        if cur and cur["k"] == k and cur["ei"] == ei and abs(cur["b"][0] - x0) < 1e-6 and abs(cur["b"][1] - y0) < 1e-6:
            cur["b"] = (x1, y1)
        else:
            runs.append({"k": k, "ei": ei, "a": (x0, y0), "b": (x1, y1), "n": (nx, ny)})
    runs = [r for r in runs if math.hypot(r["b"][0] - r["a"][0], r["b"][1] - r["a"][1]) >= 2.0]
    # a shop's name goes on its longest stretch of frontage only (corner shops have two)
    longest = {}
    for r in runs:
        L = math.hypot(r["b"][0] - r["a"][0], r["b"][1] - r["a"][1])
        if r["k"] >= 0 and L > longest.get(r["k"], (0, None))[0]:
            longest[r["k"]] = (L, id(r))
    out = []
    for r in runs:
        k = r["k"]
        sname, tags = (named[k][1], named[k][2]) if k >= 0 else (None, {})
        style_ = shop_style(sname, tags, old_building)
        key = sname or (bid + str(len(out)))
        if sname in PHOTO_SHOP:
            paint, signc = PHOTO_SHOP[sname]
        else:
            pal = PUB_PAINT if style_ == "pub" else TRAD_PAINT if style_ == "trad" else MODERN_PAINT
            paint = pal[h32(key) % len(pal)]
            signc = paint
        aw, awc = SEEN_AWNINGS.get(sname, ("", None))
        if not aw:
            aw = ("stripe" if tags.get("shop") in ("butcher", "greengrocer", "fishmonger") else
                  "plain" if (tags.get("shop") in AWNING_SHOPS or tags.get("amenity") in AWNING_AMEN) else "")
        door = "c" if style_ in ("trad",) else ("r" if h32(key + "d") % 2 else "l")
        ax, az = to_local(*r["a"])
        bx, bz = to_local(*r["b"])
        rec = {"a": [round(ax, 2), round(az, 2)], "b": [round(bx, 2), round(bz, 2)],
               "n": [round(r["n"][0], 4), round(-r["n"][1], 4)], "h": round(hs, 2), "st": style_,
               "c": "#%02x%02x%02x" % paint, "sc": "#%02x%02x%02x" % signc, "d": door}
        if aw:
            rec["aw"] = aw
            rec["ac"] = awc or rec["c"]
        if sname and longest.get(k, (0, None))[1] == id(r):
            rec["name"] = sname
        out.append(rec)
    return out


n_b = 0
stats = {"lidar": 0, "fallback": 0, "listed": 0}
for bid0, t, g in BLDG:
    if REGION:   # a building belongs to the cell its middle is in, and is never cut at a cell edge
        if not TILE.contains(g.representative_point()) or g.intersects(TOWN_BOX):
            continue   # (anything touching the town tile was already built, clipped, by the town run)
        g = g.intersection(RAST)
    else:
        g = g.intersection(TILE)
    trimmed_parts = []
    for poly0 in polys_of(g):
        if poly0.area < 6:
            continue
        t_, cut_ = carve_open_ground(poly0)
        if cut_ > 0:
            stats["courtyards_m2"] = stats.get("courtyards_m2", 0) + round(cut_)
        trimmed_parts += [p_ for p_ in polys_of(t_) if p_.area >= 6]
    for poly in trimmed_parts:
        bid = bid0
        btype = t.get("building", "yes")
        name = t.get("name")
        # listed?
        listed = None
        for i in he_tree.query(poly):
            hp, attr = he_polys[i]
            try:
                ov = poly.intersection(hp).area
            except Exception:
                continue
            if ov > 0.35 * poly.area or ov > 0.6 * hp.area:
                listed = attr
                break
        if listed:
            stats["listed"] += 1
        pm = pix_mask(poly)
        inner = poly.buffer(-0.6)
        base_vals = sample(dtm, *np.array(poly.exterior.coords).T)
        base = float(np.min(base_vals)) if len(base_vals) else 10.0
        if pm is not None and len(pm[0]) > 0:
            base = min(base, float(np.percentile(dtm[pm[0], pm[1]], 10)))
        # roof heights from DSM inside inner footprint
        ipm = pix_mask(inner) if (not inner.is_empty and inner.area > 1) else None
        if VEG is not None and pm is not None and len(pm[0]) >= 6 and btype not in ("church", "chapel"):
            fr_ = roof_under_trees(poly, pm, base, bid)
            if fr_ < 0:
                stats["roof_gable_guessed"] = stats.get("roof_gable_guessed", 0) + 1
            elif fr_ >= 0.1:
                stats["roof_under_foliage"] = stats.get("roof_under_foliage", 0) + 1
        hts = None
        if ipm is not None and len(ipm[0]) >= 3:
            hts = DSM_B[ipm[0], ipm[1]] - base
        elif pm is not None and len(pm[0]) >= 1:
            hts = DSM_B[pm[0], pm[1]] - base
        lv = t.get("building:levels")
        tag_h = None
        try:
            if "height" in t:
                tag_h = float(str(t["height"]).split()[0])
            elif lv:
                tag_h = float(lv) * 3.0 + 1.0
        except ValueError:
            pass
        use_lidar = hts is not None and np.percentile(hts, 50) > 2.0
        if use_lidar:
            stats["lidar"] += 1
            p98 = float(np.percentile(hts, 98))
            eave = float(np.clip(np.percentile(hts, 12), 2.3, p98))
            ridge_cap = p98
        else:
            stats["fallback"] += 1
            eave = tag_h or (3.0 if btype in ("garage", "garages", "shed", "service") else 5.5)
            ridge_cap = eave
        # --- style & colours
        nm = (name or (listed or {}).get("Name") or "").lower()
        photo = PHOTO_MAT.get(str(listed["ListEntry"])) if listed else None
        if photo and photo[0] == "X":
            photo = None
        if photo:
            stats["photo"] = stats.get("photo", 0) + 1
        onm = (name or "").lower()
        is_church = btype in ("church", "chapel", "cathedral") or onm == "tewkesbury abbey" or (
            ("church" in onm or "chapel" in onm) and "street" not in onm)
        roof_old = ROOF_CLAY[h32(bid) % 4] if rnd(bid, 2) < 0.55 else ROOF_SLATE[h32(bid) % 3]
        if photo and not (is_church and photo[0] == "S"):
            k, col = photo
            if k == "B":
                style, wc = STYLE_BRICK, BRICK_TINTS[h32(bid) % len(BRICK_TINTS)]
            elif k == "W":
                style, wc = STYLE_PLAIN, (240, 238, 232)
            elif k == "C":
                style, wc = STYLE_PLAIN, col
            elif k == "T":
                style, wc = STYLE_TIMBER, col or (242, 238, 228)
            else:  # S
                style, wc = STYLE_ASHLAR, col or LIMESTONE
            rc = roof_old
        elif is_church:
            style, wc = STYLE_STONE, (photo[1] if photo and photo[1] else LIMESTONE)
            rc = ROOF_SLATE[h32(bid) % 3] if "abbey" not in nm else (88, 92, 96)
        elif btype in ("industrial", "warehouse", "garages", "garage", "service", "shed") or poly.area > 1500 and btype in ("retail", "supermarket", "commercial", "yes"):
            style = STYLE_SHED
            wc = [(160, 165, 168), (150, 140, 128), (120, 128, 130)][h32(bid) % 3]
            rc = (110, 114, 118)
        elif listed:
            r = rnd(bid)
            if "tudor" in nm or r < 0.20:
                style, wc = STYLE_TIMBER, (242, 238, 228)
            elif r < 0.72:
                style, wc = STYLE_BRICK, BRICK_TINTS[h32(bid) % len(BRICK_TINTS)]
            else:
                style, wc = STYLE_PLAIN, [(240, 238, 232), (236, 228, 208), (232, 226, 210)][h32(bid) % 3]
            rc = roof_old
        else:
            if rnd(bid, 3) < 0.72:
                style, wc = STYLE_BRICK, BRICK_TINTS[h32(bid) % len(BRICK_TINTS)]
            else:
                style, wc = STYLE_PLAIN, RENDERS[h32(bid) % 7]
            rc = ROOF_CONC[h32(bid) % 3] if rnd(bid, 4) < 0.6 else ROOF_CLAY[h32(bid) % 4]
        # --- geometry: faceted LIDAR roof + storeyed walls
        inside_m = [] if is_church else markers_in(poly, bid)
        facade_hit = [None]

        def seg_material(mx, my, _inside=inside_m):
            if is_church:
                return None
            fh = facade_at(mx, my)
            if fh is None and _inside:
                fh = min(_inside, key=lambda m: m[0].distance(Point(mx, my)))
            if fh is not None:
                facade_hit[0] = fh
                return fh[1]
            return None

        shops_here = [SHOPS[i] for i in SHOP_TREE.query(poly.buffer(1.0), predicate="intersects")]
        is_shop = bool(shops_here) or btype in ("retail", "commercial", "shop")
        paint = SHOP_PAINT[h32(bid + "p") % len(SHOP_PAINT)]
        if any(sn_ in PHOTO_SHOP for (_, sn_, _) in shops_here):
            stats["photo_shopfronts"] = stats.get("photo_shopfronts", 0) + 1
            per_shop = [(spt_, PHOTO_SHOP[sn_][0] if sn_ in PHOTO_SHOP else SHOP_PAINT[h32(bid + str(i_)) % len(SHOP_PAINT)])
                        for i_, (spt_, sn_, _) in enumerate(shops_here)]
            if len(per_shop) == 1:
                paint = per_shop[0][1]
            else:   # several shops in one OSM block: each stretch of frontage takes its nearest shop's colour
                paint = lambda mx, my, _c=per_shop: min(_c, key=lambda c_: c_[0].distance(Point(mx, my)))[1]
        res = buildgeo.build(ctx, poly, bid, base, ipm, use_lidar, eave, style, wc, rc, is_church,
                             seg_material, paint, is_shop, sink)
        if res is None:
            continue
        stats["planar"] = stats.get("planar", 0) + 1
        facade_hit = facade_hit[0]
        eave = res["eave"]
        # shop units: split the building's street frontage between its shops (each 1 m piece of
        # frontage goes to the nearest shop node), one shopfront + one fascia sign per unit
        if is_shop and res["pieces"]:
            units = shop_units(res["pieces"], shops_here, bid, listed is not None or style == STYLE_TIMBER, res["hs"])
            for u_ in units:
                u_["y"] = round(res["fbase"], 2)   # first-floor line = y + h, shared with the walls
            shopfront_out.extend(units)
        # footprints for collision / minimap / labels
        ring_local = [[round(v, 2) for v in to_local(x, y)] for x, y in poly.exterior.coords]
        rec = {"p": ring_local, "h": round(eave, 1)}
        if facade_hit is not None:
            stats["facade_bldgs"] = stats.get("facade_bldgs", 0) + 1
            if not listed:
                listed = facade_hit[2]
                photo = PHOTO_MAT.get(str(listed["ListEntry"]))
        label = name or (listed and listed.get("Name").title())
        if label:
            rec["n"] = label
        if listed:
            rec["g"] = listed.get("Grade")
            if photo:
                rec["m"] = {"B": "brick", "W": "painted render", "C": "painted render", "T": "timber frame", "S": "stone"}[photo[0]]
        foot_out.append(rec)
        # building mask for tree detection (1m raster)
        bdraw.polygon([((x - E_MIN), (N_MAX - y)) for x, y in poly.buffer(1.5).exterior.coords], fill=255)
        n_b += 1

print("buildings:", n_b, stats, "roof tris", len(sink.roof["pos"]) // 9)

for s_, W in sink.walls.items():  # one mesh per wall style
    np.array(W["pos"], np.float32).tofile(os.path.join(OUT, f"walls{s_}_pos.bin"))
    np.array(W["uv"], np.float32).tofile(os.path.join(OUT, f"walls{s_}_uv.bin"))
    np.array(W["col"], np.uint8).tofile(os.path.join(OUT, f"walls{s_}_col.bin"))
    np.array(W["kind"], np.uint8).tofile(os.path.join(OUT, f"walls{s_}_kind.bin"))
np.array(sink.roof["pos"], np.float32).tofile(os.path.join(OUT, "roof_pos.bin"))
np.array(sink.roof["uv"], np.float32).tofile(os.path.join(OUT, "roof_uv.bin"))
np.array(sink.roof["col"], np.uint8).tofile(os.path.join(OUT, "roof_col.bin"))
json.dump(shopfront_out, open(os.path.join(OUT, "shopfronts.json"), "w"), separators=(",", ":"))
if os.path.exists(os.path.join(OUT, "signs.json")):
    os.remove(os.path.join(OUT, "signs.json"))
print("shop units", len(shopfront_out), "named", sum(1 for u in shopfront_out if "name" in u),
      "styles", {k: sum(1 for u in shopfront_out if u["st"] == k) for k in ("trad", "modern", "pub")},
      "awnings", sum(1 for u in shopfront_out if "aw" in u))
for f_ in ("roof_idx.bin",):
    if os.path.exists(os.path.join(OUT, f_)):
        os.remove(os.path.join(OUT, f_))
json.dump(foot_out, open(os.path.join(OUT, "footprints.json"), "w"), separators=(",", ":"))

# ---------------------------------------------------------------- water
print("water")
water_polys = []
for e in osm:
    t = e.get("tags", {})
    if t.get("natural") == "water" or t.get("waterway") == "riverbank" or t.get("landuse") in ("reservoir", "basin"):
        g = element_polygon(e)
        if g is None:
            continue
        water_polys += [p for p in polys_of(g.intersection(TILE)) if p.area > 20]
water_u = unary_union(water_polys) if water_polys else None
water_out = []
for p in polys_of(water_u):
    pm = pix_mask(p.buffer(-1.5)) or pix_mask(p)
    lvl = float(np.percentile(dtm[pm[0], pm[1]], 30)) if pm is not None and len(pm[0]) else 9.5
    ext = [[round(v, 2) for v in to_local(x, y)] for x, y in p.exterior.coords]
    holes = [[[round(v, 2) for v in to_local(x, y)] for x, y in r.coords] for r in p.interiors]
    water_out.append({"p": ext, "h": holes, "y": round(lvl, 2)})
json.dump(water_out, open(os.path.join(OUT, "water.json"), "w"), separators=(",", ":"))
print("water polys", len(water_out))

# ---------------------------------------------------------------- roads
print("roads")
WIDTH = {"motorway": 11, "trunk": 9, "primary": 7.5, "secondary": 7, "tertiary": 6.5, "unclassified": 5.5,
         "residential": 5.5, "living_street": 5, "service": 3.6, "track": 3, "pedestrian": 6,
         "primary_link": 5.5, "secondary_link": 5, "tertiary_link": 5, "trunk_link": 6, "motorway_link": 6, "road": 5}
PATHS = {"footway", "cycleway", "path", "steps", "bridleway"}
roads_out, paths_lines = [], []
# region roads run 1.5 m past their cells, closing the 1 m gap the town tile leaves at its edge
ROAD_CLIP = TILE.buffer(1.5, join_style="mitre") if REGION else TILE.buffer(-1)
_clip_edge = ROAD_CLIP.boundary
for e in osm:
    t = e.get("tags", {})
    hw = t.get("highway")
    if not hw or e["type"] != "way" or not e.get("geometry"):
        continue
    pts = proj(e["geometry"])
    line = LineString(pts).intersection(ROAD_CLIP)
    if line.is_empty:
        continue
    lines = [line] if line.geom_type == "LineString" else [l for l in getattr(line, "geoms", []) if l.geom_type == "LineString"]
    if hw in PATHS or hw in ("corridor", "platform", "construction", "proposed", "no"):
        for l in lines:
            paths_lines.append((list(l.coords), hw))
        continue
    if hw not in WIDTH:
        continue
    w = road_width(t) if hw in _DRIVE_W else WIDTH[hw]
    bridge = t.get("bridge") not in (None, "no")
    tunnel = t.get("tunnel") not in (None, "no")
    if tunnel:
        continue
    for l in lines:
        dl = l.segmentize(2.5)
        c = np.array(dl.coords)
        y = sample(dtm, c[:, 0], c[:, 1])
        if bridge and len(c) > 1:
            # deck: linear between abutments, never below terrain + clearance in middle
            d = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(c, axis=0).T))])
            y0, y1 = y[0], y[-1]
            lin = y0 + (y1 - y0) * d / max(d[-1], 1e-6)
            y = np.maximum(lin, y)
        else:
            y = ndimage.uniform_filter1d(y, size=5, mode="nearest")
        rec = {"t": hw, "w": round(w, 1), "pts": [[round(a, 2), round(b, 2), round(float(h), 2)] for (a, b), h in zip([to_local(*p) for p in c], y)]}
        if t.get("name"):
            rec["n"] = t["name"]
        elif t.get("ref"):
            rec["n"] = t["ref"]
        if t.get("ref"):
            rec["r"] = t["ref"]
        if bridge:
            rec["b"] = 1
        if t.get("oneway") == "yes":
            rec["o"] = 1
        # ends cut at the map edge continue in the neighbouring region: no round cap there
        cut = [int(_clip_edge.distance(Point(*c[0])) < 0.05), int(_clip_edge.distance(Point(*c[-1])) < 0.05)]
        if any(cut):
            rec["x"] = cut
        roads_out.append(rec)
json.dump(roads_out, open(os.path.join(OUT, "roads.json"), "w"), separators=(",", ":"))
print("roads", len(roads_out), "paths", len(paths_lines))

# ---------------------------------------------------------------- street furniture + boundaries (OSM)
print("street furniture")


def road_dir(x, y):
    """nearest drivable road: (distance, width, unit tangent (E,N), nearest point)"""
    pt = Point(x, y)
    i = ROAD_TREE.nearest(pt)
    ln = _rl[i]
    d = ln.project(pt)
    q = ln.interpolate(d)
    q2 = ln.interpolate(min(ln.length, d + 1.0))
    q1 = ln.interpolate(max(0.0, d - 1.0))
    tx, ty = q2.x - q1.x, q2.y - q1.y
    tl = math.hypot(tx, ty) or 1.0
    return pt.distance(q), _rw[i], (tx / tl, ty / tl), (q.x, q.y)


def yaw_local(dx, dy):
    """rotation.y for an object whose +z should face BNG direction (dx, dy)"""
    return round(math.atan2(dx, -dy), 3)


furn = []
NODE_KIND = {("amenity", "bench"): "bench", ("amenity", "waste_basket"): "bin", ("amenity", "post_box"): "postbox",
             ("highway", "bus_stop"): "busstop", ("amenity", "telephone"): "phone", ("man_made", "telephone_box"): "phone",
             ("man_made", "street_cabinet"): "cabinet", ("barrier", "bollard"): "bollard", ("amenity", "bicycle_parking"): "bikes",
             ("historic", "memorial"): "memorial", ("amenity", "grit_bin"): "grit"}
for e in street_osm:
    if e["type"] != "node":
        continue
    t = e.get("tags", {})
    E, N = TF.transform(e["lon"], e["lat"])
    if not in_tile(E, N):
        continue
    lx, lz = to_local(E, N)
    kind = next((v for (k, val), v in NODE_KIND.items() if t.get(k) == val), None)
    if kind:
        d, w, (tx, ty), (qx, qy) = road_dir(E, N)
        # face the road; bus stops sit at the kerb
        r = yaw_local(qx - E, qy - N) if d > 0.5 else yaw_local(-ty, tx)
        rec = {"t": kind, "x": round(lx, 2), "z": round(lz, 2), "r": r}
        if kind == "busstop" and t.get("shelter") == "yes":
            rec["s"] = 1
        furn.append(rec)
    hw = t.get("highway")
    if hw in ("crossing", "traffic_signals") or t.get("crossing") == "traffic_signals":
        d, w, (tx, ty), _ = road_dir(E, N)
        if d > 4:
            continue
        cr = t.get("crossing")
        kind = "signals" if hw == "traffic_signals" else ("zebra" if cr in ("marked", "uncontrolled", "zebra") or t.get("crossing_ref") == "zebra"
                                                         else "pelican" if cr == "traffic_signals" else None)
        if kind:
            furn.append({"t": kind, "x": round(lx, 2), "z": round(lz, 2), "r": yaw_local(tx, ty), "w": w})

# ---- Mapillary: objects detected in street-level photos (CC BY-SA), if fetched (tools/mapillary.py)
MLY_DIR = os.path.join(RDATA, "mapillary")
mly_feats, mly_signs, mly_cover = [], [], None
if os.path.exists(os.path.join(MLY_DIR, "map_features.json")):
    mly_feats = json.load(open(os.path.join(MLY_DIR, "map_features.json")))
    mly_signs = [f_ for f_ in mly_feats if f_.get("object_value", "").startswith(("regulatory--", "warning--", "information--", "complementary--"))]
if os.path.exists(os.path.join(MLY_DIR, "images.json")):
    _ip = []
    for im in json.load(open(os.path.join(MLY_DIR, "images.json"))):
        lon, lat = im["geometry"]["coordinates"]
        _ip.append(Point(*TF.transform(lon, lat)))
    if _ip:
        mly_cover = shapely.union_all([p_.buffer(25) for p_ in _ip])   # streets photographed within 25 m
        shapely.prepare(mly_cover)
MLY_KIND = {"object--street-light": "lamp", "object--bench": "bench", "object--trash-can": "bin",
            "object--mailbox": "postbox", "object--phone-booth": "phone", "object--junction-box": "cabinet",
            "object--bike-rack": "bikes", "object--support--utility-pole": "upole", "object--parking-meter": "meter",
            "object--traffic-light--general-upright": "sighead", "object--traffic-light--pedestrians": "sighead",
            "object--traffic-light--general-horizontal": "sighead"}
mly_counts = {}


def near_same(kind, x, z, r):
    return any(f["t"] == kind and (f["x"] - x) ** 2 + (f["z"] - z) ** 2 < r * r for f in furn)


for f_ in mly_feats:
    v_ = f_.get("object_value", "")
    kind = MLY_KIND.get(v_) or ("sighead" if v_.startswith("object--traffic-light--") else None)
    if not kind:
        continue
    lon, lat = f_["geometry"]["coordinates"]
    E, N = TF.transform(lon, lat)
    if not in_tile(E, N) or shapely.contains_xy(ALL_B, E, N):
        continue
    d, w, (tx, ty), (qx, qy) = road_dir(E, N)
    # detections are triangulated from photos (a few metres' error): pull roadside objects out of the
    # carriageway onto the pavement edge
    if kind in ("lamp", "upole", "sighead", "meter", "postbox", "bin") and d < w / 2 + 0.6:
        nx, ny = E - qx, N - qy
        nl = math.hypot(nx, ny)
        if nl < 1e-3:
            nx, ny = ty, -tx
            nl = 1.0
        E, N = qx + nx / nl * (w / 2 + 0.6), qy + ny / nl * (w / 2 + 0.6)
    lx, lz = to_local(E, N)
    if near_same(kind, lx, lz, 6 if kind in ("bench", "bin", "postbox", "phone", "cabinet", "bikes", "sighead") else 3):
        continue
    if kind == "sighead" and any(f["t"] in ("signals", "pelican") and (f["x"] - lx) ** 2 + (f["z"] - lz) ** 2 < 144 for f in furn):
        continue   # already have signal poles from the OSM crossing / junction node
    r = yaw_local(qx - E, qy - N)
    furn.append({"t": kind, "x": round(lx, 2), "z": round(lz, 2), "r": r, "src": "mly"})
    mly_counts[kind] = mly_counts.get(kind, 0) + 1

# traffic signs: post + plate shaped by category (regulatory roundel / warning triangle / information panel)
for f_ in mly_signs:
    v = f_.get("object_value", "")
    if not v or v.startswith("other-sign"):
        continue
    lon, lat = f_["geometry"]["coordinates"]
    E, N = TF.transform(lon, lat)
    if not in_tile(E, N) or shapely.contains_xy(ALL_B, E, N):
        continue
    d, w, (tx, ty), (qx, qy) = road_dir(E, N)
    if d > 25:
        continue
    if d < w / 2 + 0.5:
        nx, ny = E - qx, N - qy
        nl = math.hypot(nx, ny) or 1.0
        E, N = qx + nx / nl * (w / 2 + 0.5), qy + ny / nl * (w / 2 + 0.5)
    cat = "warn" if v.startswith("warning") else "reg" if v.startswith(("regulatory", "complementary")) else "info"
    sub = "speed" if "maximum-speed-limit" in v else "noentry" if "no-entry" in v else "stop" if "stop" in v.split("--")[1] else ""
    ad = f_.get("aligned_direction")
    r = yaw_local(math.sin(math.radians(ad)), math.cos(math.radians(ad))) if ad is not None else yaw_local(-tx, -ty)
    # the plate faces the traffic it is for, i.e. opposite to the direction that traffic travels
    lx, lz = to_local(E, N)
    rec = {"t": "tsign", "x": round(lx, 2), "z": round(lz, 2), "r": r, "c": cat}
    if sub:
        rec["s"] = sub
        if sub == "speed":
            try:
                rec["n"] = int(v.split("maximum-speed-limit-")[1].split("--")[0])
            except ValueError:
                pass
    furn.append(rec)
    mly_counts["tsign"] = mly_counts.get("tsign", 0) + 1
print("mapillary added", mly_counts, "coverage", "yes" if mly_cover is not None else "none")

# lamp posts: OSM has none mapped; on streets Mapillary has photographed, its detected street lights are
# used instead. Elsewhere, generate one every ~30 m along built-up streets (alternating sides).
LAMP_ROADS = {"primary", "secondary", "tertiary", "residential", "unclassified", "living_street", "primary_link"}
if RURAL:
    LAMP_ROADS = set()   # country lanes and the village are unlit
lamps = 0
lamp_pts = []
for rd in roads_out:
    if rd["t"] not in LAMP_ROADS:
        continue
    pts = rd["pts"]
    acc, side = 15.0, 1
    for a, b in zip(pts[:-1], pts[1:]):
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        acc += seg
        if acc < 30 or seg < 1e-3:
            continue
        acc = 0.0
        side = -side
        tx, tz = (b[0] - a[0]) / seg, (b[1] - a[1]) / seg
        nx, nz = -tz * side, tx * side
        off = rd["w"] / 2 + 0.7
        x, z = b[0] + nx * off, b[1] + nz * off
        E, N = x + E0, N0 - z
        if shapely.contains_xy(ALL_B, E, N) or not ALL_B.distance(Point(E, N)) < 30:
            continue
        if any(f_["t"] == "lamp" and f_.get("src") == "mly" and (f_["x"] - x) ** 2 + (f_["z"] - z) ** 2 < 400 for f_ in furn):
            continue
        if any((x - px) ** 2 + (z - pz) ** 2 < 100 for px, pz in lamp_pts[-40:]):
            continue
        lamp_pts.append((x, z))
        furn.append({"t": "lamp", "x": round(x, 2), "z": round(z, 2), "r": round(math.atan2(-nx, -nz), 3)})
        lamps += 1

barriers = []
for e in street_osm:
    t = e.get("tags", {})
    bt = t.get("barrier")
    if e["type"] != "way" or bt not in ("wall", "fence", "hedge", "retaining_wall", "city_wall") or not e.get("geometry"):
        continue
    co = proj(e["geometry"])
    try:
        h = float(str(t.get("height", "")).split()[0])
    except (ValueError, IndexError):
        h = {"wall": 1.6, "fence": 1.2, "hedge": 1.7, "retaining_wall": 1.0, "city_wall": 2.5}[bt]
    mat = t.get("material", "")
    parts_ = [co]
    if REGION:
        cl = LineString(co).intersection(TILE)
        parts_ = [list(l.coords) for l in getattr(cl, "geoms", [cl]) if l.geom_type == "LineString" and l.length > 0.5]
    for co in parts_:
        barriers.append({"t": bt, "h": round(min(h, 4), 2), "m": mat,
                         "p": [[round(v, 2) for v in to_local(x, y)] for x, y in co]})
# ---------------------------------------------------------------- pavements (OSM sidewalk tags + building frontages)
print("pavements")
PAVE_ROADS = {"primary", "secondary", "tertiary", "residential", "unclassified", "living_street",
              "primary_link", "secondary_link", "tertiary_link", "trunk", "trunk_link"}
road_surf, bands, frontage_fill, corridor = [], [], [], []
for e in osm:
    t = e.get("tags", {})
    hwy = t.get("highway")
    if e["type"] != "way" or not e.get("geometry") or hwy not in _DRIVE_W:
        continue
    if t.get("tunnel") not in (None, "no"):
        continue
    line = LineString(proj(e["geometry"]))
    w = road_width(t)
    road_surf.append(line.buffer(w / 2, cap_style="flat" if hwy != "service" else "round"))
    if hwy not in PAVE_ROADS or t.get("bridge") not in (None, "no"):
        continue
    sw = t.get("sidewalk")
    left = right = None
    if sw in ("both", "yes"):
        left = right = True
    elif sw == "left":
        left, right = True, False
    elif sw == "right":
        left, right = False, True
    elif sw in ("no", "none", "separate"):
        left = right = False
    for side in ("left", "right"):
        v = t.get(f"sidewalk:{side}") or t.get("sidewalk:both")
        if v in ("yes", "no", "separate"):
            if side == "left":
                left = v == "yes"
            else:
                right = v == "yes"
    built_up = not RURAL and ALL_B.distance(line) < 25
    if built_up:
        corridor.append(line.buffer(w / 2 + 7, cap_style="flat"))
    if left is None:
        left = built_up
    if right is None:
        right = built_up
    pw = 2.4 if hwy in ("primary", "secondary", "trunk") and built_up else 2.0
    for on, sgn in ((left, 1), (right, -1)):
        if not on:
            continue
        outer = line.buffer(sgn * (w / 2 + pw), single_sided=True, cap_style="flat")
        bands.append(outer)
        # town frontages: pave right up to buildings standing within 5.5 m of the kerb on this side
        reach = line.buffer(sgn * (w / 2 + 6.0), single_sided=True, cap_style="flat")
        for bi in ctx["B_TREE"].query(reach, predicate="intersects"):
            b = _all[bi]
            if b.distance(line) - w / 2 <= 5.5:
                frontage_fill.append(reach.intersection(b.buffer(3.6, join_style="mitre")))
sep = []
for e in osm:
    t = e.get("tags", {})
    if e["type"] == "way" and t.get("highway") == "footway" and t.get("footway") == "sidewalk" and e.get("geometry"):
        sep.append(LineString(proj(e["geometry"])).buffer(1.0, cap_style="flat"))
ROADS_U = shapely.union_all(road_surf)
pave = shapely.union_all(bands + frontage_fill + sep)
# close narrow gaps (< 5 m) between kerb, separately-mapped footways and building frontages,
# but only inside built-up street corridors so back gardens and parks stay green
solid = shapely.union_all([pave, ROADS_U, ALL_B])
closed = solid.buffer(2.5, join_style="mitre").buffer(-2.5, join_style="mitre")
CORR = shapely.union_all(corridor)
closed2 = solid.buffer(5.0, join_style="mitre").buffer(-5.0, join_style="mitre")
pave = shapely.union_all([pave, closed.intersection(CORR), closed2.intersection(CORR).intersection(ALL_B.buffer(12))])
pave = pave.difference(ROADS_U).difference(ALL_B)
if water_u is not None:
    pave = pave.difference(water_u)
pave = pave.intersection(TILE.buffer(-2)).simplify(0.15)
pave_polys = [p_ for p_ in polys_of(pave) if p_.area > 1.0]
# drop paved "islands" left between road arms: small, nowhere near a building, ringed mostly by kerb
_re = ROADS_U.buffer(0.1)
def _is_island(p_):
    if p_.area > 250 or p_.distance(ALL_B) < 3:
        return False
    ring = p_.exterior
    return ring.intersection(_re).length > 0.6 * ring.length
_n0 = len(pave_polys)
road_islands = [p_ for p_ in pave_polys if _is_island(p_)]
pave_polys = [p_ for p_ in pave_polys if not _is_island(p_)]
print("pavement islands removed:", _n0 - len(pave_polys))
PAVE_U = shapely.union_all(pave_polys)
shapely.prepare(PAVE_U)
print("pavement polygons", len(pave_polys), "area m2", round(sum(p_.area for p_ in pave_polys)))
# triangles (2D, local coords) + kerb edges (pavement edges lying on a carriageway edge)
road_edge = ROADS_U.boundary
pv_tris, kerbs = [], []
for p_ in pave_polys:
    pp = shapely.segmentize(p_, 2.0)
    for t_ in shapely.get_parts(shapely.constrained_delaunay_triangles(pp)):
        co = shapely.get_coordinates(t_)[:3]
        for E, N in co:
            pv_tris += [round(v, 2) for v in to_local(E, N)]
    for ring in [pp.exterior] + list(pp.interiors):
        co = shapely.get_coordinates(ring)
        mids = (co[:-1] + co[1:]) / 2
        on_road = shapely.distance(road_edge, shapely.points(mids)) < 0.08
        for (a_, b_), k_ in zip(zip(co[:-1], co[1:]), on_road):
            if k_:
                kerbs += [round(v, 2) for v in to_local(*a_)] + [round(v, 2) for v in to_local(*b_)]
np.array(pv_tris, np.float32).tofile(os.path.join(OUT, "pave_tris.bin"))
np.array(kerbs, np.float32).tofile(os.path.join(OUT, "kerbs.bin"))
print("pavement tris", len(pv_tris) // 6, "kerb segments", len(kerbs) // 4)

# bus stop bays: yellow box on the carriageway at the kerb nearest each stop
for f in furn:
    if f["t"] != "busstop":
        continue
    E, N = f["x"] + E0, N0 - f["z"]
    d, w, (tx, ty), (qx, qy) = road_dir(E, N)
    if d > 15:
        continue
    nx, ny = ty, -tx
    if (E - qx) * nx + (N - qy) * ny < 0:
        nx, ny = -nx, -ny
    cx, cy = qx + nx * (w / 2 - 1.3), qy + ny * (w / 2 - 1.3)
    lx, lz = to_local(cx, cy)
    furn.append({"t": "busbay", "x": round(lx, 2), "z": round(lz, 2), "r": yaw_local(tx, ty), "w": 2.4})
# ---- crossing poles (Belisha beacons / signal poles) as their own objects, so they get checked below too
for f in list(furn):
    if f["t"] not in ("zebra", "pelican", "signals"):
        continue
    cx_, cz_ = math.cos(f["r"]), -math.sin(f["r"])         # across the road, local frame
    for sgn in (-1, 1):
        o = sgn * (f["w"] / 2 + 0.7)
        furn.append({"t": "beacon" if f["t"] == "zebra" else "signal",
                     "x": round(f["x"] + cx_ * o, 2), "z": round(f["z"] + cz_ * o, 2),
                     "r": round(f["r"] + (math.pi if (sgn > 0 and f["t"] != "zebra") else 0), 3)})

# ---- keep every standing object off the carriageway: push it to the nearest kerb + 0.6 m, or drop it
ROAD_KEEP_OUT = ROADS_U.buffer(0.35)
ROAD_EDGE_OUT = ROADS_U.buffer(0.6)
shapely.prepare(ROAD_KEEP_OUT)
MARKINGS = {"zebra", "pelican", "signals", "busbay"}
from collections import Counter
kept, moved_n, dropped = [], 0, Counter()
for f in furn:
    if f["t"] in MARKINGS or f["t"] == "memorial":
        kept.append(f)
        continue
    E, N = f["x"] + E0, N0 - f["z"]
    if shapely.contains_xy(ROAD_KEEP_OUT, E, N):
        pt = Point(E, N)
        q = shapely.ops.nearest_points(ROAD_EDGE_OUT.exterior if ROAD_EDGE_OUT.geom_type == "Polygon" else ROAD_EDGE_OUT.boundary, pt)[0]
        if q.distance(pt) > 7 or shapely.contains_xy(ALL_B, q.x, q.y) or shapely.contains_xy(ROAD_KEEP_OUT, q.x, q.y):
            dropped[f["t"]] += 1
            continue
        f["x"], f["z"] = [round(v, 2) for v in to_local(q.x, q.y)]
        moved_n += 1
    # where there is pavement nearby, a street object belongs on it, not on a verge or island
    E, N = f["x"] + E0, N0 - f["z"]
    pt = Point(E, N)
    if not shapely.contains_xy(PAVE_U, E, N) and ROADS_U.distance(pt) < 2.5:
        near = PAVE_U.distance(pt)
        if near < 9:
            q = shapely.ops.nearest_points(PAVE_U, pt)[0]
            dx_, dy_ = q.x - E, q.y - N
            dl = math.hypot(dx_, dy_) or 1
            q2 = (q.x + dx_ / dl * 0.4, q.y + dy_ / dl * 0.4)
            if shapely.contains_xy(PAVE_U, *q2) and not shapely.contains_xy(ROAD_KEEP_OUT, *q2):
                f["x"], f["z"] = [round(v, 2) for v in to_local(*q2)]
                moved_n += 1
            else:
                dropped[f["t"]] += 1
                continue
    kept.append(f)
furn = kept
print("objects moved off carriageways / onto pavements:", moved_n, "dropped:", dict(dropped))


def raster_mask(geoms, buf=0.0):
    """1 m boolean raster (region grid) of polygons"""
    im = Image.new("L", (SIZE, SIZE), 0)
    dr = ImageDraw.Draw(im)
    for p_ in geoms:
        p_ = p_.buffer(buf) if buf else p_
        for q_ in polys_of(p_):
            dr.polygon([(x - E_MIN, N_MAX - y) for x, y in q_.exterior.coords], fill=255)
            for r_ in q_.interiors:
                dr.polygon([(x - E_MIN, N_MAX - y) for x, y in r_.coords], fill=0)
    return np.array(im) > 0


def trace_skeleton(sk):
    """Split a 1-pixel skeleton into pixel paths between end / junction pixels."""
    pts = set(zip(*np.nonzero(sk)))
    nb = lambda p: [(p[0] + a, p[1] + b) for a in (-1, 0, 1) for b in (-1, 0, 1) if (a or b) and (p[0] + a, p[1] + b) in pts]
    deg = {p: len(nb(p)) for p in pts}
    seen, paths = set(), []
    for start in [p for p in pts if deg[p] != 2] + list(pts):
        for q in nb(start):
            if (start, q) in seen:
                continue
            path, prev, cur = [start], start, q
            seen.add((start, q)); seen.add((q, start))
            while True:
                path.append(cur)
                if deg[cur] != 2 or cur == start:
                    break
                nxt = [r for r in nb(cur) if r != prev and (cur, r) not in seen]
                if not nxt:
                    break
                seen.add((cur, nxt[0])); seen.add((nxt[0], cur))
                prev, cur = cur, nxt[0]
            paths.append(path)
    return paths


if RURAL:
    # ---- hedgerows from LIDAR: long, narrow strips of 0.8-7 m canopy that are not buildings, water or road.
    # Each strip is thinned to its centre line and becomes a hedge (height = median canopy along it).
    from skimage.morphology import skeletonize
    print("hedgerows")
    canopy_ = dsm_s - dtm
    WATER_M = raster_mask(water_polys) if water_polys else np.zeros((SIZE, SIZE), bool)
    ROAD_M = raster_mask([ROADS_U], 0.8)
    TILE_M = raster_mask([TILE])
    hm = (canopy_ > 0.8) & (canopy_ < 7) & ~(np.array(bmask) > 0) & ~WATER_M & ~ROAD_M & TILE_M
    hm = ndimage.binary_opening(hm, np.ones((2, 2), bool))
    lab, nlab = ndimage.label(hm, np.ones((3, 3), bool))
    area = np.bincount(lab.ravel())
    sk = skeletonize(hm & (area[lab] >= 12))
    sk_len = np.bincount(lab[sk], minlength=nlab + 1)
    # hedge-like: at least 8 m of centre line, under 5 m wide on average (rules out crops, gardens, copses)
    keep = (sk_len >= 8) & (area < 5.0 * np.maximum(sk_len, 1))
    keep[0] = False
    sk &= keep[lab]
    n_h, len_h = 0, 0.0
    ROAD_CLEAR = ROADS_U.buffer(1.2)   # hedge centre line (1.6 m wide) stays clear of the carriageway
    shapely.prepare(ROAD_CLEAR)
    for path in trace_skeleton(sk):
        if len(path) < 6:
            continue
        rr_ = np.array([p_[0] for p_ in path]); cc_ = np.array([p_[1] for p_ in path])
        line = LineString(np.column_stack([E_MIN + cc_ + 0.5, N_MAX - rr_ - 0.5])).simplify(0.7)
        if line.length < 6:
            continue
        h = float(np.clip(np.median(canopy_[rr_, cc_]), 1.2, 3.0))   # taller growth along it is trees (modelled separately)
        line = line.difference(ROAD_CLEAR)
        for part in getattr(line, "geoms", [line]):
            if part.geom_type != "LineString" or part.length < 3:
                continue
            barriers.append({"t": "hedge", "h": round(h, 2), "m": "lidar",
                             "p": [[round(v, 2) for v in to_local(x, y)] for x, y in part.coords]})
            n_h += 1
            len_h += part.length
    print("hedges from LIDAR:", n_h, "length km", round(len_h / 1000, 1))

json.dump({"furniture": furn, "barriers": barriers}, open(os.path.join(OUT, "street.json"), "w"), separators=(",", ":"))
from collections import Counter as _C
print("furniture", _C(f["t"] for f in furn), "barriers", len(barriers))

if REGION:
    # ------------------------------------------------------------ region terrain: one node grid per cell
    # Nodes sit on the cell edges (201 x 201 at 2.5 m), so neighbouring cells share their edge heights.
    print("terrain (cells)")
    GS = 2.5
    GN = int(CS / GS) + 1
    dtm_f = ndimage.uniform_filter(dtm, 2)
    T_, D_, K_ = [], [], []
    for e_, n_ in CELLS:
        ys_ = (n_ + CS) - np.arange(GN) * GS
        xs_ = e_ + np.arange(GN) * GS
        rr, cc = np.meshgrid(N_MAX - ys_ - 0.5, xs_ - E_MIN - 0.5, indexing="ij")
        terr = ndimage.map_coordinates(dtm_f, [rr, cc], order=1, mode="nearest")
        gx = lambda E: (E - e_) / GS + 0.5
        gy = lambda N: (n_ + CS - N) / GS + 0.5
        kimg = Image.new("L", (GN, GN), 0)
        kd = ImageDraw.Draw(kimg)
        for p in polys_of(water_u):
            kd.polygon([(gx(x), gy(y)) for x, y in p.exterior.coords], fill=1)
            for r in p.interiors:
                kd.polygon([(gx(x), gy(y)) for x, y in r.coords], fill=0)
        bimg = Image.new("F", (GN, GN), 0)
        bd = ImageDraw.Draw(bimg)
        for rd in roads_out:
            if rd.get("b"):
                for a, b in zip(rd["pts"][:-1], rd["pts"][1:]):
                    bd.line([(gx(a[0] + E0), gy(N0 - a[1])), (gx(b[0] + E0), gy(N0 - b[1]))],
                            fill=float((a[2] + b[2]) / 2), width=max(2, int(rd["w"] / GS + 1)))
        kind = np.array(kimg, np.uint8)
        bh = np.array(bimg, np.float32)
        kind[bh > 0] = 2
        T_.append(terr); D_.append(np.where(bh > 0, np.maximum(bh, terr), terr)); K_.append(kind)
    hmin = float(min(t_.min() for t_ in T_)) - 1
    json.dump({"cells": [[e_ - E0, N0 - n_ - CS] for e_, n_ in CELLS], "n": GN, "s": GS, "cell": CS, "min": hmin},
              open(os.path.join(OUT, "terrain.json"), "w"))
    np.round((np.array(T_) - hmin) * 100).astype(np.uint16).tofile(os.path.join(OUT, "terrain.bin"))
    np.round((np.array(D_) - hmin) * 100).astype(np.uint16).tofile(os.path.join(OUT, "drive.bin"))
    np.array(K_, np.uint8).tofile(os.path.join(OUT, "kind.bin"))

    # ------------------------------------------------------------ trees from LIDAR canopy (inside the cells)
    print("trees")
    canopy = dsm_s - dtm
    bm = np.array(bmask) > 0
    cand = (canopy > 4) & ~bm & ~WATER_M & TILE_M & ~raster_mask([ROADS_U], 0.6)   # canopy over a road is not a trunk
    mx = ndimage.maximum_filter(np.where(cand, canopy, 0), size=7)
    rows, cols = np.nonzero(cand & (canopy == mx))
    trees = []
    for r, c in zip(rows, cols):
        h = float(canopy[r, c])
        if h > 30:
            continue
        x, z = to_local(E_MIN + c + 0.5, N_MAX - r - 0.5)
        trees.append([round(x, 1), round(float(dtm[r, c]), 1), round(z, 1), round(h, 1)])
    json.dump(trees, open(os.path.join(OUT, "trees.json"), "w"), separators=(",", ":"))
    print("trees", len(trees))

    # ------------------------------------------------------------ ground textures: 1024 px per cell, 16 cells per atlas
    print("ground textures")
    LAND = {
        "residential": (112, 130, 84), "retail": (150, 146, 138), "commercial": (148, 144, 136), "industrial": (138, 136, 130),
        "farmland": (128, 140, 74), "meadow": (118, 146, 72), "grass": (104, 144, 66), "recreation_ground": (98, 150, 66),
        "cemetery": (96, 128, 70), "allotments": (120, 120, 76), "forest": (70, 100, 54), "religious": (108, 142, 74),
        "construction": (150, 136, 112), "railway": (130, 122, 110), "brownfield": (140, 130, 108), "farmyard": (140, 128, 104),
        "village_green": (100, 150, 66), "orchard": (100, 130, 64),
    }
    LEIS = {"park": (98, 148, 66), "pitch": (92, 154, 70), "golf_course": (100, 152, 70), "garden": (102, 140, 70),
            "playground": (150, 140, 110), "sports_centre": (140, 138, 130), "nature_reserve": (110, 138, 70), "marina": (90, 120, 140)}
    layers = []
    for e in osm:
        t = e.get("tags", {})
        col = None
        if t.get("landuse") in LAND:
            col, pri = LAND[t["landuse"]], 0
        elif t.get("leisure") in LEIS:
            col, pri = LEIS[t["leisure"]], 1
        elif t.get("amenity") == "parking":
            col, pri = (92, 92, 94), 2
        elif t.get("natural") in ("wood", "scrub", "grassland", "wetland"):
            col, pri = {"wood": (70, 100, 54), "scrub": (96, 116, 62), "grassland": (112, 146, 70), "wetland": (96, 126, 90)}[t["natural"]], 0
        if col is None:
            continue
        g = element_polygon(e)
        if g is None:
            continue
        for p in polys_of(g.intersection(TILE)):
            layers.append((pri, -p.area, p, col))
    layers.sort(key=lambda a: (a[0], a[1]))
    road_gaps = ROADS_U.buffer(3.0, join_style="mitre").buffer(-3.0, join_style="mitre").difference(ROADS_U).difference(ALL_B)
    tarmac = road_islands + [g_ for g_ in polys_of(road_gaps) if g_.area > 0.5] + polys_of(ROADS_U)
    gy_all, gx_all = np.gradient(ndimage.uniform_filter(dtm, 3))
    TS, PER = 1024, 4
    atlases = []
    for ci, (e_, n_) in enumerate(CELLS):
        sc = TS / CS
        cellb = box(e_ - 2, n_ - 2, e_ + CS + 2, n_ + CS + 2)
        img = Image.new("RGB", (TS, TS), (104, 132, 70))
        d = ImageDraw.Draw(img)
        P = lambda E, N: ((E - e_) * sc, (n_ + CS - N) * sc)

        def fill(p, col):
            if not p.intersects(cellb):
                return
            if not p.interiors:
                d.polygon([P(x, y) for x, y in p.exterior.coords], fill=col)
                return
            m = Image.new("L", (TS, TS), 0)
            md = ImageDraw.Draw(m)
            md.polygon([P(x, y) for x, y in p.exterior.coords], fill=255)
            for r in p.interiors:
                md.polygon([P(x, y) for x, y in r.coords], fill=0)
            img.paste(col, (0, 0, TS, TS), m)
        for _, _, p, col in layers:
            fill(p, col)
        r0, c0 = N_MAX - (n_ + CS), e_ - E_MIN
        sl = (slice(r0, r0 + CS), slice(c0, c0 + CS))
        cimg = Image.fromarray((np.clip(canopy[sl], 0, 12) / 12 * 255 * (~bm[sl])).astype(np.uint8)).resize((TS, TS), Image.BILINEAR)
        shade = np.clip(1 + (gx_all[sl] * -0.6 + gy_all[sl] * 0.6) * 0.8, 0.75, 1.2)
        simg = Image.fromarray((shade * 200).astype(np.uint8)).resize((TS, TS), Image.BILINEAR)
        a = np.asarray(img).astype(np.float32)
        a *= (np.asarray(simg, np.float32) / 200)[..., None]
        cn = np.asarray(cimg, np.float32)[..., None] / 255
        a = a * (1 - 0.35 * cn) + np.array([40, 64, 30]) * 0.35 * cn
        img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
        d = ImageDraw.Draw(img)
        for p in polys_of(water_u):
            fill(p, (52, 78, 82))
        for p_ in pave_polys:
            fill(p_, (168, 165, 158))
        for p_ in tarmac:
            fill(p_, (86, 88, 90))
        for coords, hw in paths_lines:
            col = (176, 170, 158) if hw != "cycleway" else (160, 120, 110)
            d.line([P(x, y) for x, y in coords], fill=col, width=max(2, int(2.2 * sc)))
        img = img.filter(ImageFilter.GaussianBlur(0.6))
        ai, k = divmod(ci, PER * PER)
        if k == 0:
            atlases.append(Image.new("RGB", (TS * PER, TS * PER), (104, 132, 70)))
        atlases[ai].paste(img, ((k % PER) * TS, (k // PER) * TS))
    for ai, A in enumerate(atlases):
        A.save(os.path.join(OUT, f"ground{ai}.jpg"), quality=86)
    tj = json.load(open(os.path.join(OUT, "terrain.json")))
    tj["atlas"] = {"per": PER, "n": len(atlases)}

    # ------------------------------------------------------------ far landscape: 10 m EA LIDAR, 20 m mesh
    # Seen beyond the detailed cells (through the haze). Woods, hedgerows and trees show as darker green
    # wherever the 10 m surface model stands over 2.5 m above the ground.
    if os.path.exists(os.path.join(RDATA, "horizon_dtm.npy")):
        print("horizon")
        HE0, HN0, HE1, HN1 = 380500, 229000, 392500, 240000          # tools/region.py HORIZON
        hz_t = np.load(os.path.join(RDATA, "horizon_dtm.npy"))
        hz_t = np.where(np.isnan(hz_t), np.nanmedian(hz_t), hz_t)
        if os.path.exists(os.path.join(RDATA, "horizon_dsm.npy")):
            hz_s = np.load(os.path.join(RDATA, "horizon_dsm.npy"))
            hz_s = np.where(np.isnan(hz_s), hz_t, np.maximum(hz_s, hz_t))
        else:
            # no 10 m surface model: mark woods mapped in OSM (town + region extracts) as 8 m canopy
            wim = Image.new("L", (hz_t.shape[1], hz_t.shape[0]), 0)
            wd_ = ImageDraw.Draw(wim)
            for src in (osm, json.load(open(os.path.join(DATA, "osm.json")))["elements"]):
                for e in src:
                    t = e.get("tags", {})
                    if t.get("natural") == "wood" or t.get("landuse") in ("forest", "orchard"):
                        for p in polys_of(element_polygon(e)):
                            wd_.polygon([((x - HE0) / 10, (HN1 - y) / 10) for x, y in p.exterior.coords], fill=8)
            hz_s = hz_t + np.array(wim, np.float32)
        sub = hz_t[::2, ::2]
        nz_, nx_ = sub.shape
        hmin_h = float(sub.min()) - 1
        np.round((sub - hmin_h) * 100).astype(np.uint16).tofile(os.path.join(OUT, "horizon.bin"))
        tj["horizon"] = {"x0": HE0 + 5 - E0, "z0": N0 - (HN1 - 5), "nx": nx_, "nz": nz_, "s": 20, "min": hmin_h}
        span = hz_t[:2 * (nz_ - 1) + 1, :2 * (nx_ - 1) + 1]
        can = np.clip((hz_s - hz_t)[:span.shape[0], :span.shape[1]], 0, 12)
        gy_h, gx_h = np.gradient(ndimage.uniform_filter(span, 3), 10)
        shade = np.clip(1 + (gx_h * -0.6 + gy_h * 0.6) * 8, 0.75, 1.2)[..., None]
        field = np.array([116, 138, 74], np.float32)
        var_ = (ndimage.uniform_filter(np.random.default_rng(7).random(span.shape), 9) - 0.5)[..., None] * 60
        wood = np.clip((can - 2.5) / 4, 0, 1)[..., None]
        rgb = (field + var_ * np.array([0.5, 0.6, 0.3])) * (1 - wood * 0.55) + np.array([44, 70, 36]) * wood * 0.55
        Image.fromarray(np.clip(rgb * shade, 0, 255).astype(np.uint8)).save(os.path.join(OUT, "horizon.jpg"), quality=85)
    json.dump(tj, open(os.path.join(OUT, "terrain.json"), "w"))

    # the world's build stamp lives in the town's meta.json: bump it so browsers fetch the new region files
    import time as _time
    mp = os.path.join(ROOT, "game", "data", "meta.json")
    meta = json.load(open(mp))
    meta["build"] = _time.strftime("%Y%m%d%H%M%S")
    meta["regions"] = sorted(set(meta.get("regions", [])) | {REGION})
    json.dump(meta, open(mp, "w"))
    print("done")
    sys.exit(0)

# ---------------------------------------------------------------- terrain grid (2.5 m)
print("terrain")
GN, GS = 1000, 2.5
k = (np.arange(GN) + 0.5) * GS - 0.5
rr, cc = np.meshgrid(k, k, indexing="ij")
terr = ndimage.map_coordinates(ndimage.uniform_filter(dtm, 2), [rr, cc], order=1, mode="nearest")
# kind grid: 0 land, 1 water, 2 bridge
kind = np.zeros((GN, GN), np.uint8)
kimg = Image.new("L", (GN, GN), 0)
kd = ImageDraw.Draw(kimg)
gx = lambda E: (E - E_MIN) / GS
gy = lambda N: (N_MAX - N) / GS
for p in polys_of(water_u):
    kd.polygon([(gx(x), gy(y)) for x, y in p.exterior.coords], fill=1)
    for r in p.interiors:
        kd.polygon([(gx(x), gy(y)) for x, y in r.coords], fill=0)
bridge_img = Image.new("F", (GN, GN), 0)
bd = ImageDraw.Draw(bridge_img)
for rd in roads_out:
    if not rd.get("b"):
        continue
    pts = rd["pts"]
    for a, b in zip(pts[:-1], pts[1:]):
        E1, N1, E2, N2 = a[0] + E0, N0 - a[1], b[0] + E0, N0 - b[1]
        bd.line([(gx(E1), gy(N1)), (gx(E2), gy(N2))], fill=float((a[2] + b[2]) / 2), width=max(2, int(rd["w"] / GS + 1)))
kind = np.array(kimg, np.uint8)
bh = np.array(bridge_img, np.float32)
kind[bh > 0] = 2
drive = np.where(bh > 0, np.maximum(bh, terr), terr)
hmin = float(terr.min()) - 1
json.dump({"n": GN, "s": GS, "min": hmin, "origin": -SIZE / 2, "E0": E0, "N0": N0}, open(os.path.join(OUT, "terrain.json"), "w"))
np.round((terr - hmin) * 100).astype(np.uint16).tofile(os.path.join(OUT, "terrain.bin"))
np.round((drive - hmin) * 100).astype(np.uint16).tofile(os.path.join(OUT, "drive.bin"))
kind.tofile(os.path.join(OUT, "kind.bin"))

# ---------------------------------------------------------------- trees from LIDAR canopy
print("trees")
canopy = dsm_s - dtm
bm = np.array(bmask) > 0
wm = np.array(Image.fromarray(kind * 120).resize((SIZE, SIZE), Image.NEAREST)) > 0
cand = (canopy > 4) & ~bm & ~wm & ~raster_mask([ROADS_U], 0.6)   # canopy over a road is not a trunk
mx = ndimage.maximum_filter(np.where(cand, canopy, 0), size=7)
peaks = cand & (canopy == mx)
rows, cols = np.nonzero(peaks)
trees = []
for r, c in zip(rows, cols):
    h = float(canopy[r, c])
    if h > 30:
        continue
    E, N = E_MIN + c + 0.5, N_MAX - r - 0.5
    x, z = to_local(E, N)
    trees.append([round(x, 1), round(float(dtm[r, c]), 1), round(z, 1), round(h, 1)])
json.dump(trees, open(os.path.join(OUT, "trees.json"), "w"), separators=(",", ":"))
print("trees", len(trees))

# ---------------------------------------------------------------- ground texture
print("ground texture")
TS = 4096
sc = TS / SIZE
img = Image.new("RGB", (TS, TS), (104, 132, 70))
d = ImageDraw.Draw(img)
P = lambda E, N: ((E - E_MIN) * sc, (N_MAX - N) * sc)
LAND = {
    "residential": (112, 130, 84), "retail": (150, 146, 138), "commercial": (148, 144, 136), "industrial": (138, 136, 130),
    "farmland": (128, 140, 74), "meadow": (118, 146, 72), "grass": (104, 144, 66), "recreation_ground": (98, 150, 66),
    "cemetery": (96, 128, 70), "allotments": (120, 120, 76), "forest": (70, 100, 54), "religious": (108, 142, 74),
    "construction": (150, 136, 112), "railway": (130, 122, 110), "brownfield": (140, 130, 108), "farmyard": (140, 128, 104),
    "village_green": (100, 150, 66), "orchard": (100, 130, 64),
}
LEIS = {"park": (98, 148, 66), "pitch": (92, 154, 70), "golf_course": (100, 152, 70), "garden": (102, 140, 70),
        "playground": (150, 140, 110), "sports_centre": (140, 138, 130), "nature_reserve": (110, 138, 70), "marina": (90, 120, 140)}


def fill_poly(p, col):
    """Paint polygon p in col on the ground texture, leaving its holes showing whatever is underneath."""
    if not p.interiors:
        d.polygon([P(x, y) for x, y in p.exterior.coords], fill=col)
        return
    target = d._image
    minx, miny, maxx, maxy = p.bounds
    x0, y0 = P(minx, maxy)
    x1, y1 = P(maxx, miny)
    x0, y0 = max(0, int(x0) - 1), max(0, int(y0) - 1)
    x1, y1 = min(target.size[0], int(x1) + 2), min(target.size[1], int(y1) + 2)
    if x1 <= x0 or y1 <= y0:
        return
    m = Image.new("L", (x1 - x0, y1 - y0), 0)
    md = ImageDraw.Draw(m)
    md.polygon([(P(x, y)[0] - x0, P(x, y)[1] - y0) for x, y in p.exterior.coords], fill=255)
    for r in p.interiors:
        md.polygon([(P(x, y)[0] - x0, P(x, y)[1] - y0) for x, y in r.coords], fill=0)
    target.paste(col, (x0, y0, x1, y1), m)


layers = []
for e in osm:
    t = e.get("tags", {})
    col = None
    if t.get("landuse") in LAND:
        col, pri = LAND[t["landuse"]], 0
    elif t.get("leisure") in LEIS:
        col, pri = LEIS[t["leisure"]], 1
    elif t.get("amenity") == "parking":
        col, pri = (92, 92, 94), 2
    elif t.get("natural") in ("wood", "scrub", "grassland", "wetland"):
        col, pri = {"wood": (70, 100, 54), "scrub": (96, 116, 62), "grassland": (112, 146, 70), "wetland": (96, 126, 90)}[t["natural"]], 0
    if col is None:
        continue
    g = element_polygon(e)
    if g is None:
        continue
    for p in polys_of(g.intersection(TILE)):
        layers.append((pri, -p.area, p, col))
layers.sort(key=lambda a: (a[0], a[1]))
for _, _, p, col in layers:
    fill_poly(p, col)
# canopy darkening + hillshade
cimg = Image.fromarray((np.clip(canopy, 0, 12) / 12 * 255 * (~bm)).astype(np.uint8)).resize((TS, TS), Image.BILINEAR)
gy_, gx_ = np.gradient(ndimage.uniform_filter(dtm, 3))
shade = np.clip(1 + (gx_ * -0.6 + gy_ * 0.6) * 0.8, 0.75, 1.2)
simg = Image.fromarray((shade * 200).astype(np.uint8)).resize((TS, TS), Image.BILINEAR)
a = np.asarray(img).astype(np.float32)
a *= (np.asarray(simg, np.float32) / 200)[..., None]
cn = np.asarray(cimg, np.float32)[..., None] / 255
a = a * (1 - 0.35 * cn) + np.array([40, 64, 30]) * 0.35 * cn
img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
d = ImageDraw.Draw(img)
for p in polys_of(water_u):
    fill_poly(p, (52, 78, 82))
for p_ in pave_polys:
    fill_poly(p_, (168, 165, 158))
# leftover space between road arms (removed islands, narrow wedges between carriageways) reads as tarmac
road_gaps = shapely.union_all([
    ROADS_U.buffer(3.0, join_style="mitre").buffer(-3.0, join_style="mitre"),
    ROADS_U.buffer(5.0, join_style="mitre").buffer(-5.0, join_style="mitre").intersection(CORR),   # wider in town
]).difference(ROADS_U).difference(PAVE_U).difference(ALL_B)
for p_ in road_islands + [g_ for g_ in polys_of(road_gaps) if g_.area > 0.5] + polys_of(ROADS_U):
    fill_poly(p_, (86, 88, 90))
for coords, hw in paths_lines:
    col = (176, 170, 158) if hw != "cycleway" else (160, 120, 110)
    d.line([P(x, y) for x, y in coords], fill=col, width=max(2, int(2.2 * sc)))
img = img.filter(ImageFilter.GaussianBlur(0.6))
img.save(os.path.join(OUT, "ground.jpg"), quality=88)

# ---------------------------------------------------------------- spawn: The Cross
# The Cross = where High Street, Church Street and Barton Street meet
def named_pts(n):
    return np.array([p[:2] for r in roads_out if r.get("n") == n for p in r["pts"]])
H, C = named_pts("High Street"), named_pts("Church Street")
dd = np.hypot(H[:, None, 0] - C[None, :, 0], H[:, None, 1] - C[None, :, 1])
i, j = np.unravel_index(np.argmin(dd), dd.shape)
spawn = ((H[i] + C[j]) / 2).tolist()
import time as _time
_regions = json.load(open(os.path.join(OUT, "meta.json"))).get("regions", []) if os.path.exists(os.path.join(OUT, "meta.json")) else []
json.dump({"spawn": spawn, "build": _time.strftime("%Y%m%d%H%M%S"), "regions": _regions}, open(os.path.join(OUT, "meta.json"), "w"))
print("done")
