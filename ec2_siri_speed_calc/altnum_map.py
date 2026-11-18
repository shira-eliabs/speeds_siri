"""Map GTFS route identifiers to RTS / SIRI ALT_NUM and MAKAT."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

import geopandas as gpd
import pandas as pd

from prep_gtfs import route_shapes_gdf


def load_rts_route_keys(rts_edges_path: str) -> pd.DataFrame:
    """Unique RTS route keys from route_edges parquet (no geometry needed)."""
    try:
        import pyarrow.parquet as pq

        table = pq.read_table(rts_edges_path, columns=["ALT_NUM", "MAKAT", "ROUTE_ID"])
        out = table.to_pandas().drop_duplicates()
    except Exception:
        # Fallback: read with geopandas including geometry then drop it
        gdf = gpd.read_parquet(rts_edges_path)
        out = gdf[["ALT_NUM", "MAKAT", "ROUTE_ID"]].drop_duplicates()
    out["ALT_NUM"] = out["ALT_NUM"].astype(str)
    out["MAKAT"] = out["MAKAT"].astype(str)
    out["ROUTE_ID"] = out["ROUTE_ID"].astype(str)
    return out


def validate_altnum_mapping(
    gtfs_zip: str,
    rts_edges_path: str,
    cluster_to_line_path: Optional[str] = None,
    gtfs_routes: Optional[gpd.GeoDataFrame] = None,
) -> dict:
    """
    Compare GTFS route_id / Makat / route_desc against RTS ALT_NUM / MAKAT.

    Israeli Mot convention used here: GTFS route_id is treated as ALT_NUM candidate
    (SIRI LineRef typically equals Mot alternative / route_id).
    """
    if gtfs_routes is None:
        gtfs = route_shapes_gdf(gtfs_zip, cluster_to_line_path)
    else:
        gtfs = gtfs_routes
    gtfs["ALT_NUM"] = gtfs["ALT_NUM"].astype(str)
    gtfs["MAKAT"] = gtfs["MAKAT"].astype(str)
    gtfs["route_desc"] = gtfs["route_desc"].astype(str)

    rts = load_rts_route_keys(rts_edges_path)

    gtfs_alt = set(gtfs["ALT_NUM"])
    rts_alt = set(rts["ALT_NUM"])
    gtfs_makat = set(gtfs["MAKAT"])
    rts_makat = set(rts["MAKAT"])

    alt_inter = gtfs_alt & rts_alt
    makat_inter = gtfs_makat & rts_makat

    # Among shared ALT_NUM, check MAKAT agreement
    merged = gtfs[["ALT_NUM", "MAKAT", "route_desc"]].merge(
        rts[["ALT_NUM", "MAKAT"]].rename(columns={"MAKAT": "MAKAT_rts"}),
        on="ALT_NUM",
        how="inner",
    )
    makat_agree = 0.0
    if len(merged):
        makat_agree = float((merged["MAKAT"] == merged["MAKAT_rts"]).mean())

    report = {
        "n_gtfs_routes": int(len(gtfs)),
        "n_rts_alt_num": int(len(rts_alt)),
        "gtfs_alt_in_rts_pct": float(len(alt_inter) / len(gtfs_alt) * 100) if gtfs_alt else 0.0,
        "rts_alt_in_gtfs_pct": float(len(alt_inter) / len(rts_alt) * 100) if rts_alt else 0.0,
        "n_shared_alt_num": int(len(alt_inter)),
        "gtfs_makat_in_rts_pct": float(len(makat_inter) / len(gtfs_makat) * 100) if gtfs_makat else 0.0,
        "makat_agree_given_shared_alt_pct": makat_agree * 100,
        "mapping_rule": "ALT_NUM = str(GTFS route_id); MAKAT = route_desc split[0]",
        "sample_shared_alt": sorted(list(alt_inter))[:10],
        "sample_gtfs_only_alt": sorted(list(gtfs_alt - rts_alt))[:10],
        "sample_rts_only_alt": sorted(list(rts_alt - gtfs_alt))[:10],
    }
    return report


def write_mapping_report(report: dict, out_path: Union[str, Path]) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# GTFS ↔ RTS ALT_NUM / MAKAT mapping", ""]
    for k, v in report.items():
        lines.append(f"- **{k}**: `{v}`")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path
