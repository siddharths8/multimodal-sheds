# Multimodal Sheds — Drive vs Bicycle vs Walk, with traffic

Generate **drive / bicycle / walk reachable-area "sheds"** for a city's neighborhoods, then
overlay them on one map so you can *see* how little area a car covers versus a bicycle or walk
in the same time budget. Built to make the case for investing in walking and cycling
infrastructure in congested cities.

The default configuration is **Bengaluru (Bangalore)**, India, using BBMP ward boundaries — but the
tool is **config-driven**, so you can point it at any city (see *Run it for your city* below).

**Live viewers (GitHub Pages, from `/docs`):**

| Page | What it shows |
|---|---|
| `index.html` | Overlay viewer — pick a ward, compare drive/bicycle/walk sheds, toggle time-of-day & budget |
| `explore.html` | Hover explorer — move the mouse and the nearest origin point's sheds appear live |
| `categories.html` | Ward choropleth — where cycling is competitive, where driving wins, and where the street network itself limits cars |

## Headline finding (Bengaluru, 15-minute budget)

- The median ward's bicycle shed covers **92% of the peak-hour car shed**.
- Inside the Outer Ring Road, the bicycle is the better choice in **87% of wards**
  (reaching ≥75% of the car's area at either peak); beyond the ORR the car stays
  better in two-thirds of wards.
- Congestion costs the median ward's car **58% of its free-flow reach**.

## How it works

- **Driving** sheds come from the **TomTom Reachable Range API** with traffic, at the AM and PM
  peak (the worst hours are found empirically by probing central points across the day — stage 01c)
  plus a **free-flow** baseline (`traffic=false`), so the area a car *loses* to congestion is
  measurable with the same engine, network, and algorithm.
- **Bicycle & walk** sheds are computed locally on the **OpenStreetMap network** (Overpass
  extract → shortest-path search → concave hull) at fixed average speeds set in the config
  (defaults: bicycle 12 km/h, walk 4.8 km/h). No API quota is used for these; they are
  traffic-independent.
- Each ward gets one or more origin points snapped to **major-road intersections** (from OSM),
  scaled to ward size, so origins are representative travel points — not centroids in a forest.
- Areas are computed in a metric UTM CRS auto-derived from the city centre.

## Setup

```bash
pip install -r requirements.txt
```

Get a free TomTom API key (2,500 requests/day) at https://developer.tomtom.com/ and set it:

```powershell
$env:TOMTOM_API_KEY = "your-key-here"     # PowerShell (this session)
setx TOMTOM_API_KEY "your-key-here"       # persist across sessions (reopen shell)
```
```bash
export TOMTOM_API_KEY="your-key-here"     # bash
```

The Mapbox token in `scripts/00_config.py` is a **public, basemap-only token** (no routing or
paid calls go through it). It works as-is, but for your own deployment you should swap in your
own free public token from https://account.mapbox.com/ so usage counts against your account.

## Run order

```bash
python scripts/01_prep_wards_and_points.py   # wards + major-intersection sample points
python scripts/01b_review_points_map.py      # REVIEW the points in a browser BEFORE spending calls
python scripts/01c_probe_peak.py             # find the real AM/PM peak hours (small probe, ~45 calls)
python scripts/02_fetch_reachable_range.py   # fetch car sheds from TomTom (resumable; --max-calls to cap/day)
python scripts/02b_compute_active_sheds.py   # compute bicycle/walk sheds on the OSM network (no API)
python scripts/03_compute_area_comparison.py # areas, per-ward unions, summary
python scripts/04_build_viewer.py            # overlay viewer   -> docs/index.html
python scripts/05_export_shed_shapefiles.py  # ESRI shapefiles + zip
python scripts/06_qaqc_checks.py             # sanity report (band nesting, free-flow >= peak, ...)
python scripts/07_build_bike_car_category_map.py  # ward choropleth -> docs/categories.html
python scripts/08_build_hover_isochrone_map.py    # hover explorer  -> docs/explore.html
```

The car fetch is larger than one free-tier day (~4,100 calls for 515 points × 4 scenarios ×
2 bands). Every call is cached, so just re-run stage 02 until complete, or pass
`--max-calls 2300` to stop under the daily quota and resume tomorrow. **Stage 01b is a
deliberate checkpoint** — look at the origin points on the map before spending any quota.

## Run it for your city

Edit **`config/city_config.json`** only:

- `city_name`, `city_short`, `center_latlon`, `default_zoom`
- `wards_geojson_url` + the `ward_no_field` / `ward_name_field` / `zone_field` in that file
- `bands_min` (time budgets), `speeds_kmph` (bicycle/walk), `departures.probe` candidate hours,
  `road_intersections.major_road_classes`

Then run the steps above. Everything else — metric CRS, scenarios, OSM extracts, all three
viewers — adapts automatically. (You can also keep multiple configs and switch with the
`CITY_CONFIG` env var: `CITY_CONFIG=tokyo.json python scripts/01_prep_wards_and_points.py`.)

Notes for other cities:
- The hover explorer's inner/outer split looks for road segments named "ring road" in OSM.
  If your city has no ring road, that split will be degenerate — edit the ORR block in
  `scripts/08_build_hover_isochrone_map.py` or ignore that panel section.
- Overpass (OSM's query API) is rate-limited public infrastructure; the scripts fall back
  across several mirrors, but very large cities may need patience on stages 01 and 02b.

## Publish to GitHub Pages

Commit the repo and, in **Settings → Pages**, set the source to the **`main` branch, `/docs`
folder**. The viewers are served at `https://<user>.github.io/<repo>/`, `/explore.html`, and
`/categories.html`.

## Notes & attribution

- Ward boundaries (default): DataMeet *Municipal Spatial Data* (CC BY 4.0).
- Driving sheds: © TomTom (Routing API). Street network for bicycle/walk sheds:
  © OpenStreetMap contributors. Basemap tiles: © Mapbox © OpenStreetMap.
- Your TomTom key is read from the environment and never written to the repo (see
  `.gitignore` — the fetched cache in `data/` is excluded too).
