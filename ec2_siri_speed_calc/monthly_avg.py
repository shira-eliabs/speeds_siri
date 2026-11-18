"""Month-end averages from daily edges_speed parquet (street from/to)."""

from __future__ import annotations

from glob import glob
from pathlib import Path
from typing import List, Union

import geopandas as gpd
import numpy as np
import pandas as pd

from siri_s3 import leave_out_for_time_period


def avg_monthly_time(
    hour: int,
    month_path: Union[str, Path],
    unique_edges: gpd.GeoDataFrame,
    leave_out: List[int] = None,
) -> Path:
    leave_out = leave_out if leave_out is not None else leave_out_for_time_period("weekdays")
    month_path = Path(month_path)
    files = glob(str(month_path / "**" / f"edges_speed_{hour}.parquet"), recursive=True)
    if not files:
        raise FileNotFoundError(f"No edges_speed_{hour}.parquet under {month_path}")

    hour_speed = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
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
    ue = unique_edges.copy()
    if "length" not in ue.columns:
        ue["length"] = ue.to_crs(2039).length
    hour_speed_groupby = hour_speed_groupby.merge(ue[["from", "to", "length", "geometry"]], on=["from", "to"])
    hour_speed_groupby = gpd.GeoDataFrame(hour_speed_groupby, crs=2039)
    hour_speed_groupby["duration_minutes_median_speed"] = (hour_speed_groupby["length"] / 1000) / (
        hour_speed_groupby["speed_kmh_median"] / 60
    )
    hour_speed_groupby["duration_minutes_mean_speed"] = (hour_speed_groupby["length"] / 1000) / (
        hour_speed_groupby["speed_kmh_mean"] / 60
    )
    hour_speed_groupby["from"] = "str" + hour_speed_groupby["from"].astype(str)
    hour_speed_groupby["to"] = "str" + hour_speed_groupby["to"].astype(str)

    if leave_out == [4, 5]:
        period = "weekdays"
    elif leave_out == [0, 1, 2, 3, 6]:
        period = "weekend"
    elif leave_out == [0, 1, 2, 3, 5, 6]:
        period = "friday"
    else:
        period = "custom"

    out = month_path / f"{period}_{hour}_oclock.parquet"
    hour_speed_groupby.to_parquet(out)
    return out


def avg_route_monthly_time(
    month_path: Union[str, Path],
    unique_edges: gpd.GeoDataFrame,
    altnum_makat: dict,
    leave_out: List[int] = None,
) -> None:
    leave_out = leave_out if leave_out is not None else leave_out_for_time_period("weekdays")
    month_path = Path(month_path)
    files = glob(str(month_path / "**" / "edges_speed_*.parquet"), recursive=True)
    ue = unique_edges.copy()
    if "length" not in ue.columns:
        ue["length"] = ue.to_crs(2039).length

    for hour in range(0, 24):
        hour_files = [f for f in files if f"edges_speed_{hour}.parquet" in f.replace("\\", "/")]
        if not hour_files:
            continue
        frames = []
        for f in hour_files:
            df = pd.read_parquet(f)
            if "start_hour" in df.columns:
                df = df.loc[df["start_hour"] == hour]
            if len(df):
                frames.append(df)
        if not frames:
            continue
        combined = pd.concat(frames, ignore_index=True)
        result = (
            combined.groupby(["ALT_NUM", "from", "to", "start_hour"])
            .agg(
                speed_kmh_mean=("speed_kmh", "mean"),
                speed_kmh_median=("speed_kmh", "median"),
                speed_kmh_std=("speed_kmh", "std"),
                speed_kmh_count=("speed_kmh", "count"),
                duration_sec_mean=("duration_sec", "mean"),
                duration_sec_std=("duration_sec", "std"),
            )
            .reset_index()
        )
        result = result.merge(ue[["from", "to", "length", "geometry"]], on=["from", "to"])
        result = gpd.GeoDataFrame(result, crs=2039)
        result["duration_minutes_median_speed"] = (result["length"] / 1000) / (result["speed_kmh_median"] / 60)
        result["duration_minutes_mean_speed"] = (result["length"] / 1000) / (result["speed_kmh_mean"] / 60)
        result["from"] = "str" + result["from"].astype(str)
        result["to"] = "str" + result["to"].astype(str)
        result["MAKAT"] = result["ALT_NUM"].map(altnum_makat)
        result.to_parquet(month_path / f"routes_{hour}.parquet")
        print(f"Saved routes_{hour}.parquet ({len(result)} rows)", flush=True)


def run_monthly_avg(
    output_dir: Union[str, Path],
    year: int,
    month: int,
    edges_files_gdf: gpd.GeoDataFrame,
    hours=None,
    time_period: str = "weekdays",
) -> None:
    if hours is None:
        hours = list(range(24))
    month_path = Path(output_dir) / str(year) / f"{month:02d}"
    unique_edges = edges_files_gdf.drop_duplicates(["from", "to"]).copy()
    unique_edges["length"] = unique_edges.to_crs(2039).length
    altnum_makat = (
        edges_files_gdf[["ALT_NUM", "MAKAT"]].drop_duplicates().set_index("ALT_NUM")["MAKAT"].astype(str).to_dict()
    )
    leave_out = leave_out_for_time_period(time_period)
    for h in hours:
        try:
            out = avg_monthly_time(h, month_path, unique_edges, leave_out=leave_out)
            print(f"Wrote {out}", flush=True)
        except FileNotFoundError as e:
            print(e, flush=True)
    avg_route_monthly_time(month_path, unique_edges, altnum_makat, leave_out=leave_out)
