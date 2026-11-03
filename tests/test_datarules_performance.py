from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""Check every month: speed. (Its own script so that tests/run_all.py runs it alone, not beside five others.)"""
import sys
import time

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import pandas as pd

import datarules_fixtures as fx
from datarules import check as ck, suggest
from reconcile import columns as rc
from reconcile_helpers import to_raw

RULES = suggest.suggest(rc.table_from_raw(to_raw(fx.sales_month(2026, 4)))[0], "april.xlsx", "PK", name="Monthly sales")

print("TEST: speed - 50,000 rows against 17 rules")
big = pd.concat([fx.sales_month(2026, 5, 10000, seed, first_id=100000 * seed) for seed in range(1, 6)], ignore_index=True)
table, _ = rc.table_from_raw(to_raw(big))
started = time.perf_counter()
result = ck.check(table, RULES, "big.xlsx", "Sheet1")
elapsed = time.perf_counter() - started
print(f"   {len(big):,} rows: {elapsed:.1f} s, {result.status}, {result.counts()}")
assert elapsed < 20 and result.status == "PASS"                                                         # (clean data, ids made different on purpose)
print("PASSED")

