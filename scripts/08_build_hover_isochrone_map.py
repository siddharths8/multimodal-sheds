"""
Stage 8 (extra) — hover-to-explore isochrones.

Instead of clicking a ward or dropdown, you move the mouse across the map: the
nearest of the ~515 origin points snaps in, and its car/bike/walk sheds (for a
fixed 15-min budget + the current car time-of-day) redraw live under the cursor.
Zoom in past POINTS_MIN_ZOOM and every origin point appears as a small dot, so
it's clear where hovering does something.

Important honesty check: this is per-POINT data, not per-pixel. The sheds only
exist at the ~515 precomputed origins (TomTom/OSM calls aren't free or
instant, so nothing can be computed live for an arbitrary mouse position).
Hovering snaps to the nearest real origin point, which is close enough at
city zoom levels to feel continuous, but a small dot marks the true origin
being shown so it's never ambiguous what you're looking at.

Rendering: isochrones are NOT Mapbox GL layers. Mapbox GL renders everything
(basemap + data layers) into one WebGL canvas, so a layer can't get its own
CSS mix-blend-mode against the basemap beneath it. To get real color/basemap
interaction (and a desaturated "site diagram" backdrop), the isochrones, the
origin dot, and the all-points layer are drawn as an SVG overlay positioned
with map.project() on every 'render' event, sitting on top of a grayscale-
filtered map canvas with mix-blend-mode:multiply.

Inputs:
  data/sheds/<short>_sheds_combined.json   (per-point sheds, all modes/scenarios/bands)
Output:
  output/<SHORT>_Hover_Isochrones.html
"""

import os
import json
import math
import shutil
import pandas as pd
import geopandas as gpd
from shapely.geometry import shape, Point
from shapely.validation import make_valid

from config_loader import cfg
import geo_utils as gutil

SHORT = cfg.CITY_SHORT.lower()
COMBINED = os.path.join(cfg.SHEDS_DIR, f"{SHORT}_sheds_combined.json")
OUT_HTML = os.path.join(cfg.OUTPUT_DIR, f"{cfg.CITY_SHORT}_Hover_Isochrones.html")

with open(COMBINED, encoding="utf-8") as f:
    combined = json.load(f)

points = []
rows = []
for node in combined:
    pt_id = node["pt_id"]
    points.append({"id": pt_id, "lon": node["lon"], "lat": node["lat"],
                    "ward_no": node["ward_no"], "ward_name": node["ward_name"], "zone": node["zone"]})
    for mode, by_scen in node["sheds"].items():
        for scen, by_band in by_scen.items():
            for band_sec, geom in by_band.items():
                if int(band_sec) != min(cfg.BANDS_SEC):
                    continue   # 15-min only — the 20-min band is dropped from this tool
                g = shape(geom)
                if not g.is_valid:
                    g = make_valid(g)
                rows.append({"pt_id": pt_id, "mode": mode, "scenario": scen,
                             "band_sec": int(band_sec), "geometry": g})

print(f"{len(points)} origin points, {len(rows)} raw shed polygons")

# ---- bulk smoothing (car only) + area + web-simplify, all vectorized -------
gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=cfg.WGS84)
gdf_m = gdf.to_crs(cfg.WORK_CRS)

car_mask = gdf["mode"] == "car"
gdf_m.loc[car_mask, "geometry"] = gdf_m.loc[car_mask, "geometry"].apply(gutil.smooth_metric)

area_km2 = (gdf_m.geometry.area / 1e6).round(2)
gdf_web = gdf_m.to_crs(cfg.WGS84)
gdf_web["geometry"] = gdf_web.geometry.simplify(0.0003)

features = []
for i in range(len(gdf)):
    features.append({
        "type": "Feature",
        "properties": {
            "pt_id": gdf["pt_id"].iloc[i], "mode": gdf["mode"].iloc[i],
            "scenario": gdf["scenario"].iloc[i], "band_min": int(gdf["band_sec"].iloc[i]) // 60,
            "area_km2": float(area_km2.iloc[i]),
        },
        "geometry": gdf_web.geometry.iloc[i].__geo_interface__,
    })

print(f"Built {len(features)} web-simplified shed features")

# ---- per-point AM/PM bike-vs-car summary line ------------------------------
area_df = pd.DataFrame({"pt_id": gdf["pt_id"].values, "mode": gdf["mode"].values,
                        "scenario": gdf["scenario"].values, "area_km2": area_km2.values})
piv = area_df.pivot_table(index="pt_id", columns=["mode", "scenario"], values="area_km2")
piv.columns = ["_".join(c) for c in piv.columns]


BIKE_BETTER_THRESH = 0.75   # bike counts as "the better choice" once it covers >=75% of car's
                            # area in EITHER peak — matching the citywide stat below, so the
                            # hover card and the citywide chart never tell two different stories.


def summarize(row):
    bike, am, pm = row.get("bicycle_na"), row.get("car_am_peak"), row.get("car_pm_peak")
    if pd.isna(bike) or pd.isna(am) or pd.isna(pm) or am == 0 or pm == 0:
        return ""
    bike_better = (bike / am >= BIKE_BETTER_THRESH) or (bike / pm >= BIKE_BETTER_THRESH)
    return "Bicycle is the better choice here" if bike_better else "Car is the better choice here"


summary_by_pt = {pt_id: summarize(row) for pt_id, row in piv.iterrows()}
for p in points:
    p["summary"] = summary_by_pt.get(p["id"], "")

CAR_SCEN = cfg.car_scenarios()
MODE_ORDER = cfg.MODE_ORDER
MODES = cfg.MODES

BASEMAP_URL = "mapbox://styles/mapbox/outdoors-v12"


def _clock_label(iso):
    """'2026-07-01T17:30:00+05:30' -> '5:30 PM' (plain-language button labels)."""
    hh, mm = int(iso[11:13]), iso[14:16]
    ampm = "AM" if hh < 12 else "PM"
    h12 = hh % 12 or 12
    return f"{h12} {ampm}" if mm == "00" else f"{h12}:{mm} {ampm}"


# Buttons show real clock times (from the empirically probed peaks), not
# planner jargon — "AM peak" means nothing to the average map visitor.
for s in CAR_SCEN:
    s["label"] = "No traffic" if s["key"] == "free_flow" else _clock_label(s["departAt"])

# ---- arterial road network (cache built by stage 07, reused here) ---------
ROADS_CACHE = os.path.join(cfg.WARDS_DIR, f"{SHORT}_major_roads.geojson")
if not os.path.exists(ROADS_CACHE):
    raise SystemExit(f"Missing {ROADS_CACHE} — run 07_build_bike_car_category_map.py once first "
                      "(it builds and caches the road network from the stage-01 Overpass fetch).")
with open(ROADS_CACHE, encoding="utf-8") as f:
    roads_geojson = json.load(f)
print(f"Arterial road network: {len(roads_geojson['features']):,} segments (reused cache)")

# ---- inner/outer split: wards inside vs. outside the Outer Ring Road ------
# ORR isn't a single continuous OSM way, so we take every road segment whose
# name mentions "ring road", union them, and use the convex hull of that union
# as a stand-in for "the area the ring encloses" — good enough for a ward-level
# in/out split even where OSM's ORR tagging has small gaps.
from shapely.ops import unary_union

WARDS_CLEAN = os.path.join(cfg.WARDS_DIR, f"{SHORT}_wards_clean.geojson")
ward_gdf = gpd.read_file(WARDS_CLEAN)

orr_lines = [shape(f["geometry"]) for f in roads_geojson["features"]
             if "ring road" in (f["properties"].get("name") or "").lower()]
orr_boundary = unary_union(orr_lines).convex_hull

ward_group = {}
for _, w in ward_gdf.iterrows():
    rp = w.geometry.representative_point()
    ward_group[int(w["ward_no"])] = "inner" if orr_boundary.contains(rp) else "outer"

n_inner = sum(1 for v in ward_group.values() if v == "inner")
n_outer = sum(1 for v in ward_group.values() if v == "outer")
print(f"ORR split (from {len(orr_lines)} named 'ring road' segments): "
      f"{n_inner} inner wards, {n_outer} outer wards")

# ---- citywide bike-vs-car stat: bike counts as better once it covers >=75%
# of car's area in EITHER peak (same BIKE_BETTER_THRESH rule as the hover
# card), tallied separately for inner-ORR vs outer-ORR wards. -----------------
WARD_CSV = os.path.join(cfg.ANALYSIS_DIR, f"{SHORT}_area_by_ward.csv")
ward_df = pd.read_csv(WARD_CSV)
ward_df = ward_df[ward_df.band_min == min(cfg.BANDS_MIN)]
ward_piv = ward_df.pivot_table(index="ward_no", columns=["mode", "scenario"], values="area_km2")
ward_piv.columns = ["_".join(c) for c in ward_piv.columns]

# Same inks as the modes themselves: bicycle gold vs car red.
BIKE_COLOR, CAR_COLOR = "#D9A404", "#B21F24"


def bike_better_flag(row):
    bike, am, pm = row.get("bicycle_na"), row.get("car_am_peak"), row.get("car_pm_peak")
    if pd.isna(bike) or pd.isna(am) or pd.isna(pm) or am == 0 or pm == 0:
        return None
    return (bike / am >= BIKE_BETTER_THRESH) or (bike / pm >= BIKE_BETTER_THRESH)


zone_counts = {"inner": {"bike": 0, "car": 0}, "outer": {"bike": 0, "car": 0}}
for ward_no, row in ward_piv.iterrows():
    flag = bike_better_flag(row)
    grp = ward_group.get(int(ward_no))
    if flag is None or grp is None:
        continue
    zone_counts[grp]["bike" if flag else "car"] += 1

print(f"Bike-vs-car ({int(BIKE_BETTER_THRESH * 100)}% either-peak rule) by zone: {zone_counts}")


def donut_svg(bike_n, car_n, size=64, r=22, sw=13):
    total = bike_n + car_n
    circ = 2 * math.pi * r
    bike_dash = (bike_n / total) * circ if total else 0
    c = size / 2
    return (f'<circle cx="{c}" cy="{c}" r="{r}" fill="none" stroke="{CAR_COLOR}" stroke-width="{sw}"/>'
            f'<circle cx="{c}" cy="{c}" r="{r}" fill="none" stroke="{BIKE_COLOR}" stroke-width="{sw}" '
            f'stroke-dasharray="{bike_dash:.2f} {circ - bike_dash:.2f}" transform="rotate(-90 {c} {c})"/>')


def zone_block(label, counts):
    bike_n, car_n = counts["bike"], counts["car"]
    total = bike_n + car_n
    bike_pct = round(100 * bike_n / total) if total else 0
    return f'''<div class="zone-block">
      <div class="zone-lbl">{label} <span class="zone-n">({total} wards)</span></div>
      <div class="pie-wrap">
        <svg width="64" height="64" viewBox="0 0 64 64">{donut_svg(bike_n, car_n)}</svg>
        <div class="pie-legend">
          <div class="pie-legend-row"><span class="sw" style="background:{BIKE_COLOR}"></span>
            <span class="pie-lbl">Bicycle is better</span><span class="pie-n">{bike_pct}%</span></div>
          <div class="pie-legend-row"><span class="sw" style="background:{CAR_COLOR}"></span>
            <span class="pie-lbl">Car is better</span><span class="pie-n">{100 - bike_pct}%</span></div>
        </div>
      </div>
    </div>'''


ZONE_HTML = (zone_block("Inner city — within ORR", zone_counts["inner"])
             + zone_block("Outer city — beyond ORR", zone_counts["outer"]))

TEMPLATE = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>__TITLE__ — hover to compare drive/bicycle/walk</title>
<meta name="viewport" content="initial-scale=1,maximum-scale=1,user-scalable=no">
<script src="https://api.mapbox.com/mapbox-gl-js/v3.3.0/mapbox-gl.js"></script>
<link href="https://api.mapbox.com/mapbox-gl-js/v3.3.0/mapbox-gl.css" rel="stylesheet">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --surface:#fefefe; --text-primary:#0d0d0d; --text-secondary:#4a4a4a;
  --text-muted:#8a8a8a; --border:#0d0d0d; --accent:#0d0d0d;
}
html,body{height:100%}
body{font-family:"Space Grotesk",system-ui,-apple-system,"Segoe UI",sans-serif;
  color:var(--text-primary);overflow:hidden}
#map{position:absolute;inset:0;cursor:crosshair;transition:filter .25s ease}
#map.mono{filter:grayscale(1) brightness(1.16) contrast(0.84)}
#overlaySvg{position:absolute;inset:0;pointer-events:none;mix-blend-mode:multiply}

.panel{position:absolute;top:16px;left:16px;width:272px;max-width:calc(100vw - 32px);
  background:var(--surface);border:1.5px solid var(--border);z-index:2;
  max-height:calc(100vh - 32px);display:flex;flex-direction:column;overflow:hidden}
.panel-hd{display:flex;align-items:center;justify-content:space-between;gap:8px;
  padding:14px 16px;cursor:pointer;flex-shrink:0;user-select:none;
  border-bottom:1.5px solid var(--border)}
.panel-hd .ttl{font-size:14px;font-weight:700;color:var(--text-primary);line-height:1.25;
  text-transform:uppercase;letter-spacing:.4px}
.panel-hd .sub{font-size:10.5px;color:var(--text-secondary);font-weight:400;margin-top:2px;
  font-family:"Space Mono",monospace;letter-spacing:.2px}
.panel-hd .tgl{width:20px;height:20px;border:1px solid var(--border);
  background:#fff;color:var(--text-primary);font-size:13px;line-height:1;display:flex;
  align-items:center;justify-content:center;flex-shrink:0}
.panel-bd{padding:14px 16px 16px;overflow-y:auto}
.panel.collapsed .panel-bd{display:none}
.panel.collapsed .panel-hd{border-bottom:none}

h3{font-size:9.5px;color:var(--text-muted);font-weight:700;text-transform:uppercase;
  letter-spacing:1px;margin:14px 0 7px;font-family:"Space Mono",monospace}
.seg{display:flex;gap:0;flex-wrap:wrap;border:1px solid var(--border)}
.seg button{flex:1;min-width:56px;font-size:10.5px;padding:7px 4px;border:none;
  border-right:1px solid var(--border);background:#fff;color:var(--text-secondary);
  cursor:pointer;font-weight:500;text-transform:uppercase;letter-spacing:.3px;
  font-family:"Space Grotesk",sans-serif}
.seg button:last-child{border-right:none}
.seg button.active{background:var(--accent);color:#fff}

.chk{display:flex;align-items:center;gap:8px;font-size:11px;color:var(--text-secondary);
  margin-top:8px;cursor:pointer;text-transform:uppercase;letter-spacing:.3px}
.chk input{width:14px;height:14px;accent-color:var(--accent)}

.mode-row{display:flex;align-items:center;gap:9px;background:#fff;border:1px solid var(--border);
  border-top:none;padding:8px 10px}
.mode-row:first-child{border-top:1px solid var(--border)}
.mode-row .sw{width:10px;height:10px;flex-shrink:0}
.mode-row .nm{flex:1;font-size:11.5px;font-weight:500;color:var(--text-primary);
  text-transform:uppercase;letter-spacing:.4px}
.mode-row .ar{font-size:11px;color:var(--text-secondary);font-family:"Space Mono",monospace}
.mode-row input{width:14px;height:14px;accent-color:var(--accent)}
#op{width:100%;accent-color:var(--accent);margin-top:10px}

.zone-block{margin-bottom:12px}
.zone-lbl{font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.3px;
  margin-bottom:6px}
.zone-lbl .zone-n{font-weight:400;color:var(--text-muted);text-transform:none;letter-spacing:0}
.pie-wrap{display:flex;gap:12px;align-items:center;margin-top:2px}
.pie-legend{flex:1;min-width:0}
.pie-legend-row{display:flex;align-items:center;gap:6px;margin-bottom:5px;font-size:10px;
  color:var(--text-secondary)}
.pie-legend-row .sw{width:9px;height:9px;flex-shrink:0}
.pie-legend-row .pie-lbl{flex:1;line-height:1.25}
.pie-legend-row .pie-n{font-family:"Space Mono",monospace;color:var(--text-primary);font-weight:700}

.stat{border:1px solid var(--border);padding:10px 11px;margin-top:10px}
.stat .nm{font-size:13px;font-weight:700;color:var(--text-primary)}
.stat .zn{font-size:10.5px;color:var(--text-muted);font-family:"Space Mono",monospace}
.stat .cmp{font-size:11px;color:var(--text-primary);margin-top:6px;padding-top:6px;
  border-top:1px dashed var(--border);font-weight:500}
.stat .hint{font-size:11px;color:var(--text-muted);line-height:1.5}

.rule-note{font-size:10px;color:var(--text-secondary);line-height:1.55;margin:-2px 0 8px;
  font-family:"Space Mono",monospace}

.hover-tip{position:absolute;right:16px;bottom:34px;max-width:300px;z-index:2;
  background:var(--surface);border:1.5px solid var(--border);padding:10px 12px;
  font-size:9.5px;color:var(--text-secondary);line-height:1.6;font-family:"Space Mono",monospace}
</style></head>
<body>
<div id="map"></div>
<svg id="overlaySvg"></svg>

<div class="panel" id="panel">
  <div class="panel-hd" id="panelHd">
    <div><div class="ttl">__TITLE__</div><div class="sub">Hover to compare drive / bicycle / walk</div></div>
    <div class="tgl" id="panelTgl">&#8722;</div>
  </div>
  <div class="panel-bd" id="panelBd">
    <label class="chk"><input type="checkbox" id="monoToggle"> Monochrome basemap</label>
    <h3>What time of the day are you driving?</h3>
    <div class="seg" id="scen"></div>
    <h3>How much area can you cover?</h3>
    <div id="modes"></div>
    <input type="range" id="op" min="0.1" max="0.85" step="0.05" value="0.85">
    <h3>Bicycle vs. car</h3>
    <div class="rule-note">The bicycle shed is better if it reaches at least 75% of the car's
      travel shed, during the rush hours.</div>
    __ZONE_HTML__
    <div class="stat" id="stat"><div class="hint">Move the mouse over the map — the nearest of
      __NPTS__ origin points snaps in and its sheds appear.</div></div>
  </div>
</div>

<div class="hover-tip"><b>As you zoom in, hover over the dots that appear and see how far you can
  travel by each mode as you explore the city!</b> <i>Sheds only exist at __NPTS__ pre-computed
  locations and this does not connect to an API. For bicycling &amp; walking conservative speeds
  have been used.</i></div>

<script>
mapboxgl.accessToken = '__TOKEN__';
const POINTS = __POINTS_JSON__;
const SHEDS = __SHEDS_JSON__;
const MODES = __MODES_JSON__;
const MODE_ORDER = __MODEORDER_JSON__;
const CARSCEN = __CARSCEN_JSON__;
const BASEMAP_URL = '__BASEMAP_URL__';
const ROADS = __ROADS_JSON__;
const POINTS_MIN_ZOOM = 12.5;

// ---- arterial road network: a native Mapbox GL layer (large static dataset,
// so let WebGL handle it rather than SVG). style.load also covers the one
// initial style load, so this is the only place layers get added. ----------
// Single-ink cartography: hierarchy is carried by WIDTH and OPACITY, not by
// darkness, so the road mesh recedes behind the data instead of competing
// with the origin dots for "blackest thing on screen". Tertiary streets only
// fade in past the same zoom where the origin dots appear.
const ROAD_MAIN = ['motorway','motorway_link','trunk','trunk_link','primary','primary_link','secondary','secondary_link'];
const ROAD_MINOR = ['tertiary','tertiary_link'];
const ROAD_INK = '#77776f';   // faint gray ink — orientation only, never competes with sheds
const ROAD_WIDTH = ['interpolate', ['linear'], ['zoom'],
  10, ['match', ['get','highway'], ['motorway','motorway_link'],1.6, ['trunk','trunk_link'],1.4,
        ['primary','primary_link'],1.0, 0.5],
  15, ['match', ['get','highway'], ['motorway','motorway_link'],4.2, ['trunk','trunk_link'],3.6,
        ['primary','primary_link'],2.6, 1.6]];
const ROAD_OPACITY = ['match', ['get','highway'],
  ['motorway','motorway_link'], 0.35, ['trunk','trunk_link'], 0.28,
  ['primary','primary_link'], 0.2, 0.14];

function addRoadLayer(){
  if (map.getSource('roads')) return;
  map.addSource('roads', {type:'geojson', data: ROADS});
  map.addLayer({id:'roads-minor', type:'line', source:'roads', minzoom: 12.5,
    filter: ['in', ['get','highway'], ['literal', ROAD_MINOR]],
    paint:{'line-color': ROAD_INK, 'line-width': 0.6, 'line-opacity': 0.1}});
  map.addLayer({id:'roads-line', type:'line', source:'roads',
    filter: ['in', ['get','highway'], ['literal', ROAD_MAIN]],
    layout:{'line-cap':'round', 'line-join':'round'},
    paint:{'line-color': ROAD_INK, 'line-width': ROAD_WIDTH, 'line-opacity': ROAD_OPACITY}});
}

const IDX = {};
SHEDS.features.forEach(f => { const p = f.properties;
  IDX[`${p.pt_id}|${p.mode}|${p.scenario}`] = f; });

let state = { scen: CARSCEN[0].key, vis:{car:true,bicycle:true,pedestrian:true}, op:0.85, pt:null };
const fmt = v => (v==null? '—' : v.toFixed(1));

function scenKeyForMode(m){ return m==='car' ? state.scen : 'na'; }
function featFor(ptId, m){ return IDX[`${ptId}|${m}|${scenKeyForMode(m)}`]; }

function nearestPoint(lng, lat){
  let best = null, bestD = Infinity;
  for (const p of POINTS){
    const dx = p.lon - lng, dy = p.lat - lat;
    const d = dx*dx + dy*dy;
    if (d < bestD){ bestD = d; best = p; }
  }
  return best;
}

const center = [__CENTERLON__, __CENTERLAT__];
const map = new mapboxgl.Map({container:'map', style: BASEMAP_URL, center, zoom:__ZOOM__});
map.addControl(new mapboxgl.NavigationControl(), 'top-right');

// ---- SVG overlay: isochrones + origin dot + all-points, independent of the
// Mapbox style/canvas so mix-blend-mode:multiply can react to the basemap
// underneath. ----------------------------------------------------------------
const svgNS = 'http://www.w3.org/2000/svg';
const svg = document.getElementById('overlaySvg');
function svgEl(tag, attrs){ const e = document.createElementNS(svgNS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]); return e; }

// Cut-paper cartography (after regio.toekom.st): light wash fill + crisp,
// fully opaque border in a darker step of the same hue.
const STROKE = {car:'#801619', bicycle:'#8F6C02', pedestrian:'#153E8F'};
const pathByMode = {};
MODE_ORDER.forEach(m => {
  pathByMode[m] = svgEl('path', {fill: MODES[m].color, stroke: STROKE[m],
    'stroke-width': 2, 'fill-opacity': state.op, 'stroke-opacity': 1,
    'stroke-linejoin': 'round'});
  svg.appendChild(pathByMode[m]);
});
const gAllPoints = svgEl('g', {});
svg.appendChild(gAllPoints);
const pointEls = POINTS.map(() => {
  const c = svgEl('circle', {r:2.6, fill:'#0d0d0d', 'fill-opacity':0.55, stroke:'#fff', 'stroke-width':0.8});
  gAllPoints.appendChild(c);
  return c;
});
const originDot = svgEl('circle', {r:5, fill:'#0d0d0d', stroke:'#fff', 'stroke-width':2});
svg.appendChild(originDot);
originDot.style.display = 'none';

function ring2d(ring){ return ring.map(c => { const pt = map.project(c); return pt.x+','+pt.y; }).join('L'); }
function geom2d(geom){
  if (!geom) return '';
  const polys = geom.type === 'MultiPolygon' ? geom.coordinates : [geom.coordinates];
  return polys.map(rings => rings.map(r => 'M'+ring2d(r)+'Z').join('')).join('');
}

function resizeSvg(){
  const c = map.getCanvas();
  svg.setAttribute('width', c.clientWidth);
  svg.setAttribute('height', c.clientHeight);
  svg.setAttribute('viewBox', `0 0 ${c.clientWidth} ${c.clientHeight}`);
}

function updateOverlay(){
  const p = POINTS.find(pp => pp.id === state.pt);
  MODE_ORDER.forEach(m => {
    const f = (p && state.vis[m]) ? featFor(p.id, m) : null;
    pathByMode[m].setAttribute('d', f ? geom2d(f.geometry) : '');
  });
  if (p){
    const pt = map.project([p.lon, p.lat]);
    originDot.setAttribute('cx', pt.x); originDot.setAttribute('cy', pt.y);
    originDot.style.display = '';
  } else originDot.style.display = 'none';

  if (map.getZoom() >= POINTS_MIN_ZOOM){
    gAllPoints.style.display = '';
    POINTS.forEach((pp,i) => { const pt = map.project([pp.lon, pp.lat]);
      pointEls[i].setAttribute('cx', pt.x); pointEls[i].setAttribute('cy', pt.y); });
  } else gAllPoints.style.display = 'none';
}

map.on('render', updateOverlay);
map.on('resize', () => { resizeSvg(); updateOverlay(); });
map.on('load', () => { resizeSvg(); updateOverlay(); });
map.on('style.load', addRoadLayer);

let raf = null;
map.on('mousemove', e => {
  if (raf) return;
  raf = requestAnimationFrame(() => { raf = null; updateHover(e.lngLat.lng, e.lngLat.lat); });
});

function updateHover(lng, lat){
  const p = nearestPoint(lng, lat);
  if (!p || p.id === state.pt) return;
  state.pt = p.id;
  render();
}

function render(){
  const p = POINTS.find(pp => pp.id === state.pt);
  if (p){
    MODE_ORDER.forEach(m => {
      const f = state.vis[m] ? featFor(p.id, m) : null;
      const el = document.getElementById('ar-'+m); if (el) el.textContent = f ? fmt(f.properties.area_km2)+' km²' : '—';
    });
    document.getElementById('stat').innerHTML =
      `<div class="nm">${p.ward_name}</div><div class="zn">${p.zone}</div>`
      + (p.summary ? `<div class="cmp">${p.summary}</div>` : '');
  }
  updateOverlay();
}

document.getElementById('monoToggle').onchange = e => {
  document.getElementById('map').classList.toggle('mono', e.target.checked);
};

const scenEl = document.getElementById('scen');
CARSCEN.forEach(s => {
  const btn=document.createElement('button'); btn.textContent=s.label; btn.dataset.k=s.key;
  if (s.key===state.scen) btn.classList.add('active');
  btn.onclick=()=>{ state.scen=s.key; [...scenEl.children].forEach(c=>c.classList.toggle('active', c.dataset.k===s.key));
    render(); };
  scenEl.appendChild(btn);
});

const modesEl = document.getElementById('modes');
MODE_ORDER.forEach(m => {
  const row=document.createElement('div'); row.className='mode-row';
  row.innerHTML = `<span class="sw" style="background:${MODES[m].color}"></span>`
    + `<span class="nm">${MODES[m].label}</span><span class="ar" id="ar-${m}">—</span>`
    + `<input type="checkbox" ${state.vis[m]?'checked':''} data-m="${m}">`;
  row.querySelector('input').onchange = e => { state.vis[m] = e.target.checked; render(); };
  modesEl.appendChild(row);
});

document.getElementById('op').oninput = e => {
  state.op = +e.target.value;
  MODE_ORDER.forEach(m => pathByMode[m].setAttribute('fill-opacity', state.op));
};

const panel = document.getElementById('panel');
document.getElementById('panelHd').onclick = () => {
  panel.classList.toggle('collapsed');
  document.getElementById('panelTgl').innerHTML = panel.classList.contains('collapsed') ? '&#43;' : '&#8722;';
};
</script>
</body></html>"""

html = (TEMPLATE
        .replace("__TITLE__", cfg.CITY_NAME)
        .replace("__TOKEN__", cfg.MAPBOX_TOKEN)
        .replace("__CENTERLON__", str(cfg.CENTER_LON))
        .replace("__CENTERLAT__", str(cfg.CENTER_LAT))
        .replace("__ZOOM__", str(cfg.DEFAULT_ZOOM))
        .replace("__NPTS__", str(len(points)))
        .replace("__POINTS_JSON__", json.dumps(points))
        .replace("__SHEDS_JSON__", json.dumps({"type": "FeatureCollection", "features": features}))
        .replace("__MODES_JSON__", json.dumps(MODES))
        .replace("__MODEORDER_JSON__", json.dumps(MODE_ORDER))
        .replace("__CARSCEN_JSON__", json.dumps(CAR_SCEN))
        .replace("__BASEMAP_URL__", BASEMAP_URL)
        .replace("__ROADS_JSON__", json.dumps(roads_geojson))
        .replace("__ZONE_HTML__", ZONE_HTML))

with open(OUT_HTML, "w", encoding="utf-8") as f:
    f.write(html)
print(f"\nWrote {OUT_HTML}  ({os.path.getsize(OUT_HTML):,} bytes)")

DOCS_HTML = os.path.join(cfg.DOCS_DIR, "index.html")   # the landing page
shutil.copyfile(OUT_HTML, DOCS_HTML)
print(f"Copied to GitHub Pages dir: {DOCS_HTML}")
