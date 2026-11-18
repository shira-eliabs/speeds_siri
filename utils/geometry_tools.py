import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, MultiPoint, LineString, MultiLineString
import numpy as np
from typing import List, Tuple
from scipy.spatial import KDTree
import osmnx


def lon_lat_to_geom(
    df: pd.DataFrame, lon_column: str = "VehicleLocation_Longitude", lat_column: str = "VehicleLocation_Latitude"
) -> gpd.GeoDataFrame:
    """
    Convert DataFrame with longitude and latitude columns to a GeoDataFrame with Point geometries.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame containing longitude and latitude columns.
    lon_column : str
        Column containing longitude data, default "VehicleLocation_Longitude"
    lat_column : str
        Column containing latitude data, default "VehicleLocation_Latitude"

    Returns
    -------
    gdf : gpd.GeoDataFrame
        GeoDataFrame with Point geometries.
    """
    WGS84_bounds = {"longitude": {"min": 33.28, "max": 35.90}, "latitude": {"min": 29.404, "max": 33.424}}

    rule_1 = (df[lon_column] > WGS84_bounds["longitude"]["min"]) & (df[lon_column] < WGS84_bounds["longitude"]["max"])
    rule_2 = (df[lat_column] > WGS84_bounds["latitude"]["min"]) & (df[lat_column] < WGS84_bounds["latitude"]["max"])
    df = df.loc[rule_1 & rule_2]
    df["geometry"] = gpd.points_from_xy(df[lon_column].values, df[lat_column].values, crs="epsg:4326")
    df = gpd.GeoDataFrame(df, crs=4326)
    df = df.to_crs("epsg:2039")
    return df


# def remove_too_close_points(trip_df):
#     points = np.array(list(zip(trip_df.geometry.x, trip_df.geometry.y)))
#     tree = KDTree(points)

#     result = list(tree.query_ball_point(points, 50))

#     dict_neighbors = dict()
#     visited = list(range(len(points)))
#     while len(visited) > 0:
#         dict_neighbors[visited[0]] = result[visited[0]]
#         visited = list(set(visited) - set(result[visited[0]]))


#     return trip_df.iloc[list(dict_neighbors.keys())]
def remove_too_close_points(
    trip_df: gpd.GeoDataFrame,
    min_distance: float = 50.0,
    time_col: str | None = "RecordedAtTime",
    route_pos_col: str | None = None,
) -> gpd.GeoDataFrame:
    """
    Thin a trip so consecutive kept points are at least `min_distance` apart.

    Points are compared only with the last *kept* point in time order, never
    with points from another part of the trip. On circular routes or streets
    driven in both directions, the outbound and return passes stay intact even
    when they are within `min_distance` of each other.

    Parameters
    ----------
    trip_df : GeoDataFrame
        GPS points of ONE vehicle journey.
    min_distance : float
        Minimum spacing (CRS units, metres for EPSG:2039).
    time_col : str or None
        Column used to order the points. None keeps the current order.
    route_pos_col : str or None
        If given (e.g. "route_pos" from find_relevant_edge_seq), spacing is
        measured along the route instead of as a straight line. Points without
        a value in that column are kept and do not reset the spacing.
    """
    df = trip_df.sort_values(time_col, kind="stable") if time_col else trip_df
    n = len(df)
    if n == 0:
        return df

    if route_pos_col:
        pos = df[route_pos_col].to_numpy(dtype=float)
        keep = np.zeros(n, dtype=bool)
        last = None
        for i in range(n):
            if np.isnan(pos[i]):
                keep[i] = True
            elif last is None or abs(pos[i] - last) >= min_distance:
                keep[i] = True
                last = pos[i]
    else:
        xy = np.column_stack([df.geometry.x.to_numpy(), df.geometry.y.to_numpy()])
        keep = np.zeros(n, dtype=bool)
        keep[0] = True
        last = xy[0]
        for i in range(1, n):
            if np.hypot(*(xy[i] - last)) >= min_distance:
                keep[i] = True
                last = xy[i]

    return df.iloc[np.flatnonzero(keep)]


def cut_line_at_points(line: LineString, pdists: List[float]) -> MultiLineString:
    """
    Cut a LineString at specified distances along the line.

    Parameters
    ----------
    line : LineString
        The LineString to be cut.
    pdists : List[float]
        List of distances along the line where the cut should be made.

    Returns
    -------
    MultiLineString
        The resulting MultiLineString after cutting the input line.
    """
    ldists = [line.project(Point(i)) for i in line.coords[:]]
    ldists = ldists + pdists
    dists = np.array(sorted(ldists))

    split_route = []
    for i in range(len(pdists) - 1):
        if i == 0:
            split_route.append(LineString([line.interpolate(dists[(dists <= pdists[i])])][0]))
        max_dist = max([pdists[i + 1], pdists[i]])
        min_dist = min([pdists[i + 1], pdists[i]])
        split_route.append(LineString([line.interpolate(dists[(dists <= max_dist) & (dists >= min_dist)])][0]))
        if i == len(pdists):
            split_route.append(LineString([line.interpolate(dists[(dists >= pdists[i])])][0]))
    split_route = MultiLineString(split_route)
    return split_route


def cut_line_at_point(line: LineString, point: Point) -> List[LineString]:
    """
    Cut a LineString at a specified point.

    Parameters
    ----------
    line : LineString
        The LineString to be cut.
    point : Point
        The point where the cut should be made.

    Returns
    -------
    List[LineString]
        The resulting list of LineStrings after cutting the input line.
    """
    pdist = line.project(point)
    ldists = [line.project(Point(i)) for i in line.coords[:]]
    ldists.append(pdist)
    dists = np.array(sorted(ldists))
    new_lines = [
        LineString([line.interpolate(dists[dists <= pdist])][0]),
        LineString([line.interpolate(dists[dists >= pdist])][0]),
    ]
    return new_lines


def find_nearest(array: np.ndarray, value: float) -> int:
    """
    Find the index of the nearest value in an array.

    Parameters
    ----------
    array : np.ndarray
        The array to search.
    value : float
        The target value.

    Returns
    -------
    int
        The index of the nearest value in the array.
    """
    array = np.asarray(array)
    idx = (np.abs(array - value)).argmin()
    return array[idx]


def filter_extent(
    filter_name: str, gdf: gpd.GeoDataFrame, unify_column: str = "FramedVehicleJourneyRef_DatedVehicleJourneyRef"
) -> Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """
    Filter a GeoDataFrame to include only rows with geometries intersecting a buffered geographic extent.
    Parameters
    ----------
    filter_name : str
        The name of the geographic location to use for filtering (geocoded via OSM).
    gdf : gpd.GeoDataFrame
        The GeoDataFrame to filter.
    unify_column : str, optional
        The column name used to group and unify records not within the extent but connected to the geometries that are intersected (for example GPS points of a specific trip)
        Defaults to "FramedVehicleJourneyRef_DatedVehicleJourneyRef".
    Returns
    -------
    Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]
        A tuple containing filtered GeoDataFrames. Currently returns only the filtered
        GeoDataFrame; the return type annotation suggests three DataFrames but only one is returned.
    Notes
    -----
    - The filter geometry is geocoded using OSM and transformed to EPSG:2039.
    - The filter geometry is buffered by 200 meters to expand the filtering area.
    - Spatial filtering is performed using spatial index intersection queries.
    """

    filter_geom = osmnx.geocode_to_gdf(filter_name).to_crs(2039)
    filter_geom["geometry"] = filter_geom["geometry"].buffer(200)
    gdf = gdf.reset_index(drop=True)
    filter_indx, geom_indx = gdf.sindex.query(filter_geom.geometry, predicate="intersects")

    gdf_filter = set(gdf.iloc[geom_indx][unify_column])
    gdf = gdf.loc[gdf[unify_column].isin(list(gdf_filter))]
    gdf = gdf.reset_index(drop=True)

    return gdf
