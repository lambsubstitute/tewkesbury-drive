"""Fetch Mapillary map features (detected street objects) and image metadata for the town tile
(or a countryside region with --region <name>); the area comes from place.json / region.json.
Token is read from ~/.mapillary_token and sent only in the Authorization header; it is never printed or stored."""
import json, os, sys, time, urllib.parse, urllib.request

TOKEN = open(os.path.expanduser("~/.mapillary_token")).read().strip()
while TOKEN.startswith("MLY|MLY|"):          # tolerate a doubled prefix from pasting a full token after "MLY|"
    TOKEN = TOKEN[4:]
DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
# area = the OSM extract's box: data/place.json (town tile) or data/<region>/region.json ("S,W,N,E")
CFG = os.path.join(DATA, "place.json")
OUT = os.path.join(DATA, "mapillary")
if "--region" in sys.argv:
    _r = sys.argv[sys.argv.index("--region") + 1]
    CFG, OUT = os.path.join(DATA, _r, "region.json"), os.path.join(DATA, _r, "mapillary")
LAT0, LON0, LAT1, LON1 = map(float, json.load(open(CFG))["bbox_ll"].split(","))
os.makedirs(OUT, exist_ok=True)


def get(path, params):
    url = "https://graph.mapillary.com/" + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": "OAuth " + TOKEN, "User-Agent": "real-place-drive/0.1"})
    for attempt in range(4):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=60).read())
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode("utf8", "ignore")
            if e.code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(3 + attempt * 5)
                continue
            raise SystemExit(f"HTTP {e.code} on {path}: {body}")
    return {}


def tiles(n):
    for i in range(n):
        for j in range(n):
            yield (LON0 + (LON1 - LON0) * i / n, LAT0 + (LAT1 - LAT0) * j / n,
                   LON0 + (LON1 - LON0) * (i + 1) / n, LAT0 + (LAT1 - LAT0) * (j + 1) / n)


def fetch_all(path, fields, n, extra=None):
    seen = {}
    for b in tiles(n):
        params = {"fields": fields, "bbox": ",".join(f"{v:.6f}" for v in b), "limit": 2000}
        params.update(extra or {})
        d = get(path, params)
        for x in d.get("data", []):
            seen[x["id"]] = x
        if len(d.get("data", [])) >= 2000:
            print(f"  warning: tile {b} hit 2000 limit for {path}", file=sys.stderr)
        time.sleep(0.2)
    return list(seen.values())


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "check"
    if what == "check":
        cl, ca = (LON0 + LON1) / 2, (LAT0 + LAT1) / 2
        d = get("images", {"fields": "id,captured_at", "bbox": f"{cl - .002:.4f},{ca - .001:.4f},{cl + .002:.4f},{ca + .001:.4f}", "limit": 50})
        print("token OK; images near the middle of the area:", len(d.get("data", [])))
    elif what == "features":
        pts = fetch_all("map_features", "id,object_value,geometry,first_seen_at,last_seen_at,aligned_direction", 6)
        json.dump(pts, open(os.path.join(OUT, "map_features.json"), "w"))   # includes traffic signs
        print("map features", len(pts))
    elif what == "images":
        imgs = fetch_all("images", "id,geometry,compass_angle,captured_at,is_pano,sequence,camera_type", 8)
        json.dump(imgs, open(os.path.join(OUT, "images.json"), "w"))
        print("images", len(imgs))
