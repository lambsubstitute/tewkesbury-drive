"""Add a countryside region beyond the town tile: the real road route out to another place, and that place.

The region is a set of 500 m cells on the town tile's grid. Cells are picked along the fastest OSM road
route from the town's spawn point to the destination, plus every cell with buildings near the destination.

  region.py <name> init --to "Longdon"   geocode, fetch OSM for a box around town + destination
  region.py <name> os                    OS OpenMap Local buildings in that box (needs the OS zip: place.py os)
  region.py <name> cells [--buf 220] [--radius 1100]
                                         route + cell list -> data/<name>/region.json
  region.py <name> lidar                 1 m DTM, DSM, last-return DSM per cell (+60 m margin) -> data/<name>/lidar/
  region.py <name> horizon               10 m DTM (+ DSM if the server allows) for the far landscape
  region.py <name> nhle                  listed buildings in the region
  region.py <name> photos                Wikidata/Commons photos of those listings + contact sheets
  region.py <name> mapillary             (needs ~/.mapillary_token) street-object detections

Then: tools/process.py --region <name>
"""
import json, math, os, sys, heapq
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sources as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = os.path.join(ROOT, "data")
NAME = sys.argv[1]
RD = os.path.join(D, NAME)
LIDAR = os.path.join(RD, "lidar")
REGION_JSON = os.path.join(RD, "region.json")
CELL, PAD = 500, 60
DRIVE = {"trunk": 1.0, "primary": 1.0, "secondary": 1.1, "tertiary": 1.2, "unclassified": 1.5, "residential": 1.6,
         "living_street": 2.0, "trunk_link": 1.0, "primary_link": 1.0, "secondary_link": 1.1, "tertiary_link": 1.2}


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def place():
    return json.load(open(os.path.join(D, "place.json")))


def reg():
    return json.load(open(REGION_JSON))


def save(r):
    json.dump(r, open(REGION_JSON, "w"), indent=0)


def cell_box(r):
    es, ns = [c[0] for c in r["cells"]], [c[1] for c in r["cells"]]
    return min(es) - PAD, min(ns) - PAD, max(es) + CELL + PAD, max(ns) + CELL + PAD


def town_start():
    """Route start: the game's spawn point (from game/data/meta.json) or the place's centre."""
    p = place()
    mp = os.path.join(ROOT, "game", "data", "meta.json")
    if os.path.exists(mp):
        x, z = json.load(open(mp))["spawn"]
        return p["origin"][0] + x, p["origin"][1] - z
    return tuple(p.get("centre") or p["origin"])


def cmd_init():
    os.makedirs(RD, exist_ok=True)
    to = arg("--to")
    lon, lat, disp, state, E, N = S.geocode(to)
    print("destination:", disp)
    t = place()["tile"]
    m = 1500
    box_ = [int(min(t[0], E) - m), int(min(t[1], N) - m), int(max(t[2], E) + m), int(max(t[3], N) + m)]
    r = {"cell": CELL, "pad": PAD, "town": place()["tile"], "dest": {"name": to, "E": round(E, 1), "N": round(N, 1)},
         "box": box_, "bbox_ll": S.ll_bbox(*box_),
         "teleports": [[to, [round(E, 1), round(N, 1)], None]]}
    save(r)
    print("OSM map layer:", S.overpass(S.Q_MAP.replace("{bb}", r["bbox_ll"]), os.path.join(RD, "osm.json")))
    print("OSM street layer:", S.overpass(S.Q_STREET.replace("{bb}", r["bbox_ll"]), os.path.join(RD, "osm_street.json")))


def cmd_os():
    r = reg()
    out = os.path.join(RD, "os_buildings.json")
    if os.path.exists(out):
        os.remove(out)
    e0, n0, e1, n1 = r["box"]
    squares = sorted({S.os_square(x, y) for x in (e0, e1) for y in (n0, n1)})
    for sq in squares:
        z = S.os_zip_info(sq)
        zp = os.path.join(D, "os", z["fileName"])
        if not os.path.exists(zp):
            raise SystemExit(f"missing {zp} ({z['size'] / 1e6:.0f} MB). Ask the user, then run: tools/place.py os")
        print(sq, "OS buildings:", len(S.os_buildings(zp, e0, n0, e1, n1, out)))


def route(start, dest):
    """Fastest drivable route over OSM roads (cost = length x road-class factor)."""
    adj, pos = {}, {}
    for w in json.load(open(os.path.join(RD, "osm.json")))["elements"]:
        hw = w.get("tags", {}).get("highway")
        if w["type"] != "way" or hw not in DRIVE:
            continue
        for n, p in zip(w["nodes"], w["geometry"]):
            if n not in pos:
                pos[n] = S.TO_BNG.transform(p["lon"], p["lat"])
        for a, b in zip(w["nodes"], w["nodes"][1:]):
            d = math.dist(pos[a], pos[b])
            adj.setdefault(a, []).append((b, d * DRIVE[hw]))
            adj.setdefault(b, []).append((a, d * DRIVE[hw]))
    near = lambda q: min(pos, key=lambda n: math.dist(pos[n], q))
    s, t = near(start), near(dest)
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
    if t not in prev and t != s:
        raise SystemExit("no road route found inside the fetched box")
    path = [t]
    while path[-1] != s:
        path.append(prev[path[-1]])
    return [pos[n] for n in reversed(path)]


def cmd_cells():
    from shapely.geometry import LineString, box, Point
    from shapely.ops import unary_union
    r = reg()
    buf, radius = float(arg("--buf", 220)), float(arg("--radius", 1100))
    dest = (r["dest"]["E"], r["dest"]["N"])
    pts = route(town_start(), dest)
    line = LineString(pts)
    print("route length m", round(line.length))
    zone = line.buffer(buf)
    # every building near the destination (OS outlines if extracted, else OSM)
    cents = []
    p = os.path.join(RD, "os_buildings.json")
    if os.path.exists(p):
        cents = [np.mean(b["p"], axis=0) for b in json.load(open(p))]
    else:
        for e in json.load(open(os.path.join(RD, "osm.json")))["elements"]:
            if "building" in e.get("tags", {}) and e.get("geometry"):
                g = e["geometry"]
                cents.append(S.TO_BNG.transform(np.mean([q["lon"] for q in g]), np.mean([q["lat"] for q in g])))
    blds = [Point(c).buffer(40) for c in cents if math.dist(c, dest) < radius]
    zone = zone.union(unary_union(blds) if blds else Point(dest).buffer(600))
    t = r["town"]
    gx, gy = t[0] % CELL, t[1] % CELL                     # the town tile's grid
    minx, miny, maxx, maxy = zone.bounds
    e0 = math.floor((minx - gx) / CELL) * CELL + gx
    n0 = math.floor((miny - gy) / CELL) * CELL + gy
    cells = []
    for e in range(int(e0), int(maxx) + CELL, CELL):
        for n in range(int(n0), int(maxy) + CELL, CELL):
            if t[0] <= e < t[2] and t[1] <= n < t[3]:
                continue
            if box(e, n, e + CELL, n + CELL).intersects(zone):
                cells.append([e, n])
    r.update({"route": [[round(x, 1), round(y, 1)] for x, y in pts], "cells": cells})
    # far landscape: town + cells + 1.5 km, on a 500 m grid
    cb = cell_box(r)
    r["horizon"] = [int(math.floor((min(cb[0], t[0]) - 1500) / 500) * 500), int(math.floor((min(cb[1], t[1]) - 1500) / 500) * 500),
                    int(math.ceil((max(cb[2], t[2]) + 1500) / 500) * 500), int(math.ceil((max(cb[3], t[3]) + 1500) / 500) * 500)]
    save(r)
    print("cells", len(cells), "area km2", len(cells) * CELL * CELL / 1e6, "horizon", r["horizon"])


def cmd_lidar():
    """Per cell: dtm, dsm, lz rasters with a 60 m margin. Busiest cells first (lz matters most where roofs are)."""
    r = reg()
    os.makedirs(LIDAR, exist_ok=True)
    from shapely.geometry import Polygon, box
    from shapely import STRtree
    p = os.path.join(RD, "os_buildings.json")
    ob = STRtree([Polygon(b["p"]) for b in json.load(open(p))]) if os.path.exists(p) else None
    busy = (lambda c: -len(ob.query(box(c[0], c[1], c[0] + CELL, c[1] + CELL), predicate="intersects"))) if ob else (lambda c: 0)
    failed = []
    cells = sorted(r["cells"], key=busy)
    for i, (e, n) in enumerate(cells):
        for kind in ("dtm", "dsm", "lz"):
            f = os.path.join(LIDAR, f"{e}_{n}_{kind}.tif")
            if os.path.exists(f):
                continue
            try:
                S.save_tif(f, S.wcs_array(kind, e - PAD, n - PAD, e + CELL + PAD, n + CELL + PAD, step=155, pause=0.4))
            except RuntimeError as ex:      # the EA server throttles: rerun this command later to fill gaps
                failed.append(os.path.basename(f))
                print("  FAILED", f, ex)
        print(f"cell {i + 1}/{len(cells)}", e, n, flush=True)
    print("done; failed:", failed or "none")


def cmd_horizon():
    r = reg()
    E0, N0, E1, N1 = r["horizon"]
    for kind in ("dtm", "dsm"):
        f = os.path.join(RD, f"horizon_{kind}.npy")
        if os.path.exists(f):
            continue
        try:
            A = S.wcs_array(kind, E0, N0, E1, N1, step=2000, scale=0.1, pause=1.5)
        except RuntimeError as ex:
            print("horizon", kind, "failed:", ex, "(the DSM is optional: OSM woods are used instead)")
            if kind == "dtm":
                raise
            continue
        print(kind, A.shape, "missing", int(np.isnan(A).sum()))
        np.save(f, A)


def cmd_nhle():
    feats = S.nhle(*cell_box(reg()), os.path.join(RD, "lb_polys.json"))
    print("listed buildings", len(feats))


def cmd_photos():
    les = [f["attributes"]["ListEntry"] for f in json.load(open(os.path.join(RD, "lb_polys.json")))["features"]]
    imgs = S.listing_photos(les, os.path.join(RD, "wd_images.json"))
    print("listings with a Commons photo:", len(imgs), "of", len(les))
    S.commons_thumbs(imgs, os.path.join(D, "commons"))
    print(*S.contact_sheets(imgs, os.path.join(D, "commons"), os.path.join(RD, "sheets")), sep="\n")


def cmd_mapillary():
    import subprocess
    for what in ("features", "images"):
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "mapillary.py"), what, "--region", NAME], check=False)


if __name__ == "__main__":
    {"init": cmd_init, "os": cmd_os, "cells": cmd_cells, "lidar": cmd_lidar, "horizon": cmd_horizon, "nhle": cmd_nhle,
     "photos": cmd_photos, "mapillary": cmd_mapillary}[sys.argv[2]]()
