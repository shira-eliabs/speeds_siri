"""Filename: speed_layer_from_siri.py
The script creates speed layer based on vm information from "Hasadna"
Author: Shira Zylbertal
Email: Shira@eliabs.com
Date: December 06, 2023

Reference files used:
    r"T:/90265_Jerusalem_Public_Transport/jer_rs_analysis/data/RTS_4326/RS+DBD_062023"
    r"T:/RTS/Israel/Israel_June_2023/route_edges"

Local server location:
    T:/GIS/Speeds/Hasadna/tools/speed_layer_from_siri.py"""

from itertools import groupby as itter_groupby
from operator import itemgetter
import geopandas as gpd
import pandas as pd
from glob import glob
from shapely.geometry import LineString
import numpy as np
import multiprocessing as mp
from tqdm import tqdm
from collections import defaultdict
from typing import Tuple, Union, Literal, get_args
import os
import warnings
from datetime import datetime
import utils
from pathlib import Path
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
import pyarrow.feather as feather
import pyarrow as pa
from pyarrow.lib import ArrowInvalid
import polars as pl
import shapely  # requires shapely >= 2.0


warnings.filterwarnings("ignore")
pd.set_option("display.max_columns", 500)
_SIRI_TYPES = Literal["hasadna", "realtime"]
_TIME_TYPES = Literal["weekend", "weekdays", "friday"]


def find_relevant_edge_seq(trip_df: gpd.GeoDataFrame, edges_files_gdf: gpd.GeoDataFrame) -> dict:
    """
    Find relevant edge sequences for a given trip based on their spatial proximity.

    Parameters
    ----------
    trip_df : gpd.GeoDataFrame
        DataFrame containing information about the trip.
    edges_files_gdf : gpd.GeoDataFrame
        GeoDataFrame containing information about edges.

    Returns
    -------
    dict
        Dictionary with relevant edge sequences for the given trip.
    """

    def _find_relevant_edge_seq(edges_files_gdf, trip_df):
        trip_indx, edge_indx = edges_files_gdf.sindex.nearest(trip_df.geometry, return_all=True, max_distance=200)

        if "cumulative_dist" not in edges_files_gdf.columns:
            edges_files_gdf["cumulative_dist"] = edges_files_gdf.length.cumsum()
        gps_to_edge_dict = defaultdict(list)
        for x, y in zip(trip_indx, edge_indx):
            gps_to_edge_dict[x].append(y)

        for x, y in gps_to_edge_dict.items():
            if len(y) > 1:
                dist_gps = int(trip_df.iloc[x]["MonitoredCall_DistanceFromStop"])
                dists_dict = {edges_files_gdf.iloc[indx]["cumulative_dist"]: indx for indx in y}
                nearest_dist = utils.find_nearest(list(dists_dict.keys()), int(dist_gps))
                indx_correct_order = dists_dict.get(nearest_dist)
                gps_to_edge_dict[x] = indx_correct_order
            else:
                gps_to_edge_dict[x] = y[0]

        missing_keys = set(trip_df.index) - set(gps_to_edge_dict.keys())
        for k in missing_keys:
            gps_to_edge_dict[k] = None
        return gps_to_edge_dict, edge_indx

    gps_to_edge_dict, edge_indx = _find_relevant_edge_seq(edges_files_gdf, trip_df)
    trip_df["edge_seq"] = gps_to_edge_dict.values()

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
        missing_trip_df = trip_df.loc[trip_df["closest_edge_seq"].isnull()]
        missing_trip_df = missing_trip_df.reset_index()
        edge_indx = [index_to_seq[x] for x in edge_indx]
        edges_files_gdf_part = edges_files_gdf.loc[
            list(set(edges_files_gdf.index) - set(missing_trip_df["closest_edge_seq1"]))
        ]
        index_to_seq = dict(zip(range(0, len(edges_files_gdf_part.index)), edges_files_gdf_part.index))

        gps_to_edge_dict2, _ = _find_relevant_edge_seq(edges_files_gdf_part, missing_trip_df.reset_index())
        gps_to_edge_dict2 = {k: index_to_seq[y] if y else None for k, y in gps_to_edge_dict2.items()}
        gps_to_edge_dict2 = dict(sorted(gps_to_edge_dict2.items()))
        # edges_files_gdf.loc[gps_to_edge_dict2.values()]
        gps_to_edge_dict2 = [
            (
                edges_files_gdf_part.loc[[y]][["seq_1", "cumulative_dist", "geometry"]].values.flatten().tolist()
                if y
                else [None, None, None]
            )
            for k, y in gps_to_edge_dict2.items()
        ]
        edges_values2 = pd.DataFrame(
            gps_to_edge_dict2, columns=["closest_edge_seq2", "cumulative_dist2", "closest_edge_geom2"]
        )

        # missing_trip_df = pd.concat([missing_trip_df, edges_values2], axis=1)
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

    # def dist_np(geom1, geom2):
    #     return np.linalg.norm(np.array((geom1.x, geom1.y)) - np.array((geom2.x, geom2.y)))

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


def filter_too_far_gps(route_geom: LineString, trip_df: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Filter GPS points that are too far from the given route.

    Parameters
    ----------
    route_geom : LineString
        Geometry of the route.
    trip_df : gpd.GeoDataFrame
        GeoDataFrame containing information about GPS points.

    Returns
    -------
    gpd.GeoDataFrame
        Filtered GeoDataFrame with GPS points.
    """

    trip_df = trip_df.reset_index(drop=True)
    distances = trip_df.geometry.distance(route_geom)
    good_mask = distances <= 200
    bad_mask = ~good_mask
    if not good_mask.any():
        return None

    # Indices of good and bad GPS points
    good_idx = np.flatnonzero(good_mask.to_numpy())
    bad_idx = np.flatnonzero(bad_mask.to_numpy())

    # If there are no bad points, just return the filtered df
    if bad_idx.size == 0:
        return trip_df

    # route_geom_gdf = gpd.GeoDataFrame(geometry=[route_geom], crs=2039)
    # [indx_gps, _], dist_from_route = route_geom_gdf.sindex.nearest(
    #     trip_df.geometry, return_all=False, return_distance=True
    # )

    # gps_dist_dict = dict(zip(indx_gps, dist_from_route))
    # bad_gps = [gps for gps, dist in gps_dist_dict.items() if dist > 200]
    # good_gps = list(set(trip_df.index) - set(bad_gps))
    trip_df = trip_df.iloc[good_idx]
    # trip_df = trip_df.reset_index(drop=True)

    sequence_missing = [
        list(map(itemgetter(1), g)) for k, g in itter_groupby(enumerate(bad_idx), lambda i_x: i_x[0] - i_x[1])
    ]

    sequence_missing = [x for x in sequence_missing if len(x) > 4]
    sequence_missing = [x for x in sequence_missing if min(x) < max(good_idx)]
    if len(sequence_missing) > 0:
        return None
    return trip_df


def duration_on_edges(trip_df: gpd.GeoDataFrame, edges_files_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Calculate duration per each edge on route edges.

    Parameters
    ----------
    trip_df : gpd.GeoDataFrame
        GeoDataFrame containing information about GPS points.
    edges_files_gdf : gpd.GeoDataFrame
        GeoDataFrame containing information about route edges.

    Returns
    -------
    gpd.GeoDataFrame
        GeoDataFrame with calculated durations.
    """
    trip_df = find_relevant_edge_seq(trip_df, edges_files_gdf)
    trip_df = trip_df.dropna(subset=["closest_edge_seq"])
    if not all(trip_df.index == trip_df.sort_values(by=["closest_edge_seq", "RecordedAtTime"]).index):
        return None
    trip_df = trip_df.copy()
    trip_df["time_diff_seconds"] = trip_df["RecordedAtTime"].diff().dt.seconds

    def _duration_between_2_gps_calls_to_edges(
        trip_df: gpd.GeoDataFrame, i: int, edges_files_gdf: gpd.GeoDataFrame
    ) -> dict:
        """
        Calculate duration between two consecutive GPS calls along the route edges and split it between edges.

        Parameters
        ----------
        trip_df : gpd.GeoDataFrame
            GeoDataFrame containing information about GPS points.
        i : int
            Index of the GPS point in the GeoDataFrame.
        edges_files_gdf : gpd.GeoDataFrame
            GeoDataFrame containing information about route edges.

        Returns
        -------
        dict
            Dictionary containing calculated duration per edge and additional information.
        """
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
        geom[0] = utils.cut_line_at_point(edge_start, gps_start)[1]

        n_edges = len(geom)
        if n_edges > 1:
            geom[n_edges - 1] = utils.cut_line_at_point(edge_end, gps_end)[0]
        else:
            geom[0] = utils.cut_line_at_point(geom[0], gps_end)[0]

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
        # return edges_relevant.to_dict("records")

    route_edges_with_duration = [
        _duration_between_2_gps_calls_to_edges(trip_df, i, edges_files_gdf) for i in range(len(trip_df) - 1)
    ]

    if len(route_edges_with_duration) > 0:
        route_edges_with_duration = pd.concat(route_edges_with_duration, ignore_index=True)
        route_edges_with_duration["start_hour"] = trip_df.iloc[0]["start_hour"]

        return route_edges_with_duration


def speed_per_trip(zipped: Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]) -> pd.Series:
    """
    Calculate speed per trip based on the provided GeoDataFrames.

    Parameters
    ----------
    zipped : Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]
        A tuple containing trip GeoDataFrame and edges GeoDataFrame.

    Returns
    -------
    pd.Series
        Series containing calculated speeds per trip.
    """
    trip_df, edges_files_trip, TEMP_DIR_NAME = zipped
    trip_name = "-".join(
        str(n) for n in list(trip_df[["FramedVehicleJourneyRef_DatedVehicleJourneyRef", "ALT_NUM"]].iloc[0])
    )

    if len(trip_df) <= 10:
        return f"{trip_name} too few gps points"

    if edges_files_trip is None:
        return f"{trip_name} no route edges"
    if len(edges_files_trip) == 0:
        return f"{trip_name} no route edges"

    trip_hours = trip_df["RecordedAtTime"].dt.hour.value_counts()
    trip_hours = list(trip_hours.loc[trip_hours >= 10].index)
    trip_hours.sort()
    bad_continuity = [x for x in [trip_hours[i] - trip_hours[i + 1] for i in range(len(trip_hours) - 1)] if x < -2]
    if len(bad_continuity) > 0:
        return f"{trip_name} bad hours"
    # try:
    trip_df = trip_df.drop_duplicates(subset=["RecordedAtTime", "MonitoredCall_DistanceFromStop"])
    trip_df = trip_df.sort_values("RecordedAtTime")

    # trip_df = gpd.GeoDataFrame(trip_df, geometry="geometry", crs=2039)

    trip_df = utils.remove_too_close_points(trip_df)

    # filter out bad gps calls and trips
    trip_df = filter_too_far_gps(edges_files_trip.dissolve()["geometry"].iloc[0], trip_df)
    if trip_df is None:
        return f"{trip_name} bad trip"

    if len(trip_df) <= 5:
        return f"{trip_name} too few gps points"

    # trip_df = gps_dist_along_route(trip_df, route_geom)

    trip_df = trip_df.sort_values("RecordedAtTime").reset_index(drop=True)

    # try:
    route_edges_with_duration = duration_on_edges(trip_df, edges_files_trip)
    # except:
    # return f"{trip_name} failed"
    if route_edges_with_duration is None:
        return f"{trip_name} no route edges with speed"
    if len(route_edges_with_duration[route_edges_with_duration.speed_kmh > 3]) == 0:
        return f"{trip_name} too slow"

    route_edges_with_duration = route_edges_with_duration.loc[route_edges_with_duration["duration_sec"] > 0]
    # removes beginning of drive when bus id probably standing
    index_first_movement = route_edges_with_duration[route_edges_with_duration.speed_kmh > 0.5].index[0]
    route_edges_with_duration.loc[: index_first_movement - 1, "speed_kmh"] = None

    # removes end of drive when bus id probably standing
    index_last_movement = route_edges_with_duration[route_edges_with_duration.speed_kmh > 0.5].index[-1]
    if index_last_movement < max(route_edges_with_duration.index):
        route_edges_with_duration.loc[index_last_movement + 1 :, "speed_kmh"] = None

    route_edges_with_duration = route_edges_with_duration.dropna(subset=["from", "to"])
    # route_edges_with_duration["speed_kmh"] = route_edges_with_duration["speed_kmh"].round(2)
    groupby_columns = ["ROUTE_ID", "ALT_NUM", "from", "to", "seq", "cumulative_dist", "start_hour"]

    route_edges_with_duration["len"] = route_edges_with_duration.length
    route_edges_with_duration = route_edges_with_duration.loc[route_edges_with_duration["len"] > 0]

    route_edges_with_duration = route_edges_with_duration.rename(columns={"speed_kmh": "speed_kmh_og"})
    route_edges_with_duration = (
        route_edges_with_duration.groupby(groupby_columns)
        .agg({"duration_sec": "sum", "len": "max", "speed_kmh_og": "first", "hour": "first"})
        .reset_index()
    )
    route_edges_with_duration = route_edges_with_duration.sort_values("seq").reset_index(drop=True)

    # edges_files_trip = edges_files_trip.rename(columns={"seq": "seq1"})
    edges_files_trip = edges_files_trip.filter(["ALT_NUM", "seq", "geometry"])
    route_edges_with_duration = route_edges_with_duration.merge(
        edges_files_trip,
        on=["ALT_NUM", "seq"],
        how="left",
    )
    route_edges_with_duration = gpd.GeoDataFrame(route_edges_with_duration)

    route_edges_with_duration = route_edges_with_duration.loc[route_edges_with_duration["duration_sec"] > 0]

    route_edges_with_duration["speed_kmh"] = (route_edges_with_duration.length / 1000) / (
        route_edges_with_duration["duration_sec"] / 60 / 60
    )

    seq_max = route_edges_with_duration["seq"].max()
    seq_min = route_edges_with_duration["seq"].min()

    if len(route_edges_with_duration.loc[route_edges_with_duration["seq"] == seq_max]) == 1:
        route_edges_with_duration.loc[route_edges_with_duration["seq"] == seq_max, "speed_kmh"] = (
            route_edges_with_duration.loc[route_edges_with_duration["seq"] == seq_max]["speed_kmh_og"]
        )
    if len(route_edges_with_duration.loc[route_edges_with_duration["seq"] == seq_min]) == 1:
        route_edges_with_duration.loc[route_edges_with_duration["seq"] == seq_min, "speed_kmh"] = (
            route_edges_with_duration.loc[route_edges_with_duration["seq"] == seq_min]["speed_kmh_og"]
        )

    route_edges_with_duration = route_edges_with_duration.loc[route_edges_with_duration["speed_kmh"] <= 120]
    route_edges_with_duration = route_edges_with_duration.drop(
        columns=["geometry", "speed_kmh_og", "len", "seq", "cumulative_dist"], errors="ignore"
    )

    route_edges_with_duration.reset_index(drop=True).to_feather(rf"{TEMP_DIR_NAME}\{trip_name}.feather")

    # return route_edges_with_duration


def mp_speed_per_trip(siri_day_df, edges_files_gdf, temp_dir_name):
    """multiprocessing for speed_per_trip"""
    edges_files_gdf = edges_files_gdf.set_index("seq", drop=False)

    if Path(temp_dir_name).exists:
        done_trips = [Path(x).stem for x in glob(temp_dir_name + "\*")]
        siri_day_df["trip_altnum"] = (
            siri_day_df["FramedVehicleJourneyRef_DatedVehicleJourneyRef"].astype(str)
            + "-"
            + siri_day_df["ALT_NUM"].astype(str)
        )
        siri_day_df = siri_day_df.loc[~siri_day_df["trip_altnum"].isin(done_trips)]
    siri_day_df = siri_day_df.drop(
        columns=[
            "vehicleref",
            "velocity",
            "idx",
            "operatorref",
            "dataframeref",
            "bearing",
            "stoporder",
            "stoppointref",
            "originaimeddeparturetime",
            "ROUTE_ID",
            "VehicleRef",
            "ResponseTimestamp",
            "OperatorRef",
            "ResponseMessageIdentifier",
            "RequestMessageRef",
            "longitude",
            "latitude",
            "trip_altnum",
        ],
        errors="ignore",
    )

    siri_day_df_groupby = list(siri_day_df.groupby(["FramedVehicleJourneyRef_DatedVehicleJourneyRef", "ALT_NUM"]))
    siri_day_df_groupby = [(str(int(x[0][1])), x[1]) for x in siri_day_df_groupby]
    edges_groupby = list(edges_files_gdf.groupby(["ALT_NUM"]))
    # edges_groupby = {str(int(x[0][0])): x[1] for x in edges_groupby} #linux
    edges_groupby = {str(int(x[0][0])): x[1].reset_index(drop=True) for x in edges_groupby}  # windows

    zipped = [(x[1], edges_groupby.get(x[0]), temp_dir_name) for x in siri_day_df_groupby]

    processes = mp.cpu_count()
    # -10
    # if processes<=5:
    #     processes = mp.cpu_count()
    with mp.Pool(processes=processes) as p:
        errors_list = list(
            tqdm(
                p.imap_unordered(speed_per_trip, zipped, chunksize=10),
                total=len(zipped),
                desc="Speed per trip",
            )
        )
    return errors_list


def _read_route_edges(dir_edges: str) -> list:
    """
    Read route edges from a specified directory.

    Parameters
    ----------
    dir_edges : str
        The directory path where route edges data is stored.

    Returns
    -------
    list
        A list containing the route edges GeoDataFrames.
    """
    if ".shp" in dir_edges or ".geojson" in dir_edges:
        edges_files_gdf = gpd.read_file(dir_edges)
    elif ".parquet" in dir_edges:
        edges_files_gdf = gpd.read_parquet(dir_edges)
    else:
        edges_files = glob(rf"{dir_edges}/route_edges_*.geojson")
        chunksize = max(1, int(round(len(edges_files) / (mp.cpu_count() * 2))))

        with mp.Pool(processes=mp.cpu_count()) as p:
            edges_files_gdf = list(
                tqdm(
                    p.imap_unordered(gpd.read_file, edges_files, chunksize=chunksize),
                    total=len(edges_files),
                )
            )
    return edges_files_gdf


def read_route_edges(dir_edges: str) -> gpd.GeoDataFrame:
    """
    Read route edges from a directory into a GeoDataFrame.

    Parameters
    ----------
    dir_edges : str
        The directory path containing route edge files.

    Returns
    -------
    gpd.GeoDataFrame
        GeoDataFrame containing route edges.
    """
    if os.path.exists(rf"{dir_edges}/route_edges_with_stops.parquet"):
        edges_files_gdf = gpd.read_parquet(rf"{dir_edges}/route_edges_with_stops.parquet")
    else:
        edges_files_gdf = _read_route_edges(dir_edges)
        if type(edges_files_gdf) == list:
            edges_files_gdf = gpd.GeoDataFrame(pd.concat(edges_files_gdf))

    edges_files_gdf = edges_files_gdf.drop_duplicates(["ALT_NUM", "seq", "from", "to"])

    edges_files_gdf = edges_files_gdf.filter(
        [
            "MAKAT",
            "ROUTE_ID",
            "ALT_NUM",
            "from",
            "to",
            "seq",
            "geometry",
        ]
    )
    edges_files_gdf = edges_files_gdf.to_crs("epsg:2039")
    return edges_files_gdf


def trips_get_start_hour(siri_day_df: Union[pd.DataFrame, gpd.GeoDataFrame]) -> Union[pd.DataFrame, gpd.GeoDataFrame]:
    """
    Extract the start hour of each trip and add it as a new column to the DataFrame.

    Parameters
    ----------
    siri_day_df : Union[pd.DataFrame, gpd.GeoDataFrame]
        DataFrame containing SIRI day data.

    Returns
    -------
    siri_day_df : Union[pd.DataFrame, gpd.GeoDataFrame]
        Updated DataFrame with the start hour column added.
    """
    trip_start_time = siri_day_df.groupby("FramedVehicleJourneyRef_DatedVehicleJourneyRef").apply(
        lambda x: x.iloc[0]["hour"]
    )
    trip_start_time = trip_start_time.reset_index().rename(columns={0: "start_hour"})
    siri_day_df = siri_day_df.merge(trip_start_time, on=["FramedVehicleJourneyRef_DatedVehicleJourneyRef"])
    return siri_day_df


def filter_half_hour(
    siri_day_df: Union[pd.DataFrame, gpd.GeoDataFrame], hour: int
) -> Union[pd.DataFrame, gpd.GeoDataFrame]:
    """
    Filter DataFrame to include only records from a specific hour starting from an half hour
    (7:30-8:30 for example).

    Parameters
    ----------
    siri_day_df : Union[pd.DataFrame, gpd.GeoDataFrame]
        DataFrame containing SIRI day data.
    hour : int
        The half hour from which the hour records should be filtered (7:30).

    Returns
    -------
    siri_day_df_filtered : Union[pd.DataFrame, gpd.GeoDataFrame]
        DataFrame with records only from the specified hour.

    """
    date = siri_day_df["RecordedAtTime"].iloc[0][:11]
    hour_end = str(int(np.floor(hour + 1)))
    if len(hour_end) == 1:
        hour_end = f"0{hour_end}"
    hour_start = str(int(np.floor(hour)))
    if len(hour_start) == 1:
        hour_start = f"0{hour_start}"

    siri_day_df = siri_day_df[
        siri_day_df["RecordedAtTime"].between(f"{date}{hour_start}:30:00", f"{date}{hour_end}:29:99")
    ]
    return siri_day_df


def read_feather_batch(files):
    """Read a batch of feather files and return as a single table"""

    def catch(f):
        try:
            return feather.read_table(f)
        except ArrowInvalid as e:
            return None

    tables = [t for f in files if (t := catch(f)) is not None]

    return pa.concat_tables(tables) if tables else None


def concatenate_feather_files(pattern, batch_size=1000, max_workers=None):
    """
    Efficiently concatenate large numbers of feather files using parallel processing.

    Args:
        pattern: Glob pattern for files (e.g., 'temp/*.feather')
        batch_size: Number of files to process per worker (default: 1000)
        max_workers: Number of parallel workers (default: CPU count)

    Returns:
        Concatenated PyArrow table
    """
    # Get all file paths
    files = glob(pattern)
    n_files = len(files)
    print(f"Found {n_files} files to concatenate")

    if not files:
        return None

    # If max_workers not specified, use CPU count
    if max_workers is None:
        max_workers = os.cpu_count()

    # Split files into batches
    batches = [files[i : i + batch_size] for i in range(0, n_files, batch_size)]
    print(f"Processing in {len(batches)} batches with {max_workers} workers")

    tables = []
    with mp.Pool(processes=5) as p:
        tables = list(
            tqdm(
                p.imap_unordered(read_feather_batch, batches),
                total=len(batches),
            )
        )

    print("Concatenating all batches...")
    final_table = pa.concat_tables(tables)
    print(f"Final table shape: {final_table.num_rows} rows, {final_table.num_columns} cols")

    final_table = final_table.to_pandas()
    return final_table


def result_per_hour(
    siri_day_df: gpd.GeoDataFrame,
    edges_files_gdf: gpd.GeoDataFrame,
    day_path: str,
    temp_dir_name: str,
    filter_name: str = None,
):
    """
    Process and analyze data for a specific hour and filter, then saves the result to parquet files.

    Parameters
    ----------
    siri_day_df : gpd.GeoDataFrame
        GeoDataFrame containing SIRI day data.
    edges_files_gdf : gpd.GeoDataFrame
        GeoDataFrame containing edges data.
    route_based : bool
        if true, calculate avg speed per route and hour. If false, calculate only based on hour
    day_path : str
        Path to the daily data file.
    filter_name : str, optional
        The filter name for additional spatial or temporal filtering, by default None.

    Returns
    -------
    None
        Saves the result to parquet files
    """

    filter_place_holder = "Israel"
    if filter_name:
        filter_place_holder = filter_name.replace(", Israel", "")

    finished_files = glob(f"{day_path}/edges_speed_*.parquet")

    if len(finished_files) == 24:
        return None

    if filter_name:
        siri_day_df, rts_route, edges_files_gdf = utils.filter_extent(filter_name, siri_day_df)

        alt_num_list = set(siri_day_df["ALT_NUM"])
        rts_route = rts_route.loc[rts_route["ALT_NUM"].astype(str).isin(alt_num_list)].reset_index(drop=True)
        route_id_list = set(rts_route["ROUTE_ID"].astype(str))
        edges_files_gdf = edges_files_gdf.loc[edges_files_gdf["ROUTE_ID"].astype(str).isin(route_id_list)].reset_index(
            drop=True
        )

    if len(siri_day_df) == 0:
        utils.write_blank_files(
            hour,
            day_path,
            filter_place_holder,
            error_list=["no siri records at this date-hour"],
            route_based=None,
        )
        return None

    errors_list = mp_speed_per_trip(siri_day_df, edges_files_gdf, temp_dir_name)
    os.makedirs(rf"{day_path}", exist_ok=True)
    if errors_list is not None:
        errors_list = [x for x in errors_list if x is not None]
        if len(errors_list) > 0:
            with open(rf"{day_path}/errors.txt", "w") as fp:
                fp.write("\n".join(errors_list))

    edged_trip_speeds = concatenate_feather_files(temp_dir_name + "/*.feather")

    edged_trip_speeds = edged_trip_speeds.drop(columns=["ROUTE_ID"])
    edged_trip_speeds = edged_trip_speeds.round(2)
    # edged_trip_speeds.to_parquet(out_path, index="False")

    for x in edged_trip_speeds["hour"].unique():
        edged_trip_speeds.loc[edged_trip_speeds["hour"] == x].to_parquet(
            f"{day_path}/edges_speed_{x}.parquet", index="False"
        )


def avg_monthly_time(
    hour: int,
    month_path: str,
    unique_edges: gpd.GeoDataFrame,
    leave_out: list = [4, 5],
) -> None:
    """
    Calculate and print the average monthly time for a specific hour and filter.

    Parameters
    ----------
    hour : int
        The specific hour for which to calculate the average time.
    month_path : str
        The path to the monthly data file.
    day_path_list : list
        list of all available and relevant days
    siri_type : str
        siri data origin
    filter_name : str, optional
        The filter name for additional spatial or temporal filtering, by default "Israel".
    leave_out : list, optional
        Days to be left out of the calculation, by default [4,5] (weekend days).
    Returns
    -------
    None
        Saves the average time for the specified hour and month to geojson file.
    """

    hour_speed = pd.concat(
        [pd.read_parquet(file) for file in glob(month_path + f"\**\edges_speed_{hour}.parquet", recursive=True)]
    )

    # hour_speed =

    # day_hour_speed_gdf=day_hour_speed_gdf.merge(day_hour_speed_gdf_groupby[['from','to','speed_kmh_10percentile','speed_kmh_90percentile']], on=['from','to'], how='left')
    # day_hour_speed_gdf = day_hour_speed_gdf.loc[(day_hour_speed_gdf['speed_kmh']>day_hour_speed_gdf['speed_kmh_10percentile'])& (day_hour_speed_gdf['speed_kmh']<day_hour_speed_gdf['speed_kmh_90percentile'])]

    hour_speed_groupby = (
        hour_speed.groupby(["from", "to", "hour"])
        .agg(
            speed_kmh_mean=("speed_kmh", np.nanmean),
            speed_kmh_median=("speed_kmh", np.median),
            speed_kmh_std=("speed_kmh", "std"),
            speed_kmh_10percentile=("speed_kmh", lambda x: np.percentile(x, 10)),
            speed_kmh_90percentile=("speed_kmh", lambda x: np.percentile(x, 90)),
            count=("speed_kmh", "count"),
            duration_sec_mean=("duration_sec", np.nanmean),
            duration_sec_std=("duration_sec", np.nanstd),
        )
        .reset_index()
    )

    hour_speed_groupby = hour_speed_groupby.merge(
        unique_edges[["from", "to", "length", "geometry"]], on=["from", "to"]
    )

    hour_speed_groupby = gpd.GeoDataFrame(hour_speed_groupby, crs=unique_edges.crs.to_epsg())

    hour_speed_groupby["duration_minutes_median_speed"] = (hour_speed_groupby["length"] / 1000) / (
        hour_speed_groupby["speed_kmh_median"] / 60
    )
    hour_speed_groupby["duration_minutes_mean_speed"] = (hour_speed_groupby["length"] / 1000) / (
        hour_speed_groupby["speed_kmh_mean"] / 60
    )

    # avg_monthly_hour = day_hour_speed_gdf.groupby(["from", "to", "geometry"])["speed_kmh"].mean().reset_index()
    hour_speed_groupby["from"] = "str" + hour_speed_groupby["from"]
    hour_speed_groupby["to"] = "str" + hour_speed_groupby["to"]

    if leave_out == [4, 5]:
        period = "weekdays"
    elif leave_out == [0, 1, 2, 3, 6]:
        period = "weekend"
    elif leave_out == [0, 1, 2, 3, 5, 6]:
        period = "friday"
    # day_hour_speed_gdf.to_file(f"{output_path}/{period}_{hour}_oclock.geojson")
    hour_speed_groupby.to_parquet(f"{month_path}/{period}_{hour}_oclock.parquet")


def read_and_filter_hour(args):
    """Read file and filter for specific hour"""
    file, hour = args
    df = pl.read_parquet(file)
    df = df.filter(pl.col("start_hour") == hour)

    if df.height > 0:  # Polars uses .height for row count
        return df
    return None


def avg_route_monthly_time(
    month_path: str,
    unique_edges: gpd.GeoDataFrame,
    altnum_makat: dict,
    leave_out: list = [4, 5],
):
    """
    Calculate the average route time for a specific hour and month.

    Parameters
    ----------
    hour : int
        The specific hour for which to calculate the average route time.
    month_path : str
        The path to the monthly data file.
    day_path_list : list
        list of all available and relevant days
    siri_type : str
        siri data origin
    filter_name : str, optional
        The filter name for additional spatial or temporal filtering, by default "Israel".
    leave_out : list, optional
        Days to be left out of the calculation, by default [4,5] (weekend days).

    Returns
    -------
    None
        Saves the average route time for the specified hour and month to geojson file.
    """

    files = glob(month_path + f"\\**\\edges_speed_*.parquet", recursive=True)

    # Process each hour separately
    for hour in range(0, 24):
        print(f"Processing hour {hour}...")
        args = [(file, hour) for file in files]
        with mp.Pool(processes=5) as pool:  # Adjust number of processes
            results = list(tqdm(pool.imap_unordered(read_and_filter_hour, args, chunksize=1), total=len(args)))

        hour_chunks = [df.drop(["__index_level_0__"]) for df in results if df is not None]
        column_order = ["ALT_NUM", "from", "to", "start_hour", "duration_sec", "hour", "speed_kmh"]
        hour_chunks = [df.select(column_order) for df in hour_chunks]

        if hour_chunks:
            combined = pl.concat(hour_chunks)

            result = combined.group_by(["ALT_NUM", "from", "to", "start_hour"]).agg(
                [
                    pl.col("speed_kmh").mean().alias("speed_kmh_mean"),
                    pl.col("speed_kmh").median().alias("speed_kmh_median"),
                    pl.col("speed_kmh").std().alias("speed_kmh_std"),
                    pl.col("speed_kmh").count().alias("speed_kmh_count"),
                    pl.col("duration_sec").mean().alias("duration_sec_mean"),
                    pl.col("duration_sec").std().alias("duration_sec_std"),
                ]
            )
            result = result.to_pandas()
            result = result.merge(unique_edges[["from", "to", "length", "geometry"]], on=["from", "to"])

            result = gpd.GeoDataFrame(result, crs=unique_edges.crs.to_epsg())

            result["duration_minutes_median_speed"] = (result["length"] / 1000) / (result["speed_kmh_median"] / 60)
            result["duration_minutes_mean_speed"] = (result["length"] / 1000) / (result["speed_kmh_mean"] / 60)

            # avg_monthly_hour = day_hour_speed_gdf.groupby(["from", "to", "geometry"])["speed_kmh"].mean().reset_index()
            result["from"] = "str" + result["from"]
            result["to"] = "str" + result["to"]

            result["MAKAT"] = result["ALT_NUM"].map(altnum_makat)
            result.to_parquet(f"{month_path}\\routes_{hour}.parquet")
            print(f"Saved hour {hour}, {len(result)} rows")

            del combined, result, hour_chunks


def read_siri(month, year, day, output_dir, alt_num_list=None):
    import duckdb

    _, sql_query = utils.create_sql_query_n_table_name(alt_num_list, year, month, day)

    dbfile = (
        rf"{os.path.dirname(output_dir)}\data\{year}\{str(month).zfill(2)}\siri_{year}{str(month).zfill(2)}.duckdb"
    )

    with duckdb.connect(dbfile) as con:

        siri_df = con.sql(sql_query).df()
    if "1" in siri_df.columns:
        siri_df.columns
    siri_df["hour"] = siri_df["recordedattime"].dt.hour
    siri_df["start_hour"] = siri_df["originaimeddeparturetime"].dt.hour
    siri_df = utils.lon_lat_to_geom(siri_df, "longitude", "latitude")

    siri_df = siri_df.rename(
        columns={
            "lineref": "ALT_NUM",
            "distancefromstop": "MonitoredCall_DistanceFromStop",
            "recordedattime_epoch": "date",
            "datedvehiclejourneyref": "FramedVehicleJourneyRef_DatedVehicleJourneyRef",
            "recordedattime": "RecordedAtTime",
        }
    )
    siri_df["ALT_NUM"] = siri_df["ALT_NUM"].astype(str)

    return siri_df


def read_RTS(year, month):
    RTS_options = {
        x: os.path.basename(x).replace(" ", "_").split("_")[-2] for x in glob(r"T:\RTS\Israel\*") if str(year) in x
    }
    previous_year = False
    if len(RTS_options) == 0:
        previous_year = True
        RTS_options = {
            x: os.path.basename(x).replace(" ", "_").split("_")[-2]
            for x in glob(r"T:\RTS\Israel\*")
            if str(year - 1) in x
        }
    RTS_options = {
        k: datetime.strptime(v, "%B").month if len(v) > 3 else datetime.strptime(v, "%b").month
        for k, v in RTS_options.items()
    }

    if previous_year:
        closest_month = max(RTS_options.values(), key=lambda x: abs(x - month))
    else:
        closest_month = min(RTS_options.values(), key=lambda x: abs(x - month))
    RTS_dir = [key for key, val in RTS_options.items() if val == closest_month][0]
    rts_route_path = glob(RTS_dir + "\**\Routes*.shp", recursive=True)[0]

    split_path = rts_route_path.split("\\")
    for i, x in enumerate(split_path):
        if "shape" in x.lower():
            break
    dir_shp = "\\".join(split_path[: i + 1])

    dir_edges = glob(dir_shp + r"\outputs\**\route*.parquet", recursive=True)
    if len(dir_edges) > 0:
        dir_edges = dir_edges[0]
    else:
        raise FileNotFoundError("need to create route edges")
    edges_files_gdf = read_route_edges(dir_edges)

    return edges_files_gdf


def main(output_dir, siri_type, day, month, year, edges_files_gdf, filter_name=None, calc_avg_monthly=False):

    temp_dir = tempfile.TemporaryDirectory(prefix=f"siri_trip_{day}-")
    temp_dir_name = temp_dir.name

    options = get_args(_SIRI_TYPES)
    assert siri_type in options, f"'{siri_type}' is not in {options}"
    time_period_options = get_args(_TIME_TYPES)
    assert time_period in time_period_options, f"'{time_period}' is not in {time_period_options}"

    ################################################

    if not hour:
        hours = [x for x in range(0, 24)]
    else:
        if type(hour) != list:
            hours = [hour]
        else:
            hours = hour

    # alt_num_list = None
    # _, sql_query = utils.create_sql_query_n_table_name(alt_num_list, month, year, day)

    dbfile = rf"T:\GIS\Speeds\siri-realtime\data\{year}\{str(month).zfill(2)}\siri_{year}{str(month).zfill(2)}.duckdb"

    tablename, siri_df = utils.siri_to_local_duckdb(hours, day, month, year, dbfile)
    if tablename is None:
        return None

    siri_day_df = read_siri(month, year, day, output_dir, alt_num_list=None)
    day_path = rf"{output_dir}\{year}\{str(month).zfill(2)}\{day}"

    os.makedirs(day_path, exist_ok=True)

    result_per_hour(
        siri_day_df,
        edges_files_gdf,
        day_path,
        temp_dir_name,
        filter_name,
    )
    if calc_avg_monthly:
        monthly_avg(output_dir, year, month, edges_files_gdf, hours)
    ###########################################################


def monthly_avg(output_dir, year, month, edges_files_gdf, hours):
    month_path = rf"{output_dir}\{year}\{str(month).zfill(2)}"

    unique_edges = edges_files_gdf.drop_duplicates(["from", "to"])
    unique_edges["length"] = unique_edges.to_crs(2039).length

    altnum_makat = edges_files_gdf[["ALT_NUM", "MAKAT"]].drop_duplicates().set_index("ALT_NUM").to_dict()["MAKAT"]
    # max_seq =edges_files_gdf.sort_values(['ALT_NUM','seq'],ascending =False).drop_duplicates('ALT_NUM')[['ALT_NUM','MAKAT','seq']]

    # # del edges_files_gdf

    [avg_monthly_time(x, month_path, unique_edges=unique_edges) for x in hours]

    avg_route_monthly_time(month_path, unique_edges, altnum_makat)
    ###########################################################


if __name__ == "__main__":

    hour = None
    # rts_dir = r"T:\RTS\Israel\Israel_March_2025\Shape files"
    rts_dir = r"T:\RTS\Israel\Israel_June_2026\shape_files"

    filter_name = None
    # filter_name = 'Tel Aviv'
    siri_type = "realtime"
    op_code = None

    time_period = "weekdays"
    output_dir = r"T:\GIS\Speeds\siri-realtime\data"
    # output_dir = r"C:\Users\Naama\Documents\Naama\TA_new_lanes_speeds"

    month = 6
    year = 2026
    edges_files_gdf = read_RTS(year, month)
    # edges_files_gdf = read_RTS(2026, 1)

    import gc

    for day in range(1, 31):

        main(
            output_dir,
            siri_type,
            day,
            month,
            year,
            edges_files_gdf,
            filter_name,
            calc_avg_monthly=True
        )
        gc.collect()
    # edges_files_gdf = read_RTS(year, month)
    # month_path = rf"{output_dir}\{year}\{str(month).zfill(2)}"

    # unique_edges = edges_files_gdf.drop_duplicates(["from", "to"])
    # unique_edges["length"] = unique_edges.to_crs(2039).length

    # altnum_makat = edges_files_gdf[["ALT_NUM", "MAKAT"]].drop_duplicates().set_index("ALT_NUM").to_dict()["MAKAT"]
    # # max_seq =edges_files_gdf.sort_values(['ALT_NUM','seq'],ascending =False).drop_duplicates('ALT_NUM')[['ALT_NUM','MAKAT','seq']]

    # del edges_files_gdf
    # avg_route_monthly_time(month_path, unique_edges, altnum_makat)
