# DB1C Data Processing — Claude Code Context (v2)

## Project Goal

Parse six months of DOT DB1C (OD40) airline ticket data and write a filtered, enriched SQLite database to support analysis of how **hub dwell times for connecting itineraries correlate with customer preference**.

Key analytical goals:
- Measure passenger share by dwell time at connecting hubs
- Compare dwell times across hubs (e.g., CLT vs. ATL vs. IAD) for similar O&Ds
- Compare results across carriers (AA, UA, WN, DL)
- Evaluate whether conclusions change when low-cost carriers (e.g., WN) are excluded and only legacy carriers are compared

Hub dwell time (connection time) is used as a **proxy for total itinerary travel time**. Circuity is tracked alongside dwell time because significantly different routings for the same O&D can make dwell time a poor predictor of total travel time.

---

## Source Data

### DB1C Parquet Files (primary input)
- Origin: BTS 40% Origin & Destination Survey (OD40 / DB1C)
- Format: Parquet files, one per reporting month (Jul–Dec 2025; released early 2026)
- Each row = one passenger ticket (~80 million rows total = 40% sample of domestic O&D traffic)
- The schema is **wide**: fields repeat for each coupon leg, indexed `_1` through `_23`
- `CouponSeg` tells you how many legs the itinerary has (max 23)
- Since only 1-stop itineraries are written to the database, columns beyond `_2` (plus `LastApt`/`LastCityMktID`/`LastCityWAC`) can be discarded during processing

#### Key ticket-level fields (appear once per row)

| Field | Type | Description |
|-------|------|-------------|
| `RpCarrier` | char(3) | Reporting carrier code |
| `RpYear` | int | Reporting year (YYYY) |
| `RpMonth` | int | Reporting month (MM) |
| `CouponSeg` | int | Total number of coupon legs in itinerary |
| `IssueCarrier` | char(3) | Carrier that issued the ticket |
| `TotalAmt` | float | Total ticket amount including taxes (USD); null for Cat 2 tickets |
| `TaxAmt` | float | Tax portion of TotalAmt (USD); null for Cat 2 tickets |
| `DollarCred` | int | Dollar credibility flag (placeholder, not currently used) |
| `NumPax` | int | Number of passengers — default 1; each row is one passenger |
| `PurWinGrp` | char(4) | Purchase window: `2290` = 22–90 days before travel, `21AP` = ≤21 days before travel, `91UP` = ≥91 days before travel; null for Cat 2 |

#### Repeating coupon-level fields (suffixed `_1` through `_N` where N = CouponSeg)

For coupon number `N`:

| Field | Type | Description |
|-------|------|-------------|
| `SchFlYr_N` | int | Scheduled flight year of coupon N |
| `SchFlMo_N` | int | Scheduled flight month of coupon N |
| `Apt_N` | char(3) | **Origin** airport code for coupon N (also serves as destination for coupon N-1) |
| `WAC_N` | int | World Area Code for airport N's state |
| `CityMktID_N` | int | DOT city market ID for airport N (use for multi-airport city grouping) |
| `ViaApt_N` | char | Intermediate stopover airports within coupon N (colon-separated); passenger does not deplane |
| `OpCarrier_N` | char(3) | Operating carrier for coupon N; `--` = surface segment |
| `MktCarrier_N` | char(3) | Marketing carrier for coupon N; `--` = surface segment |
| `Coupon_SegDist_N` | int | Nonstop mileage for coupon N |
| `Dwell_Time_N` | float | Minutes between coupon N-1 and coupon N; `9999` = >24 hrs; `-1` = surface segment; null = unknown |
| `TripBk19_7_N` | char(1) | Trip break code (19.7 logic): `X` = directional O&D break, `Y` = domestic portion of intl O&D, blank = no break |
| `TripBk19_8_N` | char(1) | Trip break code (19.8 provisional logic): same values as above |
| `CityMktID_N` | int | City market ID for the airport at position N (destination of coupon N-1 / origin of coupon N) |
| `CityWAC_N` | int | World area code for the city at position N |

#### Special last-airport fields

| Field | Description |
|-------|-------------|
| `LastApt` | Airport code of the final destination |
| `LastCityMktID` | City market ID of the final destination |
| `LastCityWAC` | World area code of the final destination |

#### How to read a connecting itinerary example

For a 2-coupon (1-stop) ticket BUF → CLT → MIA:
- `CouponSeg = 2`
- `Apt_1 = BUF` (origin of leg 1)
- `MktCarrier_1 = AA`, `OpCarrier_1 = AA`
- `Apt_2 = CLT` (destination of leg 1 = origin of leg 2; this is the connecting airport)
- `Dwell_Time_2` = connection time at CLT in minutes
- `MktCarrier_2 = AA`, `OpCarrier_2 = AA`
- `LastApt = MIA`
- `TripBk19_7_2 = X` marks CLT as the directional trip break (turnaround point) if this is a round trip

---

### T-100 Domestic Segment Data (secondary input)
- Source: BTS T-100 Domestic Segment (All Carriers)
- Used **only** to flag/exclude O&D pairs by nonstop frequency
- Key fields needed: `ORIGIN`, `DEST`, `MONTH`, `YEAR`, `DEPARTURES_PERFORMED` (or `DEPARTURES_SCHEDULED`)
- One row per carrier × origin × destination × aircraft type × month

---

### Reference Files

#### `Connecting_Airport_List.csv`
Defines the hub airports of interest and their geographic regions. **Only connecting itineraries that pass through one of these airports should be written to the database.** The focus on geographically similar hub sets captures markets where AA, UA, WN, and DL all have viable competitive options, concentrating the analysis where competitive effects of connection time are most pronounced.

| Airport | Region |
|---------|--------|
| PHX | Mountain West |
| SLC | Mountain West |
| DEN | Mountain West |
| LAS | Mountain West |
| DFW | Texas |
| IAH | Texas |
| DAL | Texas |
| HOU | Texas |
| ATL | Southeast |
| CLT | Southeast |
| IAD | Southeast |
| BWI | Southeast |
| DCA | Southeast |
| BNA | Southeast |
| ORD | Midwest |
| DTW | Midwest |
| MSP | Midwest |
| MDW | Midwest |
| STL | Midwest |

#### `airports.csv`
Reference file with lat/long for great circle distance calculations.

| Key Field | Description |
|-----------|-------------|
| `iata_code` | 3-letter IATA airport code (join key) |
| `latitude_deg` | Latitude in decimal degrees |
| `longitude_deg` | Longitude in decimal degrees |
| `name` | Full airport name |
| `municipality` | City |
| `iso_region` | State/region code |

---

## Filtering Logic (what rows to include)

Apply all of the following filters. A row must pass **all** to be written to the database.

### 1. Connecting itinerary requirement — exactly one 1-stop connection
- `CouponSeg >= 2`
- **Non-stop only tickets** (no trip breaks with a connecting hub): excluded entirely
- **Double, triple, or more connects within a single trip break**: excluded as outliers
- The itinerary must include **exactly one 1-stop connection** through a hub of interest
- The connecting airport (i.e., `Apt_2` for a 2-coupon itinerary) must appear in `Connecting_Airport_List.csv`
- For itineraries with more than 2 coupons, identify the trip break point using `TripBk19_7` or `TripBk19_8 = 'X'` to determine which leg constitutes the directional O&D, and confirm the connection airport for the relevant segment is in the hub list

### 2. Online connections only
- For a 1-stop itinerary: `MktCarrier_1` must equal `MktCarrier_2`
- The same marketing carrier must operate both legs of the connecting segment
- Interline connections (different marketing carriers on each leg) are excluded — most domestic connections are online; interline MCTs can introduce oddities and removing them doesn't meaningfully shrink the dataset
- Surface segments (`OpCarrier = '--'`) are also excluded

### 3. T-100 frequency exclusions
Using T-100 data aggregated by O&D pair across all carriers per month:
- **Exclude** the directional O&D if the O&D has **≥ 91 monthly nonstop departures** across all carriers (≈ 3x daily or more). Connecting itineraries on high-frequency routes tend to sell during peak demand periods and may not be representative of broader demand patterns.
- **Flag but keep** O&Ds with **13–90 monthly nonstop departures** (3x weekly to 3x daily)
- O&Ds with **≤ 12 monthly departures** receive no flag (below threshold of interest)
- This flag should be written as a field in the database (see `freq_flag` below)

### 4. Minimum observation threshold
- After all other filters, exclude any **directional O&D** with fewer than **36 monthly observations** in the filtered dataset
- Based on the 40% sample: < 36 observations implies < 3 passengers/day (PDEW), meaning the O&D likely lacks competitive itinerary options across all relevant hub sets

---

## Output Database Schema

Write a SQLite file. Suggested table name: `connecting_itineraries`

### Fields to include from source data

| Field Name | Source | Description |
|------------|--------|-------------|
| `rp_carrier` | `RpCarrier` | Reporting carrier |
| `rp_year` | `RpYear` | Reporting year |
| `rp_month` | `RpMonth` | Reporting month |
| `issue_carrier` | `IssueCarrier` | Issuing carrier |
| `total_amt` | `TotalAmt` | Total ticket amount (USD) |
| `tax_amt` | `TaxAmt` | Tax amount (USD) |
| `num_pax` | `NumPax` | Number of passengers (always 1 in DB1C) |
| `pur_win_grp` | `PurWinGrp` | Purchase window group |
| `origin` | `Apt_1` | Origin airport (leg 1 origin) |
| `connect_apt` | `Apt_2` (for 2-coupon) | Connecting hub airport |
| `dest` | `LastApt` | Final destination airport |
| `origin_city_mkt_id` | `CityMktID_1` | Origin city market ID |
| `connect_city_mkt_id` | `CityMktID_2` | Connecting city market ID |
| `dest_city_mkt_id` | `LastCityMktID` | Destination city market ID |
| `mkt_carrier` | derived (see below) | Marketing carrier for both segments |
| `op_carrier_1` | `OpCarrier_1` | Operating carrier leg 1 |
| `op_carrier_2` | `OpCarrier_2` | Operating carrier leg 2 |
| `sch_fl_yr` | `SchFlYr_1` | Scheduled flight year of first coupon |
| `sch_fl_mo` | `SchFlMo_1` | Scheduled flight month of first coupon |
| `seg_dist_1` | `Coupon_SegDist_1` | Nonstop miles, leg 1 |
| `seg_dist_2` | `Coupon_SegDist_2` | Nonstop miles, leg 2 |
| `dwell_time` | `Dwell_Time_2` | Connection dwell time at hub (minutes) |
| `trip_bk_7` | `TripBk19_7_2` | Trip break code 19.7 at connection point |
| `trip_bk_8` | `TripBk19_8_2` | Trip break code 19.8 at connection point |
| `connect_region` | derived | Region from `Connecting_Airport_List.csv` |

### Derived / calculated fields to add

| Field Name | Type | Description |
|------------|------|-------------|
| `mkt_carrier` | TEXT | Marketing carrier — single value; `MktCarrier_1` when it equals `MktCarrier_2` (required for inclusion) |
| `connect_region` | TEXT | Region of the connecting hub airport from `Connecting_Airport_List.csv` |
| `od_pair` | TEXT | Directional O&D string, format `ORIGIN-DEST` (e.g., `BUF-MIA`) |
| `od_pair_undirected` | TEXT | Non-directional O&D string, alphabetically sorted, format `AAA-BBB` |
| `routed_miles` | INTEGER | Sum of `seg_dist_1 + seg_dist_2` (total miles flown) |
| `gc_miles` | REAL | Great circle distance in statute miles between `origin` and `dest` airports, calculated from `airports.csv` lat/long using the Haversine formula |
| `circuity_abs` | REAL | Absolute circuity: `routed_miles - gc_miles` (excess miles flown) |
| `circuity_ratio` | REAL | Ratio circuity: `routed_miles / gc_miles` (1.0 = perfectly direct) |
| `freq_flag` | TEXT | Frequency flag from T-100: `LOW` = ≤12 monthly O&D departures, `MED` = 13–90, `HIGH` = 91+ (HIGH rows are excluded per filter rule 3, so this field will only contain `LOW` or `MED` in the output) |
| `is_round_trip_leg` | INTEGER | 1 if `TripBk19_7` or `TripBk19_8 = 'X'` at the connect point (indicates this is the outbound leg of a round trip), 0 otherwise |

---

## Circuity — Role in Analysis

Circuity fields (`circuity_abs`, `circuity_ratio`) are **retained for downstream filtering and grouping but are not used as an exclusion criterion** during DB construction.

**Why it matters:** Dwell time is used as a proxy for total itinerary travel time. When two different routings serve the same O&D but have significantly different total flight distances, dwell time becomes a poor comparator — a shorter dwell time via a more circuitous hub may actually produce longer total travel time than a longer dwell via a more direct routing. The circuity fields allow analysts to group or filter by like routings (e.g., limit comparisons to hubs with similar routed miles for a given O&D) before drawing conclusions about dwell time preferences.

---

## Revenue Allocation

`TotalAmt` in DB1C represents **total ticket revenue** at the ticket level, not per trip break. Because tickets vary in their number of trip break portions (one-way vs. round-trip vs. multi-leg), comparing `TotalAmt` directly across tickets is not apples-to-apples.

**Proposed approach:** Divide `TotalAmt` evenly across trip break portions of the itinerary (e.g., a round-trip ticket with 2 trip breaks allocates 50% of revenue to each direction).

**Known limitation:** Airlines price each trip break portion independently using O&D-level pricing logic, so an even split will be inaccurate. However, the data does not contain the underlying per-segment price inputs needed to reconstruct a more precise allocation.

**Open question for AI coding agents:** If a better revenue proration method is feasible given the available fields (e.g., using `seg_dist_1`/`seg_dist_2` as a mileage-based allocation proxy, or using `PurWinGrp` as a signal), recommendations are welcome.

**Prerequisite step (Item 10):** Before implementing any proration logic, run a post-DB SQL query to evaluate the proportion of tickets that are one-way vs. round-trip vs. multi-leg. If the vast majority are simple one-way or single-connect itineraries, proration complexity may be unnecessary. This query should be run in DB Browser after initial DB creation.

---

## Great Circle Distance Calculation

Use the **Haversine formula** to compute `gc_miles`. The `airports.csv` file contains lat/long keyed on `iata_code`.

```python
import math

def haversine_miles(lat1, lon1, lat2, lon2):
    R = 3958.8  # Earth radius in statute miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.asin(math.sqrt(a))
```

Build an airport lookup dict from `airports.csv` before processing:

```python
import pandas as pd
apt_df = pd.read_csv('airports.csv')[['iata_code','latitude_deg','longitude_deg']].dropna()
apt_lookup = apt_df.set_index('iata_code')[['latitude_deg','longitude_deg']].to_dict('index')
```

Handle airports not found in the lookup by setting `gc_miles`, `circuity_abs`, and `circuity_ratio` to `NULL`.

---

## Parquet Processing Notes

- Read Parquet files with `pandas` or `pyarrow`
- Process files one month at a time to manage memory
- Only columns through `_2` are needed (plus `LastApt`/`LastCityMktID`/`LastCityWAC`); columns `_3` through `_23` can be discarded — they exist for complex multi-connect itineraries that are excluded by the filtering logic
- `Dwell_Time_2` values to be aware of:
  - `9999` = dwell time > 24 hours (likely an overnight connection or trip break)
  - `-1` = surface segment
  - `null` = unknown
  - Valid range: `1–1440` minutes
- Connection dwell time of `9999` may indicate a trip break rather than a true connecting itinerary; use the `TripBk19_7` and `TripBk19_8` fields to confirm the break structure

---

## Suggested Processing Order

1. Load `airports.csv` into a lookup dictionary (iata_code → lat/lon)
2. Load `Connecting_Airport_List.csv` into a set/dict of hub airports → region
3. Pre-compute T-100 O&D frequency table (origin, dest, year, month → total monthly departures across all carriers)
4. For each Parquet file (one per month):
   a. Filter to `CouponSeg >= 2`
   b. Identify the connecting airport as `Apt_2` (for the first trip-break segment)
   c. Apply hub filter — connecting airport must be in hub list
   d. Apply online-only filter — `MktCarrier_1 == MktCarrier_2`
   e. Exclude surface segments
   f. Compute derived fields: `od_pair`, `routed_miles`, `gc_miles`, `circuity_abs`, `circuity_ratio`, `connect_region`, `mkt_carrier`, `freq_flag`, `is_round_trip_leg`
   g. Apply T-100 HIGH frequency exclusion (`freq_flag == 'HIGH'`)
   h. Append to SQLite database
5. After all files are loaded, apply the minimum observation threshold (exclude O&D directional pairs with < 36 monthly observations) — add an index on `od_pair` + `rp_year` + `rp_month` before this step for performance
6. Create indexes on: `od_pair`, `connect_apt`, `mkt_carrier`, `connect_region`, `rp_year`, `rp_month`

---

## SQLite Database Notes

- Use `sqlite3` module or `sqlalchemy` with pandas `to_sql()`
- Recommended: write rows in batches (e.g., 50,000 at a time) to avoid memory pressure
- Create the table with explicit column types before inserting data
- After loading all data and applying the minimum observation exclusion, `VACUUM` the database to reclaim space
- Suggested output filename: `connecting_itineraries.db`

---

## Environment Notes

### Python
- Anaconda (Python 3.13.9) is installed at `C:\Users\mrmce\anaconda3\python.exe`
- `C:\Users\mrmce\anaconda3` and `C:\Users\mrmce\anaconda3\Scripts` are on the user PATH
- Windows Store App Execution Aliases for Python have been disabled to prevent the Store stub from intercepting `python` commands
- If Python appears missing in a future session, restart VS Code first; if still missing, verify the above PATH entries are present

---

## Source File Row Counts

Counts read from Parquet file footer metadata. For this survey dataset (40% sample), unique rows ≈ total rows — true duplicates across all 200+ columns are negligible.

| File | Month | Total Rows |
|------|-------|-----------|
| DB1C.PUBLIC.202507.REL04.24MAR2026.parquet | Jul 2025 | 14,638,341 |
| DB1C.PUBLIC.202508.REL02.25MAR2026.parquet | Aug 2025 | 13,689,693 |
| DB1C.PUBLIC.202509.REL02.25MAR2026.parquet | Sep 2025 | 12,010,559 |
| DB1C.PUBLIC.202510.REL03.27MAR2026.parquet | Oct 2025 | 13,813,374 |
| DB1C.PUBLIC.202511.REL01.31MAR2026.parquet | Nov 2025 | 12,194,164 |
| DB1C.PUBLIC.202512.REL01.02APR2026.parquet | Dec 2025 | 13,512,370 |
| **Total** | | **79,858,501** |
