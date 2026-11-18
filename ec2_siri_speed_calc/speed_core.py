"""
Package-local trip→edge speed core (adapted from parent speed_layer_from_siri.py).

Used when parent module dependencies (duckdb, etc.) are unavailable.
Does not modify any scripts outside this folder.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import groupby as itter_groupby
from operator import itemgetter
from typing import Optional, Tuple

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString

from geometry_tools import cut_line_at_point, find_nearest, remove_too_close_points


def find_relevant_edge_seq(trip_df: gpd.GeoDataFrame, edges_files_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    def _find_relevant_edge_seq(edges_files_gdf, trip_df):
        trip_indx, edge_indx = edges_files_gdf.sindex.nearest(trip_df.geometry, return_all=True, max_distance=200)
        if "cumulative_dist" not in edges_files_gdf.columns:
            edges_files_gdf = edges_files_gdf.copy()
            edges_files_gdf["cumulative_dist"] = edges_files_gdf.length.cumsum()
        gps_to_edge_dict = defaultdict(list)
        for x, y in zip(trip_indx, edge_indx):
            gps_to_edge_dict[x].append(y)
        for x, y in gps_to_edge_dict.items():
            if len(y) > 1:
                dist_gps = int(trip_df.iloc[x]["MonitoredCall_DistanceFromStop"])
                dists_dict = {edges_files_gdf.iloc[indx]["cumulative_dist"]: indx for indx in y}
                nearest_dist = find_nearest(list(dists_dict.keys()), int(dist_gps))
                gps_to_edge_dict[x] = dists_dict.get(nearest_dist)
            else:
                gps_to_edge_dict[x] = y[0]
        missing_keys = set(trip_df.index) - set(gps_to_edge_dict.keys())
        for k in missing_keys:
            gps_to_edge_dict[k] = None
        return gps_to_edge_dict, edge_indx

    edges_files_gdf = edges_files_gdf.copy()
    gps_to_edge_dict, edge_indx = _find_relevant_edge_seq(edges_files_gdf, trip_df)
    trip_df = trip_df.copy()
    trip_df["edge_seq"] = list(gps_to_edge_dict.values())

    index_to_seq = dict(zip(range(0, len(edges_files_gdf.index)), edges_files_gdf.index))
    edges_files_gdf["seq_1"] = edges_files_gdf.index

    edges_values1 = edges_files_gdf.loc[filter(None, gps_to_edge_dict.values())]
    edges_values1 = edges_values1.filter(["seq", "cumulative_dist", "geometry"]).rename(
        columns={"seq": "closest_edge_seq1", "cumulative_dist": "cumulative_dist1", "geometry": "closest_edge_geom1"}
    )
    edges_values1 = edges_values1.reset_index(drop=True)
    trip_df = trip_df.merge(edges_values1, left_on="edge_seq", right_on="closest_edge_seq1", how="left")

    mask = (trip_df["cumulative_dist1"] - trip_df["MonitoredCall_DistanceFromStop"]) <= 2000
    trip_df.loc[mask, ["closest_edge_geom", "closest_edge_seq"]] = trip_df.loc[
        mask, ["closest_edge_geom1", "closest_edge_seq1"]
    ].values

    if len(trip_df.loc[trip_df["closest_edge_seq"].isnull()]) > 0:
        not_missing_trip_df = trip_df.loc[trip_df["closest_edge_seq"].notnull()]
        missing_trip_df = trip_df.loc[trip_df["closest_edge_seq"].isnull()].reset_index()
        edge_indx = [index_to_seq[x] for x in edge_indx]
        edges_files_gdf_part = edges_files_gdf.loc[
            list(set(edges_files_gdf.index) - set(missing_trip_df["closest_edge_seq1"]))
        ]
        index_to_seq = dict(zip(range(0, len(edges_files_gdf_part.index)), edges_files_gdf_part.index))
        gps_to_edge_dict2, _ = _find_relevant_edge_seq(edges_files_gdf_part, missing_trip_df.reset_index())
        gps_to_edge_dict2 = {k: index_to_seq[y] if y else None for k, y in gps_to_edge_dict2.items()}
        gps_to_edge_dict2 = dict(sorted(gps_to_edge_dict2.items()))
        gps_to_edge_dict2 = [
            (
                edges_files_gdf_part.loc[[y]][["seq_1", "cumulative_dist", "geometry"]].values.flatten().tolist()
                if y
                else [None, None, None]
            )
            for y in gps_to_edge_dict2.values()
        ]
        edges_values2 = pd.DataFrame(
            gps_to_edge_dict2, columns=["closest_edge_seq2", "cumulative_dist2", "closest_edge_geom2"]
        )
        missing_trip_df = missing_trip_df.merge(edges_values2, left_index=True, right_index=True)
        missing_trip_df.loc[
            (missing_trip_df["cumulative_dist2"] - missing_trip_df["MonitoredCall_DistanceFromStop"]) <= 2000,
            "closest_edge_geom",
        ] = missing_trip_df["closest_edge_geom2"]
        missing_trip_df.loc[
            (missing_trip_df["cumulative_dist2"] - missing_trip_df["MonitoredCall_DistanceFromStop"]) <= 2000,
            "closest_edge_seq",
        ] = missing_trip_df["closest_edge_seq2"]
        missing_trip_df.index = missing_trip_df["index"]
        trip_df = gpd.GeoDataFrame(pd.concat([not_missing_trip_df, missing_trip_df]), crs=2039)
        trip_df = trip_df.sort_index()

    trip_df = trip_df.drop(
        columns=[
            "closest_edge_seq1",
            "cumulative_dist1",
            "closest_edge_geom1",
            "closest_edge_seq2",
            "cumulative_dist2",
            "closest_edge_geom2",
            "index",
        ],
        errors="ignore",
    )
    return trip_df


def filter_too_far_gps(route_geom: LineString, trip_df: gpd.GeoDataFrame) -> Optional[gpd.GeoDataFrame]:
    trip_df = trip_df.reset_index(drop=True)
    distances = trip_df.geometry.distance(route_geom)
    good_mask = distances <= 200
    bad_mask = ~good_mask
    if not good_mask.any():
        return None
    good_idx = np.flatnonzero(good_mask.to_numpy())
    bad_idx = np.flatnonzero(bad_mask.to_numpy())
    if bad_idx.size == 0:
        return trip_df
    trip_df = trip_df.iloc[good_idx]
    sequence_missing = [
        list(map(itemgetter(1), g)) for k, g in itter_groupby(enumerate(bad_idx), lambda i_x: i_x[0] - i_x[1])
    ]
    sequence_missing = [x for x in sequence_missing if len(x) > 4]
    sequence_missing = [x for x in sequence_missing if min(x) < max(good_idx)]
    if len(sequence_missing) > 0:
        return None
    return trip_df


def duration_on_edges(trip_df: gpd.GeoDataFrame, edges_files_gdf: gpd.GeoDataFrame) -> Optional[gpd.GeoDataFrame]:
    trip_df = find_relevant_edge_seq(trip_df, edges_files_gdf)
    trip_df = trip_df.dropna(subset=["closest_edge_seq"])
    if not all(trip_df.index == trip_df.sort_values(by=["closest_edge_seq", "RecordedAtTime"]).index):
        return None
    trip_df = trip_df.copy()
    trip_df["time_diff_seconds"] = trip_df["RecordedAtTime"].diff().dt.seconds

    def _duration_between_2_gps_calls_to_edges(trip_df, i, edges_files_gdf):
        seq_start = trip_df.iat[i, trip_df.columns.get_loc("closest_edge_seq")]
        edge_start = trip_df.iat[i, trip_df.columns.get_loc("closest_edge_geom")]
        gps_start = trip_df.iat[i, trip_df.columns.get_loc("geometry")]
        hour = trip_df.iat[i, trip_df.columns.get_loc("hour")]
        seq_end = trip_df.iat[i + 1, trip_df.columns.get_loc("closest_edge_seq")]
        edge_end = trip_df.iat[i + 1, trip_df.columns.get_loc("closest_edge_geom")]
        gps_end = trip_df.iat[i + 1, trip_df.columns.get_loc("geometry")]
        time_diff_seconds = trip_df.iat[i + 1, trip_df.columns.get_loc("time_diff_seconds")]
        seq_min, seq_max = sorted((seq_start, seq_end))
        edges_relevant = edges_files_gdf.loc[seq_min:seq_max].copy()
        if "seq" not in edges_relevant.columns:
            edges_relevant["seq"] = edges_relevant.index
        geom = edges_relevant.geometry.to_numpy()
        geom[0] = cut_line_at_point(edge_start, gps_start)[1]
        n_edges = len(geom)
        if n_edges > 1:
            geom[n_edges - 1] = cut_line_at_point(edge_end, gps_end)[0]
        else:
            geom[0] = cut_line_at_point(geom[0], gps_end)[0]
        edges_relevant["geometry"] = geom
        lengths = np.asarray(edges_relevant.length, dtype=np.float64)
        length_total = float(lengths.sum())
        if length_total == 0 or not np.isfinite(time_diff_seconds) or time_diff_seconds <= 0:
            edges_relevant["duration_sec"] = (
                time_diff_seconds / n_edges if np.isfinite(time_diff_seconds) and n_edges else 0.0
            )
            kmph = None
        else:
            edges_relevant["duration_sec"] = lengths / length_total * time_diff_seconds
            kmph = (length_total * 3.6) / time_diff_seconds
            if kmph > 120:
                kmph = None
        edges_relevant["speed_kmh"] = kmph
        edges_relevant["hour"] = hour
        return edges_relevant

    route_edges_with_duration = [
        _duration_between_2_gps_calls_to_edges(trip_df, i, edges_files_gdf) for i in range(len(trip_df) - 1)
    ]
    if len(route_edges_with_duration) > 0:
        route_edges_with_duration = pd.concat(route_edges_with_duration, ignore_index=True)
        route_edges_with_duration["start_hour"] = trip_df.iloc[0]["start_hour"]
        return route_edges_with_duration
    return None
