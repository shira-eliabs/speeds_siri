"""
Compare end-to-end GTFS route shapes vs end-to-end RTS Routes geometries (Hausdorff).

Not edges — one total LineString per ALT_NUM.
Writes reports under ./reports/ inside this package.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from prep_gtfs import route_shapes_gdf

PKG = Path(__file__).resolve().parent
REPORTS = PKG / "reports"

GTFS_ZIP = Path(r"T:\GIS\gtfs\2026\28-04-2026\GTFS_israel-public-transportation_28042026.zip")
CLUSTER_ZIP = Path(r"T:\GIS\gtfs\2026\28-04-2026\ClusterToLine_28042026.zip")
RTS_ROUTES = Path(r"T:\RTS\Israel\Israel_April_2026\shape_files\RouteSystem\Routes_4326.shp")


def hausdorff_m(g1, g2) -> float:
    try:
        return float(g1.hausdorff_distance(g2))
    except Exception:
        return float("nan")


def load_rts_routes(path: Path) -> gpd.GeoDataFrame:
    rts = gpd.read_file(path)
    if rts.crs is None or rts.crs.to_epsg() != 2039:
        rts = rts.to_crs(2039)
    rts["ALT_NUM"] = rts["ALT_NUM"].astype(str)
    rts = rts.loc[rts.geometry.notnull() & ~rts.geometry.is_empty].copy()
    rts["_len"] = rts.geometry.length
    # One total geometry per ALT_NUM (longest if duplicates)
    rts = rts.sort_values("_len", ascending=False).drop_duplicates("ALT_NUM", keep="first")
    return rts[["ALT_NUM", "MAKAT", "geometry"]].reset_index(drop=True)


def compare(gtfs: gpd.GeoDataFrame, rts: gpd.GeoDataFrame) -> pd.DataFrame:
    g = gtfs[["ALT_NUM", "MAKAT", "route_desc", "geometry"]].copy()
    g["ALT_NUM"] = g["ALT_NUM"].astype(str)
    g["MAKAT"] = g["MAKAT"].astype(str)
    r = rts.rename(columns={"geometry": "geometry_rts", "MAKAT": "MAKAT_rts"})
    merged = g.merge(r, on="ALT_NUM", how="inner")
    rows = []
    for _, row in merged.iterrows():
        ga, gb = row["geometry"], row["geometry_rts"]
        la, lb = float(ga.length), float(gb.length)
        rel = abs(la - lb) / la if la > 0 else float("nan")
        rows.append(
            {
                "ALT_NUM": row["ALT_NUM"],
                "MAKAT_gtfs": row["MAKAT"],
                "MAKAT_rts": str(row.get("MAKAT_rts", "")),
                "route_desc": row.get("route_desc", ""),
                "length_gtfs_m": la,
                "length_rts_m": lb,
                "length_rel_diff": rel,
                "hausdorff_m": hausdorff_m(ga, gb),
            }
        )
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame) -> dict:
    h = df["hausdorff_m"].dropna()
    n = len(h)
    if n == 0:
        return {"n_shared": 0}

    def pct_le(thr: float) -> float:
        return float((h <= thr).mean() * 100)

    med = float(h.median())
    p90 = float(np.nanpercentile(h, 90))
    mean = float(h.mean())
    pct_le_25 = pct_le(25)
    mostly_same = med <= 5.0 and pct_le_25 >= 80.0
    return {
        "n_shared": int(n),
        "hausdorff_median_m": med,
        "hausdorff_p50_m": med,
        "hausdorff_p90_m": p90,
        "hausdorff_mean_m": mean,
        "pct_le_5m": pct_le(5),
        "pct_le_10m": pct_le(10),
        "pct_le_25m": pct_le_25,
        "pct_le_50m": pct_le(50),
        "length_rel_diff_median": float(df["length_rel_diff"].median()),
        "mostly_same": mostly_same,
        "verdict": (
            "MOSTLY SAME (informational): median Hausdorff <= 5 m and >= 80% of routes <= 25 m"
            if mostly_same
            else "NOT mostly same (informational): median > 5 m or < 80% of routes <= 25 m"
        ),
    }


def write_report(df: pd.DataFrame, summary: dict) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    csv_path = REPORTS / "gtfs_vs_rts_route_hausdorff.csv"
    md_path = REPORTS / "gtfs_vs_rts_route_hausdorff.md"
    df.to_csv(csv_path, index=False)

    lines = [
        "# GTFS vs RTS full-route Hausdorff",
        "",
        f"**Verdict:** {summary.get('verdict', 'n/a')}",
        "",
        f"- GTFS: `{GTFS_ZIP}`",
        f"- RTS Routes: `{RTS_ROUTES}`",
        f"- Shared ALT_NUM: **{summary.get('n_shared', 0)}**",
        f"- Median Hausdorff (m): `{summary.get('hausdorff_median_m')}`",
        f"- P90 Hausdorff (m): `{summary.get('hausdorff_p90_m')}`",
        f"- Mean Hausdorff (m): `{summary.get('hausdorff_mean_m')}`",
        f"- % ≤ 5 m: `{summary.get('pct_le_5m')}`",
        f"- % ≤ 10 m: `{summary.get('pct_le_10m')}`",
        f"- % ≤ 25 m: `{summary.get('pct_le_25m')}`",
        f"- % ≤ 50 m: `{summary.get('pct_le_50m')}`",
        f"- Median length rel diff: `{summary.get('length_rel_diff_median')}`",
        "",
        f"Per-route detail: `{csv_path.name}`",
        "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> dict:
    print("Loading GTFS route shapes…", flush=True)
    gtfs = route_shapes_gdf(str(GTFS_ZIP), str(CLUSTER_ZIP))
    print(f"  GTFS routes: {len(gtfs)}", flush=True)

    print("Loading RTS Routes…", RTS_ROUTES, flush=True)
    rts = load_rts_routes(RTS_ROUTES)
    print(f"  RTS routes: {len(rts)}", flush=True)

    print("Computing Hausdorff for shared ALT_NUM…", flush=True)
    df = compare(gtfs, rts)
    summary = summarize(df)
    write_report(df, summary)
    print(summary["verdict"], flush=True)
    print(
        f"n={summary['n_shared']} median_h={summary['hausdorff_median_m']:.3f} m "
        f"p90={summary['hausdorff_p90_m']:.3f} m pct<=25m={summary['pct_le_25m']:.1f}%",
        flush=True,
    )
    return summary


if __name__ == "__main__":
    main()
