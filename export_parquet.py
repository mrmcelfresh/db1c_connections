"""
export_parquet.py
Reads connecting_itineraries.db in chunks and writes a single Parquet file
using Snappy compression. Chunked to avoid loading 20M rows into RAM at once.
"""

import os
import sqlite3
import time

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DB_PATH    = os.path.join(BASE_DIR, 'connecting_itineraries.db')
OUT_PATH   = os.path.join(BASE_DIR, 'connecting_itineraries.parquet')
CHUNK_SIZE = 500_000

print(f'Source:  {DB_PATH}')
print(f'Output:  {OUT_PATH}')
print(f'Chunk:   {CHUNK_SIZE:,} rows')

conn   = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

total_rows = cursor.execute('SELECT COUNT(*) FROM connecting_itineraries').fetchone()[0]
print(f'Total rows: {total_rows:,}')

writer = None
chunks_written = 0
rows_written   = 0
t_start = time.time()

for offset in range(0, total_rows, CHUNK_SIZE):
    t0  = time.time()
    df  = pd.read_sql(
        f'SELECT * FROM connecting_itineraries LIMIT {CHUNK_SIZE} OFFSET {offset}',
        conn,
    )

    # Enforce sensible dtypes so Parquet schema is clean
    int_cols  = ['rp_year', 'rp_month', 'sch_fl_yr', 'sch_fl_mo',
                 'origin_city_mkt_id', 'dest_city_mkt_id', 'long_connect']
    real_cols = ['num_pax', 'allocated_amt', 'allocated_tax', 'dwell_time',
                 'routed_miles', 'gc_miles', 'circuity_abs', 'circuity_ratio',
                 'od_min_circuity_abs', 'od_min_circuity_ratio']
    for c in int_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce').astype('Int64')
    for c in real_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce').astype('float64')

    table = pa.Table.from_pandas(df, preserve_index=False)

    if writer is None:
        writer = pq.ParquetWriter(OUT_PATH, table.schema, compression='snappy')

    writer.write_table(table)
    rows_written   += len(df)
    chunks_written += 1
    elapsed = time.time() - t_start
    print(f'  Chunk {chunks_written:>3}: rows {offset:>10,} – {offset + len(df):>10,}'
          f'  ({time.time()-t0:.1f}s)  total elapsed: {elapsed:.0f}s')

if writer:
    writer.close()

conn.close()

size_mb = os.path.getsize(OUT_PATH) / 1024 / 1024
print(f'\nDone. {rows_written:,} rows written.')
print(f'Output size: {size_mb:.1f} MB  ({OUT_PATH})')
