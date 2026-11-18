"""Deterministic GTFS shape → route_edges builder (content-addressed from/to)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional, Union

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString

from prep_gtfs import route_shapes_gdf


def _node_id(coord) -> str:
    """Same style as RTS streets_to_network_coords: node_ + rounded 2039 meters."""
    x, y = coord[0], coord[1]
    return "node_" + str([round(x, 3), round(y, 3)]).replace("[", "").replace("]", "").replace(" ", "")


def densify_linestring(geom: LineString, step_m: float = 50.0) -> list:
    """Return coordinates densified along the line at approximately step_m spacing."""
    if geom is None or geom.is_empty or geom.length == 0:
        return []
    length = geom.length
    dists = list(np.arange(0.0, length, step_m))
    if not dists or dists[-1] < length:
        dists.append(length)
    coords = []
    for d in dists:
        p = geom.interpolate(d)
        coords.append((p.x, p.y))
    # drop consecutive duplicates after rounding
    out = []
    prev = None
    for c in coords:
        key = (round(c[0], 3), round(c[1], 3))
        if key != prev:
            out.append(c)
            prev = key
    if len(out) < 2 and len(coords) >= 2:
        out = [coords[0], coords[-1]]
    return out


def routes_to_edges(
    routes_gdf: gpd.GeoDataFrame,
    step_m: float = 50.0,
) -> gpd.GeoDataFrame:
    """
    Split each route shape into sequenced edges with deterministic from/to node ids.

    Output columns match speed-layer expectations:
    MAKAT, ROUTE_ID, ALT_NUM, from, to, seq, geometry (+ route_desc, shape_id).
    """
    rows = []
    for _, route in routes_gdf.iterrows():
        geom = route.geometry
        if geom is None or geom.is_empty:
            continue
        if geom.geom_type == "MultiLineString":
            geom = max(geom.geoms, key=lambda g: g.length)
        coords = densify_linestring(geom, step_m=step_m)
        if len(coords) < 2:
            continue
        makat = str(route.get("MAKAT", route.get("Makat", "")))
        alt_num = str(route.get("ALT_NUM", route.get("route_id", "")))
        route_id = route.get("route_id", alt_num)
        route_desc = route.get("route_desc", "")
        shape_id = route.get("shape_id", "")
        for seq, (c0, c1) in enumerate(zip(coords[:-1], coords[1:])):
            edge_geom = LineString([c0, c1])
            if edge_geom.length <= 0:
                continue
            rows.append(
                {
                    "MAKAT": makat,
                    "ROUTE_ID": route_id,
                    "ALT_NUM": alt_num,
                    "route_desc": route_desc,
                    "shape_id": shape_id,
                    "from": _node_id(c0),
                    "to": _node_id(c1),
                    "seq": seq,
                    "geometry": edge_geom,
                }
            )
    if not rows:
        return gpd.GeoDataFrame(
            columns=["MAKAT", "ROUTE_ID", "ALT_NUM", "route_desc", "shape_id", "from", "to", "seq", "geometry"],
            crs=2039,
        )
    return gpd.GeoDataFrame(rows, crs=2039)


def build_route_edges_from_gtfs(
    zip_path: str,
    cluster_to_line_path: Optional[str] = None,
    step_m: float = 50.0,
    filter_line_type: Union[str, list, None] = None,
) -> gpd.GeoDataFrame:
    routes = route_shapes_gdf(zip_path, cluster_to_line_path, filter_line_type=filter_line_type)
    return routes_to_edges(routes, step_m=step_m)


def freeze_metadata(zip_path: str, cluster_to_line_path: Optional[str], step_m: float) -> dict:
    p = Path(zip_path)
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return {
        "gtfs_zip": str(p),
        "gtfs_sha256": h.hexdigest(),
        "cluster_to_line": cluster_to_line_path,
        "step_m": step_m,
        "crs": 2039,
        "node_round_decimals": 3,
    }


def write_month_freeze(
    zip_path: str,
    out_dir: Union[str, Path],
    cluster_to_line_path: Optional[str] = None,
    step_m: float = 50.0,
    year: int = 2026,
    month: int = 4,
) -> Path:
    """Build and write month-frozen route_edges parquet + metadata.json."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    edges = build_route_edges_from_gtfs(zip_path, cluster_to_line_path, step_m=step_m)
    out_parquet = out_dir / f"route_edges_gtfs_{year}{month:02d}.parquet"
    meta_path = out_dir / f"route_edges_gtfs_{year}{month:02d}_meta.json"
    edges.to_parquet(out_parquet, index=False)
    meta = freeze_metadata(zip_path, cluster_to_line_path, step_m)
    meta["n_edges"] = int(len(edges))
    meta["n_routes"] = int(edges["ALT_NUM"].nunique()) if len(edges) else 0
    meta["out_parquet"] = str(out_parquet)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return out_parquet
