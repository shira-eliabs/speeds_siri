"""
End-to-end route speed (mean + std) per ALT_NUM per hour from S3 SIRI realtime.

Given makat_list or alt_num_list, downloads the relevant month/days/hours from S3,
runs the same edge pipeline as speed_layer_from_siri (duration_on_edges + standing trim),
collapses each trip to one e2e speed, and aggregates mean/std per ALT_NUM per hour.

No GUI / Hasadna path.
"""

from __future__ import annotations

import calendar
import gc
import os
import warnings
from datetime import datetime
from glob import glob
from typing import List, Literal, Optional, Tuple, Union, get_args

import duckdb
import geopandas as gpd
import multiprocessing as mp
import pandas as pd
from tqdm import tqdm

import utils
from speed_layer_from_siri import duration_on_edges, filter_too_far_gps, read_route_edges

warnings.filterwarnings("ignore")
pd.set_option("display.max_columns", 500)

_TIME_TYPES = Literal["weekend", "weekdays", "friday"]
DEFAULT_DATA_ROOT = r"T:\GIS\Speeds\siri-realtime\data"


def e2e_speed_per_trip(zipped: Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]) -> Union[dict, str]:
    """
    End-to-end speed for one trip via duration_on_edges + standing trim, then sum length/duration.

    Same core filters / edge recompute as speed_layer_from_siri.speed_per_trip; returns one
    e2e row instead of writing per-edge feather.
    """
    trip_df, edges_files_trip = zipped
    trip_name = "-".join(
        str(n) for n in list(trip_df[["FramedVehicleJourneyRef_DatedVehicleJourneyRef", "ALT_NUM"]].iloc[0])
    )

    if len(trip_df) <= 10:
        return f"{trip_name} too few gps points"

    if edges_files_trip is None or len(edges_files_trip) == 0:
        return f"{trip_name} no route edges"

    trip_hours = trip_df["RecordedAtTime"].dt.hour.value_counts()
    trip_hours = list(trip_hours.loc[trip_hours >= 10].index)
    trip_hours.sort()
    bad_continuity = [x for x in [trip_hours[i] - trip_hours[i + 1] for i in range(len(trip_hours) - 1)] if x < -2]
    if len(bad_continuity) > 0:
        return f"{trip_name} bad hours"

    trip_df = trip_df.drop_duplicates(subset=["RecordedAtTime", "MonitoredCall_DistanceFromStop"])
    trip_df = trip_df.sort_values("RecordedAtTime")
    
    
    trip_df = utils.remove_too_close_points(trip_df)

    trip_df = filter_too_far_gps(edges_files_trip.dissolve()["geometry"].iloc[0], trip_df)
    if trip_df is None:
        return f"{trip_name} bad trip"

    if len(trip_df) <= 5:
        return f"{trip_name} too few gps points"

    trip_df = trip_df.sort_values("RecordedAtTime").reset_index(drop=True)

    route_edges_with_duration = duration_on_edges(trip_df, edges_files_trip)
    if route_edges_with_duration is None:
        return f"{trip_name} no route edges with speed"
    if len(route_edges_with_duration[route_edges_with_duration.speed_kmh > 3]) == 0:
        return f"{trip_name} too slow"

    

    route_edges_with_duration = route_edges_with_duration.loc[route_edges_with_duration["duration_sec"] > 0]

    # removes beginning of drive when bus is probably standing
    index_first_movement = route_edges_with_duration[route_edges_with_duration.speed_kmh > 0.5].index[0]
    route_edges_with_duration.loc[: index_first_movement - 1, "speed_kmh"] = None

    # removes end of drive when bus is probably standing
    index_last_movement = route_edges_with_duration[route_edges_with_duration.speed_kmh > 0.5].index[-1]
    if index_last_movement < max(route_edges_with_duration.index):
        route_edges_with_duration.loc[index_last_movement + 1 :, "speed_kmh"] = None

    route_edges_with_duration = route_edges_with_duration.dropna(subset=["from", "to"])
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

    edges_geom = edges_files_trip.filter(["ALT_NUM", "seq", "geometry"])
    edges_geom = edges_geom.reset_index(drop=True)
    route_edges_with_duration = route_edges_with_duration.merge(
        edges_geom,
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
    route_edges_with_duration = route_edges_with_duration.dropna(subset=["speed_kmh"])
    route_edges_with_duration = route_edges_with_duration.loc[route_edges_with_duration["duration_sec"] > 0]

    if len(route_edges_with_duration) == 0:
        return f"{trip_name} no valid edges after trim"

    length_m = float(route_edges_with_duration.length.sum())
    duration_sec = float(route_edges_with_duration["duration_sec"].sum())
    if length_m <= 0 or duration_sec <= 0:
        return f"{trip_name} zero distance/time"

    avg_speed = (length_m / 1000.0) / (duration_sec / 3600.0)
    if avg_speed <= 0 or avg_speed > 120:
        return f"{trip_name} invalid speed"

    alt_num = int(route_edges_with_duration["ALT_NUM"].iloc[0])
    start_hour = int(route_edges_with_duration["start_hour"].iloc[0])
    if "MAKAT" in edges_files_trip.columns:
        makat = int(edges_files_trip["MAKAT"].iloc[0])
    else:
        makat = None

    return {
        "ALT_NUM": alt_num,
        "MAKAT": makat,
        "hour": start_hour,
        "speed_kmh": round(avg_speed, 2),
        "duration_sec": round(duration_sec, 1),
        "trip_length_m": round(length_m, 1),
        "trip_id": trip_df["FramedVehicleJourneyRef_DatedVehicleJourneyRef"].iloc[0],
    }


def mp_e2e_speeds(siri_day_df: gpd.GeoDataFrame, edges_files_gdf: gpd.GeoDataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """Run e2e_speed_per_trip for all trips in a day (edges grouped by ALT_NUM)."""
    siri_day_df_groupby = list(siri_day_df.groupby(["FramedVehicleJourneyRef_DatedVehicleJourneyRef", "ALT_NUM"]))
    siri_day_df_groupby = [(str(int(x[0][1])), x[1]) for x in siri_day_df_groupby]
    # Index by seq so duration_on_edges can slice edges with loc[seq_min:seq_max]
    edges_groupby = {
        str(int(alt)): gdf.sort_values("seq").set_index("seq", drop=False)
        for alt, gdf in edges_files_gdf.groupby("ALT_NUM")
    }

    zipped = [(x[1], edges_groupby.get(x[0])) for x in siri_day_df_groupby]

    with mp.Pool(processes=mp.cpu_count()) as pool:
        results = list(
            tqdm(
                pool.imap_unordered(e2e_speed_per_trip, zipped, chunksize=10),
                total=len(zipped),
                desc="E2E per trip",
            )
        )

    errors = [x for x in results if isinstance(x, str)]
    rows = [x for x in results if isinstance(x, dict)]
    return pd.DataFrame(rows), errors


def read_rts_edges(
    year: int,
    month: int,
    makat_list: Optional[List[int]] = None,
    alt_num_list: Optional[List[int]] = None,
) -> gpd.GeoDataFrame:
    """Load route edges for nearest RTS month; filter by makat or altnum."""
    if makat_list is None and alt_num_list is None:
        raise ValueError("Provide makat_list or alt_num_list")

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
    rts_route_path = glob(RTS_dir + r"\**\Routes*.shp", recursive=True)[0]

    split_path = rts_route_path.split("\\")
    for i, x in enumerate(split_path):
        if "shape" in x.lower():
            break
    dir_shp = "\\".join(split_path[: i + 1])

    dir_edges = glob(dir_shp + r"\outputs\**\route*.parquet", recursive=True)
    if len(dir_edges) == 0:
        raise FileNotFoundError("need to create route edges")
    dir_edges = dir_edges[0]

    edges_files_gdf = read_route_edges(dir_edges)
    edges_files_gdf["ALT_NUM"] = edges_files_gdf["ALT_NUM"].astype(int)
    if "MAKAT" in edges_files_gdf.columns:
        edges_files_gdf["MAKAT"] = edges_files_gdf["MAKAT"].astype(int)

    if makat_list is not None:
        makat_list = [int(x) for x in makat_list]
        if "MAKAT" not in edges_files_gdf.columns:
            raise ValueError("route edges lack MAKAT; filter by alt_num_list instead")
        edges_files_gdf = edges_files_gdf.loc[edges_files_gdf["MAKAT"].isin(makat_list)]
    else:
        alt_num_list = [int(x) for x in alt_num_list]
        edges_files_gdf = edges_files_gdf.loc[edges_files_gdf["ALT_NUM"].isin(alt_num_list)]

    if len(edges_files_gdf) == 0:
        raise ValueError("No route edges matched makat_list / alt_num_list")
    return edges_files_gdf.reset_index(drop=True)


def load_siri_day(
    year: int,
    month: int,
    day: int,
    hours: List[int],
    dbfile: str,
    alt_num_list: List[int],
    time_period: str,
) -> Optional[gpd.GeoDataFrame]:
    """Download day hours from S3 into DuckDB (if needed) and return filtered SIRI GeoDataFrame."""
    tablename, siri_df = utils.siri_to_local_duckdb(
        hours,
        day,
        month,
        year,
        dbfile,
        alt_num_list=alt_num_list,
        time_period=time_period,
    )
    if tablename is None:
        return None

    if tablename == "finished" or siri_df is None:
        _, sql_query = utils.create_sql_query_n_table_name(alt_num_list, year, month, day, named_columns=True)
        with duckdb.connect(dbfile) as con:
            try:
                siri_df = con.sql(sql_query).df()
            except Exception:
                _, sql_query = utils.create_sql_query_n_table_name(alt_num_list, year, month, day, named_columns=False)
                try:
                    siri_df = con.sql(sql_query).df()
                except Exception:
                    return None
        if list(siri_df.columns) != utils.SIRI_COLUMNS and len(siri_df.columns) == len(utils.SIRI_COLUMNS):
            siri_df.columns = utils.SIRI_COLUMNS

    if siri_df is None or len(siri_df) == 0:
        return None

    rename = {
        "lineref": "ALT_NUM",
        "distancefromstop": "MonitoredCall_DistanceFromStop",
        "datedvehiclejourneyref": "FramedVehicleJourneyRef_DatedVehicleJourneyRef",
        "recordedattime": "RecordedAtTime",
        "originaimeddeparturetime": "OriginAimedDepartureTime",
    }
    siri_df = siri_df.rename(columns={k: v for k, v in rename.items() if k in siri_df.columns})

    if "longitude" in siri_df.columns:
        siri_df = utils.lon_lat_to_geom(siri_df, "longitude", "latitude")
    elif "VehicleLocation_Longitude" in siri_df.columns:
        siri_df = utils.lon_lat_to_geom(siri_df)

    siri_df["ALT_NUM"] = siri_df["ALT_NUM"].astype(int)
    siri_df = siri_df.loc[siri_df["ALT_NUM"].isin(alt_num_list)]
    if len(siri_df) == 0:
        return None

    siri_df["RecordedAtTime"] = pd.to_datetime(siri_df["RecordedAtTime"])
    siri_df["hour"] = siri_df["RecordedAtTime"].dt.hour
    if "OriginAimedDepartureTime" in siri_df.columns:
        siri_df["OriginAimedDepartureTime"] = pd.to_datetime(siri_df["OriginAimedDepartureTime"])
        siri_df["start_hour"] = siri_df["OriginAimedDepartureTime"].dt.hour
    else:
        siri_df["start_hour"] = siri_df["hour"]

    if hours is not None:
        siri_df = siri_df.loc[siri_df["start_hour"].isin(hours)]

    return siri_df


def aggregate_e2e_monthly(trip_speeds: pd.DataFrame) -> pd.DataFrame:
    """Mean / std / count of end-to-end speed (and duration) per ALT_NUM × hour."""
    if len(trip_speeds) == 0:
        return pd.DataFrame()

    stats = (
        trip_speeds.groupby(["ALT_NUM", "MAKAT", "hour"], as_index=False)
        .agg(
            speed_kmh_mean=("speed_kmh", "mean"),
            speed_kmh_std=("speed_kmh", "std"),
            speed_kmh_median=("speed_kmh", "median"),
            count=("speed_kmh", "count"),
            duration_sec_mean=("duration_sec", "mean"),
            duration_sec_std=("duration_sec", "std"),
            trip_length_m_mean=("trip_length_m", "mean"),
        )
        .round(2)
    )
    return stats.sort_values(["ALT_NUM", "hour"]).reset_index(drop=True)


def main(
    year: int,
    month: int,
    out_dir: str,
    makat_list: Optional[List[int]] = None,
    alt_num_list: Optional[List[int]] = None,
    hours: Optional[List[int]] = None,
    time_period: str = "weekdays",
    data_root: str = DEFAULT_DATA_ROOT,
) -> pd.DataFrame:
    """
    Download SIRI for the month from S3 and write end-to-end mean/std per ALT_NUM per hour.

    Trip speeds use duration_on_edges + standing trim from speed_layer_from_siri, then
    sum edge length/duration per trip.
    """
    options = get_args(_TIME_TYPES)
    assert time_period in options, f"'{time_period}' is not in {options}"

    year = int(year)
    month = int(month)
    if hours is None:
        hours = list(range(0, 24))
    elif not isinstance(hours, list):
        hours = [int(hours)]
    else:
        hours = [int(h) for h in hours]

    print("Loading route edges...")
    edges_files_gdf = read_rts_edges(year, month, makat_list=makat_list, alt_num_list=alt_num_list)
    alt_nums = sorted(edges_files_gdf["ALT_NUM"].astype(int).unique().tolist())
    print(f"Routes: {len(alt_nums)} ALT_NUM → {alt_nums}")

    # Separate DuckDB from the national edge-layer cache so filtered downloads
    # do not overwrite full-day tables used by speed_layer_from_siri.py
    dbfile = os.path.join(data_root, str(year), f"{month:02d}", f"siri_{year}{month:02d}.duckdb")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.dirname(dbfile), exist_ok=True)

    relevant_days = utils.get_days_in_month(year, month, time_period=time_period)
    num_days = calendar.monthrange(year, month)[1]
    all_trips = []

    print(f"Per-day calc ({time_period}, {len(relevant_days)} days):")
    for day in range(1, num_days + 1):
        day_ts = pd.Timestamp(year=year, month=month, day=day)
        if day_ts not in relevant_days:
            continue
        print(f"  day {day}/{num_days}")
        siri_day = load_siri_day(year, month, day, hours, dbfile, alt_nums, time_period)
        if siri_day is None or len(siri_day) == 0:
            print("    no SIRI")
            continue

        trips_df, errors = mp_e2e_speeds(siri_day, edges_files_gdf)
        if len(errors) > 0:
            err_path = os.path.join(out_dir, f"errors_{year}{month:02d}{day:02d}.txt")
            with open(err_path, "w", encoding="utf-8") as fp:
                fp.write("\n".join(errors))
        if len(trips_df) > 0:
            trips_df["day"] = day
            day_out = os.path.join(out_dir, f"trips_e2e_{year}{month:02d}{day:02d}.parquet")
            trips_df.to_parquet(day_out, index=False)
            all_trips.append(trips_df)
        gc.collect()

    if len(all_trips) == 0:
        print("No trip speeds computed.")
        return pd.DataFrame()

    trip_speeds = pd.concat(all_trips, ignore_index=True)
    trip_speeds.to_parquet(os.path.join(out_dir, f"trips_e2e_{year}{month:02d}_{time_period}.parquet"), index=False)

    print("Monthly aggregation (mean + std per ALT_NUM × hour)...")
    stats = aggregate_e2e_monthly(trip_speeds)
    stats_path = os.path.join(out_dir, f"{time_period}_{year}{month:02d}_e2e_speed_per_altnum_hour")
    stats.to_parquet(stats_path + ".parquet", index=False)
    stats.to_csv(stats_path + ".csv", index=False)
    print(f"Saved {stats_path}.parquet / .csv  ({len(stats)} rows)")
    print("FINISHED!")
    return stats


if __name__ == "__main__":
    year = 2026
    month = 6
    out_dir = r"T:\90265_Jerusalem_Public_Transport\Jerusalem_center_cluster\data\56-36"
    makat_list = None
    alt_num_list = [22122, 22123]
    hours = None
    time_period = "weekdays"

    main(
        year=year,
        month=month,
        out_dir=out_dir,
        makat_list=makat_list,
        alt_num_list=alt_num_list,
        hours=hours,
        time_period=time_period,
    )
