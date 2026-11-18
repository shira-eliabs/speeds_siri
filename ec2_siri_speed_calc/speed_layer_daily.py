"""
Daily SIRI → edge speed calc using hybrid route edges (EC2 package-local).

Adapted from parent speed_layer_from_siri.py; uses local speed_core / geometry_tools.
"""

from __future__ import annotations

import os
import tempfile
from glob import glob
from pathlib import Path
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from tqdm import tqdm

from geometry_tools import remove_too_close_points
from speed_core import duration_on_edges, filter_too_far_gps


def speed_per_trip(zipped):
    trip_df, edges_files_trip, temp_dir_name = zipped
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
    if len(trip_hours) > 1:
        bad = [x for x in [trip_hours[i] - trip_hours[i + 1] for i in range(len(trip_hours) - 1)] if x < -2]
        if bad:
            return f"{trip_name} bad hours"

    trip_df = trip_df.drop_duplicates(subset=["RecordedAtTime", "MonitoredCall_DistanceFromStop"])
    trip_df = trip_df.sort_values("RecordedAtTime")
    trip_df = remove_too_close_points(trip_df)

    edges_files_trip = edges_files_trip.sort_values("seq").set_index("seq", drop=False)
    if "cumulative_dist" not in edges_files_trip.columns:
        edges_files_trip = edges_files_trip.copy()
        edges_files_trip["cumulative_dist"] = edges_files_trip.length.cumsum()

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
    moving = route_edges_with_duration[route_edges_with_duration.speed_kmh > 0.5]
    if len(moving) == 0:
        return f"{trip_name} too slow"
    index_first_movement = moving.index[0]
    route_edges_with_duration.loc[: index_first_movement - 1, "speed_kmh"] = None
    index_last_movement = moving.index[-1]
    if index_last_movement < max(route_edges_with_duration.index):
        route_edges_with_duration.loc[index_last_movement + 1 :, "speed_kmh"] = None

    route_edges_with_duration = route_edges_with_duration.dropna(subset=["from", "to"])
    if "cumulative_dist" not in route_edges_with_duration.columns:
        route_edges_with_duration["cumulative_dist"] = 0.0

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

    edges_geom = edges_files_trip.reset_index(drop=True).filter(["ALT_NUM", "seq", "geometry"])
    route_edges_with_duration = route_edges_with_duration.merge(edges_geom, on=["ALT_NUM", "seq"], how="left")
    route_edges_with_duration = gpd.GeoDataFrame(route_edges_with_duration, geometry="geometry", crs=2039)
    route_edges_with_duration = route_edges_with_duration.loc[route_edges_with_duration["duration_sec"] > 0]
    route_edges_with_duration["speed_kmh"] = (route_edges_with_duration.length / 1000) / (
        route_edges_with_duration["duration_sec"] / 60 / 60
    )
    route_edges_with_duration = route_edges_with_duration.loc[route_edges_with_duration["speed_kmh"] <= 120]
    route_edges_with_duration = route_edges_with_duration.drop(
        columns=["geometry", "speed_kmh_og", "len", "seq", "cumulative_dist"], errors="ignore"
    )
    out = Path(temp_dir_name) / f"{trip_name}.feather"
    route_edges_with_duration.reset_index(drop=True).to_feather(out)
    return None


def run_daily_speed(
    siri_day_df: gpd.GeoDataFrame,
    edges_files_gdf: gpd.GeoDataFrame,
    day_path: str,
    max_workers: Optional[int] = None,
) -> None:
    """Process one day of SIRI against hybrid edges; write edges_speed_{hour}.parquet."""
    os.makedirs(day_path, exist_ok=True)
    finished = glob(f"{day_path}/edges_speed_*.parquet")
    if len(finished) == 24:
        print("Day already complete", flush=True)
        return

    edges_by_alt = {
        str(alt): gdf.sort_values("seq").reset_index(drop=True)
        for alt, gdf in edges_files_gdf.groupby("ALT_NUM")
    }

    trip_groups = list(siri_day_df.groupby(["FramedVehicleJourneyRef_DatedVehicleJourneyRef", "ALT_NUM"]))
    with tempfile.TemporaryDirectory(prefix="siri_trip_") as temp_dir:
        jobs = []
        for (trip_id, alt), trip_df in trip_groups:
            alt_s = str(alt)
            edges = edges_by_alt.get(alt_s)
            if edges is None:
                try:
                    edges = edges_by_alt.get(str(int(float(alt_s))))
                except Exception:
                    edges = None
            jobs.append((trip_df.copy(), edges, temp_dir))

        errors = []
        # Sequential is safer cross-platform; EC2 can later wrap ProcessPool
        for job in tqdm(jobs, desc="Speed per trip"):
            try:
                err = speed_per_trip(job)
                if err:
                    errors.append(err)
            except Exception as e:
                errors.append(str(e))

        if errors:
            with open(rf"{day_path}/errors.txt", "w", encoding="utf-8") as fp:
                fp.write("\n".join(errors))

        feathers = glob(temp_dir + "/*.feather")
        if not feathers:
            print("No trip feathers written", flush=True)
            return
        edged = pd.concat([pd.read_feather(f) for f in feathers], ignore_index=True)
        if "ROUTE_ID" in edged.columns:
            edged = edged.drop(columns=["ROUTE_ID"])
        edged = edged.round(2)
        for h in edged["hour"].dropna().unique():
            edged.loc[edged["hour"] == h].to_parquet(f"{day_path}/edges_speed_{int(h)}.parquet", index=False)
