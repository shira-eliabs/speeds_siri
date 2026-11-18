"""EC2 prep entrypoint: classify + hybrid route_edges freeze."""

from __future__ import annotations

import argparse

from build_hybrid_route_edges import build_hybrid_route_edges


def main():
    p = argparse.ArgumentParser(description="Build hybrid GTFS/RTS route_edges freeze")
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--month", type=int, default=4)
    args = p.parse_args()
    path = build_hybrid_route_edges(year=args.year, month=args.month)
    print(f"Hybrid freeze ready: {path}", flush=True)


if __name__ == "__main__":
    main()
