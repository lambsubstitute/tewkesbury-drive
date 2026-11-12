"""Find the Mapillary photo that best shows each shop sign, download a crop around it, and lay the crops out
as labelled contact sheets so the real fascia colours can be read off by eye.

Usage: shopfront_photos.py <sheet output dir>
Writes data/shopfront_index.json (sheet number -> shop name, image id, capture year).
Photo crops go to data/mly_thumbs/ (CC BY-SA imagery, kept out of the repo).
"""
import json, math, os, sys, time, urllib.request, urllib.parse
import pyproj
from pyproj import Transformer
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mapillary as mly

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = os.path.join(ROOT, "data")
OUT_SHEETS = sys.argv[1]
THUMBS = os.path.join(D, "mly_thumbs")
os.makedirs(THUMBS, exist_ok=True)
os.makedirs(OUT_SHEETS, exist_ok=True)
E0, N0 = 389050.0, 232750.0
pyproj.network.set_network_enabled(True)
TF = Transformer.from_crs(4326, 27700, always_xy=True)

signs = json.load(open(os.path.join(ROOT, "game", "data", "signs.json")))
imgs = json.load(open(os.path.join(D, "mapillary", "images.json")))
cams = []
for im in imgs:
    if im.get("compass_angle") is None:
        continue
    lon, lat = im["geometry"]["coordinates"]
    E, N = TF.transform(lon, lat)
    year = int(1970 + im.get("captured_at", 0) / (1000 * 3600 * 24 * 365.25))
    cams.append((E - E0, N0 - N, im["compass_angle"], year, im["id"]))

HFOV = 70.0
picks = []
for si, s in enumerate(signs):
    nx, nz = math.sin(s["r"]), math.cos(s["r"])         # outward normal of the shop wall (local x, z)
    best = None
    for (cx, cz, comp, year, iid) in cams:
        dx, dz = s["x"] - cx, s["z"] - cz
        dist = math.hypot(dx, dz)
        if not (5 < dist < 32):
            continue
        if -(dx * nx + dz * nz) < 2.5:                   # camera must stand in front of the facade
            continue
        bearing = math.degrees(math.atan2(dx, -dz)) % 360  # compass bearing camera -> sign
        diff = (bearing - comp + 540) % 360 - 180
        if abs(diff) > HFOV / 2 - 6:
            continue
        frontal = -(dx * nx + dz * nz) / dist             # 1 = square-on
        score = abs(diff) / 30 + abs(dist - 12) / 12 + (1 - frontal) * 1.5 + (2025 - year) * 0.25
        if best is None or score < best[0]:
            best = (score, iid, diff, year, dist)
    if best:
        picks.append((si, best))
print("signs", len(signs), "with a usable photo", len(picks))

# thumbnail URLs (signed CDN links, fetched in batches with the token in the header)
ids = sorted({b[1] for _, b in picks})
urls = {}
for k in range(0, len(ids), 50):
    d = mly.get("images", {"image_ids": ",".join(ids[k:k + 50]), "fields": "id,thumb_1024_url"})
    for x in d.get("data", []):
        urls[x["id"]] = x.get("thumb_1024_url")
    time.sleep(0.2)

crops, index = [], []
seen_img = set()
for si, (score, iid, diff, year, dist) in picks:
    if iid in seen_img:
        continue
    seen_img.add(iid)
    path = os.path.join(THUMBS, f"{iid}.jpg")
    if not os.path.exists(path):
        u = urls.get(iid)
        if not u:
            continue
        try:
            data = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "tewkesbury-drive/0.1"}), timeout=60).read()
            open(path, "wb").write(data)
        except Exception:
            continue
        time.sleep(0.15)
    im = Image.open(path).convert("RGB")
    W, H = im.size
    cx = W * (0.5 + diff / HFOV)
    half = W * 0.36
    box = (int(max(0, cx - half)), int(H * 0.12), int(min(W, cx + half)), int(H * 0.82))
    crops.append((si, im.crop(box), year))

C, R, TW, TH = 3, 3, 400, 330
font = ImageFont.load_default(size=13)
n = 0
for s0 in range(0, len(crops), C * R):
    sheet = Image.new("RGB", (C * TW, R * TH), (30, 30, 30))
    d = ImageDraw.Draw(sheet)
    for k, (si, crop, year) in enumerate(crops[s0:s0 + C * R]):
        x, y = (k % C) * TW, (k // C) * TH
        c = crop.copy()
        c.thumbnail((TW - 6, TH - 30))
        sheet.paste(c, (x + 3, y + 26))
        d.rectangle([x, y, x + TW, y + 24], fill=(0, 0, 0))
        d.text((x + 4, y + 5), f"#{n} {signs[si]['n'][:30]} ({year})", fill=(255, 230, 120), font=font)
        index.append({"n": n, "sign": si, "name": signs[si]["n"], "year": year})
        n += 1
    sheet.save(os.path.join(OUT_SHEETS, f"shops_{s0 // (C * R):02d}.jpg"), quality=85)
json.dump(index, open(os.path.join(D, "shopfront_index.json"), "w"), indent=0)
print("crops", len(crops), "sheets", (len(crops) + C * R - 1) // (C * R))
