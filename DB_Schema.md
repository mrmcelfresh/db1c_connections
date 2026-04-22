# connecting_itineraries.db — Schema Reference

## Overview

| Property | Value |
|----------|-------|
| File | `connecting_itineraries.db` |
| Format | SQLite 3 (WAL journal mode) |
| Table | `connecting_itineraries` |
| Rows | TBD after rebuild from Market source |
| File size | TBD |
| Source | DOT DB1C Market — 40% domestic O&D ticket sample, pre-split by trip break with accurate fare proration |
| Coverage | July–December 2025 (6 reporting months) |
| Built by | `build_db.py` |

---

## Data Source: DB1C Market vs. DB1C OD40

The database is built from **DB1C Market** (not DB1C OD40/PUBLIC) files. Key differences:

| | DB1C OD40 (prior source) | DB1C Market (current source) |
|--|--------------------------|------------------------------|
| Revenue | Full ticket fare — required manual proration | Prorated to market segment — accurate |
| Trip breaks | Required programmatic detection | Pre-split: each row is one directional market |
| O&D distance | Derived via haversine from lat/lon | `NonStopMiles` provided directly |
| Round trips | Required separate outbound/return logic | Each direction is a separate row naturally |

---

## Build Filters Applied

Only rows meeting **all** of the following criteria are included:

1. **Connecting itinerary** — `Nonstop == 0` and `MktCoupons == 2` (exactly 1-stop)
2. **Connecting airport is a hub of interest** — middle element of `AirportGroup` is in the 19-hub list
3. **Online connections only** — same marketing carrier on both segments (parsed from `MktCarrierGroup`); no surface (`--`) operating segments
4. **Valid dwell time** — dwell ≠ 9999 (DOT sentinel for overnight/broken connection) and dwell ≠ -1 (data anomaly); non-null
5. **Non-stop frequency filter** — O&D pairs with HIGH nonstop frequency (≥ 91 departures/month per T-100) excluded
6. **Minimum observation threshold** — directional O&D / month combinations with fewer than 36 rows deleted post-load

---

## Column Reference

### Ticket Identifiers

| Column | Type | Description |
|--------|------|-------------|
| `rp_carrier` | TEXT | Reporting carrier IATA code |
| `rp_year` | INTEGER | Reporting year (2025) |
| `rp_month` | INTEGER | Reporting month (7–12) |
| `sch_fl_yr` | INTEGER | Scheduled flight year |
| `sch_fl_mo` | INTEGER | Scheduled flight month |
| `issue_carrier` | TEXT | Carrier that issued/ticketed the fare |
| `pur_win_grp` | TEXT | Purchase window group. Values: `21AP` (≤21 days advance), `2290` (22–90 days), `91UP` (>90 days) |
| `revenue_cat` | TEXT | `CAT1` = priced (`allocated_amt` > 0); `CAT2` = zero or null fare |

### Revenue

| Column | Type | Description |
|--------|------|-------------|
| `num_pax` | REAL | Passengers on this market segment (source field is float; whole numbers expected but stored as REAL to preserve precision) |
| `allocated_amt` | REAL | Accurately prorated market fare in USD — sourced directly from `MktAmount`, which reflects actual fare break pricing. Safe to SUM at any aggregation level. |
| `allocated_tax` | REAL | Prorated tax component in USD (`MktTax`). Use to compute pre-tax fare: `allocated_amt - allocated_tax`. |

### Geography

| Column | Type | Description |
|--------|------|-------------|
| `origin` | TEXT | Origin airport IATA code |
| `connect_apt` | TEXT | Connecting (hub) airport IATA code — parsed as middle element of `AirportGroup` |
| `dest` | TEXT | Destination airport IATA code |
| `origin_city_mkt_id` | INTEGER | DOT City Market ID for origin (consolidates nearby airports, e.g. JFK/LGA/EWR → 31703) |
| `dest_city_mkt_id` | INTEGER | DOT City Market ID for destination |
| `connect_region` | TEXT | Region grouping for the connecting hub: Midwest, Mountain West, Southeast, Texas |
| `od_pair` | TEXT | Directional O&D string, e.g. `BOS-MIA` |
| `od_pair_undirected` | TEXT | Alphabetically sorted O&D — combines both directions for market-level analysis, e.g. both `BOS-MIA` and `MIA-BOS` → `BOS-MIA` |

### Carrier

| Column | Type | Description |
|--------|------|-------------|
| `mkt_carrier` | TEXT | Marketing carrier IATA code (same on both segments — guaranteed by online filter) |
| `op_carrier_1` | TEXT | Operating carrier on segment 1 (origin → hub) |
| `op_carrier_2` | TEXT | Operating carrier on segment 2 (hub → dest) |

### Dwell Time

| Column | Type | Description |
|--------|------|-------------|
| `dwell_time` | REAL | Connection time at hub in minutes — parsed from first element of `DwellTimeGroup`. Range: 1–1,440. Rows with dwell = 9999, -1, or null excluded during build. |
| `long_connect` | INTEGER | Binary flag: `1` if `dwell_time >= 240` min (4 hours); `0` if `< 240`; NULL if `dwell_time` is NULL. |

### Distance & Circuity

| Column | Type | Description |
|--------|------|-------------|
| `routed_miles` | REAL | Total routed distance for the O&D market — sourced from `TotalDistance` |
| `gc_miles` | REAL | Nonstop (great circle) distance origin → dest in statute miles — sourced directly from `NonStopMiles` (DOT distance table; no haversine needed) |
| `circuity_abs` | REAL | `routed_miles - gc_miles` — excess miles flown due to routing through hub |
| `circuity_ratio` | REAL | `routed_miles / gc_miles` — 1.0 = perfectly direct; higher = more circuitous. NULL if `gc_miles` is zero. |
| `od_min_circuity_abs` | REAL | Minimum `circuity_abs` for this directional O&D across all hub routings and months. Precomputed for relative-circuity filtering: `WHERE (circuity_abs - od_min_circuity_abs) <= X`. |
| `od_min_circuity_ratio` | REAL | Minimum `circuity_ratio` for this directional O&D across all hub routings and months. |

### T-100 Frequency Flag

| Column | Type | Description |
|--------|------|-------------|
| `freq_flag` | TEXT | Nonstop frequency classification for the origin→dest O&D in the scheduled flight month, from T-100 segment data (all carriers): `LOW` = < 13 dep/month; `MED` = 13–90; `HIGH` rows excluded during build. |

---

## Hub Reference

| Region | Airports |
|--------|----------|
| Midwest | DTW, MDW, MSP, ORD, STL |
| Mountain West | DEN, LAS, PHX, SLC |
| Southeast | ATL, BNA, BWI, CLT, DCA, IAD |
| Texas | DAL, DFW, HOU, IAH |

---

## Indexes

| Index | Columns | Purpose |
|-------|---------|---------|
| `idx_od_month` | `od_pair, rp_year, rp_month` | Primary filter for O&D time-series queries |
| `idx_od_pair` | `od_pair` | O&D lookups and grouping |
| `idx_connect_apt` | `connect_apt` | Hub-level comparisons |
| `idx_connect_region` | `connect_region` | Regional roll-up queries |
| `idx_mkt_carrier` | `mkt_carrier` | Carrier-level filtering |
| `idx_rp_year` | `rp_year` | Year filtering |
| `idx_rp_month` | `rp_month` | Month filtering |

---

## Row Counts by Month

*To be updated after rebuild from Market source files.*

---

## Notes

- **Revenue**: `allocated_amt` is sourced directly from `MktAmount` in DB1C Market, which reflects actual fare break pricing. No proration approximation is applied — this is a significant accuracy improvement over the prior OD40-based approach.
- **Circuity**: All routings are retained regardless of circuity. `circuity_ratio` and `circuity_abs` are provided for downstream filtering of highly dissimilar routings when comparing dwell times across O&D pairs.
- **CAT2 tickets**: Rows with `allocated_amt = 0` are flagged as `revenue_cat = CAT2`. Exclude via `WHERE revenue_cat = 'CAT1'` for revenue analysis.
- **Dropped from prior schema**: `total_amt`, `tax_amt`, `coupon_seg`, `seg_dist_1`, `seg_dist_2`, `trip_bk_7`, `trip_bk_8`, `is_round_trip_leg`, `passenger_trips`, `connect_city_mkt_id`. These were either replaced by more accurate Market equivalents or are no longer needed given the pre-split structure of the source data.
