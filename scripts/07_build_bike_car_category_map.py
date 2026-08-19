"""
Stage 7 (extra) — choropleth of wards into three bike-vs-car categories.

For each ward we convert the reachable-range AREAS already computed in stage 03
into an *effective speed* (area ~ speed^2 for a fixed time budget), then compare
that effective driving speed to the fixed bike speed (12 km/h):

  v_car_peak = BIKE_SPEED / sqrt(bike_area / min(car_am_peak, car_pm_peak))
  v_car_ff   = BIKE_SPEED / sqrt(bike_area / car_free_flow)

Category (comparable = within COMPARABLE_MULT of bike speed):
  C — Purple: v_car_ff  <= BIKE_SPEED * COMPARABLE_MULT
      Even with NO traffic, driving isn't much faster than biking -> the road
      network itself (narrow/indirect streets) is the constraint, not congestion.
  A — Green:  v_car_peak <= BIKE_SPEED * COMPARABLE_MULT (and not already C)
      Free-flow driving is fine, but AM/PM congestion drags it down to
      bike-comparable speed -> biking makes sense at peak.
  B — Red:    everything else
      Driving keeps a real speed advantage even at peak -> driving still helps.

Inputs:
  data/analysis/<short>_area_by_ward.csv
  data/wards/<short>_wards_clean.geojson
Output:
  output/<SHORT>_Bicycle_vs_Car_Categories.html   (self-contained Mapbox GL map)
"""

import os
import json
import shutil
import numpy as np
import pandas as pd
import geopandas as gpd

from config_loader import cfg

SHORT = cfg.CITY_SHORT.lower()
AREA_CSV = os.path.join(cfg.ANALYSIS_DIR, f"{SHORT}_area_by_ward.csv")
WARDS_GJ = os.path.join(cfg.WARDS_DIR, f"{SHORT}_wards_clean.geojson")
OUT_HTML = os.path.join(cfg.OUTPUT_DIR, f"{cfg.CITY_SHORT}_Bicycle_vs_Car_Categories.html")

BIKE_SPEED = cfg.SPEEDS_KMPH["bicycle"]
COMPARABLE_MULT = 1.25   # "comparable" = car effective speed within 25% of bicycle speed

CAT_INFO = {
    "A": {"color": "#1A9641", "label": "Bicycle makes sense (peak congestion erases car's edge)"},
    "B": {"color": "#D7191C", "label": "Driving still helps (real speed edge even at peak)"},
    "C": {"color": "#762A83", "label": "Network-limited (car isn't much faster even free-flow)"},
}

# ---- pivot per-ward areas into one row per ward, per band ------------------
df = pd.read_csv(AREA_CSV)
bands = sorted(df.band_min.unique().tolist())

per_band = {}
for band in bands:
    b = df[df.band_min == band]
    piv = b.pivot_table(index=["ward_no", "ward_name", "zone"],
                         columns=["mode", "scenario"], values="area_km2").reset_index()
    piv.columns = ["_".join([c for c in col if c]).strip("_") for col in piv.columns]
    piv["peak_area"] = piv[["car_am_peak", "car_pm_peak"]].min(axis=1)

    piv["v_car_peak"] = BIKE_SPEED / np.sqrt(piv["bicycle_na"] / piv["peak_area"])
    piv["v_car_ff"] = BIKE_SPEED / np.sqrt(piv["bicycle_na"] / piv["car_free_flow"])

    thr = BIKE_SPEED * COMPARABLE_MULT
    piv["category"] = np.where(piv.v_car_ff <= thr, "C",
                        np.where(piv.v_car_peak <= thr, "A", "B"))
    per_band[str(band)] = piv

print("Category counts by band:")
for band, piv in per_band.items():
    print(f"  {band} min:", piv.category.value_counts().to_dict())

# ---- attach to ward geometry -----------------------------------------------
wards = gpd.read_file(WARDS_GJ)
wards["geometry"] = wards.geometry.simplify(0.0004)

ward_lookup = {}   # ward_no -> {band: {category, v_car_peak, v_car_ff, bike_area, peak_area, ff_area}}
for band, piv in per_band.items():
    for _, r in piv.iterrows():
        ward_lookup.setdefault(int(r.ward_no), {})[band] = {
            "category": r.category,
            "v_car_peak": round(r.v_car_peak, 1),
            "v_car_ff": round(r.v_car_ff, 1),
            "bike_km2": round(r.bicycle_na, 2),
            "peak_km2": round(r.peak_area, 2),
            "ff_km2": round(r.car_free_flow, 2),
        }

wards_geojson = json.loads(wards.to_json())

# ---- arterial road network (from the Overpass fetch cached in stage 01) ---
ROADS_RAW = os.path.join(cfg.RAW_DIR, "osm_major_roads.json")
ROADS_CACHE = os.path.join(cfg.WARDS_DIR, f"{SHORT}_major_roads.geojson")


def build_roads_geojson():
    with open(ROADS_RAW, encoding="utf-8") as f:
        data = json.load(f)
    node_ll = {}
    ways = []
    for el in data["elements"]:
        if el["type"] == "node":
            node_ll[el["id"]] = (round(el["lon"], 5), round(el["lat"], 5))
        elif el["type"] == "way":
            ways.append(el)
    features = []
    for w in ways:
        tags = w.get("tags", {})
        coords = [node_ll[n] for n in w["nodes"] if n in node_ll]
        if len(coords) < 2:
            continue
        features.append({
            "type": "Feature",
            "properties": {"highway": tags.get("highway", ""), "name": tags.get("name", "")},
            "geometry": {"type": "LineString", "coordinates": coords},
        })
    gj = {"type": "FeatureCollection", "features": features}
    with open(ROADS_CACHE, "w", encoding="utf-8") as f:
        json.dump(gj, f)
    return gj


if os.path.exists(ROADS_CACHE):
    with open(ROADS_CACHE, encoding="utf-8") as f:
        roads_geojson = json.load(f)
else:
    roads_geojson = build_roads_geojson()
print(f"Arterial road network: {len(roads_geojson['features']):,} segments "
      f"({os.path.getsize(ROADS_CACHE):,} bytes cached)")

TEMPLATE = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>__TITLE__ — Where does cycling beat traffic?</title>
<meta name="viewport" content="initial-scale=1,maximum-scale=1,user-scalable=no">
<script src="https://api.mapbox.com/mapbox-gl-js/v3.3.0/mapbox-gl.js"></script>
<link href="https://api.mapbox.com/mapbox-gl-js/v3.3.0/mapbox-gl.css" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --surface:#fcfcfb; --text-primary:#0b0b0b; --text-secondary:#52514e;
  --text-muted:#898781; --border:rgba(11,11,11,0.10); --accent:#15406b;
}
html,body{height:100%}
body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--text-primary);overflow:hidden}
#map{position:absolute;inset:0}

.panel{position:absolute;top:16px;left:16px;width:272px;max-width:calc(100vw - 32px);
  background:var(--surface);border-radius:14px;box-shadow:0 6px 24px rgba(0,0,0,.16);
  border:1px solid var(--border);z-index:2;max-height:calc(100vh - 32px);
  display:flex;flex-direction:column;overflow:hidden}
.panel-hd{display:flex;align-items:center;justify-content:space-between;gap:8px;
  padding:12px 14px;cursor:pointer;flex-shrink:0;user-select:none}
.panel-hd .ttl{font-size:13.5px;font-weight:700;color:var(--text-primary);line-height:1.3}
.panel-hd .sub{font-size:10.5px;color:var(--text-secondary);font-weight:400;margin-top:1px}
.panel-hd .tgl{width:22px;height:22px;border-radius:50%;border:1px solid var(--border);
  background:#fff;color:var(--text-secondary);font-size:14px;line-height:1;display:flex;
  align-items:center;justify-content:center;flex-shrink:0}
.panel-bd{padding:0 14px 14px;overflow-y:auto}
.panel.collapsed .panel-bd{display:none}

h3{font-size:9.5px;color:var(--text-muted);font-weight:700;text-transform:uppercase;
  letter-spacing:.6px;margin:12px 0 6px}
.seg{display:flex;gap:5px}
.seg button{flex:1;font-size:11.5px;padding:6px 4px;border:1px solid var(--border);
  border-radius:7px;background:#fff;color:var(--text-secondary);cursor:pointer;font-weight:600}
.seg button.active{background:var(--accent);color:#fff;border-color:var(--accent)}

.legend-row{display:flex;align-items:flex-start;gap:8px;padding:6px 0}
.legend-row .sw{width:13px;height:13px;border-radius:3px;flex-shrink:0;margin-top:2px}
.legend-row .tx{font-size:11px;line-height:1.4;color:var(--text-secondary)}
.legend-row .tx b{display:block;font-size:11.5px;color:var(--text-primary);margin-bottom:1px}
.legend-row .n{color:var(--text-muted);font-weight:400}

.chk{display:flex;align-items:center;gap:7px;font-size:11.5px;color:var(--text-secondary);
  margin-bottom:6px;cursor:pointer}
.chk input{width:14px;height:14px}
.roadsub{margin-top:2px;font-size:10px;color:var(--text-muted);line-height:1.5}

.note{font-size:9.5px;color:var(--text-secondary);line-height:1.55;margin-top:10px;
  padding-top:9px;border-top:1px solid var(--border)}

.drawer{position:fixed;top:0;right:0;bottom:0;width:320px;max-width:88vw;
  background:var(--surface);box-shadow:-8px 0 28px rgba(0,0,0,.18);
  transform:translateX(100%);transition:transform .3s cubic-bezier(.2,.8,.2,1);
  z-index:5;padding:22px 20px;overflow-y:auto}
.drawer.open{transform:translateX(0)}
.drawer-close{position:absolute;top:14px;right:14px;width:26px;height:26px;border-radius:50%;
  border:1px solid var(--border);background:#fff;color:var(--text-secondary);font-size:15px;
  cursor:pointer;line-height:1}
.dw-name{font-size:17px;font-weight:700;color:var(--text-primary);margin:2px 30px 2px 0}
.dw-zone{font-size:11.5px;color:var(--text-muted);margin-bottom:10px}
.catbadge{display:inline-block;font-size:11px;font-weight:700;color:#fff;padding:3px 10px;
  border-radius:11px;margin-bottom:14px}
.kpi{font-size:12.5px;color:var(--text-secondary);line-height:1.9}
.kpi b{color:var(--text-primary)}
.dw-empty{font-size:12px;color:var(--text-muted);margin-top:40px;text-align:center}

.mapboxgl-popup-content{font-family:system-ui,sans-serif;font-size:12px;padding:9px 11px;border-radius:7px}
</style></head>
<body>
<div id="map"></div>

<div class="panel" id="panel">
  <div class="panel-hd" id="panelHd">
    <div><div class="ttl">__TITLE__</div><div class="sub">Bicycle vs. car by ward</div></div>
    <div class="tgl" id="panelTgl">&#8722;</div>
  </div>
  <div class="panel-bd" id="panelBd">
    <h3>Time budget</h3>
    <div class="seg" id="bands"></div>
    <h3>Legend</h3>
    <div id="legend"></div>
    <h3>Arterial roads</h3>
    <label class="chk"><input type="checkbox" id="roadsToggle" checked> Motorway &rarr; secondary</label>
    <label class="chk"><input type="checkbox" id="roadsTertiaryToggle"> + tertiary / local</label>
    <div class="roadsub">Darker &amp; thicker = higher road class (e.g. Outer Ring Road, MG Road).</div>
    <div class="note">Effective car speed is back-solved from reachable area (area &prop; speed&sup2;):
      <b>v = bicycle_speed &times; &radic;(car_area / bicycle_area)</b>. "Comparable" = within
      __MULT__&times; of __BIKESPEED__ km/h. Click a ward for details.</div>
  </div>
</div>

<div class="drawer" id="drawer">
  <button class="drawer-close" id="drawerClose">&times;</button>
  <div id="drawerBody"><div class="dw-empty">Click a ward on the map to see its details.</div></div>
</div>

<script>
mapboxgl.accessToken = '__TOKEN__';
const WARDS = __WARDS_JSON__;
const ROADS = __ROADS_JSON__;
const LOOKUP = __LOOKUP_JSON__;
const CATINFO = __CATINFO_JSON__;
const BANDS = __BANDS_JSON__;
let band = BANDS[0];

const ROAD_MAIN = ['motorway','motorway_link','trunk','trunk_link','primary','primary_link','secondary','secondary_link'];
const ROAD_CASING = ['motorway','motorway_link','trunk','trunk_link','primary','primary_link'];
const ROAD_TERTIARY = ['tertiary','tertiary_link'];
const ROAD_WIDTH = ['match', ['get','highway'],
  ['motorway','motorway_link'], 2.6, ['trunk','trunk_link'], 2.2,
  ['primary','primary_link'], 1.6, ['secondary','secondary_link'], 1.1, 0.7];
const ROAD_CASING_WIDTH = ['match', ['get','highway'],
  ['motorway','motorway_link'], 4.6, ['trunk','trunk_link'], 4.0, ['primary','primary_link'], 3.0, 2.4];
const ROAD_COLOR = ['match', ['get','highway'],
  ['motorway','motorway_link'], '#292929', ['trunk','trunk_link'], '#3a3a3a',
  ['primary','primary_link'], '#555555', ['secondary','secondary_link'], '#767676', '#9a9a9a'];

const center = [__CENTERLON__, __CENTERLAT__];
const map = new mapboxgl.Map({container:'map', style:'mapbox://styles/mapbox/light-v11', center, zoom:__ZOOM__,
  customAttribution: 'Ward metrics from © <a href="https://www.tomtom.com/" target="_blank">TomTom</a> &amp; © OpenStreetMap travel sheds'});
map.addControl(new mapboxgl.NavigationControl(), 'top-right');

function catOf(wardNo){ const la=(LOOKUP[wardNo]||{})[band]; return la? la.category : null; }

function buildMatch(){
  const m = ['match', ['get','ward_no']];
  Object.keys(LOOKUP).forEach(no=>{
    const la = LOOKUP[no][band];
    if (la) m.push(+no, CATINFO[la.category].color);
  });
  m.push('#ccc');
  return m;
}

function addLayers(){
  map.addSource('wards', {type:'geojson', data:WARDS});
  map.addLayer({id:'ward-fill', type:'fill', source:'wards',
    paint:{'fill-color': buildMatch(), 'fill-opacity':0.72}});
  map.addLayer({id:'ward-line', type:'line', source:'wards',
    paint:{'line-color':'#fff','line-width':0.6,'line-opacity':0.6}});
  map.addSource('roads', {type:'geojson', data: ROADS});
  map.addLayer({id:'roads-casing', type:'line', source:'roads',
    filter: ['in', ['get','highway'], ['literal', ROAD_CASING]],
    paint:{'line-color':'#fff', 'line-width': ROAD_CASING_WIDTH, 'line-opacity':0.85}});
  map.addLayer({id:'roads-line', type:'line', source:'roads',
    filter: ['in', ['get','highway'], ['literal', ROAD_MAIN]],
    paint:{'line-color': ROAD_COLOR, 'line-width': ROAD_WIDTH}});

  map.addLayer({id:'ward-sel', type:'line', source:'wards',
    paint:{'line-color':'#222','line-width':2.6}, filter:['==',['get','ward_no'], -1]});

  map.on('click','ward-fill', e => showWard(e.features[0].properties.ward_no));
  map.on('mousemove','ward-fill', e => { map.getCanvas().style.cursor='pointer'; });
  map.on('mouseleave','ward-fill', () => { map.getCanvas().style.cursor=''; });
}

function fmt(v){ return v==null? '—' : v.toFixed(1); }

const drawer = document.getElementById('drawer');
function openDrawer(){ drawer.classList.add('open'); }
function closeDrawer(){
  drawer.classList.remove('open');
  map.setFilter('ward-sel', ['==',['get','ward_no'], -1]);
}
document.getElementById('drawerClose').onclick = closeDrawer;
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawer(); });

function showWard(wardNo){
  map.setFilter('ward-sel', ['==',['get','ward_no'], wardNo]);
  const props = (WARDS.features.find(f=>f.properties.ward_no===wardNo)||{properties:{}}).properties;
  const la = (LOOKUP[wardNo]||{})[band];
  if (!la){ document.getElementById('drawerBody').innerHTML = '<div class="dw-empty">No data for this ward</div>'; openDrawer(); return; }
  const info = CATINFO[la.category];
  document.getElementById('drawerBody').innerHTML =
    `<div class="dw-name">${props.ward_name||''}</div><div class="dw-zone">${props.zone||''}</div>`
    + `<span class="catbadge" style="background:${info.color}">${la.category} · ${info.label.split('(')[0].trim()}</span>`
    + `<div class="kpi">`
    + `Effective car speed at peak: <b>${fmt(la.v_car_peak)} km/h</b><br>`
    + `Effective car speed free-flow: <b>${fmt(la.v_car_ff)} km/h</b><br>`
    + `Bicycle speed (fixed): <b>__BIKESPEED__ km/h</b><br><br>`
    + `Peak-drive shed: <b>${fmt(la.peak_km2)} km²</b> · Free-flow shed: <b>${fmt(la.ff_km2)} km²</b> `
    + `· Bicycle shed: <b>${fmt(la.bike_km2)} km²</b></div>`;
  openDrawer();
}

function buildLegend(){
  const counts = {A:0,B:0,C:0};
  Object.values(LOOKUP).forEach(byBand=>{ const la=byBand[band]; if(la) counts[la.category]++; });
  const order = ['A','B','C'];
  document.getElementById('legend').innerHTML = order.map(c=>{
    const info = CATINFO[c];
    return `<div class="legend-row"><span class="sw" style="background:${info.color}"></span>`
      + `<span class="tx"><b>${c} <span class="n">(${counts[c]} wards)</span></b>${info.label}</span></div>`;
  }).join('');
}

function refresh(){
  if (map.getLayer('ward-fill')) map.setPaintProperty('ward-fill','fill-color', buildMatch());
  buildLegend();
}

const bandsEl = document.getElementById('bands');
BANDS.forEach(b=>{
  const btn=document.createElement('button'); btn.textContent=b+' min'; btn.dataset.b=b;
  if (b===band) btn.classList.add('active');
  btn.onclick=()=>{ band=b; [...bandsEl.children].forEach(c=>c.classList.toggle('active', +c.dataset.b===b));
    refresh(); closeDrawer(); };
  bandsEl.appendChild(btn);
});

const panel = document.getElementById('panel');
document.getElementById('panelHd').onclick = () => {
  panel.classList.toggle('collapsed');
  document.getElementById('panelTgl').innerHTML = panel.classList.contains('collapsed') ? '&#43;' : '&#8722;';
};

document.getElementById('roadsToggle').onchange = e => {
  const vis = e.target.checked ? 'visible' : 'none';
  ['roads-casing','roads-line'].forEach(id => map.getLayer(id) && map.setLayoutProperty(id,'visibility',vis));
};
document.getElementById('roadsTertiaryToggle').onchange = e => {
  const classes = e.target.checked ? ROAD_MAIN.concat(ROAD_TERTIARY) : ROAD_MAIN;
  if (map.getLayer('roads-line')) map.setFilter('roads-line', ['in', ['get','highway'], ['literal', classes]]);
};

buildLegend();
map.on('load', addLayers);
</script>
</body></html>"""

html = (TEMPLATE
        .replace("__TITLE__", cfg.CITY_NAME)
        .replace("__TOKEN__", cfg.MAPBOX_TOKEN)
        .replace("__CENTERLON__", str(cfg.CENTER_LON))
        .replace("__CENTERLAT__", str(cfg.CENTER_LAT))
        .replace("__ZOOM__", str(cfg.DEFAULT_ZOOM))
        .replace("__BIKESPEED__", str(BIKE_SPEED))
        .replace("__MULT__", str(COMPARABLE_MULT))
        .replace("__WARDS_JSON__", json.dumps(wards_geojson))
        .replace("__ROADS_JSON__", json.dumps(roads_geojson))
        .replace("__LOOKUP_JSON__", json.dumps(ward_lookup))
        .replace("__CATINFO_JSON__", json.dumps(CAT_INFO))
        .replace("__BANDS_JSON__", json.dumps(bands)))

with open(OUT_HTML, "w", encoding="utf-8") as f:
    f.write(html)
print(f"\nWrote {OUT_HTML}  ({os.path.getsize(OUT_HTML):,} bytes)")

DOCS_HTML = os.path.join(cfg.DOCS_DIR, "categories.html")
shutil.copyfile(OUT_HTML, DOCS_HTML)
print(f"Copied to GitHub Pages dir: {DOCS_HTML}")
