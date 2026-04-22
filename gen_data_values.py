"""
gen_data_values.py
Queries connecting_itineraries.db for the distinct airport codes and carriers
that actually appear in the dataset, then writes data_values.json.
This file is bundled with the web tool so the browser never has to scan the
remote Parquet file at startup to populate the typeahead lists.
"""

import json
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, 'connecting_itineraries.db')
OUT_PATH = os.path.join(BASE_DIR, 'data_values.json')

conn = sqlite3.connect(DB_PATH)

origins = {r[0] for r in conn.execute('SELECT DISTINCT origin FROM connecting_itineraries')}
dests   = {r[0] for r in conn.execute('SELECT DISTINCT dest   FROM connecting_itineraries')}
airports = sorted(origins | dests)

carriers = [r[0] for r in conn.execute(
    'SELECT DISTINCT mkt_carrier FROM connecting_itineraries ORDER BY 1'
)]

conn.close()

out = {'airports': airports, 'carriers': carriers}
with open(OUT_PATH, 'w', encoding='utf-8') as f:
    json.dump(out, f)

print(f'airports : {len(airports)}')
print(f'carriers : {len(carriers)}')
print(f'Written  : {OUT_PATH}')
