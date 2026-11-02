from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""Match two files: 10,000 x 10,000 rows within the time budget, and still exactly right."""
import sys
import time

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import reconcile_fixtures as fx
from reconcile import columns as rc, match as rm, prepare as rp
from reconcile_helpers import prepared

BUDGET_SECONDS = 15
# (tests/run_all.py runs the *performance* scripts one at a time, so the budget is measured on a quiet machine.)

print("TEST: 10,000 x 10,000 rows - every row in the right bucket, within %d s (reading, detecting, preparing and matching)" % BUDGET_SECONDS)
bank, ledger, truth = fx.big_pair(10600)
started = time.perf_counter()
_, _, A, _ = prepared(bank)
_, _, B, _ = prepared(ledger)
prepared_in = time.perf_counter() - started
result = rm.reconcile(A, B)
total = time.perf_counter() - started
print(f"   {len(bank):,} x {len(ledger):,} rows: prepare {prepared_in:.1f} s + match {result.seconds:.1f} s = {total:.1f} s (budget {BUDGET_SECONDS} s)")
print("   exact %d, close %d, maybe %d, mismatches %d, only A %d, only B %d" % (
    len(result.bucket("exact")), len(result.bucket("close")), len(result.bucket("maybe")), len(result.mismatches), len(result.only_a), len(result.only_b)))
got = {}
for pair in result.pairs + result.mismatches:
    got[("a", pair.a)] = (pair.bucket, pair.b)
    got[("b", pair.b)] = (pair.bucket, pair.a)
for a in result.only_a:
    got[("a", a)] = ("only", None)
for b in result.only_b:
    got[("b", b)] = ("only", None)
wrong = [(side, row, expected, got.get((side, row))) for side, table in (("a", truth.a), ("b", truth.b)) for row, expected in table.items() if got.get((side, row)) != expected]
# With 10,000 random amounts, a few unmatched rows have a coincidental look-alike (amount within 0.5%, date within 3 days). They may surface as a
# "maybe" - that is what a maybe is for - but never as an exact or close match, and every planted pair must be found.
coincidences = [w for w in wrong if w[2][0] == "only" and w[3] is not None and w[3][0] == "maybe"]
print(f"   rows not as planted: {len(wrong)} of {len(truth.a) + len(truth.b)}; of those, unmatched rows that coincidentally look like a maybe: {len(coincidences)}")
assert len(wrong) == len(coincidences), [w for w in wrong if w not in coincidences][:5]
planted = {(side, row): e for side, table in (("a", truth.a), ("b", truth.b)) for row, e in table.items() if e[0] != "only"}
assert all(got.get(k) == v for k, v in planted.items() if v[0] in ("exact", "close")), "an exact or close pair was missed or mis-paired"
assert total <= BUDGET_SECONDS, f"{total:.1f} s is over the {BUDGET_SECONDS} s budget"
print("PASSED")

print("TEST: the worst case - 10,000 x 10,000 rows with only 25 different amounts (every row has many look-alikes) - same budget")
import random
from datetime import date, timedelta

import pandas as pd

rng = random.Random(3)
amounts = [round(5 + 4.75 * k, 2) for k in range(25)]
rows_a = [{"Date": (date(2026, 1, 1) + timedelta(days=rng.randint(0, 120))).isoformat(), "Description": rng.choice(fx.MERCHANTS), "Debit": amounts[rng.randrange(25)], "Credit": None}
          for _ in range(10000)]
rows_b = [{"Date": (date.fromisoformat(r["Date"]) + timedelta(days=rng.choice([0, 0, 1, -1, 5]))).strftime("%d/%m/%Y"), "Memo": r["Description"], "Amount": -r["Debit"]}
          for r in rows_a[:9000]] + [{"Date": "01/06/2026", "Memo": "Extra", "Amount": -amounts[rng.randrange(25)]} for _ in range(1000)]
started = time.perf_counter()
_, _, A, _ = prepared(pd.DataFrame(rows_a))
_, _, B, _ = prepared(pd.DataFrame(rows_b))
worst = rm.reconcile(A, B)
elapsed = time.perf_counter() - started
print(f"   {A.n:,} x {B.n:,}: {elapsed:.1f} s; exact {len(worst.bucket('exact'))}, close {len(worst.bucket('close'))}, maybe {len(worst.bucket('maybe'))}, only A {len(worst.only_a)}, only B {len(worst.only_b)}")
assert worst.sign == "same" and elapsed <= BUDGET_SECONDS
used = [p.a for p in worst.pairs] + [p.b for p in worst.pairs]
assert len(set(p.a for p in worst.pairs)) == len(worst.pairs) and len(set(p.b for p in worst.pairs)) == len(worst.pairs)
print("PASSED")

print("TEST: end to end with real files - two .xlsx files of 10,000+ rows are read, detected, matched, reported and written to the result workbook within the budget")
import io

from budget import importer
from reconcile import output as ro
from reconcile.pipeline import Settings, analyze, load_table

blobs = []
for frame in (bank, ledger):
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False)
    blobs.append(buffer.getvalue())
started = time.perf_counter()
tables = []
for blob in blobs:
    raw = next(iter(importer.read_raw_sheets(blob, False).values()))
    tables.append(load_table(raw))
(table_a, header_a), (table_b, header_b) = tables
read_in = time.perf_counter() - started
analysis = analyze("bank.xlsx", table_a, header_a, "ledger.xlsx", table_b, header_b, Settings())
analysed_in = time.perf_counter() - started
workbook = ro.build_workbook("bank.xlsx", "ledger.xlsx", table_a, table_b, analysis.A, analysis.B, analysis.map_a, analysis.map_b, analysis.result, analysis.report)
total = time.perf_counter() - started
print(f"   {len(table_a):,} x {len(table_b):,} rows: read {read_in:.1f} s, + match and report {analysed_in - read_in:.1f} s, + Excel result {total - analysed_in:.1f} s = {total:.1f} s "
      f"({len(workbook) / 1e6:.1f} MB); budget {BUDGET_SECONDS} s")
assert len(table_a) >= 10000 and len(table_b) >= 10000 and total <= BUDGET_SECONDS
print("PASSED")
