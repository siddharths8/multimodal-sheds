"""
Quick cross-city exercise: free-flow drive vs bike vs walk, side by side.

Reads sample_points from the active city config (set CITY_CONFIG to switch city),
fetches a FREE-FLOW car shed (TomTom traffic=false) per point and computes bike &
walk sheds on the OSM network, then renders a 3-panel synced viewer.

Run e.g.:
  CITY_CONFIG=city_config_tokyo.json TOMTOM_API_KEY=... python scripts/city_sample.py
"""

import os
import json
import time
import requests
import geopandas as gpd
from shapely.geometry import Polygon, shape, mapping
from shapely.validation import make_valid

from config_loader import cfg
import osm_isochrone as osm
import geo_utils as gutil

KEY = cfg.tomtom_key()
BAND = cfg.BANDS_SEC[0]                       # single budget for the exercise
OUT_HTML = os.path.join(cfg.OUTPUT_DIR, f"{cfg.CITY_SHORT}_FreeFlow_Compare.html")
RAW_CACHE = os.path.join(cfg.SHEDS_DIR, f"_{cfg.CITY_SHORT.lower()}_freeflow_raw.json")

POINTS = cfg.SAMPLE_POINTS
assert POINTS, "No sample_points in the active city config."
raw = json.load(open(RAW_CACHE, encoding="utf-8")) if os.path.exists(RAW_CACHE) else {}


def freeflow_car(lat, lon):
    url = cfg.TOMTOM_BASE.format(lat=lat, lon=lon)
    params = {"key": KEY, "travelMode": "car", "timeBudgetInSec": BAND,
              "traffic": "false", "routeType": "fastest"}
    r = requests.get(url, params=params, headers={"User-Agent": cfg.HTTP_UA}, timeout=60)
    r.raise_for_status()
    b = r.json()["reachableRange"]["boundary"]
    poly = Polygon([(p["longitude"], p["latitude"]) for p in b])
    return poly if poly.is_valid else make_valid(poly)


def area_km2(poly):
    return round(gpd.GeoSeries([poly], crs=cfg.WGS84).to_crs(cfg.WORK_CRS).area.iloc[0] / 1e6, 2)


# network graphs around the sample points
lons = [p[2] for p in POINTS]; lats = [p[3] for p in POINTS]
M = 0.05
bbox = (min(lats) - M, min(lons) - M, max(lats) + M, max(lons) + M)
print(f"{cfg.CITY_NAME}: {len(POINTS)} points, band {BAND//60} min")
bundles = {m: osm.build_bundle(m, bbox, f"{cfg.CITY_SHORT.lower()}_sample")
           for m in ("pedestrian", "bicycle")}

sample = []
for pid, name, lon, lat in POINTS:
    print(f"\n{name}")
    if pid in raw:
        car = shape(raw[pid])
    else:
        car = freeflow_car(lat, lon)
        raw[pid] = mapping(car)
        time.sleep(cfg.SLEEP_SEC)
    car = gutil.smooth_car(car)
    geom = {"car": mapping(car)}
    areas = {"car": area_km2(car)}
    for m in ("bicycle", "pedestrian"):
        poly = osm.isochrone(bundles[m], lon, lat, BAND, m)
        geom[m] = mapping(poly if poly.is_valid else make_valid(poly))
        areas[m] = area_km2(poly)
    print(f"  free-flow car {areas['car']} | bike {areas['bicycle']} | walk {areas['pedestrian']} km^2")
    sample.append({"pt_id": pid, "name": name, "lon": lon, "lat": lat, "geom": geom, "areas": areas})

json.dump(raw, open(RAW_CACHE, "w", encoding="utf-8"))

# ── 3-panel synced viewer ────────────────────────────────────────────────────
TEMPLATE = r"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>__TITLE__ — free-flow drive vs bike vs walk</title>
<meta name="viewport" content="initial-scale=1,maximum-scale=1,user-scalable=no">
<script src="https://api.mapbox.com/mapbox-gl-js/v3.3.0/mapbox-gl.js"></script>
<link href="https://api.mapbox.com/mapbox-gl-js/v3.3.0/mapbox-gl.css" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}body{font-family:Arial,sans-serif;height:100vh;display:flex;flex-direction:column}
#bar{background:#15406b;color:#fff;padding:10px 16px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
#bar h1{font-size:15px}#bar select{padding:6px 8px;border-radius:6px;border:0;font-size:13px}
#bar .note{font-size:11px;opacity:.9}
#row{flex:1;display:flex}
.panel{flex:1;display:flex;flex-direction:column;border-right:2px solid #fff}
.panel:last-child{border-right:0}
.phd{padding:7px 10px;font-size:13px;font-weight:700;color:#fff;display:flex;justify-content:space-between;align-items:center}
.phd .a{font-weight:400;font-size:12px;opacity:.95}
.map{flex:1}
</style></head><body>
<div id="bar"><h1>__TITLE__ — 15-min reach: free-flow drive vs bike vs walk</h1>
  <select id="pt"></select>
  <span class="note">Same time budget, same origin. Pan/zoom one panel — all three follow.</span></div>
<div id="row">
  <div class="panel"><div class="phd" id="hd-car" style="background:#D7191C">Drive (free-flow) <span class="a" id="a-car"></span></div><div class="map" id="m-car"></div></div>
  <div class="panel"><div class="phd" id="hd-bicycle" style="background:#FDAE61">Bike (__BIKEKMH__ km/h) <span class="a" id="a-bicycle"></span></div><div class="map" id="m-bicycle"></div></div>
  <div class="panel"><div class="phd" id="hd-pedestrian" style="background:#1A9641">Walk (__WALKKMH__ km/h) <span class="a" id="a-pedestrian"></span></div><div class="map" id="m-pedestrian"></div></div>
</div>
<script>
mapboxgl.accessToken='__TOKEN__';
const SAMPLE=__SAMPLE__, COLORS=__COLORS__, MORDER=['car','bicycle','pedestrian'];
let i=0, syncing=false;
const MAPS={};
MORDER.forEach(m=>{
  const mp=new mapboxgl.Map({container:'m-'+m, style:'mapbox://styles/mapbox/light-v11',
    center:[__LON__,__LAT__], zoom:__ZOOM__});
  MAPS[m]=mp;
  mp.on('load',()=>{
    mp.addSource('s',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
    mp.addLayer({id:'f',type:'fill',source:'s',paint:{'fill-color':COLORS[m],'fill-opacity':0.45}});
    mp.addLayer({id:'l',type:'line',source:'s',paint:{'line-color':COLORS[m],'line-width':1.6}});
    mp.addSource('p',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
    mp.addLayer({id:'pt',type:'circle',source:'p',paint:{'circle-radius':5,'circle-color':'#111','circle-stroke-color':'#fff','circle-stroke-width':2}});
    if(Object.values(MAPS).every(x=>x.isStyleLoaded())) render(true);
  });
  mp.on('move',()=>{ if(syncing)return; syncing=true; const c=mp.getCenter(),z=mp.getZoom(),b=mp.getBearing(),p=mp.getPitch();
    MORDER.forEach(o=>{if(MAPS[o]!==mp) MAPS[o].jumpTo({center:c,zoom:z,bearing:b,pitch:p});}); syncing=false;});
});
function fc(g){return {type:'FeatureCollection',features:[{type:'Feature',properties:{},geometry:g}]};}
function render(fit){
  const r=SAMPLE[i];
  MORDER.forEach(m=>{
    if(MAPS[m].getSource('s')) MAPS[m].getSource('s').setData(fc(r.geom[m]));
    if(MAPS[m].getSource('p')) MAPS[m].getSource('p').setData(fc({type:'Point',coordinates:[r.lon,r.lat]}));
    document.getElementById('a-'+m).textContent=r.areas[m]+' km²';
  });
  if(fit){ const b=new mapboxgl.LngLatBounds();
    const walk=cs=>cs.forEach(c=>Array.isArray(c[0])?walk(c):b.extend(c));
    walk(r.geom.bicycle.coordinates);   // fit to the bike shed so all panels stay legible
    if(!b.isEmpty()){ syncing=true; MORDER.forEach(m=>MAPS[m].fitBounds(b,{padding:40,duration:500})); syncing=false; } }
}
const sel=document.getElementById('pt');
SAMPLE.forEach((r,k)=>{const o=document.createElement('option');o.value=k;o.textContent=r.name;sel.appendChild(o);});
sel.onchange=()=>{i=+sel.value;render(true);};
</script></body></html>"""

html = (TEMPLATE
        .replace("__TITLE__", cfg.CITY_NAME)
        .replace("__TOKEN__", cfg.MAPBOX_TOKEN)
        .replace("__LON__", str(cfg.CENTER_LON)).replace("__LAT__", str(cfg.CENTER_LAT))
        .replace("__ZOOM__", str(cfg.DEFAULT_ZOOM))
        .replace("__BIKEKMH__", str(cfg.SPEEDS_KMPH["bicycle"]))
        .replace("__WALKKMH__", str(cfg.SPEEDS_KMPH["pedestrian"]))
        .replace("__SAMPLE__", json.dumps(sample))
        .replace("__COLORS__", json.dumps({m: cfg.MODES[m]["color"] for m in cfg.MODE_ORDER})))

with open(OUT_HTML, "w", encoding="utf-8") as f:
    f.write(html)
print(f"\nWrote: {OUT_HTML}  ({os.path.getsize(OUT_HTML):,} bytes)")
