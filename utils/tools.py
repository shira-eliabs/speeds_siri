import os
import pandas as pd
import geopandas as gpd
import multiprocessing as mp
from tqdm import tqdm


def write_blank_files(hour, day_path, error_list, route_based=None):
    if len(str(hour)) == 1:
        hour = f"0{hour}"

    if route_based:
        # with open(rf"{day_path}/{filter_place_holder}_superbus2/{hour}/errors_routes_{hour}.txt", "w") as fp:
        with open(rf"{day_path}/errors_routes_{hour}.txt", "w") as fp:
            fp.write("\n".join(error_list))
        pd.DataFrame().to_parquet(rf"{day_path}/route_edges_speed_{hour}.parquet")

    else:
        # with open(rf"{day_path}/{filter_place_holder}_superbus2/{hour}/errors_{hour}.txt", "w") as fp:
        with open(rf"{day_path}/errors_{hour}.txt", "w") as fp:
            fp.write("\n".join(error_list))
        pd.DataFrame().to_parquet(rf"{day_path}/edges_speed_{hour}.parquet")


def avg_groupby(edged_trip, edges_unique, groupby_columns, value_to_avg: str = "speed_kmh"):
    """
    Calculate average values grouped by specified columns and merge with edge geometry.

    Parameters
    ----------
    edged_trip : pd.DataFrame
        DataFrame containing trip data with edge information and values to average.
    edges_unique : pd.DataFrame
        DataFrame containing unique edges with 'from', 'to', and 'geometry' columns.
    groupby_columns : list or str
        Column name(s) to group by when calculating the average.
    value_to_avg : str, optional
        Name of the column to calculate the average for. Default is 'speed_kmh'.

    Returns
    -------
    gpd.GeoDataFrame
        GeoDataFrame with grouped averages merged with edge geometry,
        projected to EPSG:2039 coordinate reference system.
    """
    avg_edge = edged_trip.groupby(groupby_columns)[value_to_avg].mean().reset_index()
    avg_edge = avg_edge.merge(
        edges_unique[["from", "to", "geometry"]],
        on=[
            "from",
            "to",
        ],
        how="left",
    )
    avg_edge = gpd.GeoDataFrame(avg_edge, crs=2039)
    return avg_edge


def weighted_average(group: pd.DataFrame, value_column: str, weight_column: str) -> float:
    """
    Calculate the weighted average within a pandas group.

    Parameters
    ----------
        group : pd.DataFrame
            Grouped DataFrame.
        value_column : str
            Column containing values to be averaged.
        weight_column : str
            Column containing weights.

    Returns
    -------
    float
        Weighted average.
    """
    values = group[value_column]
    weights = group[weight_column]
    weighted_avg = (values * weights).sum() / weights.sum()
    return weighted_avg


def read_parquet_when_blank(parquet_path):
    try:
        return gpd.read_parquet(parquet_path)
    except ValueError:
        pass


def mp_read_parquet_files(file_list):
    """multiprocessing for read_parquet_files"""
    chunksize = int(round(len(file_list) / (mp.cpu_count() * 2)))
    if chunksize == 0:
        chunksize = 1
    with mp.Pool(processes=mp.cpu_count() - 6) as p:
        gdf_list = list(
            tqdm(
                p.imap_unordered(read_parquet_when_blank, file_list, chunksize=chunksize),
                total=len(file_list),
            )
        )
    return gdf_list
