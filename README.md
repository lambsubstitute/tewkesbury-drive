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

Patches inside an OSM outline where the LIDAR shows open ground (12 m² or more) are cut out, so courtyards and yards that OSM drew inside a block stay open. `tools/roof_check.py` rasterises the finished roofs and compares them with the LIDAR surface. The current result is a 0.21 m area-weighted median error, with 10% of roof area more than 1 m off, mostly at edges and steps.

Walls are split into ground floor, upper storeys and gable. Timber-framed buildings get jettied upper storeys, each oversailing the one below by 0.22 m, plus deeper eaves. Top storeys get shorter windows than the floors below, and brick, rendered and stone fronts get a cornice at the eaves. Storey height comes from the eave height, and window bays are spaced evenly per wall. Party walls get no windows. Each building's street frontage is split into shop units: every 1 m of frontage goes to the nearest OSM shop, café, pub or office, giving one unit per shop. Each unit gets a 3D shopfront (`game/shopfronts.js`) in one of three styles:

- **Traditional:** painted pilasters with console brackets, a moulded fascia and cornice, mullioned display windows with transom lights, a panelled stallriser and a recessed door.
- **Modern:** a flat fascia with slim aluminium frames, full-height glass and a side door. Used for national chains and banks.
- **Pub:** dark paint, small-paned windows and a hanging sign.

Butchers, greengrocers, bakeries, cafés and the shops seen with awnings in Mapillary photos get canvas awnings, in their photographed colours where known. The shop's name is lettered onto its own fascia, edge to edge between its pilasters, so signs can't overlap or drift off their shop.

### Street level (`game/street.js`, `data/osm_street.json`)

Benches, bins, post boxes, bus stops and shelters, phone boxes, street cabinets, memorials, traffic signals, zebra crossings (with Belisha beacons) and signal-controlled crossings are all placed from OSM. So are garden walls, fences and hedges, which also block the car.

OSM has no street lamps mapped in Tewkesbury, so lamp posts are generated every ~30 m along built-up streets. They are not real positions.

Pavements are raised 150 mm with kerb stones and 600 mm slab paving, and the car rides up onto them. Which sides of a street have one comes from the OSM `sidewalk=*` tags, plus separately mapped `footway=sidewalk` ways. Untagged streets get pavements on both sides when they are within 25 m of buildings. Where a frontage stands within 5.5 m of the kerb, the pavement runs right up to the building. Narrow leftover strips between kerb, footway and frontage are filled, as are junction corners in the town centre. Bus stops get a yellow bus-stop box on the carriageway.

Google Street View isn't used: its terms don't allow deriving map data from its imagery.

### Mapillary (`tools/mapillary.py`, `data/mapillary/`)

Mapillary's computer-vision detections from 7,672 street-level photos (2020–2025) add real positions for:

- 146 street lights
- 119 bins
- 87 telegraph poles
- 43 street cabinets
- 46 traffic-signal heads
- 12 benches
- 287 traffic signs (regulatory roundels, warning triangles, information panels, speed limits)

Anything within a few metres of the same object already in OSM is skipped. Detections in the carriageway are pulled back onto the pavement edge. A generated lamp is dropped wherever a detected one stands within 20 m, so lamps are real where Mapillary has seen them and generated elsewhere. Detected positions are triangulated from photos and can be a few metres out.

To refetch, save a Mapillary access token (Dashboard, Developers) to `~/.mapillary_token` and run:

```bash
.venv/bin/python tools/mapillary.py features
.venv/bin/python tools/mapillary.py images
```

Street objects © Mapillary contributors, CC BY-SA 4.0.

#### Shopfront colours

`tools/shopfront_photos.py` finds the Mapillary photo that best faces each shop sign and lays crops out as labelled contact sheets. The paint and fascia colours of 60 shops were read by eye into `data/shopfronts_from_photos.txt`, for example Betfred navy, Holland & Barrett dark green, the Edinburgh Woollen Mill burgundy, Chicago Pizza red with a yellow fascia, and Tewkesbury Cards & Gifts magenta.

A colour was only recorded where the shop's own name was legible on its fascia in the photo, because photo positions are a few metres out and often frame the neighbour instead. Where OSM maps several shops as one building, each stretch of frontage takes its nearest shop's colour. Shops without a legible photo keep a generic paint colour.

The photos date from 2020–2025, so a shop that has been repainted or rebranded since (e.g. WHSmith to TG Jones) may show its older colours or none. The crops (`data/mly_thumbs/`) are Mapillary imagery and are not in the repo.

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
