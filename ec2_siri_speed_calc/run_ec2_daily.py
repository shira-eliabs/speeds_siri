"""EC2 daily entrypoint: S3 SIRI + hybrid edges → day edges_speed parquet."""

from __future__ import annotations

import argparse
from pathlib import Path

from config import OUTPUT_DIR
from read_hybrid_route_edges import read_hybrid_route_edges
from siri_s3 import read_siri_day, siri_to_local_duckdb
from speed_layer_daily import run_daily_speed


def main():
    p = argparse.ArgumentParser(description="EC2 daily SIRI speed layer (hybrid edges)")
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--month", type=int, required=True)
    p.add_argument("--day", type=int, required=True)
    p.add_argument("--output-dir", default=str(OUTPUT_DIR))
    p.add_argument("--time-period", default="weekdays")
    p.add_argument(
        "--skip-s3",
        action="store_true",
        help="Only read existing DuckDB day table (no S3 pull)",
    )
    args = p.parse_args()

    output_dir = Path(args.output_dir)
    month_dir = output_dir / str(args.year) / f"{args.month:02d}"
    day_path = month_dir / str(args.day)
    dbfile = str(month_dir / f"siri_{args.year}{args.month:02d}.duckdb")

    edges = read_hybrid_route_edges(args.year, args.month)
    print(f"Hybrid edges: {len(edges)} rows, {edges['ALT_NUM'].nunique()} alts", flush=True)

    if not args.skip_s3:
        tablename, _ = siri_to_local_duckdb(
            hours=None,
            day=args.day,
            month=args.month,
            year=args.year,
            dbfile=dbfile,
            time_period=args.time_period,
        )
        if tablename is None:
            print("No SIRI for this day / outside time_period", flush=True)
            return
        if tablename == "finished":
            print("DuckDB day table already present", flush=True)

    siri_day_df = read_siri_day(dbfile, args.year, args.month, args.day)
    print(f"SIRI points: {len(siri_day_df)}", flush=True)
    run_daily_speed(siri_day_df, edges, str(day_path))
    print(f"Done day → {day_path}", flush=True)


if __name__ == "__main__":
    main()
