"""
Map-match GTFS route shapes onto RTS street_net → sequenced route edges.

Builds a connected path on the directed street graph (not independent nearest snaps).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple, Union

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
from shapely.geometry import LineString, Point

from config import (
    MAPMATCH_AZIMUTH_MAX_DEG,
    MAPMATCH_SAMPLE_M,
    MAPMATCH_SEARCH_M,
    STREET_NET,
)


def _azimuth_deg(geom: LineString) -> float:
    coords = list(geom.coords)
    if len(coords) < 2:
        return 0.0
    x0, y0 = coords[0][:2]
    x1, y1 = coords[-1][:2]
    return float(np.degrees(np.arctan2(y1 - y0, x1 - x0)) % 360)


def _angle_diff_deg(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _sample_line(geom: LineString, step_m: float) -> List[Tuple[Point, float]]:
    """Return (point, azimuth_along_line_deg) samples."""
    if geom is None or geom.is_empty or geom.length == 0:
        return []
    length = geom.length
    dists = list(np.arange(0.0, length, step_m))
    if not dists or dists[-1] < length:
        dists.append(length)
    out = []
    for d in dists:
        p = geom.interpolate(d)
        d2 = min(d + max(step_m * 0.5, 1.0), length)
        p2 = geom.interpolate(d2)
        az = float(np.degrees(np.arctan2(p2.y - p.y, p2.x - p.x)) % 360)
        out.append((p, az))
    return out


class StreetNetMatcher:
    def __init__(
        self,
        street_net_path: Union[str, Path] = STREET_NET,
        search_m: float = MAPMATCH_SEARCH_M,
        sample_m: float = MAPMATCH_SAMPLE_M,
        azimuth_max_deg: float = MAPMATCH_AZIMUTH_MAX_DEG,
    ):
        self.search_m = search_m
        self.sample_m = sample_m
        self.azimuth_max_deg = azimuth_max_deg
        streets = gpd.read_parquet(street_net_path)
        if streets.crs is None or streets.crs.to_epsg() != 2039:
            streets = streets.to_crs(2039) if streets.crs else streets.set_crs(2039)
        streets["from"] = streets["from"].astype(str)
        streets["to"] = streets["to"].astype(str)
        streets = streets.loc[streets.geometry.notnull() & ~streets.geometry.is_empty].copy()
        streets["azimuth"] = streets.geometry.map(_azimuth_deg)
        streets["length"] = streets.geometry.length
        streets = streets.reset_index(drop=True)
        streets["_idx"] = streets.index
        self.streets = streets
        self.sindex = streets.sindex

        self.G = nx.DiGraph()
        edge_geom = {}
        for _, r in streets.iterrows():
            u, v = r["from"], r["to"]
            self.G.add_edge(u, v, length=float(r["length"]), idx=int(r["_idx"]))
            edge_geom[(u, v)] = r.geometry
        self.edge_geom = edge_geom

    def _nearest_edge(self, point: Point, az: float) -> Optional[Tuple[str, str, int]]:
        buf = point.buffer(self.search_m)
        cand_idx = list(self.sindex.intersection(buf.bounds))
        if not cand_idx:
            return None
        best = None
        best_score = None
        for i in cand_idx:
            row = self.streets.iloc[i]
            dist = row.geometry.distance(point)
            if dist > self.search_m:
                continue
            adiff = _angle_diff_deg(az, row["azimuth"])
            if adiff > self.azimuth_max_deg:
                continue
            score = dist + 0.5 * adiff
            if best_score is None or score < best_score:
                best_score = score
                best = (row["from"], row["to"], int(row["_idx"]))
        return best

    def match_route(
        self,
        geom: LineString,
        alt_num: str,
        makat: str = "",
        route_id=None,
    ) -> gpd.GeoDataFrame:
        samples = _sample_line(geom, self.sample_m)
        hits = []
        for p, az in samples:
            hit = self._nearest_edge(p, az)
            if hit:
                hits.append(hit)

        seq_edges: List[Tuple[str, str]] = []
        for u, v, _ in hits:
            if not seq_edges or seq_edges[-1] != (u, v):
                seq_edges.append((u, v))

        filled: List[Tuple[str, str]] = []
        for i, (u, v) in enumerate(seq_edges):
            if i == 0:
                filled.append((u, v))
                continue
            prev_u, prev_v = filled[-1]
            if prev_v == u:
                filled.append((u, v))
                continue
            try:
                path_nodes = nx.shortest_path(self.G, prev_v, u, weight="length")
                for a, b in zip(path_nodes[:-1], path_nodes[1:]):
                    if not filled or filled[-1] != (a, b):
                        filled.append((a, b))
                if not filled or filled[-1] != (u, v):
                    filled.append((u, v))
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                filled.append((u, v))

        rows = []
        for seq, (u, v) in enumerate(filled):
            eg = self.edge_geom.get((u, v))
            if eg is None:
                continue
            rows.append(
                {
                    "MAKAT": str(makat),
                    "ROUTE_ID": route_id if route_id is not None else alt_num,
                    "ALT_NUM": str(alt_num),
                    "from": u,
                    "to": v,
                    "seq": seq,
                    "edge_source": "mapmatch_gtfs",
                    "geometry": eg,
                }
            )
        if not rows:
            return gpd.GeoDataFrame(
                columns=["MAKAT", "ROUTE_ID", "ALT_NUM", "from", "to", "seq", "edge_source", "geometry"],
                crs=2039,
            )
        return gpd.GeoDataFrame(rows, crs=2039)


def mapmatch_alts(
    routes_gdf: gpd.GeoDataFrame,
    alt_nums: List[str],
    street_net_path: Union[str, Path] = STREET_NET,
    matcher: Optional[StreetNetMatcher] = None,
) -> gpd.GeoDataFrame:
    """Map-match a list of ALT_NUMs from routes_gdf onto street_net."""
    matcher = matcher or StreetNetMatcher(street_net_path)
    alt_set = set(str(a) for a in alt_nums)
    subset = routes_gdf.loc[routes_gdf["ALT_NUM"].astype(str).isin(alt_set)]
    parts = []
    for _, row in subset.iterrows():
        g = row.geometry
        if g is None or g.is_empty:
            continue
        if g.geom_type == "MultiLineString":
            g = max(g.geoms, key=lambda x: x.length)
        edges = matcher.match_route(
            g,
            alt_num=str(row["ALT_NUM"]),
            makat=str(row.get("MAKAT", row.get("Makat", ""))),
            route_id=str(row.get("route_id", row["ALT_NUM"])),
        )
        if len(edges):
            parts.append(edges)
    if not parts:
        return gpd.GeoDataFrame(
            columns=["MAKAT", "ROUTE_ID", "ALT_NUM", "from", "to", "seq", "edge_source", "geometry"],
            crs=2039,
        )
    return gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=2039)
