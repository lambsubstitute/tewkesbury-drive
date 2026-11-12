# Tewkesbury Drive

Browser driving game set in a 2.5 km x 2.5 km square of real Tewkesbury (British National Grid E 387800–390300, N 231500–234000).

## Play

```bash
python3 -m http.server 8765 -d game
```

Open http://localhost:8765. WASD/arrows drive, Space handbrake, C camera (chase / bonnet / high / drone), M big map, R back on road, V engine sound (off by default), 1–7 teleport.

## Data pipeline (`tools/process.py`)

| Source | Used for |
|---|---|
| OpenStreetMap (Overpass, `data/osm.json`) | building footprints, roads + names, bridges, rivers, land use, footpaths |
| Environment Agency LIDAR Composite 1 m DTM + First Return DSM (WCS, `data/dtm.tif`, `data/dsm.tif`) | ground height, building height, roof shape (planes fitted to DSM: gables, hips, towers), tree positions + heights |
| Historic England NHLE listed-building polygons (`data/lb_polys.json`) | which buildings are listed, grade, names |
| Wikidata + Wikimedia Commons photos (`data/wd.json`, `data/materials_by_photo.txt`) | real wall material + colour for 270 listed buildings |

OSM lon/lat → BNG uses the OSTN15 grid (pyproj network grids) so footprints line up with LIDAR to ~0.1 m.

### Roofs and walls (`tools/roofseg.py`, `tools/buildgeo.py`)

Each building's 1 m LIDAR surface is region-grown into flat roof facets. This works even where OSM maps a whole terrace as one outline. Each facet becomes a polygon. Shared edges are simplified as a coverage, so neighbouring facets stay watertight, and vertices are snapped onto the exact line where two roof planes meet (ridges, hips, valleys). The facets are then triangulated. Neighbouring facets at different heights get vertical step walls. Eaves overhang 0.32 m except against neighbours, and small LIDAR spikes become chimneys.

Walls are split into ground floor, upper storeys and gable. Storey height comes from the eave height, and window bays are spaced evenly per wall. Party walls get no windows. Ground floors of buildings with an OSM shop, café, pub or office facing a street become shopfronts, with a fascia sign showing the business's OSM name.

### Street level (`game/street.js`, `data/osm_street.json`)

Benches, bins, post boxes, bus stops and shelters, phone boxes, street cabinets, memorials, traffic signals, zebra crossings (with Belisha beacons) and signal-controlled crossings are all placed from OSM. So are garden walls, fences and hedges, which also block the car.

OSM has no street lamps mapped in Tewkesbury, so lamp posts are generated every ~30 m along built-up streets. They are not real positions.

### Wall materials

Listed buildings use materials observed in photos. Wikidata (CC0) links 366 Tewkesbury listings to their NHLE numbers, and 280 have a Wikimedia Commons photo (property P18). `tools/sheets.py` lays the photos out as labelled contact sheets. Each facade was then classified by eye into `data/materials_by_photo.txt`: red brick, white render, coloured render (with hex colour), timber frame (with infill colour) or stone.

Most NHLE listing "polygons" are ~3 m² markers inside the building, so materials are applied per wall section. Each wall section takes the material of the nearest listing marker inside its OSM building. This splits terraces that OSM maps as one block. Markers up to 16 m outside any outline are snapped to the nearest unclaimed building.

Unlisted buildings are still assumed: 72% brick, 28% render, which roughly matches the photographed mix. The photos themselves are not shipped with the game.

Historic England's list-entry text isn't used: its robots.txt disallows Claude/Anthropic crawlers, and the official NHLE API has no description field.

Rebuild:

The LIDAR rasters aren't in the repo (26 MB each), so download them first:

```bash
curl -o data/dtm.tif "https://environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wcs?service=WCS&version=2.0.1&request=GetCoverage&CoverageId=13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m&format=image/tiff&subset=E(387800,390300)&subset=N(231500,234000)"
curl -o data/dsm.tif "https://environment.data.gov.uk/spatialdata/lidar-composite-digital-surface-model-first-return-dsm-1m/wcs?service=WCS&version=2.0.1&request=GetCoverage&CoverageId=df4e3ec3-315e-48aa-aaaf-b5ae74d7b2bb__Lidar_Composite_Elevation_FZ_DSM_1m&format=image/tiff&subset=E(387800,390300)&subset=N(231500,234000)"
python3 -m venv .venv && .venv/bin/pip install pyproj numpy scipy shapely tifffile imagecodecs pillow
.venv/bin/python tools/process.py
```

The OSM extracts (`data/osm.json`, `data/osm_street.json`) can be refreshed by POSTing `data/q.txt` and `data/q2.txt` to an Overpass API server.

The Commons photo thumbnails used to classify materials aren't in the repo either. `data/wd_images.json` lists each photo's Commons URL.

## Publishing

GitHub Pages serves the `gh-pages` branch, which holds the contents of `game/`. To publish after changes:

```bash
git subtree push --prefix game origin gh-pages
```

Attribution: © OpenStreetMap contributors (ODbL); © Environment Agency (OGL); © Historic England (OGL).
