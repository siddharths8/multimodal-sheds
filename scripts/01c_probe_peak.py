"""
Stage 1c — identify the worst AM and PM peak hour empirically.

For a few central sample points, query TomTom car reachable-range (traffic on)
at each candidate AM and PM departure time and measure the shed AREA. The hour
with the SMALLEST reachable area is the most congested -> chosen as that peak.

Writes:
  data/analysis/departures_resolved.json   (am_peak / pm_peak ISO datetimes + raw rows)
  output/<short>_peak_probe.html           (area-by-hour chart)

Requires TOMTOM_API_KEY. Cost = n_probe_points x (n_am + n_pm) calls (small).
Run AFTER 01b review and BEFORE 02_fetch_reachable_range.py.
"""

import os
import json
import time
import requests
import numpy as np
import geopandas as gpd
from shapely.geometry import Point, Polygon
from shapely.validation import make_valid

from config_loader import cfg

KEY   = cfg.tomtom_key()
SHORT = cfg.CITY_SHORT.lower()
POINTS   = os.path.join(cfg.WARDS_DIR, f"{SHORT}_sample_points.geojson")
OUT_JSON = cfg.DEPARTURES_RESOLVED
OUT_HTML = os.path.join(cfg.OUTPUT_DIR, f"{SHORT}_peak_probe.html")

BAND = cfg.PROBE["probe_band_sec"]

# ── Pick the N sample points closest to the city centre (most congested) ──────
pts = gpd.read_file(POINTS).to_crs(cfg.WGS84)
center_m = gpd.GeoSeries([Point(cfg.CENTER_LON, cfg.CENTER_LAT)], crs=cfg.WGS84) \
            .to_crs(cfg.WORK_CRS).iloc[0]
pts_m = pts.to_crs(cfg.WORK_CRS)
pts_m["_d"] = pts_m.geometry.distance(center_m)
probe_pts = pts.loc[pts_m.sort_values("_d").index[:cfg.PROBE["n_probe_points"]]]
print(f"Probe points ({len(probe_pts)}): "
      f"{', '.join(probe_pts['ward_name'].tolist())}")


def fetch_area_km2(lat, lon, depart_iso):
    url = cfg.TOMTOM_BASE.format(lat=lat, lon=lon)
    params = {"key": KEY, "travelMode": "car", "timeBudgetInSec": BAND,
              "traffic": "true", "departAt": depart_iso, "routeType": "fastest"}
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    boundary = r.json()["reachableRange"]["boundary"]
    poly = Polygon([(p["longitude"], p["latitude"]) for p in boundary])
    if not poly.is_valid:
        poly = make_valid(poly)
    return gpd.GeoSeries([poly], crs=cfg.WGS84).to_crs(cfg.WORK_CRS).area.iloc[0] / 1e6


def probe(candidates, label):
    print(f"\n{label} candidates:")
    rows = []
    for hhmm in candidates:
        iso = cfg._iso(hhmm)
        areas = []
        for _, p in probe_pts.iterrows():
            try:
                areas.append(fetch_area_km2(p["lat"], p["lon"], iso))
            except Exception as e:
                print(f"  WARN {hhmm} @ {p['pt_id']}: {e}")
            time.sleep(cfg.SLEEP_SEC)
        avg = float(np.mean(areas)) if areas else float("nan")
        rows.append({"time": hhmm, "avg_area_km2": round(avg, 3), "n": len(areas)})
        print(f"  {hhmm}:  {avg:6.2f} km^2  (n={len(areas)})")
    return rows


def worst(rows):   # most congested = smallest reachable area
    valid = [r for r in rows if r["avg_area_km2"] == r["avg_area_km2"]]
    return min(valid, key=lambda r: r["avg_area_km2"])

def lightest(rows):  # least congested = largest reachable area
    valid = [r for r in rows if r["avg_area_km2"] == r["avg_area_km2"]]
    return max(valid, key=lambda r: r["avg_area_km2"])


if not cfg.PROBE.get("enabled", True):
    raise SystemExit("Probe disabled in config; remove or set departures.probe.enabled=true.")

am_rows = probe(cfg.PROBE["am_candidates"], "AM")
pm_rows = probe(cfg.PROBE["pm_candidates"], "PM")
md_rows = probe(cfg.PROBE["midday_candidates"], "Midday")
am_best, pm_best = worst(am_rows), worst(pm_rows)
md_best = worst(md_rows)           # midday = most-congested midday (free-flow is the light ref)

resolved = {
    "am_peak": cfg._iso(am_best["time"]),
    "pm_peak": cfg._iso(pm_best["time"]),
    "midday":  cfg._iso(md_best["time"]),
    "am_peak_hhmm": am_best["time"],
    "pm_peak_hhmm": pm_best["time"],
    "midday_hhmm": md_best["time"],
    "probe_band_sec": BAND,
    "n_probe_points": int(len(probe_pts)),
    "probe_date": cfg.PROBE["probe_date"],
    "am_rows": am_rows,
    "pm_rows": pm_rows,
    "midday_rows": md_rows,
}
with open(OUT_JSON, "w", encoding="utf-8") as f:
    json.dump(resolved, f, indent=2)

print(f"\nAM peak -> {am_best['time']}  (smallest shed: {am_best['avg_area_km2']} km^2)")
print(f"PM peak -> {pm_best['time']}  (smallest shed: {pm_best['avg_area_km2']} km^2)")
print(f"Midday  -> {md_best['time']}  (smallest shed: {md_best['avg_area_km2']} km^2)")
print(f"Wrote {OUT_JSON}")

# ── Area-by-hour chart (smaller bar = worse traffic) ─────────────────────────
def bars(rows, best_time):
    amax = max((r["avg_area_km2"] for r in rows if r["avg_area_km2"] == r["avg_area_km2"]), default=1)
    out = []
    for r in rows:
        a = r["avg_area_km2"]
        w = 0 if a != a else max(2, a / amax * 100)
        cls = "bar peak" if r["time"] == best_time else "bar"
        out.append(
            f'<div class="brow"><div class="bt">{r["time"]}</div>'
            f'<div class="btrack"><div class="{cls}" style="width:{w:.1f}%"></div></div>'
            f'<div class="bv">{a:.1f} km²</div></div>')
    return "\n".join(out)

HTML = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>{cfg.CITY_NAME} — Peak-hour probe</title>
<style>
body{{font-family:Arial,sans-serif;max-width:760px;margin:30px auto;color:#222;padding:0 16px}}
h1{{font-size:19px}} h2{{font-size:14px;margin:22px 0 8px;color:#15406b}}
p.sub{{color:#666;font-size:12.5px;line-height:1.5}}
.brow{{display:flex;align-items:center;gap:10px;margin:4px 0;font-size:12px}}
.bt{{width:48px;text-align:right;color:#444}}
.btrack{{flex:1;background:#f0f2f5;border-radius:4px;overflow:hidden;height:18px}}
.bar{{height:18px;background:#9db8d2;border-radius:4px}}
.bar.peak{{background:#D7191C}}
.bv{{width:70px;font-size:11px;color:#555}}
.key{{background:#eef4fc;border-left:3px solid #0078C1;padding:10px 13px;border-radius:6px;
  font-size:12.5px;line-height:1.55;margin-top:14px}}
.pk{{color:#D7191C;font-weight:700}}
</style></head><body>
<h1>{cfg.CITY_NAME} &mdash; Identifying the AM &amp; PM peak</h1>
<p class="sub">Car reachable area in {BAND // 60} min from {len(probe_pts)} central point(s),
by departure time ({cfg.PROBE['probe_date']}, traffic on). <strong>Shorter bar = smaller
reachable area = worse congestion.</strong> The most congested hour in each window
(highlighted) is used as that peak for the full run.</p>
<div class="key">Chosen times &nbsp;&middot;&nbsp;
  AM peak: <span class="pk">{am_best['time']}</span> ({am_best['avg_area_km2']} km²) &nbsp;&middot;&nbsp;
  PM peak: <span class="pk">{pm_best['time']}</span> ({pm_best['avg_area_km2']} km²) &nbsp;&middot;&nbsp;
  Midday: <span class="pk">{md_best['time']}</span> ({md_best['avg_area_km2']} km²)</div>
<h2>Morning</h2>{bars(am_rows, am_best['time'])}
<h2>Midday</h2>{bars(md_rows, md_best['time'])}
<h2>Evening</h2>{bars(pm_rows, pm_best['time'])}
</body></html>"""

with open(OUT_HTML, "w", encoding="utf-8") as f:
    f.write(HTML)
print(f"Wrote chart: {OUT_HTML}")
