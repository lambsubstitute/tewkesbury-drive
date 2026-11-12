"""Turn OSM + EA LIDAR + Historic England data into game-ready Tewkesbury map files.

Local coords: x = Easting - E0 (east +), z = N0 - Northing (south +), y = metres above OD.
Tile covers E 387800..390300, N 231500..234000 => x,z in [-1250, 1250].
"""
import json, math, os, sys, hashlib
import numpy as np
import tifffile
import pyproj
from pyproj import Transformer
from scipy import ndimage
from scipy.spatial import Delaunay
import shapely
from shapely.geometry import Polygon, MultiPolygon, LineString, box, Point
from shapely.ops import unary_union
from PIL import Image, ImageDraw, ImageFilter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import buildgeo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "game", "data")
os.makedirs(OUT, exist_ok=True)

E_MIN, N_MAX, SIZE = 387800, 234000, 2500
E0, N0 = E_MIN + SIZE / 2, N_MAX - SIZE / 2
TILE = box(E_MIN, N_MAX - SIZE, E_MIN + SIZE, N_MAX)

pyproj.network.set_network_enabled(True)  # OSTN15 grid for metre-accurate OSM->BNG
TF = Transformer.from_crs(4326, 27700, always_xy=True)


def h32(s):
    return int(hashlib.md5(str(s).encode()).hexdigest()[:8], 16)


def rnd(seed, i=0):
    return (h32(f"{seed}:{i}") % 100000) / 100000.0


# ---------------------------------------------------------------- rasters
print("loading lidar")
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
osm = json.load(open(os.path.join(DATA, "osm.json")))["elements"]


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


# ---------------------------------------------------------------- Historic England
print("loading listed buildings")
he = json.load(open(os.path.join(DATA, "lb_polys.json")))["features"]
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
    blds = []
    for e in osm:
        t = e.get("tags", {})
        if "building" in t and t.get("building") not in ("no", "roof", "canopy", "carport"):
            g = element_polygon(e)
            if g is not None:
                blds.append((f"{e['type']}{e['id']}", g))
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
for e in osm:
    t = e.get("tags", {})
    if "building" in t and t.get("building") not in ("no", "roof", "canopy", "carport"):
        g = element_polygon(e)
        if g is not None:
            _all += [p for p in polys_of(g.intersection(TILE)) if p.area >= 6]
ALL_B = shapely.union_all(_all)
shapely.prepare(ALL_B)

_DRIVE_W = {"motorway": 11, "trunk": 9, "primary": 7.5, "secondary": 7, "tertiary": 6.5, "unclassified": 5.5,
            "residential": 5.5, "living_street": 5, "pedestrian": 6, "primary_link": 5.5, "secondary_link": 5,
            "tertiary_link": 5, "trunk_link": 6, "road": 5, "service": 3.6}
def road_width(t):
    """carriageway width (m) used for rendering, pavements, kerbs and collisions alike"""
    hw = t.get("highway")
    w = _DRIVE_W.get(hw, 5.0) if hw != "service" else 3.6
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


street_osm = json.load(open(os.path.join(DATA, "osm_street.json")))["elements"]
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
signs_out = []
ctx = {"E_MIN": E_MIN, "N_MAX": N_MAX, "E0": E0, "N0": N0, "dsm_s": dsm_s, "dtm": dtm, "sample": sample,
       "ALL_B": ALL_B, "road_near": road_near, "B_LIST": _all, "B_TREE": shapely.STRtree(_all)}
sink = buildgeo.Sink(NSTYLES)

n_b = 0
stats = {"lidar": 0, "fallback": 0, "listed": 0}
for e in osm:
    t = e.get("tags", {})
    if "building" not in t or t.get("building") in ("no",):
        continue
    if t.get("building") in ("roof", "canopy", "carport"):
        continue
    g = element_polygon(e)
    if g is None:
        continue
    g = g.intersection(TILE)
    for poly in polys_of(g):
        if poly.area < 6:
            continue
        bid = f"{e['type']}{e['id']}"
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
        hts = None
        if ipm is not None and len(ipm[0]) >= 3:
            hts = dsm_s[ipm[0], ipm[1]] - base
        elif pm is not None and len(pm[0]) >= 1:
            hts = dsm_s[pm[0], pm[1]] - base
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
        res = buildgeo.build(ctx, poly, bid, base, ipm, use_lidar, eave, style, wc, rc, is_church,
                             seg_material, paint, is_shop, sink)
        if res is None:
            continue
        stats["planar"] = stats.get("planar", 0) + 1
        facade_hit = facade_hit[0]
        eave = res["eave"]
        # fascia signs for named shops, on the nearest street-facing wall
        used = []
        for (spt, sname, stags) in shops_here:
            if not sname or not res["street"]:
                continue
            cand = sorted(res["street"], key=lambda sg: (sg[0] - spt.x) ** 2 + (sg[1] - spt.y) ** 2)
            sg = next((c_ for c_ in cand if all((c_[0] - u[0]) ** 2 + (c_[1] - u[1]) ** 2 > 9 for u in used)), cand[0])
            used.append(sg)
            mx, my, nx, ny, Le, _, _ = sg
            w = max(2.2, min(6.5, 0.28 * len(sname) + 1.2, Le * 0.95))
            gy = float(sample(dtm, mx, my)[0])
            lx, lz = to_local(mx + nx * 0.14, my + ny * 0.14)
            signs_out.append({"x": round(lx, 2), "y": round(max(gy, base) + res["hs"] - 0.5, 2), "z": round(lz, 2),
                              "r": round(math.atan2(nx, -ny), 3), "w": round(w, 2), "n": sname,
                              "c": "#%02x%02x%02x" % paint})
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

print("buildings:", n_b, stats, "roof tris", len(sink.roof["pos"]) // 9, "signs", len(signs_out))

for s_, W in sink.walls.items():  # one mesh per wall style
    np.array(W["pos"], np.float32).tofile(os.path.join(OUT, f"walls{s_}_pos.bin"))
    np.array(W["uv"], np.float32).tofile(os.path.join(OUT, f"walls{s_}_uv.bin"))
    np.array(W["col"], np.uint8).tofile(os.path.join(OUT, f"walls{s_}_col.bin"))
    np.array(W["kind"], np.uint8).tofile(os.path.join(OUT, f"walls{s_}_kind.bin"))
np.array(sink.roof["pos"], np.float32).tofile(os.path.join(OUT, "roof_pos.bin"))
np.array(sink.roof["uv"], np.float32).tofile(os.path.join(OUT, "roof_uv.bin"))
np.array(sink.roof["col"], np.uint8).tofile(os.path.join(OUT, "roof_col.bin"))
json.dump(signs_out, open(os.path.join(OUT, "signs.json"), "w"), separators=(",", ":"))
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
for e in osm:
    t = e.get("tags", {})
    hw = t.get("highway")
    if not hw or e["type"] != "way" or not e.get("geometry"):
        continue
    pts = proj(e["geometry"])
    line = LineString(pts).intersection(TILE.buffer(-1))
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
    if not (E_MIN < E < E_MIN + SIZE and N_MAX - SIZE < N < N_MAX):
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
MLY_DIR = os.path.join(DATA, "mapillary")
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
    if not (E_MIN < E < E_MIN + SIZE and N_MAX - SIZE < N < N_MAX) or shapely.contains_xy(ALL_B, E, N):
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
    if not (E_MIN < E < E_MIN + SIZE and N_MAX - SIZE < N < N_MAX) or shapely.contains_xy(ALL_B, E, N):
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
    built_up = ALL_B.distance(line) < 25
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
json.dump({"furniture": furn, "barriers": barriers}, open(os.path.join(OUT, "street.json"), "w"), separators=(",", ":"))
from collections import Counter as _C
print("furniture", _C(f["t"] for f in furn), "barriers", len(barriers))

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
cand = (canopy > 4) & ~bm & ~wm
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
    d.polygon([P(x, y) for x, y in p.exterior.coords], fill=col)
    for r in p.interiors:
        d.polygon([P(x, y) for x, y in r.coords], fill=(104, 132, 70))


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
json.dump({"spawn": spawn}, open(os.path.join(OUT, "meta.json"), "w"))
print("done")
