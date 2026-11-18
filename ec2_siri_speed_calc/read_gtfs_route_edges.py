"""Local GTFS-backed helpers for SIRI speed calc (package-local only)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

import geopandas as gpd

from gtfs_route_edges import write_month_freeze

PKG = Path(__file__).resolve().parent
FREEZE_DIR = PKG / "frozen_edges"


def read_gtfs_route_edges(
    year: int,
    month: int,
    freeze_dir: Union[str, Path] = FREEZE_DIR,
) -> gpd.GeoDataFrame:
    """
    Load month-frozen GTFS route edges produced by write_month_freeze / build_frozen_edges.

    Columns filtered to speed-layer contract: MAKAT, ROUTE_ID, ALT_NUM, from, to, seq, geometry.
    """
    freeze_dir = Path(freeze_dir)
    path = freeze_dir / f"route_edges_gtfs_{year}{month:02d}.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"Missing frozen GTFS route edges at {path}. Run build_frozen_edges.py first "
            "(only after Phase 0 audit PASS)."
        )
    gdf = gpd.read_parquet(path)
    gdf = gdf.drop_duplicates(["ALT_NUM", "seq", "from", "to"])
    keep = ["MAKAT", "ROUTE_ID", "ALT_NUM", "from", "to", "seq", "geometry"]
    gdf = gdf.filter(keep)
    if gdf.crs is None or gdf.crs.to_epsg() != 2039:
        gdf = gdf.set_crs(2039) if gdf.crs is None else gdf.to_crs(2039)
    gdf["ALT_NUM"] = gdf["ALT_NUM"].astype(str)
    gdf["MAKAT"] = gdf["MAKAT"].astype(str)
    return gdf


def build_april_2026_freeze(
    gtfs_zip: str = r"T:\GIS\gtfs\2026\28-04-2026\GTFS_israel-public-transportation_28042026.zip",
    cluster_zip: str = r"T:\GIS\gtfs\2026\28-04-2026\ClusterToLine_28042026.zip",
    step_m: float = 50.0,
) -> Path:
    """Month freeze for April 2026 using the locked 28-04-2026 feed."""
    return write_month_freeze(
        zip_path=gtfs_zip,
        out_dir=FREEZE_DIR,
        cluster_to_line_path=cluster_zip,
        step_m=step_m,
        year=2026,
        month=4,
    )
