"""
Build hybrid month route_edges freeze: use_rts + mapmatch_gtfs.

Writes frozen_edges/route_edges_hybrid_YYYYMM.parquet + meta + QA report.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

import geopandas as gpd
import pandas as pd

from classify_gtfs_vs_rts import classify_alts, write_classification
from config import (
    CLUSTER_ZIP,
    FROZEN,
    GTFS_ZIP,
    HAUSDORFF_MAX_M,
    LENGTH_REL_DIFF_MAX,
    REPORTS,
    RTS_ROUTE_EDGES,
    STREET_NET,
)
from mapmatch_gtfs_to_street_net import StreetNetMatcher, mapmatch_alts
from prep_gtfs import route_shapes_gdf


def _load_rts_route_edges(path: Union[str, Path], alt_nums: list) -> gpd.GeoDataFrame:
    alt_set = set(str(a) for a in alt_nums)
    gdf = gpd.read_parquet(path)
    gdf["ALT_NUM"] = gdf["ALT_NUM"].astype(str)
    gdf = gdf.loc[gdf["ALT_NUM"].isin(alt_set)].copy()
    if gdf.crs is None or gdf.crs.to_epsg() != 2039:
        gdf = gdf.to_crs(2039) if gdf.crs else gdf.set_crs(2039)
    keep = ["MAKAT", "ROUTE_ID", "ALT_NUM", "from", "to", "seq", "geometry"]
    for c in keep:
        if c not in gdf.columns:
            if c == "MAKAT":
                gdf["MAKAT"] = ""
            elif c == "ROUTE_ID":
                gdf["ROUTE_ID"] = gdf["ALT_NUM"]
    gdf = gdf[keep].copy()
    gdf["MAKAT"] = gdf["MAKAT"].astype(str)
    gdf["ROUTE_ID"] = gdf["ROUTE_ID"].astype(str)
    gdf["from"] = gdf["from"].astype(str)
    gdf["to"] = gdf["to"].astype(str)
    gdf["edge_source"] = "use_rts"
    return gdf


def build_hybrid_route_edges(
    year: int = 2026,
    month: int = 4,
    gtfs_zip: Union[str, Path] = GTFS_ZIP,
    cluster_zip: Union[str, Path] = CLUSTER_ZIP,
    rts_route_edges: Union[str, Path] = RTS_ROUTE_EDGES,
    street_net: Union[str, Path] = STREET_NET,
    out_dir: Optional[Path] = None,
) -> Path:
    out_dir = Path(out_dir) if out_dir else FROZEN
    out_dir.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)

    print("Classifying alts…", flush=True)
    classified = classify_alts(gtfs_zip, cluster_zip)
    write_classification(classified)

    use_rts_alts = classified.loc[classified["edge_source"] == "use_rts", "ALT_NUM"].astype(str).tolist()
    mm_alts = classified.loc[classified["edge_source"] == "mapmatch_gtfs", "ALT_NUM"].astype(str).tolist()
    print(f"  use_rts={len(use_rts_alts)}, mapmatch_gtfs={len(mm_alts)}", flush=True)

    parts = []
    if use_rts_alts:
        print("Loading RTS route_edges for use_rts alts…", flush=True)
        rts_part = _load_rts_route_edges(rts_route_edges, use_rts_alts)
        print(f"  RTS edges rows: {len(rts_part)}", flush=True)
        parts.append(rts_part)

    if mm_alts:
        print("Loading GTFS shapes for mapmatch…", flush=True)
        gtfs_routes = route_shapes_gdf(str(gtfs_zip), str(cluster_zip))
        mm_checkpoint = out_dir / f"_mapmatch_checkpoint_{year}{month:02d}.parquet"
        if mm_checkpoint.exists():
            print(f"Loading mapmatch checkpoint {mm_checkpoint}", flush=True)
            parts.append(gpd.read_parquet(mm_checkpoint))
        else:
            print("Building street_net matcher…", flush=True)
            matcher = StreetNetMatcher(street_net)
            print(f"Map-matching {len(mm_alts)} alts…", flush=True)
            chunk = []
            done = 0
            for i in range(0, len(mm_alts), 50):
                batch = mm_alts[i : i + 50]
                mm_part = mapmatch_alts(gtfs_routes, batch, street_net_path=street_net, matcher=matcher)
                if len(mm_part):
                    chunk.append(mm_part)
                done += len(batch)
                print(
                    f"  mapmatch progress {done}/{len(mm_alts)} (edges so far {sum(len(x) for x in chunk)})",
                    flush=True,
                )
            if chunk:
                mm_gdf = gpd.GeoDataFrame(pd.concat(chunk, ignore_index=True), crs=2039)
                for c in ["MAKAT", "ROUTE_ID", "ALT_NUM", "from", "to", "edge_source"]:
                    if c in mm_gdf.columns:
                        mm_gdf[c] = mm_gdf[c].astype(str)
                mm_gdf.to_parquet(mm_checkpoint, index=False)
                parts.append(mm_gdf)

    if not parts:
        raise RuntimeError("No hybrid edges produced")

    hybrid = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=2039)
    for c in ["MAKAT", "ROUTE_ID", "ALT_NUM", "from", "to", "edge_source"]:
        if c in hybrid.columns:
            hybrid[c] = hybrid[c].map(lambda x: str(x) if x is not None else "")
    hybrid = hybrid.drop_duplicates(["ALT_NUM", "seq", "from", "to"])

    out_parquet = out_dir / f"route_edges_hybrid_{year}{month:02d}.parquet"
    meta_path = out_dir / f"route_edges_hybrid_{year}{month:02d}_meta.json"
    hybrid.to_parquet(out_parquet, index=False)

    mm_ok = set(hybrid.loc[hybrid["edge_source"] == "mapmatch_gtfs", "ALT_NUM"].astype(str))
    mm_failed = sorted(set(mm_alts) - mm_ok)
    meta = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "year": year,
        "month": month,
        "gtfs_zip": str(gtfs_zip),
        "rts_route_edges": str(rts_route_edges),
        "street_net": str(street_net),
        "hausdorff_max_m": HAUSDORFF_MAX_M,
        "length_rel_diff_max": LENGTH_REL_DIFF_MAX,
        "n_edges": int(len(hybrid)),
        "n_alts": int(hybrid["ALT_NUM"].nunique()),
        "n_use_rts_alts": len(use_rts_alts),
        "n_mapmatch_requested": len(mm_alts),
        "n_mapmatch_ok": len(mm_ok),
        "n_mapmatch_failed": len(mm_failed),
        "mapmatch_failed_alts_sample": mm_failed[:50],
        "out_parquet": str(out_parquet),
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    qa_md = [
        "# Hybrid route_edges QA",
        "",
        f"- File: `{out_parquet}`",
        f"- Edges: **{meta['n_edges']}**",
        f"- Alts: **{meta['n_alts']}**",
        f"- use_rts alts: **{meta['n_use_rts_alts']}**",
        f"- mapmatch ok/requested: **{meta['n_mapmatch_ok']}/{meta['n_mapmatch_requested']}**",
        f"- mapmatch failed: **{meta['n_mapmatch_failed']}**",
        "",
    ]
    (REPORTS / "hybrid_edges_qa.md").write_text("\n".join(qa_md), encoding="utf-8")
    print(f"Wrote {out_parquet}", flush=True)
    return out_parquet


def main():
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--month", type=int, default=4)
    args = p.parse_args()
    build_hybrid_route_edges(year=args.year, month=args.month)


if __name__ == "__main__":
    main()
