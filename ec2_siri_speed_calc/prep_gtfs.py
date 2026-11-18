"""GTFS prep helpers for bus routes and shape geometries (self-contained)."""

from __future__ import annotations

from typing import Optional, Tuple, Union
from zipfile import ZipFile

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point

pd.options.display.max_columns = 100


def _read_cluster_to_line(cluster_to_line_path: str) -> pd.DataFrame:
    """Read ClusterToLine from a CSV path or a zip containing a CSV."""
    if cluster_to_line_path.lower().endswith(".zip"):
        with ZipFile(cluster_to_line_path) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith((".csv", ".txt")) and not n.endswith("/")]
            if not names:
                raise FileNotFoundError(f"No csv/txt inside {cluster_to_line_path}")
            with zf.open(names[0]) as f:
                return pd.read_csv(f)
    return pd.read_table(cluster_to_line_path, sep=",", index_col=False)


def shapes_to_geometry(df: pd.DataFrame) -> LineString:
    return LineString([[x, y] for x, y in zip(df["shape_pt_lon"], df["shape_pt_lat"])])


def load_gtfs_tables(zip_path: str, only: Optional[list] = None) -> dict:
    """Load .txt tables from a GTFS zip into DataFrames (deduped).

    Parameters
    ----------
    only : optional list of basenames without .txt (e.g. ['routes','trips','shapes']).
    """
    tables = {}
    wanted = set(only) if only else None
    with ZipFile(zip_path) as zip_file:
        for text_file in zip_file.infolist():
            if not text_file.filename.endswith(".txt") or text_file.is_dir():
                continue
            key = text_file.filename.split("/")[-1].replace(".txt", "")
            if wanted is not None and key not in wanted:
                continue
            tables[key] = pd.read_csv(zip_file.open(text_file.filename)).drop_duplicates()
    return tables


def build_shape_geometries(shapes: pd.DataFrame) -> gpd.GeoSeries:
    """Group shapes.txt into LineStrings keyed by shape_id (EPSG:4326)."""
    shapes = shapes.sort_values(["shape_id", "shape_pt_sequence"])
    # Faster than groupby.apply(LineString): build from arrays
    records = []
    for sid, grp in shapes.groupby("shape_id", sort=False):
        coords = list(zip(grp["shape_pt_lon"].to_numpy(), grp["shape_pt_lat"].to_numpy()))
        if len(coords) >= 2:
            records.append((sid, LineString(coords)))
    if not records:
        return gpd.GeoSeries(dtype="geometry", crs=4326)
    idx, geoms = zip(*records)
    return gpd.GeoSeries(list(geoms), index=list(idx), crs=4326)


def route_shapes_gdf(
    zip_path: str,
    cluster_to_line_path: Optional[str] = None,
    filter_line_type: Union[str, list, None] = None,
) -> gpd.GeoDataFrame:
    """
    Lightweight route→shape GeoDataFrame for audits / edge building.

    One row per route_id with Makat, route_desc, shape geometry (EPSG:2039).
    """
    tables = load_gtfs_tables(zip_path, only=["routes", "trips", "shapes"])
    routes = tables["routes"].loc[tables["routes"]["route_type"] == 3].copy()
    routes["Makat"] = routes["route_desc"].astype(str).str.split("-").map(lambda x: x[0])
    routes["ALT_NUM"] = routes["route_id"].astype(str)
    routes["MAKAT"] = routes["Makat"].astype(str)

    if filter_line_type and cluster_to_line_path:
        cluster_to_line = _read_cluster_to_line(cluster_to_line_path)
        if isinstance(filter_line_type, list):
            cluster_to_line = cluster_to_line.loc[cluster_to_line["LineTypeDesc"].isin(filter_line_type)]
        else:
            cluster_to_line = cluster_to_line.loc[cluster_to_line["LineTypeDesc"] == filter_line_type]
        routes = routes.loc[routes["Makat"].astype(int).isin(set(cluster_to_line["OfficeLineId"]))].reset_index(
            drop=True
        )
        cluster_to_line["OfficeLineId"] = cluster_to_line["OfficeLineId"].astype(str)
        routes = routes.merge(
            cluster_to_line[["OfficeLineId", "LineTypeDesc"]],
            left_on="Makat",
            right_on="OfficeLineId",
            how="left",
        ).drop(columns=["OfficeLineId"])

    trips = tables["trips"][["route_id", "shape_id"]].dropna().drop_duplicates()
    trips["route_id"] = trips["route_id"].astype(routes["route_id"].dtype)
    trips = trips.merge(routes[["route_id"]], on="route_id", how="inner")
    # Prefer one shape per route_id (most common shape_id if multiple)
    shape_counts = trips.groupby(["route_id", "shape_id"]).size().reset_index(name="n")
    shape_counts = shape_counts.sort_values(["route_id", "n"], ascending=[True, False])
    best = shape_counts.drop_duplicates("route_id")

    shape_gs = build_shape_geometries(tables["shapes"])
    out = best.merge(routes, on="route_id", how="left")
    out = out.merge(shape_gs.rename("geometry"), left_on="shape_id", right_index=True, how="inner")
    gdf = gpd.GeoDataFrame(out, geometry="geometry", crs=4326).to_crs(2039)
    keep = [
        c
        for c in [
            "route_id",
            "route_desc",
            "route_short_name",
            "route_long_name",
            "Makat",
            "MAKAT",
            "ALT_NUM",
            "shape_id",
            "LineTypeDesc",
            "geometry",
        ]
        if c in gdf.columns
    ]
    return gdf[keep].reset_index(drop=True)


def prep_gtfs(
    zip_path: str,
    cluster_to_line_path: str,
    makat_route_list: Optional[list] = None,
    filter_line_type: str = None,
    city_filter: str = None,
    geom: bool = True,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Prepares GTFS data for further analysis by extracting relevant route information.

    Returns
    -------
    routes, stop_times, stops, trips, shapes, central_station_stops
    """
    tables = load_gtfs_tables(zip_path)
    routes = tables["routes"]
    shapes = tables["shapes"]
    stop_times = tables["stop_times"]
    stops = tables["stops"]
    trips = tables["trips"]
    calendar = tables["calendar"]

    central_station_stops = list(set(stops[stops["location_type"] == 1]["stop_code"]))
    stops.loc[stops["stop_code"].isin(central_station_stops), "location_type"] = 1
    stops.loc[stops["stop_desc"].fillna("-").str.contains("מסילת ברזל"), "location_type"] = 0
    central_station_stops = stops.loc[stops["location_type"] == 1]

    routes = routes.loc[routes["route_type"] == 3].copy()
    routes["Makat"] = routes["route_desc"].str.split("-").map(lambda x: x[0])

    routes.loc[routes["route_color"] == "FF9933", "uniqueness"] = "קווי תלמידים"
    routes.loc[routes["route_color"] == "33CC33", "uniqueness"] = "קווי הזנה לרכבת"
    routes.loc[routes["route_color"] == "9933FF", "uniqueness"] = "קווי לילה"
    routes.loc[routes["route_color"] == "3399FF", "uniqueness"] = "קווי ים"

    if filter_line_type:
        cluster_to_line = _read_cluster_to_line(cluster_to_line_path)
        if type(filter_line_type) == list:
            cluster_to_line = cluster_to_line.loc[cluster_to_line["LineTypeDesc"].isin(filter_line_type)]
        else:
            cluster_to_line = cluster_to_line.loc[cluster_to_line["LineTypeDesc"] == filter_line_type]
        routes = routes.loc[routes["Makat"].astype(int).isin(list(set(cluster_to_line["OfficeLineId"])))]
        routes = routes.reset_index(drop=True)
        cluster_to_line["OfficeLineId"] = cluster_to_line["OfficeLineId"].astype(str)
        routes = routes.merge(
            cluster_to_line[["OfficeLineId", "LineTypeDesc"]], left_on="Makat", right_on="OfficeLineId", how="left"
        ).drop(columns=["OfficeLineId"])
    routes["city"] = routes["route_long_name"].str.split("-").map(lambda x: x[1].replace("<", "") if len(x) > 1 else "")
    if city_filter:
        routes = routes.loc[routes["city"] == city_filter]

    calendar = calendar.reset_index(drop=True)
    trips = trips.merge(calendar, on="service_id")

    if makat_route_list:
        routes = routes[routes["Makat"].isin(makat_route_list)]

    trips = trips.merge(routes, on="route_id", how="inner")
    trips["OfficeLineId"] = trips["route_desc"].map(lambda x: int(str(x).split("-")[0]))

    stop_times = stop_times.merge(trips.drop(columns=["city"], errors="ignore"), on="trip_id")
    stop_times = stop_times[stop_times["route_id"].notnull()].reset_index(drop=True)

    first_stop = stop_times.loc[stop_times["stop_sequence"] == 1]
    first_stop = first_stop.drop_duplicates(subset=["route_id", "arrival_time"]).reset_index(drop=True)

    stops["city"] = (
        stops["stop_desc"].astype(str).map(lambda x: x.split("עיר: ")[1].split("רציף: ")[0] if "עיר: " in x else "")
    )
    stops["city"] = stops["city"].map(lambda x: x[:-1] if x.endswith(" ") else x)

    stops["stop_code"] = stops["stop_code"].astype(str)
    stops["stop_code"] = stops["stop_code"].map(lambda x: x if len(x) == 5 else x.zfill(5))
    stop_times = stop_times.merge(stops[["stop_code", "stop_id", "location_type", "city"]], on="stop_id", how="left")

    trips = trips.loc[trips["trip_id"].isin(list(set(first_stop["trip_id"])))]
    stop_times = stop_times.loc[stop_times["trip_id"].isin(list(set(first_stop["trip_id"])))]
    stop_times.loc[stop_times["pickup_type"] == 1, "pickup_drop_off"] = "Drop Only"
    stop_times.loc[stop_times["drop_off_type"] == 1, "pickup_drop_off"] = "Pickup Only"
    stop_times.loc[stop_times["pickup_drop_off"].isnull(), "pickup_drop_off"] = "both"

    if geom:
        shapes = shapes.sort_values("shape_pt_sequence")
        shapes = shapes.groupby("shape_id").apply(shapes_to_geometry, include_groups=False)

    stops = stops.loc[stops["stop_id"].isin(set(stop_times["stop_id"]))].reset_index(drop=True)

    stops["geometry"] = [Point(x) for x in zip(stops["stop_lon"], stops["stop_lat"])]
    stops = gpd.GeoDataFrame(stops, crs=4326)
    stops = stops.to_crs(2039)

    stop_times_line = stop_times.drop_duplicates(["stop_id", "route_desc", "stop_code"]).drop(
        columns=["trip_id", "arrival_time", "departure_time", "start_date", "end_date"],
        errors="ignore",
    )
    routs_stops_count = stop_times_line.groupby("route_desc")["stop_sequence"].max()
    routes["number_of_stops"] = routes["route_desc"].map(routs_stops_count)

    deps_tuesday = first_stop.loc[(first_stop["tuesday"] == 1)].groupby("route_desc")["stop_sequence"].count()
    routes["Dep_tuesday"] = routes["route_desc"].map(deps_tuesday)

    for hour in range(24):
        hour_str = f"{hour:02d}"
        deps_by_hour = (
            first_stop.loc[(first_stop["tuesday"] == 1) & (first_stop["arrival_time"].str[:2] == hour_str)]
            .groupby("route_desc")["stop_sequence"]
            .count()
        )
        routes[f"Dep{hour_str}"] = routes["route_desc"].map(deps_by_hour).fillna(0).astype(int)

    return (routes, stop_times, stops, trips, shapes, central_station_stops)
