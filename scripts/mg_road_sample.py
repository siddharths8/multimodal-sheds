"""
MG Road sample — validate drive/bike/walk overlap and preview the interface
BEFORE the full city run.

- A handful of origin points along MG Road, Bengaluru.
- Car sheds from TomTom (AM peak / PM peak / midday, from resolved or fallback times).
- Bike & walk sheds computed on the OSM network at fixed average speeds (osm_isochrone).
- Builds output/BLR_MGRoad_Sample.html: pick a point, overlay the modes, compare areas.

Requires TOMTOM_API_KEY (for the car sheds only).
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
OUT_HTML = os.path.join(cfg.OUTPUT_DIR, f"{cfg.CITY_SHORT}_MGRoad_Sample.html")
RAW_CACHE = os.path.join(cfg.SHEDS_DIR, "_mgroad_raw_car.json")
raw_car = json.load(open(RAW_CACHE, encoding="utf-8")) if os.path.exists(RAW_CACHE) else {}

# ── MG Road sample points (lon, lat) ─────────────────────────────────────────
POINTS = [
    ("mg_trinity",   "Trinity Circle",       77.6203, 12.9728),
    ("mg_garuda",    "Near Garuda Mall",     77.6158, 12.9740),
    ("mg_brigade",   "MG x Brigade Road",    77.6082, 12.9750),
    ("mg_kumble",    "Anil Kumble Circle",   77.6050, 12.9753),
    ("mg_plaza",     "Near Cauvery/Plaza",   77.6018, 12.9759),
]
# network bbox: cover ~6 km around the corridor (20-min bike ~5 km)
lons = [p[2] for p in POINTS]; lats = [p[3] for p in POINTS]
M = 0.06
BBOX = (min(lats) - M, min(lons) - M, max(lats) + M, max(lons) + M)
TAG = "mgroad"


def car_shed(lat, lon, band, traffic, depart_at):
    url = cfg.TOMTOM_BASE.format(lat=lat, lon=lon)
    params = {"key": KEY, "travelMode": "car", "timeBudgetInSec": band,
              "traffic": "true" if traffic else "false", "routeType": "fastest"}
    if depart_at:
        params["departAt"] = depart_at
    r = requests.get(url, params=params, headers={"User-Agent": cfg.HTTP_UA}, timeout=60)
    r.raise_for_status()
    b = r.json()["reachableRange"]["boundary"]
    poly = Polygon([(p["longitude"], p["latitude"]) for p in b])
    return poly if poly.is_valid else make_valid(poly)


def area_km2(poly):
    return round(gpd.GeoSeries([poly], crs=cfg.WGS84).to_crs(cfg.WORK_CRS).area.iloc[0] / 1e6, 2)


# ── Build the OSM graphs for the corridor (once) ─────────────────────────────
print("Building OSM walk/bike graphs for the MG Road corridor...")
bundles = {m: osm.build_bundle(m, BBOX, TAG) for m in ("pedestrian", "bicycle")}

car_scen = cfg.car_scenarios()
print(f"Car scenarios: {[s['key'] for s in car_scen]}")

sample = []
for pid, name, lon, lat in POINTS:
    print(f"\n{name} ({lat},{lon})")
    rec = {"pt_id": pid, "name": name, "lon": lon, "lat": lat, "sheds": {}, "areas": {}}
    # car (TomTom)
    rec["sheds"]["car"] = {}
    for s in car_scen:
        rec["sheds"]["car"][s["key"]] = {}
        for band in cfg.BANDS_SEC:
            ck = f"{pid}|{s['key']}|{band}"
            if ck in raw_car:
                raw = shape(raw_car[ck])
            else:
                raw = car_shed(lat, lon, band, s["traffic"], s["departAt"])
                raw_car[ck] = mapping(raw)
                time.sleep(cfg.SLEEP_SEC)
            poly = gutil.smooth_car(raw)            # smooth the jagged TomTom edges
            a = area_km2(poly)
            rec["sheds"]["car"][s["key"]][str(band)] = mapping(poly)
            rec["areas"][f"car_{s['key']}_{band//60}"] = a
            print(f"  car/{s['key']} {band//60}min -> {a} km^2")
    # bike + walk (OSM network)
    for mode in ("bicycle", "pedestrian"):
        rec["sheds"][mode] = {"na": {}}
        for band in cfg.BANDS_SEC:
            poly = osm.isochrone(bundles[mode], lon, lat, band, mode)
            if not poly.is_valid:
                poly = make_valid(poly)
            rec["sheds"][mode]["na"][str(band)] = mapping(poly)
            rec["areas"][f"{mode}_{band//60}"] = area_km2(poly)
            print(f"  {mode} {band//60}min -> {area_km2(poly)} km^2")
    sample.append(rec)

json.dump(raw_car, open(RAW_CACHE, "w", encoding="utf-8"))   # reuse raw car sheds on re-run

# ── Build the per-point overlay preview ──────────────────────────────────────
TEMPLATE = r"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>__TITLE__ — MG Road sample</title>
<meta name="viewport" content="initial-scale=1,maximum-scale=1,user-scalable=no">
<script src="https://api.mapbox.com/mapbox-gl-js/v3.3.0/mapbox-gl.js"></script>
<link href="https://api.mapbox.com/mapbox-gl-js/v3.3.0/mapbox-gl.css" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}body{font-family:Arial,sans-serif;display:flex;height:100vh;overflow:hidden}
#sb{width:340px;background:#f7f8fa;border-right:3px solid #15406b;display:flex;flex-direction:column}
#hd{background:#15406b;color:#fff;padding:14px 16px}#hd h1{font-size:15px}#hd p{font-size:11px;opacity:.9;margin-top:4px}
#bd{flex:1;overflow:auto;padding:13px 15px}
h3{font-size:10px;color:#666;font-weight:700;text-transform:uppercase;letter-spacing:.6px;margin:13px 0 7px}
select{width:100%;padding:7px;border:1px solid #cbd5e1;border-radius:6px;font-size:13px}
.seg{display:flex;gap:5px;flex-wrap:wrap}.seg button{flex:1;min-width:60px;font-size:11.5px;padding:6px;border:1px solid #15406b;border-radius:5px;background:#fff;color:#15406b;cursor:pointer}
.seg button.active{background:#15406b;color:#fff}
.mr{display:flex;align-items:center;gap:9px;background:#fff;border:1px solid #e2e8f0;border-radius:7px;padding:7px 10px;margin:6px 0}
.mr .sw{width:20px;height:13px;border-radius:3px}.mr .nm{flex:1;font-size:12.5px;font-weight:600}.mr .ar{font-size:12px;color:#444}
.kpi{font-size:12px;color:#333;line-height:1.6;background:#eef4fc;border-left:3px solid #15406b;padding:9px 11px;border-radius:6px;margin-top:10px}
.kpi b{color:#15406b}#op{width:100%}#map{flex:1}
</style></head><body>
<div id="sb"><div id="hd"><h1>__TITLE__ — MG Road sample</h1><p>Drive vs bike vs walk overlap. Car = TomTom traffic; bike/walk = OSM network @ fixed speed.</p></div>
<div id="bd">
<h3>Sample point</h3><select id="pt"></select>
<h3>Time budget</h3><div class="seg" id="bands"></div>
<h3>Driving — time of day</h3><div class="seg" id="scen"></div>
<h3>Modes</h3><div id="modes"></div>
<div class="kpi" id="kpi"></div>
<h3>Opacity</h3><input type="range" id="op" min="0.15" max="0.85" step="0.05" value="0.45">
</div></div><div id="map"></div>
<script>
mapboxgl.accessToken='__TOKEN__';
const SAMPLE=__SAMPLE__, MODES=__MODES__, MORDER=__MORDER__, CARSCEN=__CARSCEN__, BANDS=__BANDS__, SPEEDS=__SPEEDS__;
let st={i:0, band:BANDS[0], scen:CARSCEN[0].key, vis:{car:true,bicycle:true,pedestrian:true}, op:0.45};
const EMPTY={type:'FeatureCollection',features:[]};
const map=new mapboxgl.Map({container:'map',style:'mapbox://styles/mapbox/streets-v12',center:[__LON__,__LAT__],zoom:12});
map.addControl(new mapboxgl.NavigationControl(),'top-right');
function fc(geom){return {type:'FeatureCollection',features:[{type:'Feature',properties:{},geometry:geom}]};}
function shed(m){const r=SAMPLE[st.i];const sc=(m==='car')?st.scen:'na';try{return r.sheds[m][sc][String(st.band*60)];}catch(e){return null;}}
function addLayers(){
  MORDER.forEach(m=>{map.addSource('s-'+m,{type:'geojson',data:EMPTY});
    map.addLayer({id:'f-'+m,type:'fill',source:'s-'+m,paint:{'fill-color':MODES[m].color,'fill-opacity':st.op}});
    map.addLayer({id:'l-'+m,type:'line',source:'s-'+m,paint:{'line-color':MODES[m].color,'line-width':1.6}});});
  map.addSource('pts',{type:'geojson',data:{type:'FeatureCollection',features:SAMPLE.map((r,i)=>({type:'Feature',properties:{i},geometry:{type:'Point',coordinates:[r.lon,r.lat]}}))}});
  map.addLayer({id:'pt-dot',type:'circle',source:'pts',paint:{'circle-radius':5,'circle-color':'#111','circle-stroke-color':'#fff','circle-stroke-width':2}});
  map.on('click','pt-dot',e=>{st.i=e.features[0].properties.i;document.getElementById('pt').value=st.i;refresh(true);});
  refresh(true);
}
function refresh(fly){
  MORDER.forEach(m=>{const g=st.vis[m]?shed(m):null;map.getSource('s-'+m).setData(g?fc(g):EMPTY);});
  if(fly){const r=SAMPLE[st.i];map.flyTo({center:[r.lon,r.lat],zoom:12.5});}
  const r=SAMPLE[st.i];
  const car=r.areas['car_'+st.scen+'_'+st.band], bike=r.areas['bicycle_'+st.band], walk=r.areas['pedestrian_'+st.band];
  MORDER.forEach(m=>{const a=(m==='car')?car:r.areas[m+'_'+st.band];const el=document.getElementById('ar-'+m);if(el)el.textContent=(a==null?'—':a+' km²');});
  // peak = min of am/pm
  const peak=Math.min(...CARSCEN.filter(s=>s.key!=='midday').map(s=>r.areas['car_'+s.key+'_'+st.band]).filter(v=>v!=null));
  let k=`<b>${r.name}</b> · ${st.band} min<br>`;
  if(bike!=null&&peak)k+=`Bike reaches <b>${(bike/peak*100).toFixed(0)}%</b> of the peak car area.<br>`;
  if(walk!=null&&peak)k+=`Walk reaches <b>${(walk/peak*100).toFixed(0)}%</b>.<br>`;
  const md=r.areas['car_midday_'+st.band];
  if(md!=null&&peak)k+=`Car loses <b>${((md-peak)/md*100).toFixed(0)}%</b> of its midday area at peak.`;
  document.getElementById('kpi').innerHTML=k;
}
const ptSel=document.getElementById('pt');SAMPLE.forEach((r,i)=>{const o=document.createElement('option');o.value=i;o.textContent=r.name;ptSel.appendChild(o);});
ptSel.onchange=()=>{st.i=+ptSel.value;refresh(true);};
const bandsEl=document.getElementById('bands');BANDS.forEach(b=>{const x=document.createElement('button');x.textContent=b+' min';if(b===st.band)x.classList.add('active');x.onclick=()=>{st.band=b;[...bandsEl.children].forEach((c,j)=>c.classList.toggle('active',BANDS[j]===b));refresh(false);};bandsEl.appendChild(x);});
const scenEl=document.getElementById('scen');CARSCEN.forEach(s=>{const x=document.createElement('button');x.textContent=s.label;if(s.key===st.scen)x.classList.add('active');x.onclick=()=>{st.scen=s.key;[...scenEl.children].forEach(c=>c.classList.toggle('active',c.textContent===s.label));refresh(false);};scenEl.appendChild(x);});
const modesEl=document.getElementById('modes');MORDER.forEach(m=>{const row=document.createElement('div');row.className='mr';row.innerHTML=`<span class="sw" style="background:${MODES[m].color}"></span><span class="nm">${MODES[m].label}</span><span class="ar" id="ar-${m}"></span><input type="checkbox" checked>`;row.querySelector('input').onchange=e=>{st.vis[m]=e.target.checked;refresh(false);};modesEl.appendChild(row);});
document.getElementById('op').oninput=e=>{st.op=+e.target.value;MORDER.forEach(m=>{if(map.getLayer('f-'+m))map.setPaintProperty('f-'+m,'fill-opacity',st.op);});};
map.on('load',addLayers);
</script></body></html>"""

html = (TEMPLATE
        .replace("__TITLE__", cfg.CITY_NAME)
        .replace("__TOKEN__", cfg.MAPBOX_TOKEN)
        .replace("__LON__", str(POINTS[0][2])).replace("__LAT__", str(POINTS[0][3]))
        .replace("__SAMPLE__", json.dumps(sample))
        .replace("__MODES__", json.dumps({m: {"label": cfg.MODES[m]["label"], "color": cfg.MODES[m]["color"]} for m in cfg.MODE_ORDER}))
        .replace("__MORDER__", json.dumps(cfg.MODE_ORDER))
        .replace("__CARSCEN__", json.dumps([{"key": s["key"], "label": s["label"]} for s in car_scen]))
        .replace("__BANDS__", json.dumps(cfg.BANDS_MIN))
        .replace("__SPEEDS__", json.dumps(cfg.SPEEDS_KMPH)))

with open(OUT_HTML, "w", encoding="utf-8") as f:
    f.write(html)
print(f"\nWrote sample viewer: {OUT_HTML}  ({os.path.getsize(OUT_HTML):,} bytes)")
