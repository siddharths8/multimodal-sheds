"""
Stage 6 — QA/QC report over the generated data.

Sanity checks (warnings are informative, not fatal):
  - cache completeness vs expected call count
  - per-mode shed coverage
  - band nesting (area_15 <= area_20)
  - mode ordering car >= bike >= walk (inversions are the headline cases, just counted)
  - free-flow car >= peak car
  - area plausibility
  - shapefile integrity (CRS, <=10-char DBF fields, feature counts)
"""

import os
import glob
import json
import pandas as pd
import geopandas as gpd

from config_loader import cfg

SHORT = cfg.CITY_SHORT.lower()
POINTS   = os.path.join(cfg.WARDS_DIR, f"{SHORT}_sample_points.geojson")
PT_CSV   = os.path.join(cfg.ANALYSIS_DIR, f"{SHORT}_area_by_point.csv")
WARD_CSV = os.path.join(cfg.ANALYSIS_DIR, f"{SHORT}_area_by_ward.csv")
SHP_DIR  = os.path.join(cfg.OUTPUT_DIR, f"{cfg.CITY_SHORT}_Sheds_Shapefiles")

ok, warn = [], []
def check(cond, msg):
    (ok if cond else warn).append(msg)

# ── cache completeness ───────────────────────────────────────────────────────
pts = gpd.read_file(POINTS)
expected = cfg.implied_calls(len(pts))
cached = len(glob.glob(os.path.join(cfg.SHEDS_DIR, "*__*__*__*s.json")))
check(cached == expected,
      f"cache files {cached} vs expected {expected} "
      f"({'complete' if cached == expected else 'INCOMPLETE — finish 02'})")

# ── per-point areas ──────────────────────────────────────────────────────────
if os.path.exists(PT_CSV):
    p = pd.read_csv(PT_CSV)
    for m in cfg.MODE_ORDER:
        n = p[p["mode"] == m]["pt_id"].nunique()
        check(n > 0, f"mode '{m}': {n} points with sheds")
    # car >= bike >= walk per (pt, band), using peak car (min of am/pm)
    prim = cfg.BANDS_MIN[0]
    pv = p[p["band_min"] == prim].copy()
    def _key(r):
        if r["mode"] == "car":
            return "carpeak" if r["scenario"] in cfg.PEAK_KEYS else "carother"
        return r["mode"]
    pv["key"] = pv.apply(_key, axis=1)
    car = pv[pv["key"] == "carpeak"].groupby("pt_id")["area_km2"].min()
    bike = pv[pv["mode"] == "bicycle"].groupby("pt_id")["area_km2"].max()
    walk = pv[pv["mode"] == "pedestrian"].groupby("pt_id")["area_km2"].max()
    common = car.index.intersection(bike.index).intersection(walk.index)
    inv_cb = int((bike[common] > car[common]).sum())
    inv_bw = int((walk[common] > bike[common]).sum())
    warn.append(f"[info] {prim}-min: bike>car in {inv_cb}/{len(common)} pts, "
                f"walk>bike in {inv_bw}/{len(common)} pts (expected: a few — the headline cases)")
    cr = car[common]
    check(cr.between(1, 80).mean() > 0.8,
          f"{prim}-min peak car area median {cr.median():.1f} km² "
          f"(plausible band 1–80: {cr.between(1,80).mean()*100:.0f}% in range)")

# ── band nesting ─────────────────────────────────────────────────────────────
if os.path.exists(WARD_CSV) and len(cfg.BANDS_MIN) > 1:
    w = pd.read_csv(WARD_CSV)
    lo, hi = min(cfg.BANDS_MIN), max(cfg.BANDS_MIN)
    piv = w.pivot_table(index=["ward_no", "mode", "scenario"], columns="band_min",
                        values="area_km2", aggfunc="first")
    if lo in piv.columns and hi in piv.columns:
        viol = int((piv[lo] > piv[hi] + 0.01).sum())
        check(viol == 0, f"band nesting {lo}<= {hi}: {viol} violations")
    # free-flow >= peak car
    cw = w[w["mode"] == "car"].pivot_table(index="ward_no", columns="scenario",
                                           values="area_km2", aggfunc="first")
    if "free_flow" in cw.columns:
        peakcols = [c for c in cw.columns if c != "free_flow"]
        if peakcols:
            viol = int((cw["free_flow"] + 0.01 < cw[peakcols].max(axis=1)).sum())
            check(viol == 0, f"free-flow car >= peak car: {viol} violations")

# ── shapefiles ───────────────────────────────────────────────────────────────
shps = sorted(glob.glob(os.path.join(SHP_DIR, "*.shp")))
check(len(shps) > 0, f"{len(shps)} shapefiles in {SHP_DIR}")
for s in shps:
    g = gpd.read_file(s)
    long_fields = [c for c in g.columns if c != "geometry" and len(c) > 10]
    crs_ok = g.crs is not None and g.crs.to_epsg() == cfg.WGS84
    check(not long_fields and crs_ok,
          f"{os.path.basename(s)}: {len(g)} feats, CRS={g.crs.to_epsg() if g.crs else None}"
          + (f", LONG FIELDS {long_fields}" if long_fields else ""))

# ── report ───────────────────────────────────────────────────────────────────
print("\n==== QA/QC report ====")
for m in ok:
    print(f"  OK   {m}")
for m in warn:
    print(f"  WARN {m}")
print(f"\n{len(ok)} ok, {len(warn)} warnings/info.")
