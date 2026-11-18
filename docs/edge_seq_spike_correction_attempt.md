# Edge-seq spike correction attempt (notes)

Date context: Aug 2026  
Related files: `speed_layer_from_siri.py`, `route_e2e_speed_from_siri.py`, `utils/geometry_tools.py`  
Status: **reverted** — spike repair and debug instrumentation were removed; pipeline restored to pre-spike behavior.

---

## Background

`duration_on_edges` maps each SIRI GPS point to a route edge (`closest_edge_seq`), then requires that sequence to be **monotonic in time**. If one GPS point snaps to the wrong edge (e.g. seq `2 → 14 → 2`), the whole trip is discarded (`return None`).

That shows up as lost trips in both:

- national/edge speed layer (`speed_per_trip`)
- end-to-end ALT_NUM × hour speeds (`route_e2e_speed_from_siri.py`)

---

## What we tried

### Goal

Detect clear interior **spikes** in `closest_edge_seq` and either:

1. **Reassign** the spike GPS to a neighbor-consistent edge within **30 m**, or  
2. **Drop** that GPS row if no nearby consistent edge exists  

…so the trip can still produce speeds instead of being thrown away.

### Design (implemented, then removed)

| Piece | Behavior |
|--------|----------|
| Window | ±2 GPS points around the candidate |
| Spike rule | seq outside `[min(context), max(context)]` **and** jump ≥ 3 vs context median |
| Repair | Prefer edge in `[lo, hi]` by cumulative distance to `MonitoredCall_DistanceFromStop`; accept if `distance(gps, edge) ≤ 30 m` (EPSG:2039) |
| Fast path | Already non-decreasing seq → skip detect/repair |
| Caching | Edge seq / cum dist / geom cached on `edges.attrs` |
| Hook | Called at end of `find_relevant_edge_seq` |

Optimizations added later: vectorized detect, monotonic fast path, `lo==hi` O(1) lookup, avoid copy unless mutating.

Unit tests lived in `test_edge_seq_spike_repair.py` (deleted on revert).

---

## Problems we hit

Debugging focused on day=`5` trips (e.g. `zipped[10]` / trip like `10231203-11178`). Instrumentation wrote to `debug-91f511.log` (also deleted).

### 1. `'seq' is both an index level and a column label`

**Cause:** Edges were prepared with `set_index("seq", drop=False)`, so `seq` existed as **index and column**. Spike cache / merges / `edges["seq"]` then failed under pandas.

**What we tried:** `drop=True` (optionally re-adding `seq` as a column), and `_normalize_edges_for_seq_index` so hand-built `zipped` edges were cleaned at entry to `find_relevant_edge_seq` / `duration_on_edges`.

**Lesson:** Pick one convention — either seq-as-index only, or index+column deliberately — and keep `mp_speed_per_trip` / `mp_e2e_speeds` / notebook `zipped` construction consistent.

### 2. `ValueError: The truth value of an array is ambiguous`

Several hypotheses; logging narrowed them:

| Hypothesis | Result |
|------------|--------|
| H2: `filter(None, …)` / ndarray truth on nearest indices | Rejected as primary cause |
| H1: seq index+column ambiguity | Confirmed as a real issue |
| H5: `cut_line_at_point` / Shapely 2 `interpolate(ndarray)` | Plausible — vectorized interpolate can return arrays that blow up later in truthy checks |
| Merge explosion | Confirmed: edge lookup merge without `drop_duplicates` could expand GPS rows (e.g. 108 → 221), worsening later comparisons |

Failure often appeared **after** the monotonicity check in `duration_on_edges`, not always inside spike repair.

**What we tried:** scalar `interpolate` in `cut_line_at_point`, safer `get_loc` / `_col_pos`, scalar `kmph` / distance checks, merge `drop_duplicates` on edge lookup.

### 3. Notebook / caller bypass

Even after fixing `mp_e2e_speeds`, a manually built `zipped` with `set_index(..., drop=False)` still hit the seq ambiguity. Fixes had to be either at the edge builder **or** defensively inside the shared functions.

### 4. Complexity vs value

Spike repair touched the hottest path (`find_relevant_edge_seq` → every trip). Combined with pandas index quirks and Shapely 2 behavior, the feature became hard to validate on real days without regressions. Decision: **remove all of it** and restore pre-spike code rather than ship a half-hardened path.

---

## What was reverted

Restored / removed:

- Spike constants + `detect_edge_seq_spikes` / `repair_edge_seq_spikes` / edge cache  
- All `_dbg91` / `#region agent log` instrumentation  
- `_normalize_edges_for_seq_index`, `_col_pos` helpers  
- `set_index("seq", drop=False)` again in speed + e2e multiprocessing helpers  
- Original `cut_line_at_point` (vectorized `interpolate` form)  
- `test_edge_seq_spike_repair.py`, `debug-91f511.log`  

Left in place (unrelated): `route_e2e_speed_from_siri.py` e2e tool and DuckDB/S3 helpers.

---

## If revisiting later

Suggested order before reintroducing repair:

1. **Stabilize edge indexing** — one clear contract for `edges` (recommend: `set_index("seq", drop=True)` and treat `seq` as index only inside `duration_on_edges`; always expose `seq` as a column only after `reset_index` when writing outputs).  
2. **Harden `cut_line_at_point` for Shapely 2** independently (scalar interpolate), with a small unit test.  
3. **Guard the merge** — unique edge rows per seq before joining to GPS.  
4. Only then add spike detect/repair behind a flag, with tests on real failing trips (seq `2→14→2` style) and a day-level before/after count of trips kept vs discarded.

### Useful reproduction pattern (historical)

1. Build day=`5` `zipped` for e2e.  
2. Run `e2e_speed_per_trip(zipped[10])` for the ambiguous-truth case.  
3. Run full list / `mp_e2e_speeds` for the seq index/column error.  
4. Reload modules after any edit (kernel restart).
