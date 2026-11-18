"""Load hybrid month-frozen route edges for daily speed calc."""

from __future__ import annotations

from pathlib import Path
from typing import Union

import geopandas as gpd

from config import FROZEN


def read_hybrid_route_edges(
    year: int,
    month: int,
    freeze_dir: Union[str, Path] = FROZEN,
) -> gpd.GeoDataFrame:
    freeze_dir = Path(freeze_dir)
    path = freeze_dir / f"route_edges_hybrid_{year}{month:02d}.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"Missing hybrid edges at {path}. Run build_hybrid_route_edges.py first."
        )
    gdf = gpd.read_parquet(path)
    gdf = gdf.drop_duplicates(["ALT_NUM", "seq", "from", "to"])
    keep = ["MAKAT", "ROUTE_ID", "ALT_NUM", "from", "to", "seq", "geometry"]
    for c in keep:
        if c not in gdf.columns:
            raise KeyError(f"Hybrid edges missing column {c}")
    gdf = gdf[keep].copy()
    if gdf.crs is None or gdf.crs.to_epsg() != 2039:
        gdf = gdf.set_crs(2039) if gdf.crs is None else gdf.to_crs(2039)
    gdf["ALT_NUM"] = gdf["ALT_NUM"].astype(str)
    gdf["MAKAT"] = gdf["MAKAT"].astype(str)
    gdf["from"] = gdf["from"].astype(str)
    gdf["to"] = gdf["to"].astype(str)
    return gdf
