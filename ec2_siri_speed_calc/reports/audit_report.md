# GTFS shape / route-edge stability audit

**Verdict:** PASS — proceed to Phase 1–2

- Week A: `T:\GIS\gtfs\2026\28-04-2026`
- Week B: `T:\GIS\gtfs\2026\21-04-2026`
- Routes A/B: 6785 / 6797
- shape_id Jaccard: `0.9848`
- route_desc Jaccard: `0.9947`
- ALT_NUM Jaccard: `0.9947`
- Matched routes: 6773
- Median Hausdorff (m): `0.0`
- Median length rel diff: `0.0`

## Pass/fail checks

- **matched_route_median_hausdorff_m**: value=`0.0` threshold=`5.0` → PASS
- **matched_route_median_length_rel_diff**: value=`0.0` threshold=`0.01` → PASS
- **from_to_retention_jaccard**: value=`0.9933976698988946` threshold=`0.95` → PASS
- **seq_identity_jaccard**: value=`0.9938271873095363` threshold=`0.9` → PASS

## Edge stability

```json
{
  "n_shared_alts": 6773,
  "n_from_to_a": 4141888,
  "n_from_to_b": 4142587,
  "from_to_jaccard": 0.9933976698988946,
  "seq_identity_jaccard": 0.9938271873095363,
  "matched_edge_median_hausdorff_m": 0.0,
  "n_hausdorff_sample": 5000
}
```

## ALT_NUM mapping (see altnum_mapping.md)

- GTFS ALT in RTS %: `93.88`
- RTS ALT in GTFS %: `99.44`
- MAKAT agree given shared ALT %: `100.00`

## GTFS vs RTS offset (informational)

```json
{
  "skipped": true,
  "reason": "informational; enable with AUDIT_RTS_GEOM=1"
}
```
