"""
build_db.py
Reads DB1C Market Parquet files and T-100 reference CSV, applies all filtering
logic defined in DB_Schema.md, and writes connecting_itineraries.db.

Source: DB1C Market (not OD40/PUBLIC). Each source row is already one
directional market segment (pre-split by trip break) with accurately prorated
fare via MktAmount/MktTax. No trip-break parsing or revenue proration needed.

Input files expected in Raw_Parquet_CSV_Inputs/:
  DB1C.MARKET.YYYYMM.*.parquet  (6 files, Jul–Dec 2025)
  T_T100D_SEGMENT_ALL_CARRIER.csv
  Connecting_Airport_List.csv
"""

import os
import sqlite3
import time

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR = os.path.join(BASE_DIR, 'Raw_Parquet_CSV_Inputs')
DB_PATH   = os.path.join(BASE_DIR, 'connecting_itineraries.db')

PARQUET_FILES = sorted(
    f for f in os.listdir(INPUT_DIR)
    if f.endswith('.parquet') and 'MARKET' in f.upper()
)
BATCH_SIZE = 50_000

# ── Columns to read from each Market Parquet file ─────────────────────────────
PARQUET_COLS = [
    # Ticket identifiers
    'RpCarrier', 'RpYear', 'RpMonth', 'SchFlYear', 'SchFlMonth',
    'IssuingCarrier', 'MktCoupons', 'PurchaseWindowGroup',
    # Carrier groups (colon-delimited per segment)
    'MktCarrier',       # single overall marketing carrier
    'MktCarrierGroup',  # "AA:AA" per segment — used for online filter
    'OpCarrierGroup',   # "AA:AA" per segment — used for surface segment filter
    # Geography
    'Origin', 'OriginCityMarketID',
    'Dest',   'DestCityMarketID',
    'AirportGroup',   # "ORG:HUB:DST" — hub = split(':')[1]
    # Dwell time (colon-delimited; first element = hub dwell minutes)
    'DwellTimeGroup',
    # Revenue — already prorated to this market segment
    'Passengers', 'MktAmount', 'MktTax',
    # Distance
    'TotalDistance',  # routed miles
    'NonStopMiles',   # O&D nonstop miles (DOT table — no haversine needed)
    # Filters
    'Nonstop',        # 0 = connecting, 1 = nonstop
]

# ── Load reference data ────────────────────────────────────────────────────────
print('Loading reference data...')

hub_df     = pd.read_csv(os.path.join(INPUT_DIR, 'Connecting_Airport_List.csv'))
hub_lookup = dict(zip(hub_df['Airport'], hub_df['Region']))
hub_set    = set(hub_lookup.keys())
print(f'  {len(hub_set)} hub airports loaded.')

t100 = pd.read_csv(
    os.path.join(INPUT_DIR, 'T_T100D_SEGMENT_ALL_CARRIER.csv'),
    usecols=['ORIGIN', 'DEST', 'YEAR', 'MONTH', 'DEPARTURES_PERFORMED'],
)
t100_freq = (
    t100.groupby(['ORIGIN', 'DEST', 'YEAR', 'MONTH'], as_index=False)
        ['DEPARTURES_PERFORMED'].sum()
        .rename(columns={
            'ORIGIN': 'Origin',
            'DEST':   'Dest',
            'YEAR':   'SchFlYear',
            'MONTH':  'SchFlMonth',
            'DEPARTURES_PERFORMED': '_deps',
        })
)
print(f'  T-100 frequency table: {len(t100_freq):,} O&D-month records.')
print(f'  Market Parquet files found: {len(PARQUET_FILES)}')
for f in PARQUET_FILES:
    print(f'    {f}')

# ── SQLite setup ──────────────────────────────────────────────────────────────
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
    print(f'\nRemoved existing database at {DB_PATH}')

conn = sqlite3.connect(DB_PATH)
conn.execute('PRAGMA journal_mode = WAL')
conn.execute('PRAGMA synchronous = NORMAL')

conn.execute("""
CREATE TABLE connecting_itineraries (
    rp_carrier             TEXT,
    rp_year                INTEGER,
    rp_month               INTEGER,
    sch_fl_yr              INTEGER,
    sch_fl_mo              INTEGER,
    issue_carrier          TEXT,
    pur_win_grp            TEXT,
    revenue_cat            TEXT,
    mkt_carrier            TEXT,
    op_carrier_1           TEXT,
    op_carrier_2           TEXT,
    num_pax                REAL,
    allocated_amt          REAL,
    allocated_tax          REAL,
    origin                 TEXT,
    connect_apt            TEXT,
    dest                   TEXT,
    origin_city_mkt_id     INTEGER,
    dest_city_mkt_id       INTEGER,
    connect_region         TEXT,
    od_pair                TEXT,
    od_pair_undirected     TEXT,
    dwell_time             REAL,
    long_connect           INTEGER,
    routed_miles           REAL,
    gc_miles               REAL,
    circuity_abs           REAL,
    circuity_ratio         REAL,
    freq_flag              TEXT,
    od_min_circuity_abs    REAL,
    od_min_circuity_ratio  REAL
)
""")
conn.commit()

# ── Per-file processing ───────────────────────────────────────────────────────
total_written = 0

for fname in PARQUET_FILES:
    fpath = os.path.join(INPUT_DIR, fname)
    print(f'\n{"-"*60}')
    print(f'Processing: {fname}')

    df = pq.read_table(fpath, columns=PARQUET_COLS).to_pandas()
    print(f'  Loaded:  {len(df):,} rows')

    # ── Filter 1: Connecting only, exactly 1-stop ─────────────────────────────
    df = df[(df['Nonstop'] == 0) & (df['MktCoupons'] == 2)].copy()

    # ── Parse group string fields ─────────────────────────────────────────────
    # AirportGroup = "ORG:HUB:DST"
    apt_split = df['AirportGroup'].str.split(':', expand=True)
    df['connect_apt'] = apt_split[1]

    # DwellTimeGroup = "90:Null" — first element is hub dwell in minutes
    df['dwell_time'] = pd.to_numeric(
        df['DwellTimeGroup'].str.split(':').str[0], errors='coerce'
    )

    # MktCarrierGroup / OpCarrierGroup = "AA:AA"
    mkt_split = df['MktCarrierGroup'].str.split(':', expand=True)
    op_split  = df['OpCarrierGroup'].str.split(':', expand=True)
    df['_mkt1'] = mkt_split[0]
    df['_mkt2'] = mkt_split[1]
    df['op_carrier_1'] = op_split[0]
    df['op_carrier_2'] = op_split[1]

    # ── Filter 2: Hub of interest ─────────────────────────────────────────────
    df = df[df['connect_apt'].isin(hub_set)].copy()

    # ── Filter 3: Online only (same mkt carrier both legs, no surface segs) ───
    df = df[
        (df['_mkt1'] == df['_mkt2']) &
        (df['op_carrier_1'].fillna('') != '--') &
        (df['op_carrier_2'].fillna('') != '--')
    ].copy()

    # ── Filter 4: Valid dwell time ────────────────────────────────────────────
    df = df[
        df['dwell_time'].notna() &
        (df['dwell_time'] != 9999) &
        (df['dwell_time'] != -1)
    ].copy()

    print(f'  After filters: {len(df):,} rows')
    if df.empty:
        print('  (No rows to write — skipping.)')
        continue

    # ── Derived fields ────────────────────────────────────────────────────────
    df['revenue_cat'] = np.where(
        df['MktAmount'].isna() | (df['MktAmount'] == 0), 'CAT2', 'CAT1'
    )

    has_dwell = df['dwell_time'].notna()
    df['long_connect'] = pd.array(
        np.where(has_dwell, (df['dwell_time'] >= 240).astype(float), np.nan),
        dtype=pd.Int64Dtype(),
    )

    df['connect_region'] = df['connect_apt'].map(hub_lookup)

    df['od_pair'] = df['Origin'] + '-' + df['Dest']
    df['od_pair_undirected'] = np.where(
        df['Origin'] <= df['Dest'],
        df['Origin'] + '-' + df['Dest'],
        df['Dest']   + '-' + df['Origin'],
    )

    df['routed_miles'] = df['TotalDistance']
    df['gc_miles']     = df['NonStopMiles']
    df['circuity_abs'] = np.where(
        df['NonStopMiles'].isna(), np.nan,
        df['TotalDistance'] - df['NonStopMiles']
    )
    df['circuity_ratio'] = np.where(
        df['NonStopMiles'].isna() | (df['NonStopMiles'] == 0), np.nan,
        df['TotalDistance'] / df['NonStopMiles']
    )

    # T-100 freq flag — join on Origin, Dest, scheduled flight year/month
    df = df.merge(t100_freq, on=['Origin', 'Dest', 'SchFlYear', 'SchFlMonth'], how='left')
    df['_deps'] = df['_deps'].fillna(0)
    df['freq_flag'] = np.where(
        df['_deps'] >= 91, 'HIGH',
        np.where(df['_deps'] >= 13, 'MED', 'LOW')
    )
    df.drop(columns=['_deps'], inplace=True)

    # ── Filter 5: Exclude HIGH-frequency O&Ds ────────────────────────────────
    df = df[df['freq_flag'] != 'HIGH'].copy()
    print(f'  After T-100 HIGH exclusion: {len(df):,} rows')

    # od_min_circuity populated post-load
    df['od_min_circuity_abs']   = np.nan
    df['od_min_circuity_ratio'] = np.nan

    # ── Write to SQLite in batches ────────────────────────────────────────────
    out = df[[
        'RpCarrier', 'RpYear', 'RpMonth', 'SchFlYear', 'SchFlMonth',
        'IssuingCarrier', 'PurchaseWindowGroup', 'revenue_cat',
        'MktCarrier', 'op_carrier_1', 'op_carrier_2',
        'Passengers', 'MktAmount', 'MktTax',
        'Origin', 'connect_apt', 'Dest',
        'OriginCityMarketID', 'DestCityMarketID',
        'connect_region', 'od_pair', 'od_pair_undirected',
        'dwell_time', 'long_connect',
        'routed_miles', 'gc_miles', 'circuity_abs', 'circuity_ratio',
        'freq_flag', 'od_min_circuity_abs', 'od_min_circuity_ratio',
    ]].rename(columns={
        'RpCarrier':          'rp_carrier',
        'RpYear':             'rp_year',
        'RpMonth':            'rp_month',
        'SchFlYear':          'sch_fl_yr',
        'SchFlMonth':         'sch_fl_mo',
        'IssuingCarrier':     'issue_carrier',
        'PurchaseWindowGroup':'pur_win_grp',
        'MktCarrier':         'mkt_carrier',
        'Passengers':         'num_pax',
        'MktAmount':          'allocated_amt',
        'MktTax':             'allocated_tax',
        'Origin':             'origin',
        'Dest':               'dest',
        'OriginCityMarketID': 'origin_city_mkt_id',
        'DestCityMarketID':   'dest_city_mkt_id',
    })

    for start in range(0, len(out), BATCH_SIZE):
        out.iloc[start:start + BATCH_SIZE].to_sql(
            'connecting_itineraries', conn,
            if_exists='append', index=False,
        )

    total_written += len(out)
    print(f'  Written:  {len(out):,} rows  (running total: {total_written:,})')

# ── Post-load: minimum observation threshold ──────────────────────────────────
print(f'\n{"-"*60}')
print('Applying minimum observation threshold (< 36 rows per directional O&D per month)...')

conn.execute("""
    CREATE INDEX idx_od_month
    ON connecting_itineraries (od_pair, rp_year, rp_month)
""")
conn.commit()

conn.execute("""
    DELETE FROM connecting_itineraries
    WHERE od_pair || '|' || rp_year || '|' || rp_month IN (
        SELECT od_pair || '|' || rp_year || '|' || rp_month
        FROM connecting_itineraries
        GROUP BY od_pair, rp_year, rp_month
        HAVING COUNT(*) < 36
    )
""")
conn.commit()
print('  Threshold deletion complete.')

# ── Post-load: precompute per-O&D minimum circuity ───────────────────────────
print('\nPrecomputing per-O&D minimum circuity...')
t0 = time.time()
mins = conn.execute('''
    SELECT od_pair, MIN(circuity_abs), MIN(circuity_ratio)
    FROM connecting_itineraries
    WHERE circuity_abs IS NOT NULL AND circuity_ratio IS NOT NULL
    GROUP BY od_pair
''').fetchall()
print(f'  {len(mins):,} unique O&D pairs ({time.time()-t0:.1f}s). Running batch UPDATE...')
t1 = time.time()
conn.executemany(
    'UPDATE connecting_itineraries SET od_min_circuity_abs=?, od_min_circuity_ratio=? WHERE od_pair=?',
    [(row[1], row[2], row[0]) for row in mins]
)
conn.commit()
print(f'  Done ({time.time()-t1:.1f}s).')

# ── Final indexes ─────────────────────────────────────────────────────────────
print('\nCreating indexes...')
for ddl in [
    'CREATE INDEX idx_od_pair        ON connecting_itineraries (od_pair)',
    'CREATE INDEX idx_connect_apt    ON connecting_itineraries (connect_apt)',
    'CREATE INDEX idx_mkt_carrier    ON connecting_itineraries (mkt_carrier)',
    'CREATE INDEX idx_connect_region ON connecting_itineraries (connect_region)',
    'CREATE INDEX idx_rp_year        ON connecting_itineraries (rp_year)',
    'CREATE INDEX idx_rp_month       ON connecting_itineraries (rp_month)',
]:
    conn.execute(ddl)
conn.commit()

# ── VACUUM ────────────────────────────────────────────────────────────────────
print('Vacuuming...')
conn.execute('VACUUM')
conn.close()

print(f'\n{"="*60}')
print(f'Done.  {total_written:,} rows written before threshold exclusion.')
print(f'Database: {DB_PATH}')
