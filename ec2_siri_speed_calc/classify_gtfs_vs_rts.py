"""Classify ALT_NUM into use_rts vs mapmatch_gtfs using Hausdorff / length thresholds."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Union

import pandas as pd

from compare_gtfs_rts_route_geometry import compare, load_rts_routes
from config import (
    CLUSTER_ZIP,
    GTFS_ZIP,
    HAUSDORFF_MAX_M,
    LENGTH_REL_DIFF_MAX,
    REPORTS,
    RTS_ROUTES,
)
from prep_gtfs import route_shapes_gdf


def classify_from_compare_df(
    df: pd.DataFrame,
    hausdorff_max_m: float = HAUSDORFF_MAX_M,
    length_rel_max: float = LENGTH_REL_DIFF_MAX,
) -> pd.DataFrame:
    out = df.copy()
    out["ALT_NUM"] = out["ALT_NUM"].astype(str)
    use_rts = (out["hausdorff_m"] < hausdorff_max_m) & (out["length_rel_diff"] < length_rel_max)
    out["edge_source"] = "mapmatch_gtfs"
    out.loc[use_rts, "edge_source"] = "use_rts"
    # GTFS-only alts (no RTS compare row) handled separately by caller
    return out


def classify_alts(
    gtfs_zip: Union[str, Path] = GTFS_ZIP,
    cluster_zip: Union[str, Path] = CLUSTER_ZIP,
    rts_routes: Union[str, Path] = RTS_ROUTES,
    compare_csv: Optional[Union[str, Path]] = None,
    hausdorff_max_m: float = HAUSDORFF_MAX_M,
    length_rel_max: float = LENGTH_REL_DIFF_MAX,
) -> pd.DataFrame:
    """
    Return one row per GTFS ALT_NUM with edge_source in {use_rts, mapmatch_gtfs}.

    If compare_csv exists (from compare_gtfs_rts_route_geometry), reuse it;
    otherwise recompute Hausdorff vs RTS Routes.
    """
    compare_csv = Path(compare_csv) if compare_csv else REPORTS / "gtfs_vs_rts_route_hausdorff.csv"
    gtfs = route_shapes_gdf(str(gtfs_zip), str(cluster_zip))
    gtfs["ALT_NUM"] = gtfs["ALT_NUM"].astype(str)

    if compare_csv.exists():
        cmp = pd.read_csv(compare_csv)
    else:
        rts = load_rts_routes(Path(rts_routes))
        cmp = compare(gtfs, rts)

    classified = classify_from_compare_df(cmp, hausdorff_max_m, length_rel_max)

    # Alts only in GTFS (new Mot routes) → mapmatch
    gtfs_only = set(gtfs["ALT_NUM"]) - set(classified["ALT_NUM"].astype(str))
    extra_rows = []
    for alt in sorted(gtfs_only):
        row = gtfs.loc[gtfs["ALT_NUM"] == alt].iloc[0]
        extra_rows.append(
            {
                "ALT_NUM": alt,
                "MAKAT_gtfs": str(row.get("MAKAT", "")),
                "MAKAT_rts": "",
                "route_desc": row.get("route_desc", ""),
                "length_gtfs_m": float(row.geometry.length),
                "length_rts_m": float("nan"),
                "length_rel_diff": float("nan"),
                "hausdorff_m": float("nan"),
                "edge_source": "mapmatch_gtfs",
            }
        )
    if extra_rows:
        classified = pd.concat([classified, pd.DataFrame(extra_rows)], ignore_index=True)

    return classified


def write_classification(classified: pd.DataFrame, out_dir: Optional[Path] = None) -> Path:
    out_dir = out_dir or REPORTS
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "alt_edge_source_classification.csv"
    classified.to_csv(csv_path, index=False)

    n = len(classified)
    n_rts = int((classified["edge_source"] == "use_rts").sum())
    n_mm = int((classified["edge_source"] == "mapmatch_gtfs").sum())
    summary = {
        "n_alts": n,
        "n_use_rts": n_rts,
        "n_mapmatch_gtfs": n_mm,
        "pct_use_rts": round(100.0 * n_rts / n, 2) if n else 0.0,
        "hausdorff_max_m": HAUSDORFF_MAX_M,
        "length_rel_diff_max": LENGTH_REL_DIFF_MAX,
    }
    (out_dir / "alt_edge_source_classification.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    md = [
        "# ALT_NUM edge source classification",
        "",
        f"- Total alts: **{n}**",
        f"- use_rts: **{n_rts}** ({summary['pct_use_rts']}%)",
        f"- mapmatch_gtfs: **{n_mm}**",
        f"- Thresholds: Hausdorff < `{HAUSDORFF_MAX_M}` m and length_rel_diff < `{LENGTH_REL_DIFF_MAX}`",
        "",
        f"Detail: `{csv_path.name}`",
        "",
    ]
    (out_dir / "alt_edge_source_classification.md").write_text("\n".join(md), encoding="utf-8")
    return csv_path


def main() -> pd.DataFrame:
    print("Classifying ALT_NUM edge sources…", flush=True)
    classified = classify_alts()
    path = write_classification(classified)
    n_rts = (classified["edge_source"] == "use_rts").sum()
    n_mm = (classified["edge_source"] == "mapmatch_gtfs").sum()
    print(f"Wrote {path}: use_rts={n_rts}, mapmatch_gtfs={n_mm}", flush=True)
    return classified


if __name__ == "__main__":
    main()
