"""
gen_airports_json.py
Filters airports.csv to US airports with valid IATA codes and writes airports.json
for the web tool's airport typeahead feature.
"""

import json
import os
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, 'Raw_Parquet_CSV_Inputs', 'airports.csv')
OUT_PATH = os.path.join(BASE_DIR, 'airports.json')

df = pd.read_csv(CSV_PATH, usecols=['iata_code', 'municipality', 'name', 'iso_country', 'type'])

df = df[
    (df['iso_country'] == 'US') &
    df['iata_code'].notna() &
    (df['iata_code'].str.strip() != '') &
    (df['type'].isin(['small_airport', 'medium_airport', 'large_airport']))
].copy()

df['iata_code']   = df['iata_code'].str.strip().str.upper()
df['municipality'] = df['municipality'].fillna('').str.strip()
df['name']        = df['name'].fillna('').str.strip()

df = df.drop_duplicates(subset='iata_code').sort_values('iata_code')

airports = df[['iata_code', 'municipality', 'name']].to_dict(orient='records')

with open(OUT_PATH, 'w', encoding='utf-8') as f:
    json.dump(airports, f, ensure_ascii=False)

print(f'Wrote {len(airports)} airports to {OUT_PATH}')
