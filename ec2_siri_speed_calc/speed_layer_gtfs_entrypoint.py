"""
Standalone GTFS-backed speed-layer entrypoint (Phase 2).

Does not modify parent speed_layer_from_siri.py.

- --dry-run: validates frozen GTFS edges + local speed_core import
- full day: optionally delegates to parent module if its deps are installed;
  otherwise prints how to run with an environment that has duckdb/utils
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

from read_gtfs_route_edges import read_gtfs_route_edges
import speed_core  # local trip→edge core

PKG = Path(__file__).resolve().parent
PARENT = PKG.parent


def _load_parent_speed_module():
    """Load parent speed_layer_from_siri.py without editing it."""
    path = PARENT / "speed_layer_from_siri.py"
    if not path.exists():
        raise FileNotFoundError(path)
    if str(PARENT) not in sys.path:
        sys.path.insert(0, str(PARENT))
    spec = importlib.util.spec_from_file_location("speed_layer_from_siri_readonly", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def main(year: int, month: int, day: int, output_dir: str, siri_type: str = "realtime") -> None:
    edges = read_gtfs_route_edges(year, month)
    print(f"Loaded GTFS route edges: {len(edges)} rows, {edges['ALT_NUM'].nunique()} ALT_NUM", flush=True)

    try:
        mod = _load_parent_speed_module()
    except ModuleNotFoundError as e:
        print(
            "Parent speed_layer_from_siri.py could not be imported "
            f"(missing dependency: {e}).\n"
            "Frozen GTFS edges and local speed_core are ready. "
            "Run this entrypoint in the same Python env as production SIRI calc "
            "(with duckdb, etc.), or call read_gtfs_route_edges() + speed_core from your own driver.",
            flush=True,
        )
        sys.exit(3)

    mod.hour = None
    mod.time_period = "weekdays"
    mod.main(
        output_dir=output_dir,
        siri_type=siri_type,
        day=day,
        month=month,
        year=year,
        edges_files_gdf=edges,
        filter_name=None,
        calc_avg_monthly=False,
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="GTFS-backed SIRI speed layer (ec2_siri_speed_calc only)")
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--month", type=int, default=4)
    p.add_argument("--day", type=int, default=None, help="Required unless --dry-run")
    p.add_argument(
        "--output-dir",
        default=r"T:\GIS\Speeds\siri-realtime\data",
        help="Output root (same layout as production)",
    )
    p.add_argument("--siri-type", default="realtime")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Load frozen GTFS edges + local speed_core only; do not run SIRI day calc",
    )
    args = p.parse_args()
    if args.dry_run:
        edges = read_gtfs_route_edges(args.year, args.month)
        assert hasattr(speed_core, "duration_on_edges")
        assert hasattr(speed_core, "find_relevant_edge_seq")
        print(
            f"dry-run OK: edges={len(edges)}, alts={edges['ALT_NUM'].nunique()}, "
            f"crs={edges.crs}, speed_core=OK",
            flush=True,
        )
    else:
        if args.day is None:
            p.error("--day is required unless --dry-run")
        main(args.year, args.month, args.day, args.output_dir, args.siri_type)
