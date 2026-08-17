"""
Pluggable active-mode shed provider: network isochrones on the OSM graph.

Builds a routing graph from OpenStreetMap (Overpass) for a mode + bounding box,
then computes reachable-area polygons at a FIXED average speed via Dijkstra with
a distance cutoff (distance = speed x time). The isochrone polygon is the
concave hull of the reachable nodes (+ a small buffer to bridge the network).

Pure Python: networkx + scipy KDTree + shapely.concave_hull. No API key, no Java.
This is the swap-point if you later move to r5py / Valhalla / GraphHopper —
keep the isochrone(bundle, lon, lat, band_sec, mode) signature.
"""

import os
import json
import time
import pickle

import requests
import numpy as np
import networkx as nx
from scipy.spatial import cKDTree
from shapely.geometry import MultiPoint, Point
from shapely import concave_hull
from pyproj import Transformer

from config_loader import cfg

# Highway classes traversable per mode (motorways excluded for both).
NETWORK_FILTERS = {
    "pedestrian": ["footway", "path", "pedestrian", "steps", "living_street",
                   "residential", "service", "unclassified", "tertiary", "tertiary_link",
                   "secondary", "secondary_link", "primary", "primary_link",
                   "trunk", "trunk_link", "road", "track", "cycleway", "busway"],
    "bicycle": ["cycleway", "path", "living_street", "residential", "service",
                "unclassified", "tertiary", "tertiary_link", "secondary", "secondary_link",
                "primary", "primary_link", "trunk", "trunk_link", "road", "track", "busway"],
}

_TF = Transformer.from_crs(cfg.WGS84, cfg.WORK_CRS, always_xy=True)


def _overpass(query):
    headers = {"User-Agent": cfg.HTTP_UA}
    last = None
    for attempt in range(1, 5):
        url = cfg.OVERPASS_URLS[(attempt - 1) % len(cfg.OVERPASS_URLS)]
        try:
            r = requests.post(url, data={"data": query}, headers=headers, timeout=300)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            print(f"    overpass {url} failed: {e}")
            time.sleep(5 * attempt)
    raise SystemExit(f"Overpass failed across mirrors: {last}")


def fetch_network(mode, bbox, tag):
    """bbox = (south, west, north, east). Returns raw OSM json (cached)."""
    cache = os.path.join(cfg.RAW_DIR, f"osm_net_{mode}_{tag}.json")
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    classes = "|".join(NETWORK_FILTERS[mode])
    s, w, n, e = bbox
    query = (f"[out:json][timeout:300];"
             f'(way["highway"~"^({classes})$"]({s},{w},{n},{e}););'
             f"out body;>;out skel qt;")
    print(f"  Overpass {mode} network bbox=({s:.3f},{w:.3f},{n:.3f},{e:.3f}) ...")
    data = _overpass(query)
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(data, f)
    print(f"    cached {len(data.get('elements', []))} elements -> {cache}")
    return data


def build_bundle(mode, bbox, tag):
    """Return a dict bundle {G, ids, xy, kdtree} (graph cached via pickle)."""
    pkl = os.path.join(cfg.RAW_DIR, f"osm_graph_{mode}_{tag}.pkl")
    if os.path.exists(pkl):
        with open(pkl, "rb") as f:
            data = pickle.load(f)
        data["kdtree"] = cKDTree(data["xy"])
        return data

    osm = fetch_network(mode, bbox, tag)
    lonlat = {}
    ways = []
    for el in osm.get("elements", []):
        if el["type"] == "node":
            lonlat[el["id"]] = (el["lon"], el["lat"])
        elif el["type"] == "way" and "nodes" in el:
            ways.append(el["nodes"])

    # project node coords
    ids = [nid for nid in lonlat]
    lons = np.array([lonlat[i][0] for i in ids])
    lats = np.array([lonlat[i][1] for i in ids])
    xs, ys = _TF.transform(lons, lats)
    xy_by_id = {nid: (float(xs[k]), float(ys[k])) for k, nid in enumerate(ids)}

    G = nx.Graph()
    for nodes in ways:
        for a, b in zip(nodes[:-1], nodes[1:]):
            if a in xy_by_id and b in xy_by_id and a != b:
                ax, ay = xy_by_id[a]; bx, by = xy_by_id[b]
                G.add_edge(a, b, length=((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5)

    # keep only the largest connected component so origins never snap onto an
    # isolated fragment (which would yield a near-zero isochrone)
    if G.number_of_nodes():
        giant = max(nx.connected_components(G), key=len)
        G = G.subgraph(giant).copy()

    g_ids = list(G.nodes)
    xy = np.array([xy_by_id[i] for i in g_ids])
    bundle = {"mode": mode, "ids": g_ids, "xy": xy, "G": G}
    with open(pkl, "wb") as f:
        pickle.dump(bundle, f)
    bundle["kdtree"] = cKDTree(xy)
    print(f"  graph[{mode}/{tag}]: {G.number_of_nodes():,} nodes, {G.number_of_edges():,} edges")
    return bundle


# Hull tightness: lower ratio = more concave (hugs the reachable frontier).
# buffer bridges the gap between discrete network nodes.
HULL_RATIO = 0.12
HULL_BUFFER_M = 18

_TF_BACK = Transformer.from_crs(cfg.WORK_CRS, cfg.WGS84, always_xy=True)


def isochrone(bundle, lon, lat, band_sec, mode):
    """Reachable-area Polygon (EPSG:4326) at the fixed average speed for `mode`."""
    dist_m = cfg.speed_mps(mode) * band_sec
    ox, oy = _TF.transform(lon, lat)
    _, k = bundle["kdtree"].query([ox, oy])
    src = bundle["ids"][int(k)]

    lengths = nx.single_source_dijkstra_path_length(bundle["G"], src, cutoff=dist_m, weight="length")
    id_to_row = {nid: i for i, nid in enumerate(bundle["ids"])}
    pts = bundle["xy"][[id_to_row[n] for n in lengths]]

    if len(pts) < 3:
        hull = Point(ox, oy).buffer(max(dist_m * 0.5, 50))
    else:
        hull = concave_hull(MultiPoint([tuple(p) for p in pts]), ratio=HULL_RATIO)
        hull = hull.buffer(HULL_BUFFER_M)        # bridge between network nodes
    from shapely.ops import transform as shp_transform
    return shp_transform(lambda x, y, z=None: _TF_BACK.transform(x, y), hull)
