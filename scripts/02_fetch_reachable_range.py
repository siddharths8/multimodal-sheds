"""
Stage 2 — fetch TomTom CAR reachable-range sheds for every sample point.

TomTom's Reachable Range supports car only (it rejects bicycle/pedestrian), so
this stage fetches just the car scenarios (AM peak, PM peak, midday — all with
traffic at a departAt — plus a free-flow baseline with traffic off), at every
time band. Bike & walk sheds are computed locally on the OSM network by
02b_compute_active_sheds.py and share this stage's cache + combined file.

- Per-call JSON cache (data/sheds/) => safe to stop and resume across days
  (the free tier is ~2,500 calls/day; this run is larger, so just re-run until done).
- Rebuilds data/sheds/<short>_sheds_combined.json from the cache every run.

Requires TOMTOM_API_KEY and a resolved peak (run 01c_probe_peak.py first).

Usage:
  python 02_fetch_reachable_range.py                 # fetch everything outstanding
  python 02_fetch_reachable_range.py --max-calls 2300  # stop after N live calls (daily cap)
  python 02_fetch_reachable_range.py --test          # one point only (smoke test)
  python 02_fetch_reachable_range.py --combine-only   # just rebuild the combined file
"""

import os
import sys
import json
import time
import glob
import argparse
import requests
import geopandas as gpd
from shapely.geometry import Polygon, mapping
from shapely.validation import make_valid

from config_loader import cfg

KEY   = cfg.tomtom_key()
SHORT = cfg.CITY_SHORT.lower()
POINTS   = os.path.join(cfg.WARDS_DIR, f"{SHORT}_sample_points.geojson")
COMBINED = os.path.join(cfg.SHEDS_DIR, f"{SHORT}_sheds_combined.json")
HEADERS  = {"User-Agent": cfg.HTTP_UA}


def cache_path(pt_id, mode, scen_key, band):
    return os.path.join(cfg.SHEDS_DIR, f"{pt_id}__{mode}__{scen_key}__{band}s.json")


def call_tomtom(lat, lon, travel_mode, band, traffic, depart_at):
    """Return a shapely Polygon for one reachable-range call, or None on hard failure."""
    url = cfg.TOMTOM_BASE.format(lat=lat, lon=lon)
    params = {"key": KEY, "travelMode": travel_mode, "timeBudgetInSec": band}
    if travel_mode == "car":
        params["traffic"] = "true" if traffic else "false"
        params["routeType"] = "fastest"
        if depart_at:
            params["departAt"] = depart_at

    backoff = 5
    for attempt in range(5):
        r = requests.get(url, params=params, headers=HEADERS, timeout=60)
        if r.status_code == 200:
            try:
                boundary = r.json()["reachableRange"]["boundary"]
            except (KeyError, ValueError):
                print(f"      no reachableRange in 200 response; skipping")
                return None
            poly = Polygon([(p["longitude"], p["latitude"]) for p in boundary])
            return poly if poly.is_valid else make_valid(poly)
        if r.status_code == 429:                       # rate limited
            print(f"      429 rate-limited; backing off {backoff}s")
            time.sleep(backoff); backoff *= 2; continue
        if r.status_code in (403,):                    # daily quota exhausted
            raise QuotaExceeded(r.text[:200])
        if r.status_code == 400:
            print(f"      400 ({r.text[:120]}); skipping this call")
            return None
        print(f"      HTTP {r.status_code}; retrying")
        time.sleep(backoff); backoff *= 2
    print("      exhausted retries; skipping")
    return None


class QuotaExceeded(Exception):
    pass


def build_combined():
    """Reassemble the combined file from whatever is in the cache."""
    pts = gpd.read_file(POINTS)
    meta = {r.pt_id: r for r in pts.itertuples()}
    combined = {}
    for fp in glob.glob(os.path.join(cfg.SHEDS_DIR, "*__*__*__*s.json")):
        with open(fp, encoding="utf-8") as f:
            rec = json.load(f)
        pid = rec["pt_id"]
        node = combined.setdefault(pid, {
            "pt_id": pid, "ward_no": rec["ward_no"], "ward_name": rec["ward_name"],
            "zone": rec["zone"], "lat": rec["lat"], "lon": rec["lon"], "sheds": {}
        })
        node["sheds"].setdefault(rec["mode"], {}).setdefault(rec["scenario"], {})[str(rec["band_sec"])] = rec["geometry"]
    with open(COMBINED, "w", encoding="utf-8") as f:
        json.dump(list(combined.values()), f)
    n_poly = sum(len(v["sheds"].get(m, {}).get(s, {}))
                 for v in combined.values() for m in v["sheds"] for s in v["sheds"][m])
    print(f"Combined: {len(combined)} points, {n_poly} polygons -> {COMBINED}")
    return combined


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-calls", type=int, default=None, help="stop after N live calls")
    ap.add_argument("--sleep", type=float, default=cfg.SLEEP_SEC, help="delay between live calls (s)")
    ap.add_argument("--test", action="store_true", help="only the first point")
    ap.add_argument("--combine-only", action="store_true", help="rebuild combined file and exit")
    args = ap.parse_args()

    if args.combine_only:
        build_combined(); return

    if not os.path.exists(cfg.DEPARTURES_RESOLVED):
        print("WARNING: no resolved peaks found (run 01c_probe_peak.py). "
              "Falling back to config fallback_am_peak / fallback_pm_peak.")

    pts = gpd.read_file(POINTS)
    if args.test:
        pts = pts.iloc[:1]

    # build the full work list (CAR ONLY — bike/walk come from 02b)
    work = []
    for r in pts.itertuples():
        tm = cfg.MODES["car"]["travelMode"]
        for scen in cfg.scenarios_for_mode("car"):
            for band in cfg.BANDS_SEC:
                work.append((r, "car", tm, scen, band))

    done = sum(1 for (r, mode, tm, scen, band) in work
               if os.path.exists(cache_path(r.pt_id, mode, scen["key"], band)))
    todo = len(work) - done
    print(f"Work items: {len(work)}  | cached: {done}  | to fetch: {todo}")
    if args.max_calls:
        print(f"Will stop after {args.max_calls} live calls this run.")

    live = 0
    try:
        for (r, mode, tm, scen, band) in work:
            cp = cache_path(r.pt_id, mode, scen["key"], band)
            if os.path.exists(cp):
                continue
            if args.max_calls and live >= args.max_calls:
                print(f"Reached --max-calls={args.max_calls}; stopping (resume later).")
                break
            poly = call_tomtom(r.lat, r.lon, tm, band, scen["traffic"], scen["departAt"])
            live += 1
            if poly is None or poly.is_empty:
                continue
            rec = {
                "pt_id": r.pt_id, "ward_no": int(r.ward_no), "ward_name": r.ward_name,
                "zone": r.zone, "lat": float(r.lat), "lon": float(r.lon),
                "mode": mode, "scenario": scen["key"], "scenario_label": scen["label"],
                "band_sec": int(band), "band_min": int(band // 60),
                "traffic": bool(scen["traffic"]), "departAt": scen["departAt"],
                "geometry": mapping(poly),
            }
            with open(cp, "w", encoding="utf-8") as f:
                json.dump(rec, f)
            if live % 50 == 0:
                print(f"  ...{live} live calls (last: {r.pt_id} {mode}/{scen['key']} {band//60}min)")
            time.sleep(args.sleep)
    except QuotaExceeded as e:
        print(f"\nDaily quota exhausted ({e}). Re-run tomorrow to resume from cache.")

    print(f"\nLive calls this run: {live}")
    build_combined()
    remaining = todo - live
    if remaining > 0:
        print(f"~{remaining} calls still outstanding — re-run to continue.")
    else:
        print("All sheds fetched. Next: 03_compute_area_comparison.py")


if __name__ == "__main__":
    main()
