"""
Stage 2b — compute bike & walk sheds on the OSM network for every sample point.

TomTom can't do active modes, so these are computed locally (no API quota) with
osm_isochrone at the fixed average speeds in city_config.json. Writes per-call
JSON in the SAME cache format as stage 2, so 02 --combine-only assembles both
car and active-mode sheds into one combined file.

Resumable: skips any cache file that already exists. One city-wide OSM graph is
built per mode (cached as a pickle), then Dijkstra runs per point.
"""

import os
import json
import geopandas as gpd
from shapely.geometry import mapping
from shapely.validation import make_valid

from config_loader import cfg
import osm_isochrone as osm

SHORT = cfg.CITY_SHORT.lower()
POINTS = os.path.join(cfg.WARDS_DIR, f"{SHORT}_sample_points.geojson")
ACTIVE_MODES = [m for m in cfg.MODE_ORDER if m != "car"]   # bicycle, pedestrian
TAG = f"{SHORT}_full"


def cache_path(pt_id, mode, band):
    return os.path.join(cfg.SHEDS_DIR, f"{pt_id}__{mode}__na__{band}s.json")


pts = gpd.read_file(POINTS)
# network bbox: points extent + ~6 km margin (covers a 20-min bike reach)
minx, miny, maxx, maxy = pts.total_bounds
M = 0.06
bbox = (miny - M, minx - M, maxy + M, maxx + M)
print(f"Active modes: {ACTIVE_MODES} | {len(pts)} points | bbox(s,w,n,e)="
      f"({bbox[0]:.3f},{bbox[1]:.3f},{bbox[2]:.3f},{bbox[3]:.3f})")

total_written = 0
for mode in ACTIVE_MODES:
    todo = [(r, band) for r in pts.itertuples() for band in cfg.BANDS_SEC
            if not os.path.exists(cache_path(r.pt_id, mode, band))]
    print(f"\n[{mode}] speed {cfg.SPEEDS_KMPH[mode]} km/h | {len(todo)} sheds to compute")
    if not todo:
        continue
    bundle = osm.build_bundle(mode, bbox, TAG)
    for i, (r, band) in enumerate(todo, 1):
        poly = osm.isochrone(bundle, float(r.lon), float(r.lat), band, mode)
        if not poly.is_valid:
            poly = make_valid(poly)
        rec = {
            "pt_id": r.pt_id, "ward_no": int(r.ward_no), "ward_name": r.ward_name,
            "zone": r.zone, "lat": float(r.lat), "lon": float(r.lon),
            "mode": mode, "scenario": "na", "scenario_label": "",
            "band_sec": int(band), "band_min": int(band) // 60,
            "traffic": False, "departAt": None, "geometry": mapping(poly),
        }
        with open(cache_path(r.pt_id, mode, band), "w", encoding="utf-8") as f:
            json.dump(rec, f)
        total_written += 1
        if i % 200 == 0:
            print(f"  {mode}: {i}/{len(todo)}")
    del bundle      # free memory before the next mode's graph

print(f"\nWrote {total_written} active-mode sheds.")
print("Next: run  python 02_fetch_reachable_range.py --combine-only  to merge car + active sheds.")
