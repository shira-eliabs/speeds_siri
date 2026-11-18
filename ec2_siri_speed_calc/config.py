"""Shared config defaults for EC2 SIRI speed + hybrid GTFS/RTS edges."""

from __future__ import annotations

import os
from pathlib import Path

PKG = Path(__file__).resolve().parent
REPORTS = PKG / "reports"
FROZEN = PKG / "frozen_edges"

# Thresholds (locked)
HAUSDORFF_MAX_M = 200.0
LENGTH_REL_DIFF_MAX = 0.03

# Default local/dev paths (override via env on EC2)
GTFS_ZIP = Path(
    os.getenv(
        "GTFS_ZIP",
        r"T:\GIS\gtfs\2026\28-04-2026\GTFS_israel-public-transportation_28042026.zip",
    )
)
CLUSTER_ZIP = Path(
    os.getenv(
        "CLUSTER_ZIP",
        r"T:\GIS\gtfs\2026\28-04-2026\ClusterToLine_28042026.zip",
    )
)
RTS_ROUTES = Path(
    os.getenv(
        "RTS_ROUTES",
        r"T:\RTS\Israel\Israel_April_2026\shape_files\RouteSystem\Routes_4326.shp",
    )
)
RTS_ROUTE_EDGES = Path(
    os.getenv(
        "RTS_ROUTE_EDGES",
        r"T:\RTS\Israel\Israel_April_2026\shape_files\outputs\route_edges\route_edges_2026.parquet",
    )
)
STREET_NET = Path(
    os.getenv(
        "STREET_NET",
        r"T:\RTS\Israel\Israel_April_2026\shape_files\outputs\output_street_nets_coords\street_net_2026.parquet",
    )
)
OUTPUT_DIR = Path(os.getenv("SPEED_OUTPUT_DIR", str(PKG / "data")))

# Map-match knobs
MAPMATCH_SAMPLE_M = 40.0
MAPMATCH_SEARCH_M = 55.0
MAPMATCH_AZIMUTH_MAX_DEG = 35.0
