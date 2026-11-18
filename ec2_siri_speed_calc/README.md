# ec2_siri_speed_calc — operator manual

Self-contained package for running **SIRI edge speeds on EC2**, with route edges that track **GTFS Mot changes** while still keying results on the **RTS street network** (`from` / `to`).

Parent script [`speed_layer_from_siri.py`](../speed_layer_from_siri.py) is **not** edited by this package. EC2 should run the CLIs below from this folder.

---

## 1. Why this exists

| Source | Cadence | Problem |
|--------|---------|---------|
| **GTFS** | Weekly (sometimes daily) | Reflects Mot route changes quickly |
| **RTS** | ~every other month, ~1 month delay | Stable street topology, but late for new Mot geometry |

**Approach:** build a **hybrid route-edges freeze** once per month (or when GTFS updates):

- If a route still matches RTS closely → reuse **RTS route_edges**
- If Mot changed (GTFS differs) → **map-match the GTFS shape onto RTS `street_net`** so edges still use street `from`/`to`

Then run the usual daily speed calc and month-end averages on that freeze.

```text
  GTFS shapes ──compare──► RTS Routes
       │                      │
       │  close?              │
       ▼                      ▼
  map-match to street_net   copy RTS route_edges
       │                      │
       └──────── merge ───────┘
                    │
            hybrid route_edges freeze
                    │
     ┌──────────────┼──────────────┐
     ▼              ▼              ▼
  Daily S3 SIRI   edges_speed   Month-end avg
  → DuckDB        per day       by street from/to
```

---

## 2. Requirements

**Python:** 3.11+ recommended (`py -3`).

**Packages (typical):** `geopandas`, `pandas`, `shapely`, `numpy`, `networkx`, `pyarrow`, `tqdm`, `scipy`.

**For daily S3 pull:** `duckdb`, `boto3`, `python-dotenv`, and optionally `hdate` (Hebrew holidays filter).

**AWS env (daily job):**

| Variable | Purpose |
|----------|---------|
| `BUCKET_NAME` | S3 bucket with `national/YYYY/MM/DD/HH.parquet` |
| `AWS_ACCESS_KEY_ID` | Access key |
| `AWS_SECRET_ACCESS_KEY` | Secret key |

Region used in code: `eu-west-3`.

---

## 3. Folder layout

```text
ec2_siri_speed_calc/
├── README.md                          ← this manual
├── config.py                          ← paths, thresholds, map-match knobs
├── run_ec2_prep_edges.py              ← CLI: build hybrid freeze
├── run_ec2_daily.py                   ← CLI: one day of speeds
├── run_ec2_monthly.py                 ← CLI: month-end averages
│
├── classify_gtfs_vs_rts.py            ← use_rts vs mapmatch_gtfs
├── mapmatch_gtfs_to_street_net.py     ← GTFS → street_net path
├── build_hybrid_route_edges.py        ← merge freeze + QA
├── read_hybrid_route_edges.py         ← loader for daily/monthly
├── siri_s3.py                         ← S3 → DuckDB
├── speed_layer_daily.py               ← trip → edge speeds
├── monthly_avg.py                     ← month aggregates
├── speed_core.py / geometry_tools.py  ← snap / duration helpers
│
├── prep_gtfs.py                       ← GTFS zip → route shapes
├── compare_gtfs_rts_route_geometry.py ← full-route Hausdorff QA
├── audit_gtfs_shape_stability.py      ← week-vs-week GTFS stability
├── altnum_map.py                      ← GTFS route_id ↔ RTS ALT_NUM
│
├── frozen_edges/                      ← month freezes (parquet + meta)
├── reports/                           ← QA markdown/csv/json
└── data/                              ← default SPEED_OUTPUT_DIR
```

---

## 4. Configuration

Defaults live in [`config.py`](config.py). On EC2, **override with environment variables** (do not rely on `T:\` paths).

### Paths

| Env var | Default (dev) | Meaning |
|---------|---------------|---------|
| `GTFS_ZIP` | `T:\GIS\gtfs\2026\28-04-2026\GTFS_israel-public-transportation_*.zip` | Israel GTFS zip |
| `CLUSTER_ZIP` | matching `ClusterToLine_*.zip` | ClusterToLine |
| `RTS_ROUTES` | `...\RouteSystem\Routes_4326.shp` | Full RTS route polylines |
| `RTS_ROUTE_EDGES` | `...\route_edges\route_edges_2026.parquet` | RTS sequenced edges |
| `STREET_NET` | `...\street_net_2026.parquet` | Directed street network |
| `SPEED_OUTPUT_DIR` | `ec2_siri_speed_calc/data` | Day/month speed outputs |

### Decision thresholds (locked in config)

| Constant | Value | Rule |
|----------|-------|------|
| `HAUSDORFF_MAX_M` | **200** | Full-route GTFS vs RTS Hausdorff (m) |
| `LENGTH_REL_DIFF_MAX` | **0.03** | Relative length difference |

**use_rts** if `hausdorff_m < 200` **and** `length_rel_diff < 0.03`.  
Otherwise **mapmatch_gtfs**. New Mot alts with no RTS match also go to mapmatch.

### Map-match knobs

| Constant | Default | Meaning |
|----------|---------|---------|
| `MAPMATCH_SAMPLE_M` | 40 | Sample spacing along GTFS shape (m) |
| `MAPMATCH_SEARCH_M` | 55 | Search radius to street edges (m) |
| `MAPMATCH_AZIMUTH_MAX_DEG` | 35 | Max heading difference |

Map-match builds a **connected path on the street DiGraph** (gap fill via shortest path). It does **not** independently snap each sample to “any nearby street” without graph continuity.

---

## 5. Operational workflow

Run all commands from this directory:

```bat
cd /d T:\GIS\Speeds\siri-realtime\python\ec2_siri_speed_calc
```

On EC2 use the package path on the instance and `py -3` (or your venv python).

### Step A — Prep edges (start of month / new GTFS week)

```bat
py -3 run_ec2_prep_edges.py --year 2026 --month 4
```

**Does:**

1. Load GTFS shapes + compare to RTS Routes (or reuse `reports/gtfs_vs_rts_route_hausdorff.csv` if present)
2. Classify each `ALT_NUM` → `use_rts` / `mapmatch_gtfs`
3. Copy RTS edges for close alts; map-match changed alts onto `street_net`
4. Write freeze + QA

**Outputs:**

| File | Description |
|------|-------------|
| `frozen_edges/route_edges_hybrid_YYYYMM.parquet` | Edge freeze for daily jobs |
| `frozen_edges/route_edges_hybrid_YYYYMM_meta.json` | Counts, paths, failed alts |
| `frozen_edges/_mapmatch_checkpoint_YYYYMM.parquet` | Resume cache for mapmatch |
| `reports/alt_edge_source_classification.csv` | Per-alt decision |
| `reports/hybrid_edges_qa.md` | Summary QA |

**Example (April 2026 freeze already built):**

- 6,785 alts → **5,544 use_rts** (~82%), **1,241 mapmatch** (0 failed)
- ~1.93M edge rows in `route_edges_hybrid_202604.parquet`

Prep can take **tens of minutes** (street graph load + ~1k mapmatches). Re-running reuses the mapmatch checkpoint if it exists.

### Step B — Daily speeds (cron / EventBridge)

```bat
py -3 run_ec2_daily.py --year 2026 --month 4 --day 1
```

Optional:

```bat
py -3 run_ec2_daily.py --year 2026 --month 4 --day 1 --output-dir D:\speeds_out --time-period weekdays
py -3 run_ec2_daily.py --year 2026 --month 4 --day 1 --skip-s3
```

`--skip-s3` only reads an existing DuckDB day table (no S3 pull).

**Does:**

1. Load `route_edges_hybrid_YYYYMM.parquet`
2. Pull SIRI hours from S3 → monthly DuckDB (unless `--skip-s3`)
3. Snap trips to hybrid edges → write day parquets

**Outputs under `{SPEED_OUTPUT_DIR}/{year}/{mm}/`:**

| Path | Description |
|------|-------------|
| `siri_YYYYMM.duckdb` | Cached SIRI |
| `{day}/edges_speed_{hour}.parquet` | Per-hour edge speeds |
| `{day}/errors.txt` | Trip-level skip reasons |

`time-period` filters which calendar days are eligible (`weekdays` | `weekend` | `friday`), excluding selected Hebrew holidays when `hdate` is available.

### Step C — Month-end averages

```bat
py -3 run_ec2_monthly.py --year 2026 --month 4
```

**Does:** aggregate all `edges_speed_*.parquet` for the month onto unique street `(from, to)` (and route-level products).

**Outputs under `{SPEED_OUTPUT_DIR}/{year}/{mm}/`:**

| File | Description |
|------|-------------|
| `weekdays_{hour}_oclock.parquet` | Street-level hour stats (if weekdays) |
| `routes_{hour}.parquet` | ALT_NUM × street × hour |

---

## 6. Recommended EC2 schedule

| When | Job |
|------|-----|
| After new GTFS is staged (or 1st of month) | `run_ec2_prep_edges.py` |
| Each eligible day (e.g. 02:00) | `run_ec2_daily.py --year … --month … --day …` |
| After last day of month processed | `run_ec2_monthly.py` |

When a **new RTS** arrives: update `RTS_*` / `STREET_NET` env paths and re-run prep — many alts should flip back to `use_rts`.

---

## 7. Supporting QA scripts (optional)

Useful before trusting a freeze or investigating Mot vs RTS.

```bat
REM Full-route Hausdorff GTFS vs RTS Routes (all shared ALT_NUM)
py -3 compare_gtfs_rts_route_geometry.py

REM Week-vs-week GTFS geometry / densified-edge stability
py -3 audit_gtfs_shape_stability.py

REM Classify only (no freeze build)
py -3 classify_gtfs_vs_rts.py
```

Reports land in `reports/` (`gtfs_vs_rts_route_hausdorff.md`, `audit_report.md`, etc.).

**Note:** GTFS end-to-end shapes are often **not** geometrically identical to RTS Routes (median Hausdorff was ~66 m in the April 2026 check). That is expected; the hybrid rule uses the 200 m / 3% gates, then street map-match for the rest.

---

## 8. Edge schema (speed contract)

Hybrid freeze columns used by the daily job:

| Column | Role |
|--------|------|
| `MAKAT` | Line id |
| `ROUTE_ID` | Route id |
| `ALT_NUM` | Mot alternative (= GTFS `route_id` / SIRI `lineref`) |
| `from`, `to` | RTS street node ids |
| `seq` | Order along the route |
| `geometry` | Edge linestring EPSG:2039 (street geom for mapmatched; RTS edge geom for use_rts) |

Daily trip join key: SIRI `lineref` → `ALT_NUM`.

---

## 9. Troubleshooting

| Symptom | What to check |
|---------|----------------|
| `Missing hybrid edges` | Run `run_ec2_prep_edges.py` for that year/month |
| `BUCKET_NAME not set` / S3 errors | Env vars; IAM; keys under `national/YYYY/MM/DD/HH.parquet` |
| `No SIRI for this day` | Day outside `time-period`, holiday filter, or missing S3 hours |
| Prep parquet `ArrowTypeError` on `ROUTE_ID` | Fixed by stringifying ids; delete bad freeze and re-run prep |
| Mapmatch slow / want resume | Leave `_mapmatch_checkpoint_YYYYMM.parquet`; delete it to force full rematch |
| Many `mapmatch failed` in meta | Inspect failed ALT sample in meta JSON; check `STREET_NET` CRS/path and search radius |
| Wrong parallel street concerns | Mapmatch is graph-constrained per route; use_rts never nearest-snaps across the whole network |

---

## 10. Relation to legacy / experimental modules

| Module | Status |
|--------|--------|
| `run_ec2_*.py` + hybrid freeze | **Production path for EC2** |
| `gtfs_route_edges.py` / `build_frozen_edges.py` | Earlier GTFS-only densify freeze (feasibility); not the EC2 hybrid path |
| `speed_layer_gtfs_entrypoint.py` | Experimental wrapper toward parent module |
| Parent `speed_layer_from_siri.py` | File-server / RTS-only workflow; unchanged |

---

## 11. Quick checklist (new month)

1. Stage GTFS zip + ClusterToLine; set `GTFS_ZIP` / `CLUSTER_ZIP`
2. Confirm RTS Routes, route_edges, street_net paths for the RTS vintage you want
3. `py -3 run_ec2_prep_edges.py --year YYYY --month MM`
4. Read `reports/hybrid_edges_qa.md` (mapmatch failures should be ~0)
5. Ensure AWS env on the instance
6. Loop days: `run_ec2_daily.py --year YYYY --month MM --day D`
7. `run_ec2_monthly.py --year YYYY --month MM`
8. Publish / copy outputs from `SPEED_OUTPUT_DIR`
