"""
Stage 1b — CHECKPOINT: render ward boundaries + sample points for review.

Builds a self-contained HTML map so you can eyeball coverage BEFORE spending
any TomTom API calls. If the point layout looks wrong (too sparse/dense, points
clustered oddly), tweak the thresholds in 01_prep_wards_and_points.py and re-run
01 -> 01b until it looks right. Only then proceed to 02_fetch_reachable_range.py.

Output: output/<short>_review_points_map.html
"""

import os
import json
import geopandas as gpd

from config_loader import cfg

SHORT = cfg.CITY_SHORT.lower()
WARDS  = os.path.join(cfg.WARDS_DIR, f"{SHORT}_wards_clean.geojson")
POINTS = os.path.join(cfg.WARDS_DIR, f"{SHORT}_sample_points.geojson")
OUT_HTML = os.path.join(cfg.OUTPUT_DIR, f"{SHORT}_review_points_map.html")

wards = gpd.read_file(WARDS)
pts   = gpd.read_file(POINTS)

n_bands, n_pts = len(cfg.BANDS_SEC), len(pts)
implied = cfg.implied_calls(n_pts)
n_car_scen = len(cfg.car_scenarios())
calls_label = (f"{n_pts} pts &times; (car&times;{n_car_scen} + bike + walk) &times; {n_bands} bands")
ppw = pts.groupby("ward_no").size()

WARDS_JSON = wards.to_json()
POINTS_JSON = pts.to_json()

HTML = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{cfg.CITY_NAME} — Ward Sample Points (Review)</title>
<meta name="viewport" content="initial-scale=1,maximum-scale=1,user-scalable=no">
<script src="https://api.mapbox.com/mapbox-gl-js/v3.3.0/mapbox-gl.js"></script>
<link href="https://api.mapbox.com/mapbox-gl-js/v3.3.0/mapbox-gl.css" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:Arial,sans-serif;display:flex;height:100vh;overflow:hidden}}
#sidebar{{width:330px;flex-shrink:0;background:#f8f9fa;border-right:3px solid #0078C1;
  display:flex;flex-direction:column;box-shadow:2px 0 10px rgba(0,0,0,.12);z-index:1}}
#hdr{{background:#0078C1;color:#fff;padding:15px 18px}}
#hdr h1{{font-size:15px;margin-bottom:4px}}
#hdr p{{font-size:10.5px;opacity:.9;line-height:1.45}}
#body{{flex:1;overflow-y:auto;padding:14px 16px}}
.stat{{background:#fff;border:1px solid #e0e7f0;border-radius:7px;padding:10px 12px;margin-bottom:9px}}
.stat .big{{font-size:22px;font-weight:700;color:#0078C1}}
.stat .lbl{{font-size:10.5px;color:#666;text-transform:uppercase;letter-spacing:.5px}}
.warn{{background:#fff7e6;border-left:3px solid #f5a623;padding:9px 11px;border-radius:6px;
  font-size:10.5px;color:#664d00;line-height:1.5;margin-top:6px}}
.gate{{background:#eafbea;border-left:3px solid #1A9641;padding:9px 11px;border-radius:6px;
  font-size:10.5px;color:#14521f;line-height:1.5;margin-top:9px}}
h3{{font-size:10px;color:#555;font-weight:700;text-transform:uppercase;letter-spacing:.6px;
  margin:14px 0 8px}}
.bm-btns{{display:flex;gap:5px;flex-wrap:wrap}}
.bm-btn{{font-size:11px;padding:5px 11px;border:1px solid #0078C1;border-radius:4px;cursor:pointer;
  background:#fff;color:#0078C1}}
.bm-btn.active{{background:#0078C1;color:#fff}}
#map{{flex:1}}
.mapboxgl-popup-content{{font-family:Arial;font-size:12px;padding:10px 12px;border-radius:7px}}
.pu-name{{font-size:13px;font-weight:700;color:#163378}}
.pu-row{{font-size:11px;color:#555;margin-top:3px}}
</style>
</head>
<body>
<div id="sidebar">
  <div id="hdr">
    <h1>{cfg.CITY_NAME} &mdash; Sample Points Review</h1>
    <p>Checkpoint before any API calls. Confirm every ward is represented and points sit
       in sensible places.</p>
  </div>
  <div id="body">
    <div class="stat"><div class="big">{len(wards)}</div><div class="lbl">Wards</div></div>
    <div class="stat"><div class="big">{n_pts}</div><div class="lbl">Sample points</div></div>
    <div class="stat"><div class="big">{implied:,}</div>
      <div class="lbl">Implied API calls<br>{calls_label}</div></div>
    <div class="{'warn' if implied > cfg.FREE_TIER_DAILY else 'gate'}">
      TomTom free tier = {cfg.FREE_TIER_DAILY:,} requests/day.
      { 'This run EXCEEDS one day &mdash; 02 will split across days via its cache.'
        if implied > cfg.FREE_TIER_DAILY else 'This run fits within a single day.' }
    </div>
    <h3>Points per ward</h3>
    <div class="stat" style="font-size:12px;color:#333">
      min {int(ppw.min())} &middot; max {int(ppw.max())} &middot; mean {ppw.mean():.2f}
    </div>
    <h3>Basemap</h3>
    <div class="bm-btns">
      <button class="bm-btn active" data-sty="light-v11">Light</button>
      <button class="bm-btn" data-sty="streets-v12">Streets</button>
      <button class="bm-btn" data-sty="satellite-streets-v12">Satellite</button>
    </div>
    <div class="gate">
      <strong>Next step:</strong> if this looks right, run
      <code>02_fetch_reachable_range.py</code>. Otherwise adjust the sampling rule in
      <code>01_prep_wards_and_points.py</code> and re-run 01 &rarr; 01b.
    </div>
  </div>
</div>
<div id="map"></div>
<script>
mapboxgl.accessToken = '{cfg.MAPBOX_TOKEN}';
const WARDS = {WARDS_JSON};
const POINTS = {POINTS_JSON};

const map = new mapboxgl.Map({{
  container:'map', style:'mapbox://styles/mapbox/light-v11',
  center:[{cfg.CENTER_LON}, {cfg.CENTER_LAT}], zoom:{cfg.DEFAULT_ZOOM}
}});
map.addControl(new mapboxgl.NavigationControl(),'top-right');

function addLayers() {{
  map.addSource('wards', {{type:'geojson', data:WARDS}});
  map.addLayer({{id:'ward-fill', type:'fill', source:'wards',
    paint:{{'fill-color':'#0078C1','fill-opacity':0.06}}}});
  map.addLayer({{id:'ward-line', type:'line', source:'wards',
    paint:{{'line-color':'#0078C1','line-width':1,'line-opacity':0.55}}}});
  map.addLayer({{id:'ward-label', type:'symbol', source:'wards',
    layout:{{'text-field':['get','ward_name'],'text-size':10,
      'text-font':['Open Sans Regular','Arial Unicode MS Regular']}},
    paint:{{'text-color':'#15406b','text-halo-color':'#fff','text-halo-width':1.4,
      'text-opacity':['step',['zoom'],0,12,0.9]}}}});

  map.addSource('pts', {{type:'geojson', data:POINTS}});
  map.addLayer({{id:'pt-dot', type:'circle', source:'pts',
    paint:{{'circle-radius':['interpolate',['linear'],['zoom'],9,3.2,14,6],
      'circle-color':'#D7191C','circle-stroke-color':'#fff','circle-stroke-width':1.3,
      'circle-opacity':0.92}}}});

  map.on('mouseenter','pt-dot', e => {{
    map.getCanvas().style.cursor='pointer';
    const p = e.features[0].properties;
    new mapboxgl.Popup({{closeButton:false, offset:8}}).setLngLat(e.lngLat)
      .setHTML(`<div class="pu-name">${{p.ward_name}}</div>
        <div class="pu-row">Ward ${{p.ward_no}} &middot; point ${{p.pt_id}}</div>
        <div class="pu-row">${{p.n_pts_in_ward}} point(s) in this ward</div>
        <div class="pu-row">source: ${{p.src}}</div>`).addTo(map);
  }});
  map.on('mouseleave','pt-dot', () => {{
    map.getCanvas().style.cursor='';
    document.querySelectorAll('.mapboxgl-popup').forEach(el=>el.remove());
  }});
}}
map.on('load', addLayers);

document.querySelectorAll('.bm-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('.bm-btn').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    map.setStyle('mapbox://styles/mapbox/'+btn.dataset.sty);
    map.once('style.load', addLayers);
  }});
}});
</script>
</body>
</html>"""

with open(OUT_HTML, "w", encoding="utf-8") as f:
    f.write(HTML)

print(f"Wrote review map: {OUT_HTML}  ({os.path.getsize(OUT_HTML):,} bytes)")
print(f"  {len(wards)} wards, {n_pts} points, {implied:,} implied API calls")
print("Open it in a browser to review BEFORE running 02_fetch_reachable_range.py.")
