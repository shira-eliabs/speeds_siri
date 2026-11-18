
"""
Filename: speed_layer_from_siri.py

The script creates speed layer based on vm information from "Hasadna"
Author: Shira Zylbertal
Email: Shira@eliabs.com
Date: December 06, 2023

Reference files used:
    r"/mnt/t/90265_Jerusalem_Public_Transport/jer_rs_analysis/data/RTS_4326/RS+DBD_062023"
    r"/mnt/t/RTS/Israel/Israel_June_2023/route_edges"

Local server location:
    /mnt/t/GIS/Speeds/Hasadna/tools/speed_layer_from_siri.py
"""

from datetime import datetime, timedelta
from tqdm import tqdm
import operator
from math import floor
import geopandas as gpd
import pandas as pd
from utils.siri_df_raw import siri_df
# from download_siri_hasadna import download_siri_month
from glob import glob
from shapely.geometry import Point, MultiPoint, LineString, MultiLineString, Polygon
from shapely.errors import TopologicalError
import numpy as np
import multiprocessing as mp
from tqdm import tqdm
from collections import defaultdict
from typing import List, Tuple, Union, Optional
import os
import warnings
import osmnx
import arrow
from itertools import repeat
from itertools import groupby as itter_groupby
from collections import Counter


warnings.filterwarnings("ignore")
pd.set_option("display.max_columns", 500)


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
    route_geom_gdf = gpd.GeoDataFrame(geometry=[route_geom], crs=2039)
    trip_df = trip_df.reset_index(drop=True)
    [indx_gps, _], dist_from_route = route_geom_gdf.sindex.nearest(
        trip_df.geometry, return_all=False, return_distance=True
    )
    gps_dist_dict = dict(zip(indx_gps, dist_from_route))
    bad_gps = [gps for gps, dist in gps_dist_dict.items() if dist > 200]
    good_gps = list(set(trip_df.index) - set(bad_gps))
    trip_df = trip_df.iloc[good_gps]
    # trip_df = trip_df.reset_index(drop=True)

    sequence_missing = [
        list(map(operator.itemgetter(1), g)) for k, g in itter_groupby(enumerate(bad_gps), lambda i_x: i_x[0] - i_x[1])
    ]

    sequence_missing = [x for x in sequence_missing if len(x) > 4]
    if len(good_gps) > 0:
        sequence_missing = [x for x in sequence_missing if min(x) < max(good_gps)]
    if len(sequence_missing) > 0:
        return None
    return trip_df


def filter_extent(
    study_area: gpd.GeoDataFrame,
    siri_day_df: gpd.GeoDataFrame,
    rts_route: gpd.GeoDataFrame,
    delete_parts_not_in_study_area: bool,
) -> Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """
    Apply spatial extent filtering based on the given filter name to the provided GeoDataFrames.

    Parameters
    ----------
    study_area : str
        Name of area for the spatial filter.
    siri_day_df : gpd.GeoDataFrame
        GeoDataFrame containing SIRI day data.
    rts_route : gpd.GeoDataFrame
        GeoDataFrame containing RTS route data.
    delete_parts_not_in_study_area : bool
        if true, deletes parts not in study area, by default None.

    Returns
    -------
    Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]
        Filtered GeoDataFrames.
    """
    # filter_geom = osmnx.geocode_to_gdf(filter_name)
    # filter_geom["geometry"] = filter_geom["geometry"].buffer(200)
    siri_day_df = siri_day_df.reset_index(drop=True)
    filter_indx, siri_indx = siri_day_df.sindex.query(study_area.geometry, predicate="intersects")

    siri_filter_trips = set(siri_day_df.iloc[siri_indx]["FramedVehicleJourneyRef_DatedVehicleJourneyRef"])
    if delete_parts_not_in_study_area:
        siri_day_df = siri_day_df.iloc[siri_indx]
    siri_day_df = siri_day_df.loc[
        siri_day_df["FramedVehicleJourneyRef_DatedVehicleJourneyRef"].isin(list(siri_filter_trips))
    ]

    siri_day_df = siri_day_df.reset_index(drop=True)

    alt_num_list = set(siri_day_df["LineRef"])
    rts_route = rts_route.loc[rts_route["ALT_NUM"].astype(str).isin(alt_num_list)].reset_index(drop=True)
    route_id_list = set(rts_route["ROUTE_ID"].astype(str))

    return (
        siri_day_df,
        rts_route,
    )


def trip_length(
    trip: Union[pd.DataFrame, gpd.GeoDataFrame],
    study_area: gpd.GeoDataFrame,
    rts_route: gpd.GeoDataFrame,
    delete_parts_not_in_study_area: bool,
) -> gpd.GeoDataFrame:
    """
    Calculate the length of a trip.

    Parameters
    ----------
    trip : Union[pd.DataFrame, gpd.GeoDataFrame]
        DataFrame or GeoDataFrame containing information about the trip.
    study_area : gpd.GeoDataFrame
        GeoDataFrame containing mask polygon
    rts_route : gpd.GeoDataFrame
        GeoDataFrame containing information about the routes.
    delete_parts_not_in_study_area:bool

    Returns
    -------
    gpd.GeoDataFrame
        GeoDataFrame with the trip length calculated.
    """
    rts_route = rts_route.to_crs(2039)
    bad_response_time = [i for i in Counter(trip["RecordedAtTime"]).values() if i > 2]
    trip_name = trip["FramedVehicleJourneyRef_DatedVehicleJourneyRef"].iloc[0]
    if len(bad_response_time) > 0:
        return f"{trip_name} bad_response_time"
    trip["geometry"] = trip.apply(
        lambda row: Point(row["VehicleLocation_Longitude"], row["VehicleLocation_Latitude"]), axis=1
    )
    trip = trip.drop(columns=["VehicleLocation_Longitude", "VehicleLocation_Latitude"])
    trip = gpd.GeoDataFrame(trip, geometry="geometry", crs=4326)
    trip = trip.to_crs("epsg:2039")

    if study_area is not None:
        trip, rts_route = filter_extent(study_area, trip, rts_route, delete_parts_not_in_study_area)

    rts_route["ALT_NUM"] = rts_route["ALT_NUM"].astype(str)
    trip = trip.merge(rts_route, left_on="LineRef", right_on="ALT_NUM", how="left").drop(columns=["LineRef"])
    trip = trip.rename(columns={"geometry_y": "route_geom", "geometry_x": "geometry"})
    try:
        trip = trip.sort_values("RecordedAtTime")
        trip = trip.drop_duplicates(
            subset=list(
                set(trip.columns) - set(["ResponseTimestamp", "ResponseMessageIdentifier", "RequestMessageRef"])
            )
        )

        trip = gpd.GeoDataFrame(trip, geometry="geometry", crs=2039)

        route_geom = trip.iloc[0]["route_geom"]
        # filter out bad gps calls and trips
        trip = filter_too_far_gps(route_geom, trip)

        # # or after_filter_len/original_len<0.
        # if len(trip) <= 10:
        #     return f"{trip_name} too few gps points"

        trip = trip.copy()
        trip["time_diff_seconds"] = trip["date"].diff()
        # if trip["time_diff_seconds"].sum() <= 5 * 60:
        #     return f"{trip_name} too short"

        # removes beginning of drive when bus id probably standing
        trip["diff_distance"] = trip["MonitoredCall_DistanceFromStop"].diff()
        index_first_movement = trip[trip.diff_distance > 3].index[0]
        trip.loc[: index_first_movement - 1, "time_diff_seconds"] = None

        # removes end of drive when bus id probably standing
        index_last_movement = trip[trip.diff_distance > 3].index[-1]
        if index_last_movement < max(trip.index):
            trip.loc[index_last_movement + 1 :, "time_diff_seconds"] = None

        # trip = gps_dist_along_route(trip, route_geom)

        trip_gpd_dist_km = max(trip["MonitoredCall_DistanceFromStop"] / 1000) - min(
            trip["MonitoredCall_DistanceFromStop"] / 1000
        )
        trip_gps_duration_hours = trip["time_diff_seconds"].sum() / 60 / 60
        avg_speed = trip_gpd_dist_km / trip_gps_duration_hours

        missing_dist = max(trip["length_route"]) - max(trip["MonitoredCall_DistanceFromStop"])

        # if missing_dist / max(trip["length_route"]) > 0.15:
        #     return f"{trip_name} missing dist"

        trip["total_duration_seconds"] = (
            trip["time_diff_seconds"].sum() + ((missing_dist / 1000) / avg_speed) * 60 * 60
        )
        trip["avg_speed_kmh"] = avg_speed
        return trip
    except:
        return f"{trip_name} failed"


def filter_by_hour(trip_in_area: pd.DataFrame, hour: int) -> Tuple[pd.DataFrame, int, int, float, float]:
    """
    Filter trips by hour.

    Parameters
    ----------
    trip_in_area : pd.DataFrame
        DataFrame containing information about trips in the area.
    hour : int
        Hour to filter the trips.

    Returns
    -------
    Tuple[pd.DataFrame, int, int, float, float]
        Tuple containing filtered trip DataFrame, ALT_NUM, total_duration_seconds, avg_speed_kmh, and length_route.
    """
    fmt = "%Y-%m-%dT%H:%M:%S"

    trip_seconds = datetime.strptime(trip_in_area["RecordedAtTime"].iloc[-1][:-6], fmt) - datetime.strptime(
        trip_in_area["RecordedAtTime"].iloc[0][:-6], fmt
    )

    if hour in list(trip_in_area["hour"]):
        if len(set(trip_in_area["hour"])) > 1:
            trip_hour = trip_in_area[trip_in_area["hour"] == hour]

            trip_seconds_hour = datetime.strptime(trip_hour["RecordedAtTime"].iloc[-1][:-6], fmt) - datetime.strptime(
                trip_hour["RecordedAtTime"].iloc[0][:-6], fmt
            )

            # if (trip_seconds_hour / trip_seconds) >= 0.4:
            return (
                trip_in_area,
                trip_in_area.iloc[0]["ALT_NUM"],
                trip_in_area.iloc[0]["total_duration_seconds"],
                trip_in_area.iloc[0]["avg_speed_kmh"],
                trip_in_area.iloc[0]["length_route"],
            )
            # else:
            #     return None
        return (
            trip_in_area,
            trip_in_area.iloc[0]["ALT_NUM"],
            trip_in_area.iloc[0]["total_duration_seconds"],
            trip_in_area.iloc[0]["avg_speed_kmh"],
            trip_in_area.iloc[0]["length_route"],
        )

    return None


def filter_by_polygon(trip: pd.DataFrame, hour: int, study_area: gpd.GeoDataFrame, only_area: bool) -> pd.DataFrame:
    """
    Filter trips by polygon.

    Parameters
    ----------
    trip : pd.DataFrame
        DataFrame containing information about trips.
    hour : int
        Hour to filter the trips.
    study_area : gpd.GeoDataFrame
        GeoDataFrame containing mask polygon
    only_area : bool
        Flag indicating whether to include only trips inside the study area.

    Returns
    -------
    gpd.GeoDataFrame
        GeoDataFrame containing filtered trips.
    """
    trip_hour_spec = trip[trip["hour"] == hour].reset_index(drop=True)
    _, trip_indx = trip_hour_spec.sindex.query(study_area.geometry, predicate="intersects")

    trip_in_area = trip_hour_spec.iloc[trip_indx]
    if only_area:
        if len(trip) > 0:
            return trip_in_area
        else:
            return None
    trip = trip.loc[
        trip["FramedVehicleJourneyRef_DatedVehicleJourneyRef"].isin(
            list(set(trip_in_area["FramedVehicleJourneyRef_DatedVehicleJourneyRef"]))
        )
    ]
    if len(trip) > 0:
        return trip


def trip_process(args: Tuple) -> Tuple[int, int, float, float, int]:
    """
    Process trip data.

    Parameters
    ----------
    args : tuple
        Tuple containing trip data.

    Returns
    -------
    tuple
        Tuple containing processed trip data.
    """
    trip, delete_parts_not_in_study_area, rts_route, hour, study_area, only_area = args
    # if study_area is not None:
    #     study_area["geometry"] = study_area["geometry"].buffer(50)

    trip = trip_length(trip, study_area, rts_route, delete_parts_not_in_study_area)
    if type(trip) == gpd.GeoDataFrame:
        (trip_info) = filter_by_hour(trip, floor(hour))
        if trip_info is not None:
            trip, trip_alt_num, trip_duration, avg_speed, route_length = trip_info
            if study_area is not None:
                trip = filter_by_polygon(trip, hour, study_area, only_area)
                if trip is not None and len(trip) > 0:
                    # if trip[1] != 0:
                    trip = trip.sort_values("RecordedAtTime")
                    trip["time_diff_seconds"] = trip["date"].diff()
                    return (
                        trip.iloc[0]["ALT_NUM"],
                        trip["time_diff_seconds"].sum(),
                        trip.iloc[0]["avg_speed_kmh"],
                        trip.iloc[0]["length_route"],
                        hour,
                    )
            else:
                return trip_alt_num, trip_duration, avg_speed, route_length, hour


def merge_study_area(study_area_emek_path, study_area_center_path):
    study_area_emek = gpd.read_file(study_area_emek_path).to_crs(2039)
    study_area_center = gpd.read_file(study_area_center_path).to_crs(2039)
    study_area = gpd.GeoDataFrame(pd.concat([study_area_emek, study_area_center]), crs=2039)

    return study_area


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


def read_gdf(path: str) -> gpd.GeoDataFrame:
    if path.endswith(".parquet"):
        gdf = gpd.read_parquet(path)
    else:
        gdf = gpd.read_file(path)
    gdf = gdf.reset_index(drop=True)
    gdf = gdf.to_crs(2039)
    return gdf


def main(
    year: int,
    month: int,
    rts_route_path: str,
    siri_type: str,
    hours: int,
    eshkol_id: Optional[int] = None,
    op_code: Optional[int] = None,
    delete_parts_not_in_study_area: Optional[bool] = None,
    study_area_path: Optional[str] = None,
    only_area: bool = False,
    makat_list: Optional[List[int]] = None,
    altnum_list: Optional[List[int]] = None,
    weekend: bool = False,
) -> None:
    """
    Main function for processing transportation data - getting duration and speed information about each route per hour.

    Parameters
    ----------
    year : int
        Year of the data.
    month : int
        Month of the data.
    rts_route_path : str
        Path to the RTS route data.
    siri_type : str
        Type of SIRI data.
    hours : int
        Number of hours to process.
    eshkol_id : int, optional
        Eshkol ID, by default None.
    op_code : int, optional
        Operation code, by default None.
    delete_parts_not_in_study_area : book, optional
        if true, deletes parts not in study area, by default None.
    study_area_emek_path : str, optional
        Path to the study area (Emek), by default None.
    study_area_center_path : str, optional
        Path to the study area (Center), by default None.
    only_area : bool, optional
        Flag to indicate if only the study area should be considered, by default False.
    makat_list : list of int, optional
        List of MAKAT values, by default None.
    altnum_list : list of int, optional
        List of alt num values, by default None.
    weekend : str
        if True, the results are for weekend only, if False, results are weekday
    Returns
    -------
    None
    """
    if siri_type == "hasadna":
        month_path = download_siri_month(year, month)
        day_path_list = glob(month_path + "/*")
        day_path_list = [x for x in day_path_list if x[-1].isdigit()]
        if weekend:
            day_path_list = [
                x
                for x in day_path_list
                if np.isin(datetime.strptime(x.replace("/stride-siri", "")[-10:], "%Y/%m\\%d").weekday(), [5, 4])
            ]
        else:
            day_path_list = [
                x
                for x in day_path_list
                if not np.isin(datetime.strptime(x.replace("/stride-siri", "")[-10:], "%Y/%m\\%d").weekday(), [5, 4])
            ]

    else:
        month_path = rf"T:/GIS/Speeds/realtime/{year}/{month}"
        day_path_list = glob(month_path + "/*")
        day_path_list = [x for x in day_path_list if x.split(".")[0][-1].isdigit() if "." in x]
        day_path_list = [
            x
            for x in day_path_list
            if not np.isin(datetime.strptime(os.path.splitext(os.path.basename(x))[0], "%Y%m%d").weekday(), [5, 4])
        ]

    rts_route = read_gdf(rts_route_path)

    rts_route = rts_route.rename(columns={"Makat": "MAKAT", "Alt_num": "ALT_NUM"})
    if eshkol_id:
        rts_route = rts_route.loc[rts_route["ESHKOLM"] == eshkol_id]
    if op_code:
        rts_route = rts_route.loc[rts_route["OPCODE"] == op_code]
    if makat_list:
        rts_route = rts_route[rts_route["MAKAT"].isin(makat_list)]
    if altnum_list:
        rts_route = rts_route[rts_route["ALT_NUM"].isin(altnum_list)]

    rts_route = rts_route.filter(["ROUTE_ID", "ALT_NUM", "MAKAT", "ROUTE", "ALT", "DIRECTION", "geometry"])
    rts_route["ALT_NUM"] = rts_route["ALT_NUM"].astype(str)

    name = None
    study_area = None
    if study_area_path:
        study_area = read_gdf(study_area_path)
        rts_route = rts_route.clip(study_area)
    rts_route["length_route"] = rts_route.length

    route_time = []
    for day_path in day_path_list:
        print(day_path)
        siri_day_df = siri_df(day_path, siri_type)
        rts_route["ALT_NUM"] = rts_route["ALT_NUM"].astype(int)
        siri_day_df = siri_day_df.loc[siri_day_df["LineRef"].astype(int).isin(list(rts_route["ALT_NUM"]))]

        if len(siri_day_df) == 0:
            continue
        siri_day_df = siri_day_df.loc[siri_day_df["OperatorRef"] != "2"]  # remove trains
        siri_day_df = siri_day_df.drop(columns=["OperatorRef"])

        for hour in hours:
            print(hour)
            siri_day_df_hour = siri_day_df.loc[siri_day_df["hour"].isin(range(int(hour - 1), int(hour + 2)))]

            groupby_list = [
                (x[1], delete_parts_not_in_study_area, rts_route, hour, study_area, only_area)
                for x in list(siri_day_df_hour.groupby(["FramedVehicleJourneyRef_DatedVehicleJourneyRef"]))
            ]

            # with mp.Pool(processes=mp.cpu_count()) as p:
            #     trips = list(
            #         tqdm(
            #             p.imap_unordered(
            #                 trip_process,
            #                 groupby_list,
            #                 chunksize=50,
            #             ),
            #             total=len(groupby_list),
            #         )
            #     )
            trips = [trip_process(x) for x in groupby_list]

            route_time.append(trips)

    route_time2 = [sublist for l in route_time for sublist in l]
    route_time2 = [x for x in route_time2 if x]
    route_time_df = pd.DataFrame(
        route_time2, columns=["LineRef", "trip_duration", "avg_speed_kmh", "trip_length", "hour"]
    )

    route_time_df = route_time_df.sort_values("LineRef")
    routes_stats = route_time_df.groupby(["LineRef", "hour", "trip_length"])[["trip_duration", "avg_speed_kmh"]].agg(
        ["min", "max", "mean", np.median, "std", "var", "count"]
    )
    rts_route["ALT_NUM"] = rts_route["ALT_NUM"].astype(str)
    # routes_stats["LineRef"] = routes_stats["LineRef"].astype(str)

    # routes_stats.reset_index().reset_index()
    routes_stats.columns = routes_stats.columns.map("|".join).str.strip("|")
    routes_stats = routes_stats.reset_index()
    routes_stats = routes_stats.merge(
        rts_route[["ALT_NUM", "MAKAT"]], right_on="ALT_NUM", left_on="LineRef", how="left"
    )

    duration_columns = [x for x in routes_stats.columns if "duration" in x]
    for x in duration_columns:
        routes_stats[x.replace("trip_duration", "trip_duration_m")] = routes_stats[x] / 60
        if x == "trip_duration|var":
            routes_stats["trip_duration_m|var"] = routes_stats["trip_duration_m|var"] / 60  # * variance is in ^2
        routes_stats[x.replace("trip_duration", "trip_duration_m")] = routes_stats[x] / 60
    routes_stats = routes_stats.drop(columns=duration_columns)
    routes_stats = routes_stats.round(2)

    route_time_df["trip_duration_m"] = round(route_time_df["trip_duration"] / 60, 2)

    # route_time_df.to_excel(
    #     r"T:\90532_Beit_Shemesh_re-irgun\2024\הסטת קווי פרוזדור ליפו\data\נסיעות קו 62 היכל המשפט.xlsx",
    #     sheet_name="נסיעות",
    # )

    # with pd.ExcelWriter(
    #     r"T:\90532_Beit_Shemesh_re-irgun\2024\הסטת קווי פרוזדור ליפו\data\נסיעות קו 62 היכל המשפט.xlsx",
    #     mode="a",
    #     if_sheet_exists="new",
    # ) as f:
    #     routes_stats.to_excel(f, sheet_name="סטטיסטיקה")


if __name__ == "__main__":
    year = 2026
    month = "06"
    hours = [14,15,16,17,18,19]
    rts_dir = r"T:\RTS\Israel\Israel_January_2026\shape_files"
    rts_route_path = glob(rf"{rts_dir}/**/*Routes*.shp")[0]
    rts_stops_path = glob(rf"{rts_dir}/**/stops*.shp")[0]
    dir_edges = glob(fr"{rts_dir}\outputs\route_edges*/route_edges_*.parquet")[0]

    #study_area_path = r"T:\90532_Beit_Shemesh_re-irgun\2024\הסטת קווי פרוזדור ליפו\Layers\Heikhal ha-Mishpat.shp"
    study_area_path = r"C:\Users\Naama\Documents\Naama\TA_new_lanes_speeds\tested_area.gpkg"
    # filter_name = "Jerusalem, Israel"
    filter_name = None
    only_area = False
    # eshkol_id = 134  # ירושלים מרכז
    # eshkol_id = 75  # רחובות פרברי
    eshkol_id = None  #
    # op_code = 16  # סופרבוס
    op_code = None
    siri_type = "hasadna"

    weekend = False
    makat_list = None
    altnum_list = [11537, 24281, 38931]

    delete_parts_not_in_study_area = True

    main(year, month, rts_route_path, siri_type, hours, eshkol_id, op_code, delete_parts_not_in_study_area, study_area_path, only_area, makat_list, altnum_list, weekend)
