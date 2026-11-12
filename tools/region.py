"""Set up the countryside region beyond the Tewkesbury tile: the road to Longdon and Longdon village.

The region is a set of 500 m cells on the same grid as the town tile (E = 300 mod 500, N = 0 mod 500).
Cells are picked along the real road route from Tewkesbury to Longdon and around the village's buildings.

  region.py cells      route + cell list            -> data/longdon/region.json
  region.py lidar      1 m DTM, DSM, last-return DSM per cell (+pad) -> data/longdon/lidar/  (not in the repo)
  region.py horizon    10 m DTM/DSM for the horizon -> data/longdon/horizon_{dtm,dsm}.npy
  region.py nhle       listed buildings in the region -> data/longdon/lb_polys.json
  region.py os         OS OpenMap Local buildings in the region -> data/longdon/os_buildings.json
"""
import json, math, os, sys, heapq, io, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pyproj
from pyproj import Transformer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RD = os.path.join(ROOT, "data", "longdon")
LIDAR = os.path.join(RD, "lidar")
pyproj.network.set_network_enabled(True)
TF = Transformer.from_crs(4326, 27700, always_xy=True)

CELL = 500
PAD = 60                                  # raster margin around each cell, so buildings on a cell edge are whole
TOWN = (387800, 231500, 390300, 234000)   # the existing Tewkesbury tile (E_MIN, N_MIN, E_MAX, N_MAX)
START = (389180, 232670)                  # The Cross, Tewkesbury
LONGDON = (383715, 236354)                # OSM place=village node
ROUTE_BUF = 220                           # keep this much real countryside either side of the road
VILLAGE_R = 1100                          # cells with buildings within this radius of the village node
HORIZON = (380500, 229000, 392500, 240000)
UA = {"User-Agent": "tewkesbury-drive/0.1"}
# last-return surface: passes through foliage to the roof or ground beneath, so first - last marks trees
LZ_WCS = ("https://environment.data.gov.uk/spatialdata/lidar-composite-digital-surface-model-last-return-dsm-1m/wcs?service=WCS"
          "&version=2.0.1&request=GetCoverage&CoverageId=9ba4d5ac-d596-445a-9056-dae3ddec0178__Lidar_Composite_Elevation_LZ_DSM_1m"
          "&format=image/tiff")

DTM_WCS = ("https://environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wcs?service=WCS"
           "&version=2.0.1&request=GetCoverage&CoverageId=13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m"
           "&format=image/tiff")
DSM_WCS = ("https://environment.data.gov.uk/spatialdata/lidar-composite-digital-surface-model-first-return-dsm-1m/wcs?service=WCS"
           "&version=2.0.1&request=GetCoverage&CoverageId=df4e3ec3-315e-48aa-aaaf-b5ae74d7b2bb__Lidar_Composite_Elevation_FZ_DSM_1m"
           "&format=image/tiff")

DRIVE = {"trunk": 1.0, "primary": 1.0, "secondary": 1.1, "tertiary": 1.2, "unclassified": 1.5, "residential": 1.6,
         "living_street": 2.0, "trunk_link": 1.0, "primary_link": 1.0, "secondary_link": 1.1, "tertiary_link": 1.2}


def osm():
    return json.load(open(os.path.join(RD, "osm.json")))["elements"]


def route():
    """Fastest drivable route The Cross -> Longdon over OSM roads (cost = length x road-class factor)."""
    adj, pos = {}, {}
    for w in osm():
        hw = w.get("tags", {}).get("highway")
        if w["type"] != "way" or hw not in DRIVE:
            continue
        for n, p in zip(w["nodes"], w["geometry"]):
            if n not in pos:
                pos[n] = TF.transform(p["lon"], p["lat"])
        for a, b in zip(w["nodes"], w["nodes"][1:]):
            d = math.dist(pos[a], pos[b])
            adj.setdefault(a, []).append((b, d * DRIVE[hw]))
            adj.setdefault(b, []).append((a, d * DRIVE[hw]))
    near = lambda q: min(pos, key=lambda n: math.dist(pos[n], q))
    s, t = near(START), near(LONGDON)
    dist, prev, pq = {s: 0}, {}, [(0, s)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == t:
            break
        if d > dist[u]:
            continue
        for v, c in adj.get(u, []):
            if d + c < dist.get(v, 1e18):
                dist[v], prev[v] = d + c, u
                heapq.heappush(pq, (d + c, v))
    path = [t]
    while path[-1] != s:
        path.append(prev[path[-1]])
    return [pos[n] for n in reversed(path)]


def in_town(e, n):
    return TOWN[0] <= e < TOWN[2] and TOWN[1] <= n < TOWN[3]


def cmd_cells():
    from shapely.geometry import LineString, box, Point
    pts = route()
    line = LineString(pts)
    print("route length m", round(line.length))
    zone = line.buffer(ROUTE_BUF)
    blds = []
    p = os.path.join(RD, "os_buildings.json")
    if os.path.exists(p):
        for b in json.load(open(p)):
            c = np.mean(b["p"], axis=0)
            if math.dist(c, LONGDON) < VILLAGE_R:
                blds.append(Point(c).buffer(40))
    from shapely.ops import unary_union
    if blds:
        zone = zone.union(unary_union(blds))
    else:
        zone = zone.union(Point(LONGDON).buffer(600))
    minx, miny, maxx, maxy = zone.bounds
    e0 = math.floor((minx - 300) / CELL) * CELL + 300
    n0 = math.floor(miny / CELL) * CELL
    cells = []
    for e in range(e0, int(maxx) + CELL, CELL):
        for n in range(n0, int(maxy) + CELL, CELL):
            if in_town(e, n):
                continue
            if box(e, n, e + CELL, n + CELL).intersects(zone):
                cells.append([e, n])
    json.dump({"cell": CELL, "pad": PAD, "town": TOWN, "route": [[round(x, 1), round(y, 1)] for x, y in pts],
               "cells": cells, "longdon": LONGDON}, open(os.path.join(RD, "region.json"), "w"))
    print("cells", len(cells), "area km2", len(cells) * CELL * CELL / 1e6)


def wcs(base, e0, n0, e1, n1, scale=None):
    u = f"{base}&subset=E({e0},{e1})&subset=N({n0},{n1})" + (f"&SCALEFACTOR={scale}" if scale else "")
    for k in range(9):
        try:
            data = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=240).read()
            if data[:2] in (b"MM", b"II"):
                return data
            print("  not a tiff", e0, n0, data[:120])
        except Exception as ex:
            print("  retry", e0, n0, ex)
        time.sleep(min(60, 4 * 2 ** k))
    raise RuntimeError(f"WCS failed {e0},{n0}")


def wcs_pieces(base, e0, n0, e1, n1, step=155):
    """One raster fetched as small pieces and stitched (the server times out on large 1 m requests when busy).
    Returns GeoTIFF-free float32 array bytes written by tifffile."""
    import tifffile
    W, H = e1 - e0, n1 - n0
    A = np.full((H, W), np.nan, np.float32)
    for e in range(e0, e1, step):
        for n in range(n0, n1, step):
            ee, nn = min(e + step, e1), min(n + step, n1)
            a = tifffile.imread(io.BytesIO(wcs(base, e, n, ee, nn))).astype(np.float32)
            r0, c0 = n1 - nn, e - e0
            A[r0:r0 + a.shape[0], c0:c0 + a.shape[1]] = a[:H - r0, :W - c0]
            time.sleep(0.4)
    buf = io.BytesIO()
    tifffile.imwrite(buf, A)
    return buf.getvalue()


def cmd_lidar():
    reg = json.load(open(os.path.join(RD, "region.json")))
    os.makedirs(LIDAR, exist_ok=True)
    jobs = []
    # most built-up cells first: the last return only matters where roofs hide under trees
    from shapely.geometry import Polygon, box
    from shapely import STRtree
    ob = STRtree([Polygon(b["p"]) for b in json.load(open(os.path.join(RD, "os_buildings.json")))])
    busy = lambda c: -len(ob.query(box(c[0], c[1], c[0] + CELL, c[1] + CELL), predicate="intersects"))
    for e, n in sorted(reg["cells"], key=busy):
        for kind, base in (("dtm", DTM_WCS), ("dsm", DSM_WCS), ("lz", LZ_WCS)):
            f = os.path.join(LIDAR, f"{e}_{n}_{kind}.tif")
            if not os.path.exists(f):
                jobs.append((f, base, e - PAD, n - PAD, e + CELL + PAD, n + CELL + PAD))

    def run(j):
        f, base, *bb = j
        try:
            open(f + ".part", "wb").write(wcs_pieces(base, *bb) if f.endswith("_lz.tif") else wcs(base, *bb))
        except RuntimeError as ex:      # the EA server throttles; rerun the command later to fill gaps
            return f"{os.path.basename(f)} FAILED ({ex})"
        os.replace(f + ".part", f)
        time.sleep(1.0)
        return f
    with ThreadPoolExecutor(1) as ex:
        for i, f in enumerate(ex.map(run, jobs)):
            print(i + 1, "/", len(jobs), os.path.basename(f), flush=True)


def cmd_horizon():
    """10 m DTM + DSM over a wide box, for the far landscape outside the detailed cells."""
    import tifffile
    E0, N0, E1, N1 = HORIZON
    step = 2000
    out = {}
    for kind, base in (("dtm", DTM_WCS), ("dsm", DSM_WCS)):
        W, H = (E1 - E0) // 10, (N1 - N0) // 10
        A = np.full((H, W), np.nan, np.float32)
        for e in range(E0, E1, step):
            for n in range(N0, N1, step):
                e1, n1 = min(e + step, E1), min(n + step, N1)
                a = tifffile.imread(io.BytesIO(wcs(base, e, n, e1, n1, 0.1))).astype(np.float32)
                time.sleep(1.5)   # the EA server rate-limits bursts (HTTP 429)
                a[(a < -100) | (a > 2000)] = np.nan
                r0, c0 = (N1 - n1) // 10, (e - E0) // 10
                h_, w_ = min(a.shape[0], H - r0), min(a.shape[1], W - c0)
                A[r0:r0 + h_, c0:c0 + w_] = a[:h_, :w_]
        print(kind, A.shape, "missing", int(np.isnan(A).sum()))
        np.save(os.path.join(RD, f"horizon_{kind}.npy"), A)


def cmd_nhle():
    B = "https://services-eu1.arcgis.com/ZOdPfBS3aqqDYPUQ/arcgis/rest/services/National_Heritage_List_for_England_NHLE_v02_VIEW/FeatureServer"
    reg = json.load(open(os.path.join(RD, "region.json")))
    es = [c[0] for c in reg["cells"]]
    ns = [c[1] for c in reg["cells"]]
    q = urllib.parse.urlencode({"geometry": f"{min(es) - PAD},{min(ns) - PAD},{max(es) + CELL + PAD},{max(ns) + CELL + PAD}",
                                "geometryType": "esriGeometryEnvelope", "inSR": 27700, "outSR": 27700,
                                "outFields": "ListEntry,Name,Grade", "f": "json"})
    d = json.loads(urllib.request.urlopen(urllib.request.Request(f"{B}/3/query?{q}", headers=UA), timeout=120).read())
    json.dump(d, open(os.path.join(RD, "lb_polys.json"), "w"))
    print("listed buildings", len(d.get("features", [])), [f["attributes"]["Name"][:40] for f in d.get("features", [])][:60])


def cmd_os():
    """Building outlines from OS OpenMap Local (OGL) inside the region, as BNG rings.
    Names and types come from the same product's ImportantBuilding layer (churches, schools, halls)."""
    import zipfile, shapefile
    from shapely.geometry import Polygon
    z = zipfile.ZipFile(os.path.join(ROOT, "data", "os", "opmplc_essh_so.zip"))
    base = next(n for n in z.namelist() if n.endswith("SO_Building.shp"))[:-len("SO_Building.shp")]
    E0, N0, E1, N1 = 381000, 231000, 389000, 238500

    def read(layer):
        r = shapefile.Reader(shp=io.BytesIO(z.read(base + layer + ".shp")), dbf=io.BytesIO(z.read(base + layer + ".dbf")),
                             shx=io.BytesIO(z.read(base + layer + ".shx")), encoding="latin-1")
        for sr in r.iterShapeRecords():
            bb = sr.shape.bbox
            if bb[2] < E0 or bb[0] > E1 or bb[3] < N0 or bb[1] > N1:
                continue
            parts = list(sr.shape.parts) + [len(sr.shape.points)]
            rings = [[[round(x, 2), round(y, 2)] for x, y in sr.shape.points[parts[k]:parts[k + 1]]]
                     for k in range(len(parts) - 1)]
            yield rings, sr.record
    important = [(Polygon(r[0]), rec) for r, rec in read("SO_ImportantBuilding")]
    out = []
    for rings, rec in read("SO_Building"):
        b = {"p": rings[0], "h": rings[1:], "id": str(rec[0])}
        P = Polygon(rings[0])
        for ip, irec in important:
            if P.intersects(ip) and P.intersection(ip).area > 0.5 * min(P.area, ip.area):
                b["n"], b["cls"] = irec[1], irec[3]
                break
        out.append(b)
    json.dump(out, open(os.path.join(RD, "os_buildings.json"), "w"))
    print("OS buildings in region", len(out), "named", [b["n"] for b in out if "n" in b])


if __name__ == "__main__":
    {"cells": cmd_cells, "lidar": cmd_lidar, "horizon": cmd_horizon, "nhle": cmd_nhle, "os": cmd_os}[sys.argv[1]]()
