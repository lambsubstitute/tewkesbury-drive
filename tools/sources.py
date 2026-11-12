"""Open data sources shared by place.py (the town tile) and region.py (countryside beyond it).

Every fetch here identifies itself with a generic user agent, retries with backoff, and is polite about
rate limits; the services involved (Overpass, Nominatim, the Environment Agency WCS, Wikimedia) all throttle.
Coordinates are British National Grid (EPSG:27700) metres unless named lon/lat.
"""
import io, json, math, os, time, urllib.parse, urllib.request
import numpy as np
import pyproj
from pyproj import Transformer

pyproj.network.set_network_enabled(True)            # OSTN15 grid: OSM lon/lat -> BNG to ~0.1 m
TO_BNG = Transformer.from_crs(4326, 27700, always_xy=True)
TO_LL = Transformer.from_crs(27700, 4326, always_xy=True)
UA = {"User-Agent": "real-place-drive/0.1 (hobby map game)"}

# ---------------------------------------------------------------- geocoding


def geocode(name):
    """Place name -> (lon, lat, display name, BNG E, N). Nominatim, England first. Raises if nothing found."""
    q = urllib.parse.urlencode({"q": name, "format": "json", "countrycodes": "gb", "limit": 5, "addressdetails": 1})
    res = json.loads(get(f"https://nominatim.openstreetmap.org/search?{q}", timeout=60))
    if not res:
        raise SystemExit(f"Nominatim found nothing for {name!r}")
    # prefer settlements in England (EA LIDAR and the Historic England list cover England only)
    rank = lambda r: (r.get("address", {}).get("country_code") != "gb",
                      r.get("address", {}).get("state") != "England",
                      r.get("addresstype") not in ("city", "town", "village", "hamlet", "suburb", "neighbourhood"))
    r = sorted(res, key=rank)[0]
    lon, lat = float(r["lon"]), float(r["lat"])
    E, N = TO_BNG.transform(lon, lat)
    return lon, lat, r["display_name"], r.get("address", {}).get("state"), E, N


def ll_bbox(e0, n0, e1, n1, pad=0.0005):
    """BNG box -> Overpass bbox string 'S,W,N,E' (generously covering the box's curved edges)."""
    lons, lats = TO_LL.transform([e0, e1, e0, e1], [n0, n0, n1, n1])
    return f"{min(lats) - pad:.5f},{min(lons) - pad:.5f},{max(lats) + pad:.5f},{max(lons) + pad:.5f}"


# ---------------------------------------------------------------- HTTP


def get(url, data=None, timeout=120, tries=6, headers=None):
    h = dict(UA, **(headers or {}))
    for k in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            return urllib.request.urlopen(req, timeout=timeout).read()
        except Exception as ex:
            if k == tries - 1:
                raise
            print(f"  retry {k + 1} {url[:70]}…: {ex}", flush=True)
            time.sleep(min(60, 4 * 2 ** k))


# ---------------------------------------------------------------- OpenStreetMap (Overpass)
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]

# map layer: roads, buildings, water, land use, trees.  `out geom;` (not `out geom tags`) keeps relation members.
Q_MAP = """[out:json][timeout:180];
(
  way["highway"]({bb});
  way["building"]({bb});
  relation["building"]({bb});
  way["waterway"]({bb});
  way["natural"="water"]({bb});
  relation["natural"="water"]({bb});
  way["water"]({bb});
  way["landuse"]({bb});
  relation["landuse"]({bb});
  way["natural"~"wood|scrub|grassland|wetland"]({bb});
  way["leisure"]({bb});
  way["amenity"="parking"]({bb});
  way["railway"]({bb});
  node["natural"="tree"]({bb});
);
out geom;"""
# street layer: shops, amenities, street furniture, boundary walls/fences/hedges
Q_STREET = """[out:json][timeout:180];
(
  node["shop"]({bb});
  node["amenity"]({bb});
  node["tourism"]({bb});
  node["office"]({bb});
  node["craft"]({bb});
  node["highway"~"street_lamp|crossing|traffic_signals|bus_stop|give_way|stop"]({bb});
  node["barrier"~"bollard|gate"]({bb});
  node["man_made"]({bb});
  node["historic"]({bb});
  way["barrier"~"wall|fence|hedge|retaining_wall|city_wall"]({bb});
  way["shop"]({bb});
);
out geom;"""


def overpass(query, out_path):
    """Run an Overpass query (main server, then mirror), save the JSON, return the element count."""
    body = urllib.parse.urlencode({"data": query}).encode()
    last = None
    for attempt in range(8):
        url = OVERPASS[attempt % len(OVERPASS)]
        try:
            raw = get(url, data=body, timeout=240, tries=1)
            d = json.loads(raw)
            if "elements" in d:
                json.dump(d, open(out_path, "w"))
                return len(d["elements"])
            last = str(d)[:200]
        except Exception as ex:          # 429/504 "at capacity" pages are HTML, not JSON
            last = ex
        print(f"  overpass busy ({last}); retrying", flush=True)
        time.sleep(15 * (attempt + 1))
    raise SystemExit(f"Overpass failed: {last}")


# ---------------------------------------------------------------- Environment Agency LIDAR (WCS, OGL)
_WCS = "https://environment.data.gov.uk/spatialdata/{svc}/wcs?service=WCS&version=2.0.1&request=GetCoverage&CoverageId={cov}&format=image/tiff"
LIDAR = {
    "dtm": _WCS.format(svc="lidar-composite-digital-terrain-model-dtm-1m",
                       cov="13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m"),
    "dsm": _WCS.format(svc="lidar-composite-digital-surface-model-first-return-dsm-1m",
                       cov="df4e3ec3-315e-48aa-aaaf-b5ae74d7b2bb__Lidar_Composite_Elevation_FZ_DSM_1m"),
    # last return: passes through foliage to the roof or ground beneath, so first - last marks trees
    "lz": _WCS.format(svc="lidar-composite-digital-surface-model-last-return-dsm-1m",
                      cov="9ba4d5ac-d596-445a-9056-dae3ddec0178__Lidar_Composite_Elevation_LZ_DSM_1m"),
}


def wcs(kind, e0, n0, e1, n1, scale=None, timeout=60, tries=9):
    """One GeoTIFF from the EA WCS as bytes. The server throttles bursts (429) and times out on big 1 m
    requests when busy (504), so retries back off and callers fetch in pieces (wcs_array)."""
    u = f"{LIDAR[kind]}&subset=E({e0},{e1})&subset=N({n0},{n1})" + (f"&SCALEFACTOR={scale}" if scale else "")
    for k in range(tries):
        try:
            data = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=timeout).read()
            if data[:2] in (b"MM", b"II"):
                return data
            print("  WCS returned non-TIFF", e0, n0, data[:100], flush=True)
        except Exception as ex:
            print("  WCS retry", kind, e0, n0, ex, flush=True)
        time.sleep(min(30, 3 * 2 ** k))
    raise RuntimeError(f"WCS failed {kind} {e0},{n0}")


def wcs_array(kind, e0, n0, e1, n1, step=250, scale=None, pause=0.5):
    """A raster over a BNG box, fetched in step-metre pieces and stitched. Row 0 = north edge.
    NaN where the EA has no data. With scale (e.g. 0.1 = 10 m pixels) the step is in metres too."""
    import tifffile
    px = 1 / scale if scale else 1
    W, H = round((e1 - e0) / px), round((n1 - n0) / px)
    A = np.full((H, W), np.nan, np.float32)
    for e in range(e0, e1, step):
        for n in range(n0, n1, step):
            ee, nn = min(e + step, e1), min(n + step, n1)
            a = tifffile.imread(io.BytesIO(wcs(kind, e, n, ee, nn, scale))).astype(np.float32)
            a[(a < -100) | (a > 2000)] = np.nan
            r0, c0 = round((n1 - nn) / px), round((e - e0) / px)
            h_, w_ = min(a.shape[0], H - r0), min(a.shape[1], W - c0)
            A[r0:r0 + h_, c0:c0 + w_] = a[:h_, :w_]
            time.sleep(pause)
    return A


def save_tif(path, A):
    import tifffile
    tifffile.imwrite(path + ".part", A.astype(np.float32))
    os.replace(path + ".part", path)


# ---------------------------------------------------------------- Historic England (NHLE, OGL)
NHLE = ("https://services-eu1.arcgis.com/ZOdPfBS3aqqDYPUQ/arcgis/rest/services/"
        "National_Heritage_List_for_England_NHLE_v02_VIEW/FeatureServer/3/query")


def nhle(e0, n0, e1, n1, out_path):
    """Listed-building polygons in a BNG box (official ArcGIS service; the HE website itself blocks bots)."""
    feats, offset = [], 0
    while True:
        q = urllib.parse.urlencode({"geometry": f"{e0},{n0},{e1},{n1}", "geometryType": "esriGeometryEnvelope",
                                    "inSR": 27700, "outSR": 27700, "outFields": "ListEntry,Name,Grade", "f": "json",
                                    "resultOffset": offset, "resultRecordCount": 1000})
        d = json.loads(get(f"{NHLE}?{q}"))
        feats += d.get("features", [])
        if not d.get("exceededTransferLimit"):
            break
        offset += 1000
    json.dump({"features": feats}, open(out_path, "w"))
    return feats


# ---------------------------------------------------------------- Wikidata + Wikimedia Commons photos


def listing_photos(list_entries, out_path):
    """NHLE list entry -> (Commons image URL, label) via Wikidata P1216/P18 (CC0)."""
    out = {}
    ids = sorted(set(map(str, list_entries)))
    for k in range(0, len(ids), 300):
        vals = " ".join(f'"{i}"' for i in ids[k:k + 300])
        q = f"""SELECT ?le ?itemLabel ?img WHERE {{ VALUES ?le {{ {vals} }} ?item wdt:P1216 ?le .
                OPTIONAL {{ ?item wdt:P18 ?img . }} SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }} }}"""
        d = json.loads(get("https://query.wikidata.org/sparql", data=urllib.parse.urlencode({"query": q}).encode(),
                           headers={"Accept": "application/sparql-results+json"}))
        for b in d["results"]["bindings"]:
            if "img" in b:
                out[b["le"]["value"]] = [b["img"]["value"], b["itemLabel"]["value"]]
        time.sleep(1)
    json.dump(out, open(out_path, "w"), indent=0)
    return out


def commons_thumbs(images, folder, width=500):
    """Download Commons thumbnails slowly (Wikimedia answers bursts with 429). width: a standard size."""
    os.makedirs(folder, exist_ok=True)
    for le, (url, _) in sorted(images.items()):
        f = os.path.join(folder, f"{le}.jpg")
        if os.path.exists(f) and os.path.getsize(f) > 1000:
            continue
        try:
            open(f, "wb").write(get(url.replace("http://", "https://") + f"?width={width}", timeout=60, tries=4))
        except Exception as ex:
            print("  photo failed", le, ex)
        time.sleep(3)


def contact_sheets(images, folder, out_dir, prefix="sheet"):
    """3x3 labelled sheets ('<list entry> <name>') for classifying wall materials by eye."""
    from PIL import Image, ImageDraw, ImageFont
    os.makedirs(out_dir, exist_ok=True)
    items = [(le, lab) for le, (_, lab) in sorted(images.items()) if os.path.exists(os.path.join(folder, f"{le}.jpg"))]
    C, R, W, H = 3, 3, 420, 330
    font = ImageFont.load_default(size=14)
    paths = []
    for s0 in range(0, len(items), C * R):
        sh = Image.new("RGB", (C * W, R * H), (30, 30, 30))
        d = ImageDraw.Draw(sh)
        for k, (le, lab) in enumerate(items[s0:s0 + C * R]):
            try:
                im = Image.open(os.path.join(folder, f"{le}.jpg")).convert("RGB")
            except Exception:
                continue
            im.thumbnail((W - 6, H - 30))
            x, y = (k % C) * W, (k // C) * H
            sh.paste(im, (x + 3, y + 26))
            d.rectangle([x, y, x + W, y + 24], fill=(0, 0, 0))
            d.text((x + 4, y + 4), f"{le} {lab[:40]}", fill=(255, 230, 120), font=font)
        p = os.path.join(out_dir, f"{prefix}_{s0 // (C * R):02d}.jpg")
        sh.save(p, quality=85)
        paths.append(p)
    return paths


# ---------------------------------------------------------------- Ordnance Survey OpenMap Local (OGL)


def os_square(E, N):
    """BNG 100 km square letters for a point, e.g. 'SO'."""
    e100, n100 = int(E // 100000), int(N // 100000)
    l1 = (19 - n100) - (19 - n100) % 5 + (e100 + 10) // 5
    l2 = (19 - n100) * 5 % 25 + e100 % 5
    l1 += l1 > 7
    l2 += l2 > 7
    return chr(65 + l1) + chr(65 + l2)


def os_zip_info(square):
    d = json.loads(get(f"https://api.os.uk/downloads/v1/products/OpenMapLocal/downloads?area={square}"))
    return next(x for x in d if x["format"].startswith("ESRI"))


def os_buildings(zip_path, e0, n0, e1, n1, out_path):
    """Building outlines (+ names/types from the ImportantBuilding layer) inside a BNG box."""
    import zipfile, shapefile
    from shapely.geometry import Polygon
    z = zipfile.ZipFile(zip_path)
    shp = next(n for n in z.namelist() if n.endswith("_Building.shp"))
    base, sq = shp[:-len("_Building.shp")], ""

    def read(layer):
        r = shapefile.Reader(shp=io.BytesIO(z.read(f"{base}_{layer}.shp")), dbf=io.BytesIO(z.read(f"{base}_{layer}.dbf")),
                             shx=io.BytesIO(z.read(f"{base}_{layer}.shx")), encoding="latin-1")
        for sr in r.iterShapeRecords():
            bb = sr.shape.bbox
            if bb[2] < e0 or bb[0] > e1 or bb[3] < n0 or bb[1] > n1:
                continue
            parts = list(sr.shape.parts) + [len(sr.shape.points)]
            rings = [[[round(x, 2), round(y, 2)] for x, y in sr.shape.points[parts[k]:parts[k + 1]]]
                     for k in range(len(parts) - 1)]
            yield rings, sr.record
    important = [(Polygon(r[0]), rec) for r, rec in read("ImportantBuilding")]
    out = []
    for rings, rec in read("Building"):
        b = {"p": rings[0], "h": rings[1:], "id": str(rec[0])}
        P = Polygon(rings[0])
        for ip, irec in important:
            if P.intersects(ip) and P.intersection(ip).area > 0.5 * min(P.area, ip.area):
                b["n"], b["cls"] = irec[1], irec[3]
                break
        out.append(b)
    existing = json.load(open(out_path)) if os.path.exists(out_path) else []
    seen = {b["id"] for b in existing}
    json.dump(existing + [b for b in out if b["id"] not in seen], open(out_path, "w"))
    return out
