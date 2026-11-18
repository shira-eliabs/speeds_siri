"""
Build month-frozen GTFS route_edges (Phase 1).

Only intended to run after Phase 0 audit PASS (see reports/audit_summary.json).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from read_gtfs_route_edges import build_april_2026_freeze, FREEZE_DIR

PKG = Path(__file__).resolve().parent
AUDIT_SUMMARY = PKG / "reports" / "audit_summary.json"


def main(force: bool = False) -> None:
    if AUDIT_SUMMARY.exists() and not force:
        summary = json.loads(AUDIT_SUMMARY.read_text(encoding="utf-8"))
        if not summary.get("overall_pass"):
            print("Phase 0 audit FAIL — refusing to build freeze. Use --force to override.")
            print("Verdict:", summary.get("verdict"))
            sys.exit(2)
    elif not AUDIT_SUMMARY.exists() and not force:
        print("No audit_summary.json found. Run audit_gtfs_shape_stability.py first, or pass --force.")
        sys.exit(2)

    out = build_april_2026_freeze()
    print(f"Wrote frozen edges: {out}")
    print(f"Freeze dir: {FREEZE_DIR}")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
