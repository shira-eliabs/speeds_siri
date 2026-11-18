"""Minimal geometry helpers copied for package-local speed calc (no osmnx)."""

from __future__ import annotations

from typing import List

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import KDTree
from shapely.geometry import LineString, Point


def lon_lat_to_geom(
    df: pd.DataFrame,
    lon_column: str = "VehicleLocation_Longitude",
    lat_column: str = "VehicleLocation_Latitude",
) -> gpd.GeoDataFrame:
    wgs84_bounds = {
        "longitude": {"min": 33.28, "max": 35.90},
        "latitude": {"min": 29.404, "max": 33.424},
    }
    rule_1 = (df[lon_column] > wgs84_bounds["longitude"]["min"]) & (df[lon_column] < wgs84_bounds["longitude"]["max"])
    rule_2 = (df[lat_column] > wgs84_bounds["latitude"]["min"]) & (df[lat_column] < wgs84_bounds["latitude"]["max"])
    df = df.loc[rule_1 & rule_2].copy()
    df["geometry"] = gpd.points_from_xy(df[lon_column].values, df[lat_column].values, crs="epsg:4326")
    df = gpd.GeoDataFrame(df, crs=4326).to_crs("epsg:2039")
    return df


def remove_too_close_points(trip_df: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    points = np.array(list(zip(trip_df.geometry.x, trip_df.geometry.y)))
    tree = KDTree(points)
    result = list(tree.query_ball_point(points, 50))
    dict_neighbors = {}
    visited = list(range(len(points)))
    while len(visited) > 0:
        dict_neighbors[visited[0]] = result[visited[0]]
        visited = list(set(visited) - set(result[visited[0]]))
    return trip_df.iloc[list(dict_neighbors.keys())]


def cut_line_at_point(line: LineString, point: Point) -> List[LineString]:
    pdist = line.project(point)
    ldists = [line.project(Point(i)) for i in line.coords[:]]
    ldists.append(pdist)
    dists = np.array(sorted(ldists))
    return [
        LineString([line.interpolate(dists[dists <= pdist])][0]),
        LineString([line.interpolate(dists[dists >= pdist])][0]),
    ]


def find_nearest(array, value: float):
    array = np.asarray(array)
    idx = (np.abs(array - value)).argmin()
    return array[idx]
