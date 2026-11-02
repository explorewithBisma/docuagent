from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""Match two files: detection and matching against a bank statement / ledger with a ground-truth table."""
import sys
from collections import Counter

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import pandas as pd

import reconcile_fixtures as fx
from reconcile import columns as rc, match as rm, prepare as rp
from reconcile_helpers import prepared, to_raw


bank, ledger, truth = fx.bank_vs_ledger()
print(f"synthetic files: bank {len(bank)} rows, ledger {len(ledger)} rows; planted: {truth.kinds}")

print("TEST: the columns of both files are found from headers and values, with the reason")
table_a, map_a, A, header_a = prepared(bank, title_rows=3)
table_b, map_b, B, header_b = prepared(ledger)
assert header_a == 3 and header_b == 0 and len(table_a) == len(bank)
got_a = {role: map_a.column(role) for role in rc.ROLES}
got_b = {role: map_b.column(role) for role in rc.ROLES}
print("  bank:  ", got_a)
print("  ledger:", got_b)
assert got_a == {"date": "Date", "amount": None, "debit": "Debit", "credit": "Credit", "reference": "Reference", "description": "Description"}
assert got_b == {"date": "Date", "amount": "Amount", "debit": None, "credit": None, "reference": "Doc No", "description": "Memo"}
assert map_a.findings["date"].confidence == "high" and "values are dates" in map_a.findings["date"].source
assert map_a.money_mode == "debit_credit" and map_b.money_mode == "signed"
assert map_a.day_first is True or map_a.day_first_certain is False
assert A.row_numbers[0] == 5 and B.row_numbers[0] == 2                       # the row each record has in the user's file
assert "money out, shown as negative" in A.signs
print("PASSED")

print("TEST: every row lands in the right bucket with the right partner (ground truth)")
result = rm.reconcile(A, B)
print("   sign:", result.sign, "|", result.sign_text)
print("   exact %d, close %d, maybe %d, mismatches %d, only A %d, only B %d  (%.2f s)" % (
    len(result.bucket("exact")), len(result.bucket("close")), len(result.bucket("maybe")), len(result.mismatches), len(result.only_a), len(result.only_b), result.seconds))
assert result.sign == "same"
got = {}
for pair in result.pairs + result.mismatches:
    got[("a", pair.a)] = (pair.bucket, pair.b)
    got[("b", pair.b)] = (pair.bucket, pair.a)
for a in result.only_a:
    got[("a", a)] = ("only", None)
for b in result.only_b:
    got[("b", b)] = ("only", None)
wrong = []
for side, table in (("a", truth.a), ("b", truth.b)):
    for row, expected in table.items():
        if got.get((side, row)) != expected:
            wrong.append((side, row, expected, got.get((side, row))))
print("   wrong rows:", len(wrong), wrong[:5])
assert not wrong
assert len(got) == len(bank) + len(ledger) and set(got) == {("a", r) for r in range(len(bank))} | {("b", r) for r in range(len(ledger))}   # every row somewhere
used_a = Counter(p.a for p in result.pairs + result.mismatches)
used_b = Counter(p.b for p in result.pairs + result.mismatches)
assert max(used_a.values()) == 1 and max(used_b.values()) == 1                  # one-to-one: no row matched twice
print("PASSED")

print("TEST: reasons are in words")
reasons = {p.bucket: p.reason for p in result.pairs + result.mismatches}
print("  ", reasons)
assert reasons["exact"].startswith("same ") and "dates" in reasons["close"] and "differs by" in reasons["maybe"] and "but the amounts differ" in reasons["mismatch"]
print("PASSED")

print("TEST: a mismatch is never also a match, and a 'maybe' never appears for an exact amount")
assert not {p.a for p in result.mismatches} & {p.a for p in result.pairs}
assert all(abs(p.difference) > 0 for p in result.bucket("maybe"))
print("PASSED")
