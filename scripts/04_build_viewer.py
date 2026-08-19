"""
Stage 4 — build the overlay-first, GitHub-Pages-ready viewer.

Centerpiece: for a selected neighborhood, the car / bike / walk sheds are drawn
on ONE map simultaneously so the size difference is the message. A small
supporting bar shows the areas (km2) and how bike/walk compare to the peak car
shed, plus the area a car loses to congestion (peak vs free-flow).

Controls: neighborhood dropdown + map click, time-band toggle, car time-of-day
toggle (AM peak / PM peak / free-flow), per-mode show/hide, opacity, basemap.

Inputs:
  data/wards/<short>_wards_clean.geojson
  data/wards/<short>_ward_sheds.geojson
  data/analysis/<short>_summary.json
Outputs:
  output/<SHORT>_Multimodal_Viewer/index.html
  docs/index.html                       (same file, for GitHub Pages)
"""

import os
import json
import shutil
import geopandas as gpd

from config_loader import cfg

SHORT = cfg.CITY_SHORT.lower()
WARDS  = os.path.join(cfg.WARDS_DIR, f"{SHORT}_wards_clean.geojson")
SHEDS  = os.path.join(cfg.WARDS_DIR, f"{SHORT}_ward_sheds.geojson")
SUMMARY = os.path.join(cfg.ANALYSIS_DIR, f"{SHORT}_summary.json")
OUT_DIR = os.path.join(cfg.OUTPUT_DIR, f"{cfg.CITY_SHORT}_Multimodal_Viewer")
OUT_HTML = os.path.join(OUT_DIR, "index.html")
DOCS_HTML = os.path.join(cfg.DOCS_DIR, "compare.html")   # landing page is the hover explorer (stage 08)
os.makedirs(OUT_DIR, exist_ok=True)

wards = gpd.read_file(WARDS)
wards["geometry"] = wards.geometry.simplify(0.0004)
with open(SHEDS, encoding="utf-8") as f:
    sheds = json.load(f)
with open(SUMMARY, encoding="utf-8") as f:
    summary = json.load(f)

TEMPLATE = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>__TITLE__ — Drive vs Bicycle vs Walk</title>
<meta name="viewport" content="initial-scale=1,maximum-scale=1,user-scalable=no">
<script src="https://api.mapbox.com/mapbox-gl-js/v3.3.0/mapbox-gl.js"></script>
<link href="https://api.mapbox.com/mapbox-gl-js/v3.3.0/mapbox-gl.css" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:Arial,Helvetica,sans-serif;display:flex;height:100vh;overflow:hidden}
#sidebar{width:360px;flex-shrink:0;background:#f7f8fa;border-right:3px solid #15406b;
  display:flex;flex-direction:column;box-shadow:2px 0 10px rgba(0,0,0,.12);z-index:1}
#hdr{background:#15406b;color:#fff;padding:14px 16px}
#hdr h1{font-size:15px;margin-bottom:5px}
#headline{font-size:11.5px;line-height:1.5;opacity:.95}
#headline b{color:#FFD166}
#body{flex:1;overflow-y:auto;padding:13px 15px 20px}
h3{font-size:10px;color:#666;font-weight:700;text-transform:uppercase;letter-spacing:.6px;margin:14px 0 7px}
select,.seg{width:100%;font-size:13px}
select{padding:7px 8px;border:1px solid #cbd5e1;border-radius:6px;background:#fff}
.seg{display:flex;gap:5px;flex-wrap:wrap}
.seg button{flex:1;min-width:64px;font-size:11.5px;padding:6px 8px;border:1px solid #15406b;
  border-radius:5px;background:#fff;color:#15406b;cursor:pointer}
.seg button.active{background:#15406b;color:#fff}
.modes{display:flex;flex-direction:column;gap:6px}
.mode-row{display:flex;align-items:center;gap:9px;background:#fff;border:1px solid #e2e8f0;
  border-radius:7px;padding:7px 10px}
.mode-row .sw{width:22px;height:14px;border-radius:3px;flex-shrink:0}
.mode-row .nm{flex:1;font-size:12.5px;font-weight:600;color:#222}
.mode-row .ar{font-size:12px;color:#444;font-variant-numeric:tabular-nums}
.mode-row input{width:16px;height:16px}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:11px 12px;margin-top:8px}
.card .sel{font-size:13.5px;font-weight:700;color:#15406b;margin-bottom:2px}
.card .meta{font-size:10.5px;color:#777;margin-bottom:9px}
.bar{display:flex;align-items:center;gap:8px;margin:5px 0;font-size:11.5px}
.bar .bl{width:42px;color:#444}
.bar .bt{flex:1;background:#eef1f5;border-radius:4px;height:16px;overflow:hidden}
.bar .bf{height:16px;border-radius:4px}
.bar .bv{width:62px;text-align:right;color:#333;font-variant-numeric:tabular-nums}
.kpi{font-size:11.5px;color:#333;line-height:1.6;margin-top:8px;border-top:1px solid #eee;padding-top:8px}
.kpi b{color:#15406b}
.bm{display:flex;gap:5px;flex-wrap:wrap}
.bm button{font-size:11px;padding:5px 10px;border:1px solid #15406b;border-radius:4px;background:#fff;color:#15406b;cursor:pointer}
.bm button.active{background:#15406b;color:#fff}
#op{width:100%;accent-color:#15406b}
.acc{margin-top:14px}
.acc-h{display:flex;justify-content:space-between;cursor:pointer;padding:8px 11px;background:#15406b;color:#fff;
  border-radius:6px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.5px}
.acc-b{display:none;max-height:260px;overflow:auto;border:1px solid #e2e8f0;border-top:0;border-radius:0 0 6px 6px}
.acc-b.open{display:block}
table{width:100%;border-collapse:collapse;font-size:10.5px}
th{position:sticky;top:0;background:#eef2f7;padding:5px 6px;text-align:right;cursor:pointer;color:#15406b}
th:first-child,td:first-child{text-align:left}
td{padding:4px 6px;border-bottom:1px solid #f1f4f8;text-align:right;font-variant-numeric:tabular-nums}
tr.sel td{background:#fff7e6}
.note{font-size:10px;color:#667;line-height:1.5;margin-top:12px;background:#eef4fc;border-left:3px solid #15406b;padding:8px 10px;border-radius:6px}
#map{flex:1}
.mapboxgl-popup-content{font-family:Arial;font-size:12px;padding:9px 11px;border-radius:7px}
</style></head>
<body>
<div id="sidebar">
  <div id="hdr"><h1>__TITLE__ — how far in <span id="hb">15</span> minutes?</h1>
    <div id="headline"></div></div>
  <div id="body">
    <h3>Neighborhood</h3>
    <select id="ward"></select>
    <h3>Time budget</h3>
    <div class="seg" id="bands"></div>
    <h3>Driving — time of day</h3>
    <div class="seg" id="scen"></div>
    <h3>Modes (toggle &amp; compare area)</h3>
    <div class="modes" id="modes"></div>
    <div class="card" id="cmp"></div>
    <h3>Fill opacity</h3>
    <input type="range" id="op" min="0.15" max="0.85" step="0.05" value="0.45">
    <h3>Basemap</h3>
    <div class="bm">
      <button class="active" data-s="light-v11">Light</button>
      <button data-s="streets-v12">Streets</button>
      <button data-s="dark-v11">Dark</button>
      <button data-s="satellite-streets-v12">Satellite</button>
    </div>
    <div class="acc">
      <div class="acc-h" id="acch"><span>All wards — area by mode</span><span>&#9658;</span></div>
      <div class="acc-b" id="accb"></div>
    </div>
    <div class="note">Sheds are TomTom reachable areas (calculateReachableRange). Driving uses live/historical
      traffic at the chosen time; <b>free-flow</b> is the no-congestion baseline (traffic off).
      Walk &amp; bicycle use fixed average-speed profiles. Compare how much area each mode reaches in the same time.</div>
  </div>
</div>
<div id="map"></div>
<script>
mapboxgl.accessToken = '__TOKEN__';
const WARDS = __WARDS_JSON__;
const SHEDS = __SHEDS_JSON__;
const SUMMARY = __SUMMARY_JSON__;
const MODES = SUMMARY.modes;                 // {car:{label,color}, bicycle:{...}, pedestrian:{...}}
const MODE_ORDER = __MODEORDER__;            // ['car','bicycle','pedestrian']
const CARSCEN = SUMMARY.car_scenarios;       // [{key,label}...]
const BANDS = SUMMARY.bands_min;
const PRIMARY = SUMMARY.primary_band_min;
const PEAK_KEYS = __PEAKKEYS__;              // car scenarios counted as 'peak'

// index sheds: ward|mode|scenario|band -> feature
const IDX = {};
SHEDS.features.forEach(f => {const p=f.properties; IDX[`${p.ward_no}|${p.mode}|${p.scenario}|${p.band_min}`]=f;});

// ward name list
const WLIST = [...new Map(SHEDS.features.map(f=>[f.properties.ward_no, f.properties.ward_name])).entries()]
  .sort((a,b)=>a[1].localeCompare(b[1]));

let state = {
  ward: (SUMMARY.top_bike_beats_car[0]||{}).ward_no || WLIST[0][0],
  band: PRIMARY,
  scen: CARSCEN[0].key,
  vis: {car:true, bicycle:true, pedestrian:true},
  op: 0.45
};
const EMPTY = {type:'FeatureCollection', features:[]};
const fmt = v => (v==null? '—' : v.toFixed(1));

function carPeak(la){ // min (worst) of the peak car scenarios
  const peaks = PEAK_KEYS.map(k=>la['car_'+k]).filter(v=>v!=null);
  return peaks.length? Math.min(...peaks) : null;
}
function scenKeyForMode(m){ return m==='car'? state.scen : 'na'; }
function featFor(m){ return IDX[`${state.ward}|${m}|${scenKeyForMode(m)}|${state.band}`]; }

// ---- headline ----
function setHeadline(){
  const pb = SUMMARY.per_band[state.band] || {};
  const n = pb.n_wards_bike_ge_car_peak, N = pb.n_wards;
  const mb = pb.median_bike_pct_of_car_peak, cp = pb.median_congestion_penalty_pct;
  document.getElementById('hb').textContent = state.band;
  document.getElementById('headline').innerHTML =
    `In <b>${n}/${N}</b> wards a <b>${state.band}-min</b> bicycle ride reaches as much area as the peak-hour drive. `
    + `Citywide the bicycle shed is a median <b>${fmt(mb)}%</b> of the peak car shed`
    + (cp!=null? `, and congestion costs the car a median <b>${fmt(cp)}%</b> of its free-flow area.` : '.');
}

// ---- map ----
const center = [__CENTERLON__, __CENTERLAT__];
const map = new mapboxgl.Map({container:'map', style:'mapbox://styles/mapbox/light-v11', center, zoom:__ZOOM__,
  customAttribution: 'Drive sheds © <a href="https://www.tomtom.com/" target="_blank">TomTom</a> · bicycle/walk sheds © OpenStreetMap'});
map.addControl(new mapboxgl.NavigationControl(), 'top-right');

function addLayers(){
  map.addSource('wards', {type:'geojson', data:WARDS});
  map.addLayer({id:'ward-line', type:'line', source:'wards',
    paint:{'line-color':'#7b8794','line-width':0.7,'line-opacity':0.5}});
  map.addLayer({id:'ward-sel', type:'line', source:'wards',
    paint:{'line-color':'#15406b','line-width':2.4},
    filter:['==',['get','ward_no'], state.ward]});
  map.addLayer({id:'ward-hit', type:'fill', source:'wards', paint:{'fill-color':'#000','fill-opacity':0}});
  map.addLayer({id:'ward-label', type:'symbol', source:'wards',
    layout:{'text-field':['get','ward_name'],'text-size':10,
      'text-font':['Open Sans Regular','Arial Unicode MS Regular']},
    paint:{'text-color':'#33415c','text-halo-color':'#fff','text-halo-width':1.3,
      'text-opacity':['step',['zoom'],0,12.5,0.85]}});

  // shed layers: add car (bottom) -> bike -> walk (top) so smaller sits on top
  MODE_ORDER.forEach(m => {
    map.addSource('s-'+m, {type:'geojson', data:EMPTY});
    map.addLayer({id:'f-'+m, type:'fill', source:'s-'+m,
      paint:{'fill-color':MODES[m].color, 'fill-opacity':state.op}});
    map.addLayer({id:'l-'+m, type:'line', source:'s-'+m,
      paint:{'line-color':MODES[m].color, 'line-width':1.6}});
  });

  map.on('click','ward-hit', e => { state.ward = e.features[0].properties.ward_no;
    document.getElementById('ward').value = state.ward; refresh(true); });
  refresh(true);
}

function refresh(fly){
  // update shed layers
  let big=null;
  MODE_ORDER.forEach(m => {
    const f = state.vis[m] ? featFor(m) : null;
    map.getSource('s-'+m).setData(f ? {type:'FeatureCollection', features:[f]} : EMPTY);
    if (f && (m==='car')) big = f;
  });
  map.setFilter('ward-sel', ['==',['get','ward_no'], state.ward]);
  if (fly){
    const f = featFor('car') || featFor('bicycle');
    if (f){ const b=new mapboxgl.LngLatBounds();
      const walk=coords=>coords.forEach(c=> Array.isArray(c[0])? walk(c): b.extend(c));
      walk(f.geometry.coordinates); if(!b.isEmpty()) map.fitBounds(b,{padding:80,maxZoom:14,duration:600}); }
  }
  renderCmp(); setHeadline(); markTable();
}

// ---- comparison card ----
function renderCmp(){
  const la = (SUMMARY.ward_area_lookup[state.ward]||{})[state.band] || {};
  const wname = (WLIST.find(w=>w[0]===state.ward)||[null,''])[1];
  const carScenKey = 'car_'+state.scen;
  const vals = {car: la[carScenKey], bicycle: la['bicycle'], pedestrian: la['pedestrian']};
  const mx = Math.max(...Object.values(vals).filter(v=>v!=null), 0.01);
  let bars='';
  MODE_ORDER.forEach(m=>{
    const v=vals[m]; const w=v==null?0:Math.max(2, v/mx*100);
    bars += `<div class="bar"><span class="bl">${MODES[m].label}</span>`
      + `<span class="bt"><span class="bf" style="width:${w}%;background:${MODES[m].color}"></span></span>`
      + `<span class="bv">${fmt(v)} km²</span></div>`;
  });
  const cp = carPeak(la);
  const ff = la['car_free_flow'];
  let kpi='';
  if (cp!=null && la['bicycle']!=null) kpi += `Bicycle reaches <b>${fmt(la['bicycle']/cp*100)}%</b> of the peak car area. `;
  if (cp!=null && la['pedestrian']!=null) kpi += `Walk reaches <b>${fmt(la['pedestrian']/cp*100)}%</b>. `;
  if (cp!=null && ff!=null) kpi += `<br>Car loses <b>${fmt((ff-cp)/ff*100)}%</b> of its free-flow area to congestion.`;
  const scenLabel = (CARSCEN.find(s=>s.key===state.scen)||{}).label || '';
  document.getElementById('cmp').innerHTML =
    `<div class="sel">${wname}</div><div class="meta">${state.band} min · driving: ${scenLabel}</div>${bars}<div class="kpi">${kpi}</div>`;
  MODE_ORDER.forEach(m=>{const el=document.getElementById('ar-'+m); if(el) el.textContent=fmt(vals[m])+' km²';});
}

// ---- controls ----
const wardSel = document.getElementById('ward');
WLIST.forEach(([no,nm])=>{const o=document.createElement('option');o.value=no;o.textContent=nm;wardSel.appendChild(o);});
wardSel.value = state.ward;
wardSel.onchange = ()=>{ state.ward=+wardSel.value; refresh(true); };

const bandsEl=document.getElementById('bands');
BANDS.forEach(b=>{const btn=document.createElement('button');btn.textContent=b+' min';btn.dataset.b=b;
  if(b===state.band)btn.classList.add('active');
  btn.onclick=()=>{state.band=b;[...bandsEl.children].forEach(c=>c.classList.toggle('active',+c.dataset.b===b));refresh(false);};
  bandsEl.appendChild(btn);});

const scenEl=document.getElementById('scen');
CARSCEN.forEach(s=>{const btn=document.createElement('button');btn.textContent=s.label;btn.dataset.k=s.key;
  if(s.key===state.scen)btn.classList.add('active');
  btn.onclick=()=>{state.scen=s.key;[...scenEl.children].forEach(c=>c.classList.toggle('active',c.dataset.k===s.key));refresh(false);};
  scenEl.appendChild(btn);});

const modesEl=document.getElementById('modes');
MODE_ORDER.forEach(m=>{const row=document.createElement('div');row.className='mode-row';
  row.innerHTML=`<span class="sw" style="background:${MODES[m].color}"></span>`
    +`<span class="nm">${MODES[m].label}</span><span class="ar" id="ar-${m}"></span>`
    +`<input type="checkbox" ${state.vis[m]?'checked':''} data-m="${m}">`;
  row.querySelector('input').onchange=e=>{state.vis[m]=e.target.checked;refresh(false);};
  modesEl.appendChild(row);});

document.getElementById('op').oninput=e=>{state.op=+e.target.value;
  MODE_ORDER.forEach(m=>{if(map.getLayer('f-'+m))map.setPaintProperty('f-'+m,'fill-opacity',state.op);});};

document.querySelectorAll('.bm button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('.bm button').forEach(x=>x.classList.remove('active'));b.classList.add('active');
  map.setStyle('mapbox://styles/mapbox/'+b.dataset.s); map.once('style.load',addLayers);});

// ---- all-wards table ----
function buildTable(){
  const rows = WLIST.map(([no,nm])=>{
    const la=(SUMMARY.ward_area_lookup[no]||{})[PRIMARY]||{};
    const cp=carPeak(la); const bk=la['bicycle']; const wk=la['pedestrian'];
    return {no,nm,cp,bk,wk,pct: (cp&&bk)? bk/cp*100:null};
  });
  rows.sort((a,b)=>(b.pct||-1)-(a.pct||-1));
  let h=`<table id="wt"><thead><tr><th data-k="nm">Ward</th><th data-k="cp">Car</th>`
    +`<th data-k="bk">Bicycle</th><th data-k="wk">Walk</th><th data-k="pct">Bicycle%</th></tr></thead><tbody>`;
  rows.forEach(r=>{h+=`<tr data-no="${r.no}"><td>${r.nm}</td><td>${fmt(r.cp)}</td><td>${fmt(r.bk)}</td>`
    +`<td>${fmt(r.wk)}</td><td>${r.pct==null?'—':r.pct.toFixed(0)+'%'}</td></tr>`;});
  h+='</tbody></table>';
  document.getElementById('accb').innerHTML=h;
  document.querySelectorAll('#wt tbody tr').forEach(tr=>tr.onclick=()=>{state.ward=+tr.dataset.no;
    document.getElementById('ward').value=state.ward;refresh(true);});
}
function markTable(){document.querySelectorAll('#wt tbody tr').forEach(tr=>
  tr.classList.toggle('sel', +tr.dataset.no===state.ward));}
document.getElementById('acch').onclick=()=>document.getElementById('accb').classList.toggle('open');

buildTable();
map.on('load', addLayers);
</script>
</body></html>"""

html = (TEMPLATE
        .replace("__TITLE__", cfg.CITY_NAME)
        .replace("__TOKEN__", cfg.MAPBOX_TOKEN)
        .replace("__CENTERLON__", str(cfg.CENTER_LON))
        .replace("__CENTERLAT__", str(cfg.CENTER_LAT))
        .replace("__ZOOM__", str(cfg.DEFAULT_ZOOM))
        .replace("__MODEORDER__", json.dumps(cfg.MODE_ORDER))
        .replace("__PEAKKEYS__", json.dumps(cfg.PEAK_KEYS))
        .replace("__WARDS_JSON__", wards.to_json())
        .replace("__SHEDS_JSON__", json.dumps(sheds))
        .replace("__SUMMARY_JSON__", json.dumps(summary)))

with open(OUT_HTML, "w", encoding="utf-8") as f:
    f.write(html)
shutil.copyfile(OUT_HTML, DOCS_HTML)
print(f"Wrote viewer: {OUT_HTML}  ({os.path.getsize(OUT_HTML):,} bytes)")
print(f"Copied to GitHub Pages dir: {DOCS_HTML}")
