"""
Shared configuration for the multimodal "sheds" pipeline.

Everything city-specific lives in config/city_config.json — this module just
loads it, resolves paths, derives the metric (UTM) CRS from the city centre,
and exposes the mode/colour/band constants used by every other script.

To run the tool for a DIFFERENT city, you should only ever need to edit
config/city_config.json (and set the TOMTOM_API_KEY environment variable).

Import this from the numbered scripts:  `import importlib; cfg = importlib...`
or simply run them from the scripts/ folder (they do `import config_loader`).
Because the file is named 00_config.py (not a valid module name), the other
scripts load it via a tiny helper; see _load_config() below for direct use.
"""

import os
import sys
import json

# Windows consoles default to cp1252 and choke on non-Latin-1 output; force UTF-8
# so prints (km², box-drawing, ward names) never crash a script.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

# ── Paths ───────────────────────────────────────────────────────────────────
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR    = os.path.dirname(SCRIPTS_DIR)
CONFIG_DIR  = os.path.join(BASE_DIR, "config")
DATA_DIR    = os.path.join(BASE_DIR, "data")
RAW_DIR     = os.path.join(DATA_DIR, "raw")
WARDS_DIR   = os.path.join(DATA_DIR, "wards")
SHEDS_DIR   = os.path.join(DATA_DIR, "sheds")
ANALYSIS_DIR= os.path.join(DATA_DIR, "analysis")
OUTPUT_DIR  = os.path.join(BASE_DIR, "output")
DOCS_DIR    = os.path.join(BASE_DIR, "docs")

for _d in (RAW_DIR, WARDS_DIR, SHEDS_DIR, ANALYSIS_DIR, OUTPUT_DIR, DOCS_DIR):
    os.makedirs(_d, exist_ok=True)

# ── City config ─────────────────────────────────────────────────────────────
# Allow an alternate config via the CITY_CONFIG env var (a filename in config/
# or an absolute path), so the same code runs any city.
_cfg_name = os.environ.get("CITY_CONFIG", "city_config.json")
CONFIG_PATH = _cfg_name if os.path.isabs(_cfg_name) else os.path.join(CONFIG_DIR, _cfg_name)
with open(CONFIG_PATH, encoding="utf-8") as _f:
    CITY = json.load(_f)

CITY_NAME   = CITY["city_name"]
CITY_SHORT  = CITY["city_short"]
WARDS_URL   = CITY["wards_geojson_url"]
WARDS_FILE  = os.path.join(RAW_DIR, CITY["wards_local_filename"])
WARD_NO_F   = CITY["ward_no_field"]
WARD_NAME_F = CITY["ward_name_field"]
ZONE_F      = CITY.get("zone_field")
CENTER_LAT, CENTER_LON = CITY["center_latlon"]
DEFAULT_ZOOM = CITY.get("default_zoom", 11)
BANDS_MIN   = CITY["bands_min"]
BANDS_SEC   = [int(m) * 60 for m in BANDS_MIN]
ATTRIBUTION = CITY.get("attribution", "")
SAMPLE_POINTS = CITY.get("sample_points", [])   # [[id, name, lon, lat], ...] for quick exercises

# ── Departure scenarios ─────────────────────────────────────────────────────
# Only the car mode is traffic-sensitive, so it gets multiple scenarios
# (AM peak, PM peak, and a free-flow baseline). The peak HOURS are discovered
# empirically by 01c_probe_peak.py and written to departures_resolved.json;
# walk & bike are traffic-independent and use a single scenario.
DEPARTURES        = CITY["departures"]
PROBE             = DEPARTURES["probe"]
DEPARTURES_RESOLVED = os.path.join(ANALYSIS_DIR, "departures_resolved.json")

# Which car scenarios count as "peak" (worst-case), and the light baseline used
# to measure the congestion penalty.
PEAK_KEYS    = ["am_peak", "pm_peak"]
BASELINE_KEY = "free_flow"

def _iso(hhmm):
    return f"{PROBE['probe_date']}T{hhmm}:00{PROBE['tz_offset']}"

def car_scenarios():
    """Car departure scenarios: AM peak, PM peak, midday (all traffic-on at a
    departAt) plus a free-flow baseline (traffic off). Uses empirically resolved
    times if 01c has run, else config fallbacks so previews work pre-probe.
    """
    resolved = {}
    if os.path.exists(DEPARTURES_RESOLVED):
        with open(DEPARTURES_RESOLVED, encoding="utf-8") as f:
            resolved = json.load(f)
    am = resolved.get("am_peak", _iso(DEPARTURES["fallback_am_peak"]))
    pm = resolved.get("pm_peak", _iso(DEPARTURES["fallback_pm_peak"]))
    md = resolved.get("midday",  _iso(DEPARTURES["fallback_midday"]))
    return [
        {"key": "am_peak",   "label": "AM peak",   "traffic": True,  "departAt": am},
        {"key": "pm_peak",   "label": "PM peak",   "traffic": True,  "departAt": pm},
        {"key": "midday",    "label": "Midday",    "traffic": True,  "departAt": md},
        {"key": "free_flow", "label": "Free-flow", "traffic": False, "departAt": None},
    ]

# Single, time-independent scenario for non-car modes.
_NA_SCENARIO = [{"key": "na", "label": "", "traffic": False, "departAt": None}]

def scenarios_for_mode(mode):
    return car_scenarios() if mode == "car" else _NA_SCENARIO

def implied_calls(n_points):
    """Total reachable-range calls for n_points (excludes the small probe run)."""
    per_point = sum(len(scenarios_for_mode(m)) for m in MODE_ORDER) * len(BANDS_SEC)
    return n_points * per_point

# ── CRS ─────────────────────────────────────────────────────────────────────
WGS84 = 4326

def utm_epsg(lat, lon):
    """EPSG code of the UTM zone containing (lat, lon)."""
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone

WORK_CRS = utm_epsg(CENTER_LAT, CENTER_LON)   # metric CRS for area math

# ── Travel modes (TomTom Reachable Range) ───────────────────────────────────
# traffic/departAt are only meaningful for car; walk & bike use fixed
# average-speed profiles.
# Bauhaus-classic inks (validated for CVD separation + lightness band):
# deep signal red / warm gold / printer's ultramarine.
MODES = {
    "car":        {"label": "Drive", "travelMode": "car",        "color": "#B21F24"},
    "bicycle":    {"label": "Bicycle",  "travelMode": "bicycle",    "color": "#D9A404"},
    "pedestrian": {"label": "Walk",  "travelMode": "pedestrian", "color": "#1E56C8"},
}
MODE_ORDER = ["car", "bicycle", "pedestrian"]

# Average speeds for the network-computed active modes (km/h). Car comes from
# TomTom; bike/walk sheds are computed on the OSM network at these fixed speeds.
SPEEDS_KMPH = CITY.get("speeds_kmph", {"pedestrian": 4.8, "bicycle": 15})

def speed_mps(mode):
    return SPEEDS_KMPH[mode] * 1000.0 / 3600.0

# ── Road intersections (OSM Overpass) ───────────────────────────────────────
_RI = CITY.get("road_intersections", {})
# Primary endpoint from config, then public mirrors as fallbacks (the .de host
# is frequently overloaded and drops connections).
OVERPASS_URLS = [_RI.get("overpass_url", "https://overpass-api.de/api/interpreter")] + [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]
# De-dupe, preserve order
OVERPASS_URLS = list(dict.fromkeys(OVERPASS_URLS))
MAJOR_ROAD_CLASSES = _RI.get("major_road_classes",
                             ["motorway", "trunk", "primary", "secondary", "tertiary"])
HTTP_UA = "multimodal-sheds/1.0 (geospatial planning tool)"

# ── API ─────────────────────────────────────────────────────────────────────
TOMTOM_BASE = "https://api.tomtom.com/routing/1/calculateReachableRange/{lat},{lon}/json"
SLEEP_SEC   = 1.2          # politeness delay between live calls (~QPS guard)
FREE_TIER_DAILY = 2500     # TomTom free-tier non-tile requests/day

def tomtom_key():
    """Return the TomTom API key from the environment, or raise a clear error."""
    key = os.environ.get("TOMTOM_API_KEY")
    if not key:
        raise SystemExit(
            "TOMTOM_API_KEY environment variable is not set.\n"
            "  PowerShell:  $env:TOMTOM_API_KEY = 'your-key-here'\n"
            "  bash:        export TOMTOM_API_KEY='your-key-here'\n"
            "Get a free key at https://developer.tomtom.com/ (2,500 requests/day)."
        )
    return key

# Public Mapbox token used ONLY as a basemap/renderer in the HTML viewers
# (no Mapbox routing/isochrone calls are made — all shed data comes from TomTom).
MAPBOX_TOKEN = "pk.eyJ1Ijoic3NpdmFrdW1hcjEzIiwiYSI6ImNtbXV3NnAzaDBjMzgycnB4eG8yOG5tN2YifQ.qTKfHq8BVySJNNjelKOGIw"


if __name__ == "__main__":
    print(f"City        : {CITY_NAME} ({CITY_SHORT})")
    print(f"Centre      : {CENTER_LAT}, {CENTER_LON}")
    print(f"Work CRS    : EPSG:{WORK_CRS} (UTM)")
    print(f"Bands       : {BANDS_MIN} min  -> {BANDS_SEC} sec")
    print(f"Modes       : {[MODES[m]['label'] for m in MODE_ORDER]}")
    print(f"Car scenarios: {[s['key'] for s in car_scenarios()]}")
    print(f"Wards file  : {WARDS_FILE}")
    print(f"TomTom key  : {'set' if os.environ.get('TOMTOM_API_KEY') else 'NOT set'}")
