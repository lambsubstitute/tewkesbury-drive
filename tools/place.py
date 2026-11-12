"""Set up the town tile for a place: geocode it, then fetch every open dataset the pipeline needs.

  place.py init "Ledbury" [--size 2500] [--title "Ledbury Drive"]   -> data/place.json
  place.py fetch            OSM map + street layers, 1 m EA DTM / DSM / last-return DSM, NHLE listings,
                            Wikidata photos of listed buildings (+ Commons thumbnails and contact sheets)
  place.py os-check         compare OSM building count with OS OpenMap Local (prints the zip size to ask about)
  place.py os               download OS OpenMap Local for the tile's 100 km square(s) and extract its buildings
  place.py mapillary        (needs ~/.mapillary_token) street-object detections + photo positions

Afterwards: tools/process.py builds game/data/ from data/.
"""
import json, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sources as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = os.path.join(ROOT, "data")
PLACE = os.path.join(D, "place.json")


def load():
    return json.load(open(PLACE))


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def cmd_init():
    name = sys.argv[2]
    size = int(arg("--size", 2500))
    lon, lat, disp, state, E, N = S.geocode(name)
    if state != "England":
        print(f"WARNING: {disp} is not in England ({state}). EA LIDAR and the Historic England list cover "
              "England only, so heights and listings will be missing.")
    # tile on a 100 m grid, centred on the place; the tile centre is the game's local origin
    e_min = int(round((E - size / 2) / 100) * 100)
    n_min = int(round((N - size / 2) / 100) * 100)
    tile = [e_min, n_min, e_min + size, n_min + size]
    p = {"name": name, "title": arg("--title", f"{name} Drive"), "display": disp, "lonlat": [lon, lat],
         "centre": [round(E, 1), round(N, 1)], "tile": tile, "origin": [e_min + size / 2, n_min + size / 2],
         "bbox_ll": S.ll_bbox(*tile), "os_square": sorted({S.os_square(x, y) for x in tile[::2] for y in tile[1::2]})}
    os.makedirs(D, exist_ok=True)
    if os.path.exists(PLACE):            # keep hand-set spawn / teleports when re-initialising
        old = load()
        for k in ("spawn", "teleports"):
            if k in old:
                p[k] = old[k]
    json.dump(p, open(PLACE, "w"), indent=1)
    print(json.dumps(p, indent=1))


def cmd_fetch():
    p = load()
    e0, n0, e1, n1 = p["tile"]
    bb = p["bbox_ll"]
    what = sys.argv[2:] or ["osm", "lidar", "nhle", "photos"]
    if "osm" in what or "all" in what:
        print("OSM map layer:", S.overpass(S.Q_MAP.replace("{bb}", bb), os.path.join(D, "osm.json")), "elements")
        print("OSM street layer:", S.overpass(S.Q_STREET.replace("{bb}", bb), os.path.join(D, "osm_street.json")), "elements")
    if "lidar" in what or "all" in what:
        for kind in ("dtm", "dsm", "lz"):
            f = os.path.join(D, f"{kind}.tif")
            if os.path.exists(f):
                continue
            print("EA LIDAR", kind, "(1 m, fetched in 250 m pieces)", flush=True)
            A = S.wcs_array(kind, e0, n0, e1, n1)
            miss = int(S.np.isnan(A).sum())
            if miss:
                print(f"  {miss} pixels without LIDAR (filled from neighbours)")
                from scipy import ndimage
                idx = ndimage.distance_transform_edt(S.np.isnan(A), return_distances=False, return_indices=True)
                A = A[tuple(idx)] if miss < A.size else S.np.zeros_like(A)
            S.save_tif(f, A)
    if "nhle" in what or "all" in what:
        feats = S.nhle(e0, n0, e1, n1, os.path.join(D, "lb_polys.json"))
        print("listed buildings", len(feats))
    if "photos" in what or "all" in what:
        les = [f["attributes"]["ListEntry"] for f in json.load(open(os.path.join(D, "lb_polys.json")))["features"]]
        imgs = S.listing_photos(les, os.path.join(D, "wd_images.json"))
        print("listings with a Commons photo:", len(imgs), "of", len(les))
        S.commons_thumbs(imgs, os.path.join(D, "commons"))
        sheets = S.contact_sheets(imgs, os.path.join(D, "commons"), os.path.join(D, "sheets"))
        print("contact sheets for classifying wall materials:", *sheets, sep="\n  ")


def cmd_os_check():
    p = load()
    osm = json.load(open(os.path.join(D, "osm.json")))["elements"]
    n_osm = sum(1 for e in osm if "building" in e.get("tags", {}))
    print("OSM buildings in tile:", n_osm)
    for sq in p["os_square"]:
        z = S.os_zip_info(sq)
        print(f"OS OpenMap Local {sq}: {z['fileName']} {z['size'] / 1e6:.0f} MB  ({z['url']})")
    print("If OSM has noticeably fewer buildings than the place really has (villages often do), download OS.")


def cmd_os():
    p = load()
    os.makedirs(os.path.join(D, "os"), exist_ok=True)
    out = os.path.join(D, "os_buildings.json")
    if os.path.exists(out):
        os.remove(out)
    for sq in p["os_square"]:
        z = S.os_zip_info(sq)
        zp = os.path.join(D, "os", z["fileName"])
        if not os.path.exists(zp):
            print("downloading", z["fileName"], f"{z['size'] / 1e6:.0f} MB", flush=True)
            open(zp + ".part", "wb").write(S.get(z["url"], timeout=1800, tries=3))
            os.replace(zp + ".part", zp)
        n = S.os_buildings(zp, *p["tile"], out)
        print("OS buildings from", sq, len(n))


def cmd_mapillary():
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "mapillary.py"), "features"], check=False)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "mapillary.py"), "images"], check=False)


if __name__ == "__main__":
    {"init": cmd_init, "fetch": cmd_fetch, "os-check": cmd_os_check, "os": cmd_os, "mapillary": cmd_mapillary}[sys.argv[1]]()
