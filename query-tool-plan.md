# Web Query Tool — Architecture \& Build Plan

## Goal

Build a publicly accessible web tool that runs aggregated SQL queries and renders data visualizations against a \~3 GB database file, with no backend server and no ongoing hosting costs.

\---

## Architecture

```
\[Parquet file on Cloudflare R2 or Google Drive]
        ↓ HTTP range requests
\[DuckDB-WASM — runs in the user's browser]
        ↓ query results
\[Chart.js or Apache ECharts — visualizations]
        ↓
\[index.html hosted on GitHub Pages — public URL]
```

Everything is static. There is no server. Queries execute on the end user's machine.

\---

## Stack

|Layer|Tool|Notes|
|-|-|-|
|SQL engine|**DuckDB-WASM**|Full SQL in-browser; supports HTTP range requests on Parquet|
|Data format|**Apache Parquet**|Columnar, compressed; partial-fetch friendly|
|Data hosting|**Cloudflare R2** (preferred) or Google Drive + proxy|Must support CORS and HTTP range requests|
|Visualizations|**Apache ECharts** or **Chart.js**|Load via CDN; no install needed|
|App hosting|**GitHub Pages**|Free, public URL, serves static files|

\---

## Step 1 — Convert Data to Parquet

Convert your existing database to Parquet before uploading. Parquet's columnar format enables DuckDB to fetch only the columns/row groups it needs per query, keeping bandwidth low.

### From SQLite

```python
import sqlite3
import pandas as pd

conn = sqlite3.connect("your\_database.db")
df = pd.read\_sql("SELECT \* FROM your\_table", conn)
df.to\_parquet("data.parquet", index=False, compression="snappy")
```

### From CSV

```python
import pandas as pd
df = pd.read\_csv("your\_data.csv")
df.to\_parquet("data.parquet", index=False, compression="snappy")
```

### Using DuckDB directly (fastest for large files)

```python
import duckdb
duckdb.execute("""
    COPY (SELECT \* FROM read\_csv\_auto('your\_data.csv'))
    TO 'data.parquet' (FORMAT PARQUET, COMPRESSION SNAPPY)
""")
```

**Expected size reduction:** 3 GB source data often compresses to 300 MB–1 GB in Parquet depending on data types and cardinality.

\---

## Step 2 — Host the Parquet File

### Option A: Cloudflare R2 (Recommended)

Cloudflare R2 is the most reliable option. It natively supports HTTP range requests (required by DuckDB) and allows custom CORS configuration.

**Free tier:** 10 GB storage, unlimited egress.

1. Create a free [Cloudflare account](https://dash.cloudflare.com).
2. Go to R2 → Create bucket.
3. Upload `data.parquet`.
4. Set the bucket to **public** or create a public access policy.
5. Add a CORS rule under bucket Settings:

```json
\[
  {
    "AllowedOrigins": \["\*"],
    "AllowedMethods": \["GET", "HEAD"],
    "AllowedHeaders": \["\*"],
    "ExposeHeaders": \["Content-Length", "Content-Range"],
    "MaxAgeSeconds": 3600
  }
]
```

6. Your file URL will be: `https://<account-id>.r2.cloudflarestorage.com/<bucket>/data.parquet`

### Option B: Google Drive + Proxy

Google Drive's share links do not support HTTP range requests natively, so a proxy is required.

1. Upload `data.parquet` to Google Drive.
2. Set sharing to **"Anyone with the link can view"**.
3. Extract the file ID from the share URL.
4. Use a proxy such as `https://drive.lienuc.com/uc?id=<FILE\_ID>` to generate a direct URL with range-request support.

**Caveat:** Third-party proxies can be unreliable. Cloudflare R2 is strongly preferred.

\---

## Step 3 — Build the Web App

Create a single `index.html` file. All dependencies load from CDN.

### Minimal skeleton

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>Data Query Tool</title>
  <script src="https://cdn.jsdelivr.net/npm/apache-echarts/dist/echarts.min.js"></script>
</head>
<body>
  <h1>Query Tool</h1>

  <!-- Query UI: either pre-built buttons or a freeform SQL editor -->
  <textarea id="sql-input" rows="5" cols="60">
    SELECT category, COUNT(\*) AS count FROM data GROUP BY category ORDER BY count DESC
  </textarea>
  <button onclick="runQuery()">Run Query</button>

  <div id="results-table"></div>
  <div id="chart" style="width:800px;height:400px;"></div>

  <script type="module">
    import \* as duckdb from "https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm/+esm";

    const PARQUET\_URL = "https://YOUR\_R2\_OR\_PROXY\_URL/data.parquet";

    let conn;

    async function init() {
      const JSDELIVR\_BUNDLES = duckdb.getJsDelivrBundles();
      const bundle = await duckdb.selectBundle(JSDELIVR\_BUNDLES);
      const worker = await duckdb.createWorker(bundle.mainWorker);
      const db = new duckdb.AsyncDuckDB(bundle.logger, worker);
      await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
      conn = await db.connect();
      await conn.query(`CREATE VIEW data AS SELECT \* FROM parquet\_scan('${PARQUET\_URL}')`);
      console.log("DuckDB ready.");
    }

    window.runQuery = async function () {
      const sql = document.getElementById("sql-input").value;
      const result = await conn.query(sql);
      renderTable(result.toArray());
      renderChart(result.toArray());
    };

    function renderTable(rows) {
      // build an HTML table from rows and insert into #results-table
    }

    function renderChart(rows) {
      // use ECharts to render a bar/line/pie chart from rows
    }

    init();
  </script>
</body>
</html>
```

### Suggested features to implement

* Pre-built query buttons for your most common aggregations (alongside or instead of a freeform SQL editor)
* A results table rendered below each query
* Chart type selector (bar, line, pie) that re-renders without re-querying
* Loading spinner while DuckDB initializes and while queries run
* Error display for malformed SQL

\---

## Step 4 — Deploy to GitHub Pages

**Files that must be in the repo root:**

| File | Purpose |
|------|---------|
| `index.html` | The web tool (single-file app — all CSS and JS inline) |
| `airports.json` | Airport lookup for typeahead (1,944 US airports; generated by `gen_airports_json.py`) |
| `data_values.json` | Pre-baked distinct airports and carriers from the dataset (295 airports, 12 carriers; generated by `gen_data_values.py`) |
| `user-guide.html` | Lay-person user guide |

**Steps:**
1. Create a new GitHub repository (public).
2. Add the four files above to the repo root.
3. Go to **Settings → Pages → Source** and select `main` branch, `/ (root)`.
4. Tool live at: `https://<your-username>.github.io/<repo-name>/`
5. User guide at: `https://<your-username>.github.io/<repo-name>/user-guide.html`

The Parquet file lives on Cloudflare R2, not in the repo.

---

## Performance Notes

* **DuckDB-WASM init:** ~2–4 seconds on first load.
* **Airport/carrier list load:** Near-instant — served from pre-baked `data_values.json`. Original design queried DuckDB/Parquet at startup (~30 s over HTTP); replaced with a bundled JSON file fetched as plain text.
* **Query speed:** Aggregation queries on the 210 MB Parquet file typically complete in 2–10 seconds depending on filters and user bandwidth. DuckDB fetches only the needed row groups via HTTP range requests.
* **Memory:** Aggregation results are small. Avoid `SELECT *` on large row ranges in the SQL panel — full scans pull significant data into the browser tab.
* **Caching:** Parquet row groups fetched are cached in the browser; repeated queries on the same data are faster.

---

## File Update Workflow

When source data changes, run these steps in order:

1. Rebuild `connecting_itineraries.db` — run `build_db.py`
2. Re-export Parquet — run `export_parquet.py` → produces `connecting_itineraries.parquet`
3. Upload Parquet to Cloudflare R2 (overwrite existing file)
4. Regenerate pre-baked values — run `gen_data_values.py` → produces `data_values.json`
5. Commit updated `data_values.json` to the GitHub repo and push
6. `airports.json` only needs regeneration if the airport source CSV changes (run `gen_airports_json.py`)
7. No changes needed to `index.html` or `user-guide.html`

---

## Hosting — Resolved

**Cloudflare R2** selected. Parquet file uploaded and live.

| Item | Value |
|------|-------|
| Public base URL | `https://pub-1833d458128d49baaf210e9830b85ef1.r2.dev` |
| Parquet URL | `https://pub-1833d458128d49baaf210e9830b85ef1.r2.dev/connecting_itineraries.parquet` |
| File size | 210 MB (20,465,117 rows, Snappy compressed) |
| CORS | Configured — GET/HEAD allowed from all origins, Content-Range exposed |

---

## Scripts Reference

| Script | Input | Output | When to run |
|--------|-------|--------|-------------|
| `build_db.py` | Raw Parquet files + CSVs in `Raw_Parquet_CSV_Inputs/` | `connecting_itineraries.db` | When source data changes |
| `export_parquet.py` | `connecting_itineraries.db` | `connecting_itineraries.parquet` | After `build_db.py` |
| `gen_airports_json.py` | `Raw_Parquet_CSV_Inputs/airports.csv` | `airports.json` | If airport CSV changes |
| `gen_data_values.py` | `connecting_itineraries.db` | `data_values.json` | After `build_db.py` |

---

## Open Questions — Resolved

1. **Database format:** SQLite → exported to Parquet via `export_parquet.py`. Done.
2. **Hosting:** Cloudflare R2. Done.
3. **Queries:** Pivot table UI (pre-built aggregations) + freeform SQL editor. Done.
4. **Chart types:** Bar, line, pie, scatter — user-selectable palette. Done.





\----------------------------------------------



## Field Display Labels & Visibility

### Hidden by default (not shown in UI)
rp_carrier, issue_carrier, rp_year, rp_month, pur_win_grp, origin_city_mkt_id, dest_city_mkt_id

### Label mapping (db column → display label → tooltip)

| DB Column | Display Label | Tooltip |
|-----------|--------------|---------|
| `origin` | Origin Airport | IATA airport code for the trip origin |
| `dest` | Destination Airport | IATA airport code for the destination |
| `connect_apt` | Connecting Hub | IATA code for the connecting (layover) airport |
| `connect_region` | Hub Region | Geographic grouping: Midwest, Mountain West, Southeast, Texas |
| `od_pair` | Route (Directional) | Origin-destination pair in travel direction, e.g. BOS-MIA |
| `od_pair_undirected` | Route (Bidirectional) | O&D sorted alphabetically — combines both directions for market-level analysis |
| `mkt_carrier` | Marketing Carrier | Airline that sold the ticket (IATA code) |
| `op_carrier_1` | Operating Carrier — Leg 1 | Airline that operated the origin-to-hub flight |
| `op_carrier_2` | Operating Carrier — Leg 2 | Airline that operated the hub-to-destination flight |
| `sch_fl_yr` | Flight Year | Scheduled departure year |
| `sch_fl_mo` | Flight Month | Scheduled departure month (1–12) |
| `num_pax` | Passengers | Number of passengers on this market segment |
| `allocated_amt` | Revenue (USD) | Accurately prorated fare for this O&D market segment, sourced from DOT DB1C Market fare break data. Safe to SUM at any aggregation level. Displayed in UI as "Revenue." |
| `allocated_tax` | Taxes & Fees (USD) | Prorated tax component. Pre-tax fare = Revenue − Taxes & Fees. |
| `revenue_cat` | Revenue Category | CAT1 = fare reported (> $0); CAT2 = zero or unreported fare |
| `dwell_time` | Connection Time (min) | Minutes between arriving at hub and departing on the outbound leg |
| `long_connect` | Long Connection | 1 = connection ≥ 4 hours; 0 = connection < 4 hours |
| `routed_miles` | Total Routed Miles | Total distance flown for this O&D market |
| `gc_miles` | Nonstop Miles | Straight-line (nonstop) distance origin to destination, from DOT distance tables |
| `circuity_abs` | Excess Miles Traveled | Routed miles minus nonstop miles — how far out of the way this routing goes |
| `circuity_ratio` | Circuity Ratio | Routed miles ÷ nonstop miles (1.0 = perfectly direct; higher = more circuitous) |
| `od_min_circuity_abs` | Min. Excess Miles (This Route) | Lowest excess miles observed for this O&D across all hub routings in the dataset — used for relative circuity filtering |
| `od_min_circuity_ratio` | Min. Circuity Ratio (This Route) | Lowest circuity ratio for this O&D across all hub routings |
| `freq_flag` | Nonstop Frequency | Nonstop competition level: LOW < 13 departures/month, MED = 13–90 |
| *(derived)* | Avg OD Fare | `SUM(allocated_amt) / SUM(num_pax)` — average fare per passenger for the O&D market segment. **Not** airline yield (which is Rev/RPM). Formerly labeled "Yield / Pax." |

---

## Feature Implementation Status

All features implemented in `index.html` as of current build.

| # | Feature | Status | Implementation notes |
|---|---------|--------|----------------------|
| 1 | Pivot table (SUM pax, revenue, avg OD fare; Show as %) | ✅ Done | Pivot Table tab; client-side % computed from grand total; sortable columns; passengers formatted as #,### |
| 2 | Relative circuity gap filter | ✅ Done | Sidebar → Circuity Filters; `WHERE (circuity_abs - od_min_circuity_abs) <= X` |
| 3 | Absolute circuity filter (abs miles + ratio) | ✅ Done | Sidebar → Circuity Filters; separate inputs for excess miles and ratio cap |
| 4 | Dwell time bucketing (user-defined interval) | ✅ Done | Available in both Pivot Table and Chart tabs; `FLOOR(dwell_time / N) * N` expression |
| 5 | CSV and XLSX export | ✅ Done | Export buttons in Pivot Table and SQL panels; XLSX via SheetJS CDN; headers match UI display labels (spaces → underscores); pivot export reflects per-day divisor and only includes visible columns |
| 6 | Charts including scatter | ✅ Done | Chart tab; bar, line, pie, scatter; X-axis sort control; row limit |
| 7 | Human-readable field labels + tooltips | ✅ Done | `FIELD_META` object maps all columns to display labels and tooltip text |
| 8 | Airport typeahead (IATA prefix ≤3 chars; city name ≥4 chars) | ✅ Done | Origin, destination, and connecting hub searches all use dual-mode typeahead against `airports.json` filtered to dataset airports via `data_values.json` |
| 9a | Minimum dwell time filter | ✅ Done | Sidebar → Dwell Time; `WHERE dwell_time >= X` |
| 9b | Factor Adjustment toggle (×2.5 multiplier) | ✅ Done | Top bar toggle; labeled "Raw Data" / "Factor Adjustment (×2.5)"; applies to pax and revenue SUM only — Avg OD Fare ratio unaffected |
| 10 | Light/dark mode toggle | ✅ Done | Top bar; persisted in `localStorage`; charts re-render on switch without re-querying |
| 11 | Color palette selector (6 palettes) | ✅ Done | Default, Colorblind-Safe, Monochrome, High Contrast, Warm, Cool; persisted in `localStorage` |
| 12 | Daily average passengers | ✅ Done | Pivot Table metric; `SUM(pax) ÷ total calendar days` derived from selected months |
| — | Column sort on pivot table | ✅ Done | Click ↕ on any header; numeric cols default desc, text cols default asc; TOTAL row pinned |
| — | X-axis sort control on charts | ✅ Done | By Y value (high/low) or by X label (alpha/numeric); numeric sort used for dwell buckets |
| — | Aviation-themed query loading animation | ✅ Done | Plane (✈) sweeps across a progress bar during all DuckDB queries |
| — | Startup optimization (fast filter list load) | ✅ Done | `data_values.json` replaces cold Parquet scans; load time reduced from ~30 s to <1 s |
| — | User guide | ✅ Done | `user-guide.html`; written for non-technical audience with airline/economics background |
| — | Factor Adjustment instant re-render | ✅ Done | Toggle now scales existing pivot rows in-memory without re-querying DuckDB |
| — | Scatter trendline + R² | ✅ Done | OLS regression computed client-side; dashed trendline drawn as second ECharts series; `y = mx + b  R² = x.xxxx` displayed top-right of chart |
| — | Maximum dwell time filter | ✅ Done | Sidebar → Dwell Time; `WHERE dwell_time <= X`; paired with existing min filter |
| — | Dropdown z-index fix | ✅ Done | Typeahead dropdowns changed to `position: fixed` with JS anchor to input rect; always renders above sidebar content |
| — | User Guide link in top bar | ✅ Done | 📖 User Guide link opens `user-guide.html` in new tab |
| — | Group By 3 & 4 on pivot table | ✅ Done | Two additional optional group-by selectors; pivot SQL builds GROUP BY dynamically from however many dims are selected (1–4) |
| — | Metric label renames | ✅ Done | "Market Fare" → "Revenue"; "Yield / Pax" → "Avg OD Fare" (Revenue ÷ Passengers; distinct from airline yield = Rev/RPM) |
| — | Per Period / Per Day toggle on charts | ✅ Done | Chart tab has its own independent Per Period / Per Day toggle; auto-refreshes chart on change; separate from pivot table toggle |
| — | Factor Adjustment auto-refresh on chart | ✅ Done | Factor Adjustment toggle now re-runs chart query automatically when a chart is drawn, same as pivot instant re-render |
| — | Palette fix on bar/line charts | ✅ Done | Removed per-series `itemStyle.color` override; top-level `color` array now controls all bar/line colors correctly |
| — | Stop Run button | ✅ Done | ■ Stop button next to Run on all three tabs; uses `conn.cancelSent()` to interrupt DuckDB engine immediately; animation stops instantly; status shows "Query stopped."; error suppressed on intentional cancel |
| — | Passenger formatting (#,###) | ✅ Done | Pivot table passengers column displays as whole number with comma thousands separator |
| — | Export header labels | ✅ Done | Exported CSV/XLSX headers match UI display labels (spaces → underscores); pivot exports only include visible columns and reflect per-day divisor |