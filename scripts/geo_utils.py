"""Shared geometry helpers."""

import geopandas as gpd
from config_loader import cfg


def smooth_metric(g, r=150, s=80):
    """Smooth a polygon already in the metric CRS (r/s in metres).

    Morphological open (remove thin radial spikes) then close (fill jagged
    notches), then simplify. Area is roughly preserved.
    """
    g = g.buffer(0)
    out = g.buffer(-r).buffer(r).buffer(r).buffer(-r).simplify(s).buffer(0)
    if out.is_empty:                    # opening erased a tiny shed -> close+simplify only
        out = g.buffer(r).buffer(-r).simplify(s).buffer(0)
    return out


def smooth_car(poly_4326, r=150, s=80):
    """Smooth a raw TomTom car shed (EPSG:4326 in, EPSG:4326 out)."""
    gm = gpd.GeoSeries([poly_4326], crs=cfg.WGS84).to_crs(cfg.WORK_CRS).iloc[0]
    gm = smooth_metric(gm, r, s)
    return gpd.GeoSeries([gm], crs=cfg.WORK_CRS).to_crs(cfg.WGS84).iloc[0]
