"""
Phase 0 go/no-go: GTFS week-vs-week shape / route-edge stability audit.

Writes reports under ./reports/ inside this package.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import geopandas as gpd
import numpy as np
import pandas as pd

from altnum_map import validate_altnum_mapping, write_mapping_report
from gtfs_route_edges import routes_to_edges
from prep_gtfs import route_shapes_gdf

PKG = Path(__file__).resolve().parent
REPORTS = PKG / "reports"

# Pass / fail thresholds from plan
THRESH_HAUSDORFF_M = 5.0
THRESH_LENGTH_REL = 0.01
THRESH_FROM_TO_RETENTION = 0.95
THRESH_SEQ_IDENTITY = 0.90

DEFAULT_WEEK_A = {
    "folder": Path(r"T:\GIS\gtfs\2026\28-04-2026"),
    "gtfs": Path(r"T:\GIS\gtfs\2026\28-04-2026\GTFS_israel-public-transportation_28042026.zip"),
    "cluster": Path(r"T:\GIS\gtfs\2026\28-04-2026\ClusterToLine_28042026.zip"),
}
DEFAULT_WEEK_B = {
    "folder": Path(r"T:\GIS\gtfs\2026\21-04-2026"),
    "gtfs": Path(r"T:\GIS\gtfs\2026\21-04-2026\GTFS_israel-public-transportation_21042026.zip"),
    "cluster": Path(r"T:\GIS\gtfs\2026\21-04-2026\ClusterToLine_21042026.zip"),
}
RTS_EDGES = Path(r"T:\RTS\Israel\Israel_April_2026\shape_files\outputs\route_edges\route_edges_2026.parquet")
STEP_M = 50.0


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    u = a | b
    return len(a & b) / len(u) if u else 0.0


def hausdorff_m(g1, g2) -> float:
    try:
        return float(g1.hausdorff_distance(g2))
    except Exception:
        return float("nan")


def compare_route_geometries(a: gpd.GeoDataFrame, b: gpd.GeoDataFrame) -> pd.DataFrame:
    """Match on route_desc; fall back to ALT_NUM."""
    aa = a.copy()
    bb = b.copy()
    aa["shape_id"] = aa["shape_id"].astype(str)
    bb["shape_id"] = bb["shape_id"].astype(str)

    key = "route_desc"
    merged = aa.merge(bb, on=key, how="inner", suffixes=("_a", "_b"))
    if len(merged) == 0:
        key = "ALT_NUM"
        merged = aa.merge(bb, on=key, how="inner", suffixes=("_a", "_b"))

    rows = []
    for _, r in merged.iterrows():
        ga, gb = r["geometry_a"], r["geometry_b"]
        la, lb = ga.length, gb.length
        rel = abs(la - lb) / la if la > 0 else float("nan")
        rows.append(
            {
                "match_key": key,
                "route_desc": r.get("route_desc", r.get("route_desc_a", "")),
                "ALT_NUM": str(r.get("ALT_NUM_a", r.get("ALT_NUM", ""))),
                "shape_id_a": r.get("shape_id_a"),
                "shape_id_b": r.get("shape_id_b"),
                "length_a": la,
                "length_b": lb,
                "length_rel_diff": rel,
                "hausdorff_m": hausdorff_m(ga, gb),
                "same_shape_id": str(r.get("shape_id_a")) == str(r.get("shape_id_b")),
            }
        )
    return pd.DataFrame(rows)


def edge_stability(edges_a: gpd.GeoDataFrame, edges_b: gpd.GeoDataFrame) -> dict:
    """Compare densified route edges between two weeks."""
    # Restrict to routes present in both
    alts_a = set(edges_a["ALT_NUM"].astype(str))
    alts_b = set(edges_b["ALT_NUM"].astype(str))
    shared_alts = alts_a & alts_b
    ea = edges_a.loc[edges_a["ALT_NUM"].astype(str).isin(shared_alts)].copy()
    eb = edges_b.loc[edges_b["ALT_NUM"].astype(str).isin(shared_alts)].copy()

    pairs_a = set(zip(ea["from"], ea["to"]))
    pairs_b = set(zip(eb["from"], eb["to"]))
    retention = jaccard(pairs_a, pairs_b)

    seq_a = set(zip(ea["ALT_NUM"].astype(str), ea["seq"].astype(int), ea["from"], ea["to"]))
    seq_b = set(zip(eb["ALT_NUM"].astype(str), eb["seq"].astype(int), eb["from"], eb["to"]))
    seq_identity = jaccard(seq_a, seq_b)

    # Hausdorff on matched (from,to) — sample up to 5000 for speed
    common = pairs_a & pairs_b
    ea_idx = ea.drop_duplicates(["from", "to"]).set_index(["from", "to"])
    eb_idx = eb.drop_duplicates(["from", "to"]).set_index(["from", "to"])
    sample = list(common)[:5000]
    hs = []
    for ft in sample:
        try:
            hs.append(hausdorff_m(ea_idx.loc[ft].geometry, eb_idx.loc[ft].geometry))
        except Exception:
            continue
    return {
        "n_shared_alts": len(shared_alts),
        "n_from_to_a": len(pairs_a),
        "n_from_to_b": len(pairs_b),
        "from_to_jaccard": retention,
        "seq_identity_jaccard": seq_identity,
        "matched_edge_median_hausdorff_m": float(np.nanmedian(hs)) if hs else float("nan"),
        "n_hausdorff_sample": len(hs),
    }


def rts_offset_sample(routes: gpd.GeoDataFrame, rts_path: Path, max_routes: int = 200) -> dict:
    """Informational: median distance from GTFS shape to dissolved RTS edges for shared ALT_NUM."""
    # Read only needed columns via pyarrow filter then rebuild geometries for sample alts
    rts = gpd.read_parquet(rts_path)
    rts["ALT_NUM"] = rts["ALT_NUM"].astype(str)
    shared = set(routes["ALT_NUM"].astype(str)) & set(rts["ALT_NUM"])
    sample_alts = sorted(shared)[:max_routes]
    rts = rts.loc[rts["ALT_NUM"].isin(sample_alts)]
    dists = []
    for alt in sample_alts:
        g = routes.loc[routes["ALT_NUM"].astype(str) == alt].geometry.iloc[0]
        edges = rts.loc[rts["ALT_NUM"] == alt]
        if edges.empty:
            continue
        dissolved = edges.unary_union
        try:
            dists.append(float(g.hausdorff_distance(dissolved)))
        except Exception:
            continue
    return {
        "n_shared_alt_for_rts_compare": len(shared),
        "n_sampled": len(dists),
        "median_hausdorff_gtfs_vs_rts_m": float(np.nanmedian(dists)) if dists else float("nan"),
        "p90_hausdorff_gtfs_vs_rts_m": float(np.nanpercentile(dists, 90)) if dists else float("nan"),
    }


def evaluate_pass(geom_cmp: pd.DataFrame, edge_stats: dict) -> Tuple[bool, dict]:
    med_h = float(geom_cmp["hausdorff_m"].median()) if len(geom_cmp) else float("inf")
    med_len = float(geom_cmp["length_rel_diff"].median()) if len(geom_cmp) else float("inf")
    checks = {
        "matched_route_median_hausdorff_m": {
            "value": med_h,
            "threshold": THRESH_HAUSDORFF_M,
            "pass": med_h <= THRESH_HAUSDORFF_M,
        },
        "matched_route_median_length_rel_diff": {
            "value": med_len,
            "threshold": THRESH_LENGTH_REL,
            "pass": med_len <= THRESH_LENGTH_REL,
        },
        "from_to_retention_jaccard": {
            "value": edge_stats["from_to_jaccard"],
            "threshold": THRESH_FROM_TO_RETENTION,
            "pass": edge_stats["from_to_jaccard"] >= THRESH_FROM_TO_RETENTION,
        },
        "seq_identity_jaccard": {
            "value": edge_stats["seq_identity_jaccard"],
            "threshold": THRESH_SEQ_IDENTITY,
            "pass": edge_stats["seq_identity_jaccard"] >= THRESH_SEQ_IDENTITY,
        },
    }
    overall = all(c["pass"] for c in checks.values())
    return overall, checks


def run_audit(
    week_a: dict = DEFAULT_WEEK_A,
    week_b: dict = DEFAULT_WEEK_B,
    rts_edges: Path = RTS_EDGES,
    step_m: float = STEP_M,
) -> dict:
    REPORTS.mkdir(parents=True, exist_ok=True)
    print("Loading week A routes…", week_a["gtfs"].name, flush=True)
    routes_a = route_shapes_gdf(str(week_a["gtfs"]), str(week_a["cluster"]))
    print(f"  week A routes: {len(routes_a)}", flush=True)
    print("Loading week B routes…", week_b["gtfs"].name, flush=True)
    routes_b = route_shapes_gdf(str(week_b["gtfs"]), str(week_b["cluster"]))
    print(f"  week B routes: {len(routes_b)}", flush=True)

    shape_ids_a = set(routes_a["shape_id"].astype(str))
    shape_ids_b = set(routes_b["shape_id"].astype(str))
    desc_a = set(routes_a["route_desc"].astype(str))
    desc_b = set(routes_b["route_desc"].astype(str))
    alt_a = set(routes_a["ALT_NUM"].astype(str))
    alt_b = set(routes_b["ALT_NUM"].astype(str))

    print("Comparing matched route geometries…")
    geom_cmp = compare_route_geometries(routes_a, routes_b)
    geom_cmp.to_csv(REPORTS / "week_geometry_compare.csv", index=False)

    print(f"Building route edges (step={step_m} m)…")
    edges_a = routes_to_edges(routes_a, step_m=step_m)
    edges_b = routes_to_edges(routes_b, step_m=step_m)
    edge_stats = edge_stability(edges_a, edges_b)

    print("ALT_NUM / MAKAT mapping vs RTS…", flush=True)
    mapping = validate_altnum_mapping(
        str(week_a["gtfs"]), str(rts_edges), str(week_a["cluster"]), gtfs_routes=routes_a
    )
    write_mapping_report(mapping, REPORTS / "altnum_mapping.md")

    # Informational only and expensive (1.6M RTS edges) — skip unless explicitly enabled
    import os

    if os.environ.get("AUDIT_RTS_GEOM", "0") == "1":
        print("Informational GTFS vs RTS offset sample…", flush=True)
        rts_cmp = rts_offset_sample(routes_a, rts_edges, max_routes=50)
    else:
        print("Skipping GTFS vs RTS geometry sample (set AUDIT_RTS_GEOM=1 to enable).", flush=True)
        rts_cmp = {"skipped": True, "reason": "informational; enable with AUDIT_RTS_GEOM=1"}

    overall_pass, checks = evaluate_pass(geom_cmp, edge_stats)

    summary = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "week_a": str(week_a["folder"]),
        "week_b": str(week_b["folder"]),
        "step_m": step_m,
        "n_routes_a": int(len(routes_a)),
        "n_routes_b": int(len(routes_b)),
        "shape_id_jaccard": jaccard(shape_ids_a, shape_ids_b),
        "route_desc_jaccard": jaccard(desc_a, desc_b),
        "alt_num_jaccard": jaccard(alt_a, alt_b),
        "n_matched_routes_geometry": int(len(geom_cmp)),
        "geometry_median_hausdorff_m": float(geom_cmp["hausdorff_m"].median()) if len(geom_cmp) else None,
        "geometry_median_length_rel_diff": float(geom_cmp["length_rel_diff"].median()) if len(geom_cmp) else None,
        "edge_stats": edge_stats,
        "altnum_mapping": mapping,
        "rts_offset_informational": rts_cmp,
        "checks": checks,
        "overall_pass": overall_pass,
        "verdict": "PASS — proceed to Phase 1–2" if overall_pass else "FAIL — do not replace RTS; keep RTS route_edges",
    }

    (REPORTS / "audit_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    md = [
        "# GTFS shape / route-edge stability audit",
        "",
        f"**Verdict:** {summary['verdict']}",
        "",
        f"- Week A: `{week_a['folder']}`",
        f"- Week B: `{week_b['folder']}`",
        f"- Routes A/B: {summary['n_routes_a']} / {summary['n_routes_b']}",
        f"- shape_id Jaccard: `{summary['shape_id_jaccard']:.4f}`",
        f"- route_desc Jaccard: `{summary['route_desc_jaccard']:.4f}`",
        f"- ALT_NUM Jaccard: `{summary['alt_num_jaccard']:.4f}`",
        f"- Matched routes: {summary['n_matched_routes_geometry']}",
        f"- Median Hausdorff (m): `{summary['geometry_median_hausdorff_m']}`",
        f"- Median length rel diff: `{summary['geometry_median_length_rel_diff']}`",
        "",
        "## Pass/fail checks",
        "",
    ]
    for name, c in checks.items():
        md.append(
            f"- **{name}**: value=`{c['value']}` threshold=`{c['threshold']}` → "
            f"{'PASS' if c['pass'] else 'FAIL'}"
        )
    md += [
        "",
        "## Edge stability",
        "",
        f"```json\n{json.dumps(edge_stats, indent=2)}\n```",
        "",
        "## ALT_NUM mapping (see altnum_mapping.md)",
        "",
        f"- GTFS ALT in RTS %: `{mapping['gtfs_alt_in_rts_pct']:.2f}`",
        f"- RTS ALT in GTFS %: `{mapping['rts_alt_in_gtfs_pct']:.2f}`",
        f"- MAKAT agree given shared ALT %: `{mapping['makat_agree_given_shared_alt_pct']:.2f}`",
        "",
        "## GTFS vs RTS offset (informational)",
        "",
        f"```json\n{json.dumps(rts_cmp, indent=2)}\n```",
        "",
    ]
    (REPORTS / "audit_report.md").write_text("\n".join(md), encoding="utf-8")
    print(summary["verdict"])
    return summary


if __name__ == "__main__":
    run_audit()
