"""Sanity checks on the built game data (town + every region in meta.json).

  check.py                 obstacles on carriageways, buildings over roads, sizes
  check.py route <file>    also: does a BNG route (JSON [[E, N], ...], e.g. data/<region>/region.json "route")
                           stay on drawn road ribbons all the way?

Writes game/route_test.json (local coords) for the in-browser autopilot when a route is checked.
"""
import json, os, sys
from shapely.geometry import LineString, Point, Polygon
import shapely
from shapely import STRtree, union_all

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G = os.path.join(ROOT, "game", "data")
place = json.load(open(os.path.join(ROOT, "data", "place.json")))
E0, N0 = place["origin"]
meta = json.load(open(os.path.join(G, "meta.json")))
sets = [G] + [os.path.join(G, r) for r in meta.get("regions", [])]
MARKINGS = {"zebra", "pelican", "signals", "busbay", "memorial"}
problems = 0
all_roads = []
for d in sets:
    roads = json.load(open(os.path.join(d, "roads.json")))
    all_roads += roads
    drive = [r for r in roads if r["t"] not in ("track", "path") and len(r["pts"]) > 1]
    U = union_all([LineString([(p[0], p[1]) for p in r["pts"]]).buffer(r["w"] / 2) for r in drive])
    trees = json.load(open(os.path.join(d, "trees.json")))
    st = json.load(open(os.path.join(d, "street.json")))
    foots = json.load(open(os.path.join(d, "footprints.json")))
    Ui = U.buffer(-0.3)
    shapely.prepare(U); shapely.prepare(Ui)
    inside = lambda P, A: shapely.contains_xy(A, [q[0] for q in P], [q[1] for q in P]) if P else []
    t_on = [t for t, k in zip(trees, inside([(t[0], t[2]) for t in trees], U)) if k]
    fu = [f for f in st["furniture"] if f["t"] not in MARKINGS]
    f_on = [f for f, k in zip(fu, inside([(f["x"], f["z"]) for f in fu], Ui)) if k]
    b_on = [b for b in st["barriers"] if any(inside(b["p"], Ui))]
    Um = union_all([LineString([(p[0], p[1]) for p in r["pts"]]).buffer(r["w"] / 2) for r in drive if r["t"] != "service"])
    shapely.prepare(Um)
    bl_on = [f.get("n", "?") for f in foots     # service roads legitimately pass through arches / car washes
             if (lambda P: P.intersects(Um) and P.intersection(Um).area > 4)(Polygon(f["p"]).buffer(-0.5))]
    size = sum(os.path.getsize(os.path.join(d, f)) for f in os.listdir(d) if os.path.isfile(os.path.join(d, f)))
    print(f"{os.path.relpath(d, ROOT)}: {len(foots)} buildings, {len(drive)} roads, {len(trees)} trees, "
          f"{len(st['furniture'])} street objects, {len(st['barriers'])} walls/hedges, {size / 1e6:.1f} MB")
    for label, items in (("trees on a carriageway", t_on), ("street objects on a carriageway", f_on),
                         ("walls/hedges crossing a carriageway", b_on), ("buildings over a road (>4 m²)", bl_on)):
        if items:
            problems += len(items)
            ex = items[:5] if isinstance(items[0], str) else [
                (round(i[0]), round(i[2])) if isinstance(i, list) else (i.get("t"), round(i["x"]) if "x" in i else i["p"][0])
                for i in items[:5]]
            print(f"  {len(items):4d} {label}  e.g. {ex}")

if len(sys.argv) > 2 and sys.argv[1] == "route":
    pts = json.load(open(sys.argv[2]))
    pts = pts.get("route", pts) if isinstance(pts, dict) else pts
    L = LineString([(e - E0, N0 - n) for e, n in pts])
    segs = [(LineString([(p[0], p[1]) for p in r["pts"]]), r["w"]) for r in all_roads if len(r["pts"]) > 1]
    tree = STRtree([s for s, _ in segs])
    samples = list(L.segmentize(2.0).coords)
    off = [(round(x), round(z)) for x, z in samples
           if (lambda i: segs[i][0].distance(Point(x, z)) > segs[i][1] / 2)(tree.nearest(Point(x, z)))]
    print(f"route {L.length / 1000:.1f} km: {len(off)} of {len(samples)} samples off the drawn road", off[:10])
    problems += len(off)
    json.dump([[round(x, 1), round(z, 1)] for x, z in L.segmentize(8).coords],
              open(os.path.join(ROOT, "game", "route_test.json"), "w"), separators=(",", ":"))
print("OK" if not problems else f"{problems} things to look at")
