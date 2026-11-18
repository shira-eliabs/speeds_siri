"""EC2 month-end entrypoint: monthly_avg from daily edges_speed outputs."""

from __future__ import annotations

import argparse
from pathlib import Path

from config import OUTPUT_DIR
from monthly_avg import run_monthly_avg
from read_hybrid_route_edges import read_hybrid_route_edges


def main():
    p = argparse.ArgumentParser(description="EC2 month-end SIRI speed averages")
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--month", type=int, required=True)
    p.add_argument("--output-dir", default=str(OUTPUT_DIR))
    p.add_argument("--time-period", default="weekdays")
    args = p.parse_args()

    edges = read_hybrid_route_edges(args.year, args.month)
    run_monthly_avg(
        output_dir=args.output_dir,
        year=args.year,
        month=args.month,
        edges_files_gdf=edges,
        time_period=args.time_period,
    )
    print("Month-end averages complete", flush=True)


if __name__ == "__main__":
    main()
