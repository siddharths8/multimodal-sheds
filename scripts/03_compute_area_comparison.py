"""
Stage 3 — compute reachable AREA (sq km) by mode/scenario/band and build the
per-ward comparison that powers the viewer.

- Per point shed: area in the metric CRS.
- Per ward: union of the ward's point sheds for each (mode, scenario, band),
  with strict band nesting (15-min shed forced inside the 20-min shed).
- Citywide summary: how bike/walk compare to the (worst) peak car shed, and how
  much area the car loses to congestion (peak vs free-flow).

Outputs:
  data/analysis/<short>_area_by_point.csv
  data/analysis/<short>_area_by_ward.csv      (long: ward x mode x scenario x band)
  data/analysis/<short>_summary.json          (embedded in the viewer)
  data/wards/<short>_ward_sheds.geojson       (ward-union polygons; viewer + shapefiles)
"""

import os
import json
import math
import pandas as pd
import geopandas as gpd
from shapely.geometry import shape
from shapely.ops import unary_union
from shapely.validation import make_valid

from config_loader import cfg
import geo_utils as gutil

SHORT = cfg.CITY_SHORT.lower()
COMBINED   = os.path.join(cfg.SHEDS_DIR, f"{SHORT}_sheds_combined.json")
PT_CSV     = os.path.join(cfg.ANALYSIS_DIR, f"{SHORT}_area_by_point.csv")
WARD_CSV   = os.path.join(cfg.ANALYSIS_DIR, f"{SHORT}_area_by_ward.csv")
SUMMARY    = os.path.join(cfg.ANALYSIS_DIR, f"{SHORT}_summary.json")
WARD_SHEDS = os.path.join(cfg.WARDS_DIR, f"{SHORT}_ward_sheds.geojson")

PRIMARY_BAND = min(cfg.BANDS_MIN)            # headline band (e.g. 15 min)
CAR_SCEN = [s["key"] for s in cfg.car_scenarios()]
PEAK_SCEN = cfg.PEAK_KEYS                     # am_peak, pm_peak (worst-case)


# ── Load every point shed into one GeoDataFrame ──────────────────────────────
with open(COMBINED, encoding="utf-8") as f:
    combined = json.load(f)

rows = []
for node in combined:
    for mode, by_scen in node["sheds"].items():
        for scen, by_band in by_scen.items():
            for band_sec, geom in by_band.items():
                g = shape(geom)
                if not g.is_valid:
                    g = make_valid(g)
                rows.append({
                    "pt_id": node["pt_id"], "ward_no": node["ward_no"],
                    "ward_name": node["ward_name"], "zone": node["zone"],
                    "mode": mode, "scenario": scen,
                    "band_sec": int(band_sec), "band_min": int(band_sec) // 60,
                    "geometry": g,
                })
pt = gpd.GeoDataFrame(rows, geometry="geometry", crs=cfg.WGS84)
pt_m = pt.to_crs(cfg.WORK_CRS)
pt["area_km2"] = (pt_m.geometry.area / 1e6).round(3)
print(f"Loaded {len(pt)} point sheds across "
      f"{pt['mode'].nunique()} modes, {pt['scenario'].nunique()} scenarios, "
      f"{pt['band_min'].nunique()} bands")

pt.drop(columns="geometry").to_csv(PT_CSV, index=False)
print(f"Wrote {PT_CSV}")


# ── Union per ward x mode x scenario x band, with band nesting ───────────────
pt_m = pt_m.assign(mode=pt["mode"], scenario=pt["scenario"],
                   band_sec=pt["band_sec"], band_min=pt["band_min"],
                   ward_no=pt["ward_no"], ward_name=pt["ward_name"], zone=pt["zone"])

ward_features = []
ward_rows = []
ward_lookup = {}     # {ward_no: {band_min: {series_key: area}}}

for (wn, mode, scen), grp in pt_m.groupby(["ward_no", "mode", "scenario"], sort=False):
    wname = grp["ward_name"].iloc[0]
    zone = grp["zone"].iloc[0]
    prev = None
    for band in sorted(grp["band_sec"].unique()):
        polys = list(grp[grp["band_sec"] == band].geometry)
        g = unary_union(polys).buffer(0)
        if prev is not None:                      # nest smaller band inside larger
            g = g.union(prev).buffer(0)
        if mode == "car":                         # smooth the jagged TomTom edges
            g = gutil.smooth_metric(g)
            if prev is not None:
                g = g.union(prev).buffer(0)       # re-assert nesting after smoothing
        prev = g
        bmin = band // 60
        area = round(g.area / 1e6, 3)
        series_key = f"car_{scen}" if mode == "car" else mode
        ward_rows.append({"ward_no": wn, "ward_name": wname, "zone": zone,
                          "mode": mode, "scenario": scen, "band_min": bmin,
                          "area_km2": area, "n_pts": int(grp["pt_id"].nunique())})
        ward_lookup.setdefault(str(wn), {}).setdefault(str(bmin), {})[series_key] = area
        # reproject + simplify once for the web output geojson
        g_web = gpd.GeoSeries([g], crs=cfg.WORK_CRS).to_crs(cfg.WGS84).simplify(0.0003).iloc[0]
        ward_features.append({
            "type": "Feature",
            "properties": {"ward_no": int(wn), "ward_name": wname, "zone": zone,
                           "mode": mode, "scenario": scen, "band_min": int(bmin),
                           "area_km2": area},
            "geometry": g_web.__geo_interface__,
        })

ward_df = pd.DataFrame(ward_rows)
ward_df.to_csv(WARD_CSV, index=False)
print(f"Wrote {WARD_CSV}  ({len(ward_df)} rows)")

with open(WARD_SHEDS, "w", encoding="utf-8") as f:
    json.dump({"type": "FeatureCollection", "features": ward_features}, f)
print(f"Wrote {WARD_SHEDS}  ({len(ward_features)} features, {os.path.getsize(WARD_SHEDS):,} bytes)")


# ── Citywide summary (the argument) ──────────────────────────────────────────
def citywide_union_km2(mode, scen, band):
    sub = pt_m[(pt_m["mode"] == mode) & (pt_m["scenario"] == scen) & (pt_m["band_min"] == band)]
    if sub.empty:
        return None
    return round(unary_union(list(sub.geometry)).buffer(0).area / 1e6, 1)

per_band = {}
for band in cfg.BANDS_MIN:
    wide = ward_df[ward_df["band_min"] == band].pivot_table(
        index="ward_no", columns=["mode", "scenario"], values="area_km2", aggfunc="first")
    # worst (smallest) peak car shed per ward
    peak_cols = [("car", s) for s in PEAK_SCEN if ("car", s) in wide.columns]
    car_peak = wide[peak_cols].min(axis=1) if peak_cols else None
    bike = wide[("bicycle", "na")] if ("bicycle", "na") in wide.columns else None
    walk = wide[("pedestrian", "na")] if ("pedestrian", "na") in wide.columns else None

    rec = {"n_wards": int(wide.shape[0])}
    if car_peak is not None and bike is not None:
        rec["median_bike_pct_of_car_peak"] = round((bike / car_peak * 100).median(), 1)
        rec["n_wards_bike_ge_car_peak"] = int((bike >= car_peak).sum())
    if car_peak is not None and walk is not None:
        rec["median_walk_pct_of_car_peak"] = round((walk / car_peak * 100).median(), 1)
    if ("car", "free_flow") in wide.columns and car_peak is not None:
        ff = wide[("car", "free_flow")]
        rec["median_congestion_penalty_pct"] = round(((ff - car_peak) / ff * 100).median(), 1)
    rec["citywide_union_km2"] = {}
    for mode in cfg.MODE_ORDER:
        for s in cfg.scenarios_for_mode(mode):
            key = f"car_{s['key']}" if mode == "car" else mode
            rec["citywide_union_km2"][key] = citywide_union_km2(mode, s["key"], band)
    per_band[str(band)] = rec

# top wards where a 15-min bike beats the peak car shed (best investment cases)
top = []
pb = ward_df[ward_df["band_min"] == PRIMARY_BAND]
wide = pb.pivot_table(index=["ward_no", "ward_name"], columns=["mode", "scenario"],
                      values="area_km2", aggfunc="first")
peak_cols = [("car", s) for s in PEAK_SCEN if ("car", s) in wide.columns]
if peak_cols and ("bicycle", "na") in wide.columns:
    cp = wide[peak_cols].min(axis=1)
    bk = wide[("bicycle", "na")]
    ratio = (bk / cp).sort_values(ascending=False)
    for (wn, wname), rr in ratio.head(10).items():
        top.append({"ward_no": int(wn), "ward_name": wname,
                    "bike_km2": round(float(bk[(wn, wname)]), 2),
                    "car_peak_km2": round(float(cp[(wn, wname)]), 2),
                    "bike_over_car": round(float(rr), 2)})

summary = {
    "city_name": cfg.CITY_NAME,
    "primary_band_min": PRIMARY_BAND,
    "bands_min": cfg.BANDS_MIN,
    "modes": {m: {"label": cfg.MODES[m]["label"], "color": cfg.MODES[m]["color"]} for m in cfg.MODE_ORDER},
    "car_scenarios": [{"key": s["key"], "label": s["label"]} for s in cfg.car_scenarios()],
    "per_band": per_band,
    "top_bike_beats_car": top,
    "ward_area_lookup": ward_lookup,
}
with open(SUMMARY, "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2)
print(f"Wrote {SUMMARY}")

pb_rec = per_band[str(PRIMARY_BAND)]
print(f"\n-- {PRIMARY_BAND}-min headline ----------------------------------")
print(f"  Median bike area as % of peak car : {pb_rec.get('median_bike_pct_of_car_peak')}%")
print(f"  Median walk area as % of peak car : {pb_rec.get('median_walk_pct_of_car_peak')}%")
print(f"  Wards where bike >= peak car      : {pb_rec.get('n_wards_bike_ge_car_peak')} / {pb_rec['n_wards']}")
print(f"  Median car congestion penalty     : {pb_rec.get('median_congestion_penalty_pct')}% (peak vs free-flow)")
print("\nNext: 04_build_viewer.py and 05_export_shed_shapefiles.py")
