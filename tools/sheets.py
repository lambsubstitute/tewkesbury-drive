"""Make labelled contact sheets of Commons photos so each listed building can be classified by eye."""
import json, os, sys
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = os.path.join(ROOT, "data")
OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
imgs = json.load(open(os.path.join(D, "wd_images.json")))
items = [(le, lab) for le, (_, lab) in sorted(imgs.items()) if os.path.exists(os.path.join(D, "commons", f"{le}.jpg"))]
C, R, W, H = 4, 4, 300, 250
font = ImageFont.load_default(size=13) if hasattr(ImageFont, "load_default") else None
index = []
for s in range(0, len(items), C * R):
    sheet = Image.new("RGB", (C * W, R * H), (30, 30, 30))
    d = ImageDraw.Draw(sheet)
    for k, (le, lab) in enumerate(items[s:s + C * R]):
        n = s + k
        try:
            im = Image.open(os.path.join(D, "commons", f"{le}.jpg")).convert("RGB")
        except Exception:
            continue
        im.thumbnail((W - 6, H - 30))
        x, y = (k % C) * W, (k // C) * H
        sheet.paste(im, (x + 3, y + 26))
        d.rectangle([x, y, x + W, y + 24], fill=(0, 0, 0))
        d.text((x + 4, y + 4), f"#{n} {lab[:36]}", fill=(255, 230, 120), font=font)
        index.append({"n": n, "le": le, "name": lab})
    sheet.save(os.path.join(OUT, f"sheet_{s // (C * R):02d}.jpg"), quality=82)
json.dump(index, open(os.path.join(OUT, "index.json"), "w"), indent=0)
print(len(index), "images,", (len(items) + C * R - 1) // (C * R), "sheets")
