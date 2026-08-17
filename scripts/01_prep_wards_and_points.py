"""
Stage 1 — prepare ward boundaries and sample points at MAJOR-ROAD INTERSECTIONS.

- Downloads the ward GeoJSON (if not cached) from the URL in city_config.json.
- Normalises attributes to ward_no / ward_name / zone, cleans geometry, area (km2).
- Pulls the major-road network from OpenStreetMap (Overpass) and derives
  intersections (nodes shared by 2+ major roads).
- Places 1+ sample points per ward, scaled to ward size, snapped to well-spread
  major-road intersections inside the ward (so shed origins are representative
  travel points, not forests or dead-end lanes).

Point source per ward (recorded in the `src` attribute):
  intersection      -> a major-road crossing inside the ward (preferred)
  on_major_road     -> a node on a major road (ward had a major road but no crossing)
  centroid_fallback -> interior representative point (ward had no major road)

Outputs (EPSG:4326):
  data/wards/<short>_wards_clean.geojson
  data/wards/<short>_sample_points.geojson
  data/wards/<short>_major_intersections.geojson   (context layer)

Next step (01b) renders these for review BEFORE any TomTom calls.
"""

import os
import time
import json
import math
import requests
import numpy as np
import geopandas as gpd
from shapely.geometry import Point
from shapely.validation import make_valid

from config_loader import cfg

SHORT = cfg.CITY_SHORT.lower()
WARDS_OUT  = os.path.join(cfg.WARDS_DIR, f"{SHORT}_wards_clean.geojson")
POINTS_OUT = os.path.join(cfg.WARDS_DIR, f"{SHORT}_sample_points.geojson")
INTER_OUT  = os.path.join(cfg.WARDS_DIR, f"{SHORT}_major_intersections.geojson")
OSM_CACHE  = os.path.join(cfg.RAW_DIR, "osm_major_roads.json")


# ── Size-adaptive target count ───────────────────────────────────────────────
def target_count(area_km2):
    if area_km2 <= 3:
        return 1
    if area_km2 <= 8:
        return 4
    if area_km2 <= 20:
        return 9
    return math.ceil(area_km2 / 2.5)


def farthest_point_sample(xy, n, seed):
    """Greedy max-min spread. xy: (k,2) array; returns up to n row indices."""
    n = min(n, len(xy))
    chosen = [seed]
    if n <= 1:
        return chosen
    d = np.linalg.norm(xy - xy[seed], axis=1)
    while len(chosen) < n:
        nxt = int(np.argmax(d))
        if nxt in chosen:
            break
        chosen.append(nxt)
        d = np.minimum(d, np.linalg.norm(xy - xy[nxt], axis=1))
    return chosen


# ── Load + clean wards ────────────────────────────────────────────────────────
if not os.path.exists(cfg.WARDS_FILE):
    print(f"Downloading wards: {cfg.WARDS_URL}")
    r = requests.get(cfg.WARDS_URL, timeout=180)
    r.raise_for_status()
    with open(cfg.WARDS_FILE, "wb") as f:
        f.write(r.content)
    print(f"  saved -> {cfg.WARDS_FILE}  ({len(r.content):,} bytes)")

print(f"Reading {cfg.WARDS_FILE}")
gdf = gpd.read_file(cfg.WARDS_FILE)
gdf = gdf.set_crs(cfg.WGS84) if gdf.crs is None else gdf.to_crs(cfg.WGS84)
print(f"  {len(gdf)} wards")


def _ward_no(v):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return v

gdf["ward_no"]   = gdf[cfg.WARD_NO_F].map(_ward_no)
gdf["ward_name"] = gdf[cfg.WARD_NAME_F].astype(str).str.strip()
gdf["zone"]      = (gdf[cfg.ZONE_F].astype(str).str.strip()
                    if cfg.ZONE_F and cfg.ZONE_F in gdf.columns else "")
gdf["geometry"]  = gdf.geometry.apply(lambda g: g if g.is_valid else make_valid(g)).buffer(0)

gdf_m = gdf.to_crs(cfg.WORK_CRS)
gdf["area_km2"] = (gdf_m.geometry.area / 1e6).round(3)


# ── Major-road intersections from OSM (cached) ───────────────────────────────
def fetch_osm_major_roads():
    if os.path.exists(OSM_CACHE):
        print(f"Using cached OSM roads: {OSM_CACHE}")
        with open(OSM_CACHE, encoding="utf-8") as f:
            return json.load(f)
    minx, miny, maxx, maxy = gdf.total_bounds          # lon/lat
    bbox = f"{miny},{minx},{maxy},{maxx}"              # Overpass: s,w,n,e
    classes = "|".join(cfg.MAJOR_ROAD_CLASSES)
    query = (
        f"[out:json][timeout:180];"
        f'(way["highway"~"^({classes})(_link)?$"]({bbox}););'
        f"out body;>;out skel qt;"
    )
    headers = {"User-Agent": cfg.HTTP_UA}
    print(f"Querying Overpass for major roads in bbox {bbox} ...")
    last_err = None
    for attempt in range(1, 5):
        url = cfg.OVERPASS_URLS[(attempt - 1) % len(cfg.OVERPASS_URLS)]
        try:
            print(f"  attempt {attempt} -> {url}")
            r = requests.post(url, data={"data": query}, headers=headers, timeout=300)
            r.raise_for_status()
            data = r.json()
            with open(OSM_CACHE, "w", encoding="utf-8") as f:
                json.dump(data, f)
            print(f"  cached {len(data.get('elements', []))} OSM elements -> {OSM_CACHE}")
            return data
        except Exception as e:
            last_err = e
            print(f"  failed: {e}")
            time.sleep(5 * attempt)
    raise SystemExit(f"Overpass failed after retries across mirrors: {last_err}")


def derive_intersections(osm):
    """Return (intersections_lonlat, all_major_nodes_lonlat)."""
    node_xy = {}
    way_nodes = []
    for el in osm.get("elements", []):
        if el["type"] == "node":
            node_xy[el["id"]] = (el["lon"], el["lat"])
        elif el["type"] == "way" and "nodes" in el:
            way_nodes.append(el["nodes"])
    freq = {}
    for nodes in way_nodes:
        for nid in nodes:
            freq[nid] = freq.get(nid, 0) + 1
    inter = [node_xy[nid] for nid, c in freq.items() if c >= 2 and nid in node_xy]
    alln  = [node_xy[nid] for nid in freq if nid in node_xy]
    return inter, alln

osm = fetch_osm_major_roads()
inter_ll, nodes_ll = derive_intersections(osm)
print(f"  major-road intersections: {len(inter_ll):,}  |  all major nodes: {len(nodes_ll):,}")

inter_m = gpd.GeoDataFrame(geometry=[Point(x, y) for x, y in inter_ll], crs=cfg.WGS84).to_crs(cfg.WORK_CRS)
nodes_m = gpd.GeoDataFrame(geometry=[Point(x, y) for x, y in nodes_ll], crs=cfg.WGS84).to_crs(cfg.WORK_CRS)

ward_idx = gdf_m[["ward_no", "geometry"]].copy()
inter_j = gpd.sjoin(inter_m, ward_idx, predicate="within").groupby("ward_no")
nodes_j = gpd.sjoin(nodes_m, ward_idx, predicate="within").groupby("ward_no")
inter_groups = {wn: g for wn, g in inter_j}
nodes_groups = {wn: g for wn, g in nodes_j}


# ── Build sample points ───────────────────────────────────────────────────────
print("Selecting major-intersection sample points per ward...")
rows_m = []
src_counts = {"intersection": 0, "on_major_road": 0, "centroid_fallback": 0}
for (_, w), (_, wm) in zip(gdf.iterrows(), gdf_m.iterrows()):
    wn = w["ward_no"]
    n = target_count(w["area_km2"])
    centroid = wm.geometry.representative_point()

    pool = inter_groups.get(wn)
    src = "intersection"
    if pool is None or pool.empty:
        pool = nodes_groups.get(wn)
        src = "on_major_road"
    if pool is None or pool.empty:
        chosen_pts, src = [centroid], "centroid_fallback"
    else:
        xy = np.array([(g.x, g.y) for g in pool.geometry])
        seed = int(np.argmin(np.linalg.norm(xy - np.array([centroid.x, centroid.y]), axis=1)))
        idx = farthest_point_sample(xy, n, seed)
        chosen_pts = [pool.geometry.iloc[i] for i in idx]

    src_counts[src] += 1
    for i, p in enumerate(chosen_pts):
        rows_m.append({
            "pt_id": f"{wn}_{i}", "ward_no": wn, "ward_name": w["ward_name"],
            "zone": w["zone"], "n_pts_in_ward": len(chosen_pts), "src": src,
            "geometry": p,
        })

pts_m = gpd.GeoDataFrame(rows_m, geometry="geometry", crs=cfg.WORK_CRS)

# integrity: every point inside its ward (1 m tolerance)
ward_by_no = {w["ward_no"]: wm.geometry for (_, w), (_, wm) in zip(gdf.iterrows(), gdf_m.iterrows())}
bad = sum(0 if ward_by_no[r.ward_no].buffer(1).contains(r.geometry) else 1 for r in pts_m.itertuples())
assert bad == 0, f"{bad} sample points fell outside their ward"

pts = pts_m.to_crs(cfg.WGS84)
pts["lon"] = pts.geometry.x.round(6)
pts["lat"] = pts.geometry.y.round(6)

# ── Save ──────────────────────────────────────────────────────────────────────
gdf[["ward_no", "ward_name", "zone", "area_km2", "geometry"]].to_file(WARDS_OUT, driver="GeoJSON")
pts[["pt_id", "ward_no", "ward_name", "zone", "n_pts_in_ward", "src", "lat", "lon", "geometry"]] \
    .to_file(POINTS_OUT, driver="GeoJSON")
inter_m.to_crs(cfg.WGS84).to_file(INTER_OUT, driver="GeoJSON")

# ── Summary ───────────────────────────────────────────────────────────────────
n_pts = len(pts)
ppw = pts.groupby("ward_no").size()
print("\n-- Summary ------------------------------------------------")
print(f"Wards          : {len(gdf)}")
print(f"Sample points  : {n_pts}")
print(f"Points/ward    : min {ppw.min()}, max {ppw.max()}, mean {ppw.mean():.2f}")
print(f"Point sources  : {src_counts}")
print(f"Ward area km2  : min {gdf['area_km2'].min():.2f}, "
      f"max {gdf['area_km2'].max():.2f}, median {gdf['area_km2'].median():.2f}")
print(f"Implied API calls = {cfg.implied_calls(n_pts):,}  "
      f"(car x{len(cfg.car_scenarios())} + bike + walk, x{len(cfg.BANDS_SEC)} bands; "
      f"free tier {cfg.FREE_TIER_DAILY}/day)")
print(f"\nWrote: {WARDS_OUT}")
print(f"Wrote: {POINTS_OUT}")
print(f"Wrote: {INTER_OUT}")
print("\nNext: run 01b_review_points_map.py and review the map BEFORE fetching.")
