from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""Match two files: other header names, sign conventions, missing columns, ambiguous dates, repeated references, symmetry."""
import sys
from datetime import date, datetime

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import pandas as pd

import reconcile_fixtures as fx
from reconcile import columns as rc, match as rm
from reconcile.pipeline import Settings, analyze
from reconcile_helpers import prepared, to_raw

bank, ledger, truth = fx.bank_vs_ledger()


def outcome(result):
    got = {}
    for pair in result.pairs + result.mismatches:
        got[("a", pair.a)] = (pair.bucket, pair.b)
        got[("b", pair.b)] = (pair.bucket, pair.a)
    for a in result.only_a:
        got[("a", a)] = ("only", None)
    for b in result.only_b:
        got[("b", b)] = ("only", None)
    return got


def expected(truth):
    return {**{("a", r): e for r, e in truth.a.items()}, **{("b", r): e for r, e in truth.b.items()}}


def run(a, b, **kw):
    _, map_a, A, _ = prepared(a)
    _, map_b, B, _ = prepared(b)
    return rm.reconcile(A, B, rm.Options(**kw)), map_a, map_b


print("TEST: the ledger writes money out as POSITIVE numbers (the bank uses a debit column): signs are flipped automatically, every row still right")
flipped = ledger.assign(Amount=-ledger["Amount"])
result, *_ = run(bank, flipped)
print("  ", result.sign, "|", result.sign_text)
assert result.sign == "opposite" and outcome(result) == expected(truth)
print("PASSED")

print("TEST: one file has no minus signs at all (money in and out both positive): amounts are compared by size")
unsigned = ledger.assign(Amount=ledger["Amount"].abs())
result, *_ = run(bank, unsigned)
print("  ", result.sign, "|", result.sign_text)
assert result.sign == "absolute" and outcome(result) == expected(truth)
print("PASSED")

print("TEST: the user can force the sign reading")
result, *_ = run(bank, flipped, sign="same")
assert result.sign == "same" and len(result.pairs) < 60 and "set by you" in result.sign_text
print("PASSED")

print("TEST: other header names - posting/value date, narration, cheque no, withdrawals/deposits, closing balance; and 'Amount (AED)', 'Voucher #'")
other_bank = pd.DataFrame({"Posting Date": ["03/01/2026", "15/01/2026", "20/01/2026"], "Value Date": ["04/01/2026", "16/01/2026", "21/01/2026"],
                           "Narration": ["POS COFFEE SHOP", "SALARY JAN", "RENT"], "Chq/Ref No": ["CHQ1001", "CHQ1002", "CHQ1003"],
                           "Withdrawals": [12.5, None, 3000.0], "Deposits": [None, 9000.0, None], "Closing Balance": [500.0, 9500.0, 6500.0]})
other_ledger = pd.DataFrame({"Trans Date": [date(2026, 1, 3), date(2026, 1, 15), date(2026, 1, 20)], "Details": ["Coffee shop", "Salary", "Rent"],
                             "Amount (AED)": [-12.5, 9000.0, -3000.0], "Voucher #": ["CHQ1001", "CHQ1002", "CHQ1003"], "Running": [1, 2, 3]})
_, ma, A, _ = prepared(other_bank)
_, mb, B, _ = prepared(other_ledger)
print("  ", {r: ma.column(r) for r in rc.ROLES}, {r: mb.column(r) for r in rc.ROLES})
assert {r: ma.column(r) for r in rc.ROLES} == {"date": "Posting Date", "amount": None, "debit": "Withdrawals", "credit": "Deposits", "reference": "Chq/Ref No", "description": "Narration"}
assert {r: mb.column(r) for r in rc.ROLES} == {"date": "Trans Date", "amount": "Amount (AED)", "debit": None, "credit": None, "reference": "Voucher #", "description": "Details"}
assert ma.day_first is True and ma.day_first_certain                         # 15/01/2026 can only be day first
result = rm.reconcile(A, B)
assert len(result.bucket("exact")) == 3 and not result.only_a and not result.only_b
print("PASSED")

print("TEST: a lone 'Debit' column is money OUT (read as negative), a lone 'Credit' column money in")
lone = pd.DataFrame({"Date": ["2026-01-03", "2026-01-04"], "Narration": ["a b c", "d e f"], "Debit": [10.0, 20.0]})
_, m, S, _ = prepared(lone)
assert m.money_mode == "debit_credit" and list(S.amounts) == [-10.0, -20.0]
print("PASSED")

print("TEST: no reference column in either file - exact by amount + date + description, close by date, no mismatches possible")
result, *_ = run(bank.drop(columns=["Reference"]), ledger.drop(columns=["Doc No"]))
buckets = {b: len(result.bucket(b)) for b in ("exact", "close", "maybe")}
print("  ", buckets, "mismatches", len(result.mismatches))
assert not result.mismatches and buckets["exact"] >= 170 and buckets["close"] >= 30
print("PASSED")

print("TEST: dates that can be read either way (03/04/2026) use the default order and say they are not certain; the user can switch")
a = pd.DataFrame({"Date": ["2026-04-03", "2026-05-06"], "Description": ["Shop one", "Shop two"], "Amount": [-10.0, -20.0]})
b = pd.DataFrame({"Date": ["03/04/2026", "06/05/2026"], "Description": ["Shop one", "Shop two"], "Amount": [-10.0, -20.0]})
_, map_b, B, _ = prepared(b)
assert map_b.day_first is True and map_b.day_first_certain is False
ta, ha = rc.table_from_raw(to_raw(a))
tb, hb = rc.table_from_raw(to_raw(b))
day = analyze("a", ta, ha, "b", tb, hb, Settings(day_first_b="day"))
month = analyze("a", ta, ha, "b", tb, hb, Settings(day_first_b="month"))
print("  ", day.summary, "|", month.summary)
assert len(day.result.bucket("exact")) == 2 and len(month.result.bucket("exact")) == 0
print("PASSED")

print("TEST: rows without an amount or date cannot be matched by them - they are listed as unmatched, with a note")
gaps = ledger.copy()
gaps.loc[0:2, "Amount"] = None
gaps.loc[3:5, "Date"] = None
result, ma, mb = run(bank, gaps)
_, _, B, _ = prepared(gaps)
assert len(B.notes) == 2 and any("no readable amount" in n for n in B.notes) and any("no readable date" in n for n in B.notes)
assert {0, 1, 2} <= set(result.only_b)
print("PASSED")

print("TEST: the same reference twice in one file - one pair per reference, the other row goes on to the next rounds")
a = pd.DataFrame({"Date": ["2026-01-01", "2026-01-01", "2026-02-02"], "Description": ["x one", "x two", "y"], "Ref": ["R1", "R1", "R2"], "Amount": [-5.0, -7.0, -9.0]})
b = pd.DataFrame({"Date": ["2026-01-01", "2026-02-02"], "Description": ["x one", "y"], "Ref": ["R1", "R2"], "Amount": [-5.0, -9.0]})
result, *_ = run(a, b)
assert {(p.a, p.b, p.bucket) for p in result.pairs} == {(0, 0, "exact"), (2, 1, "exact")} and result.only_a == [1]
print("PASSED")

print("TEST: a day window of 0 means dates must be equal; a bigger window finds more")
tight, *_ = run(bank, ledger, day_window=0)
wide, *_ = run(bank, ledger, day_window=5)
assert len(tight.bucket("close")) == 0 and len(wide.bucket("close")) >= 30
print("PASSED")

print("TEST: symmetric - swapping the two files gives the mirrored result")
forward, *_ = run(bank, ledger)
backward, *_ = run(ledger, bank)
assert {(p.a, p.b, p.bucket) for p in forward.pairs + forward.mismatches} == {(p.b, p.a, p.bucket) for p in backward.pairs + backward.mismatches}
assert forward.only_a == backward.only_b and forward.only_b == backward.only_a
print("PASSED")

print("TEST: nothing in common / empty-ish files do not crash")
empty = pd.DataFrame({"Date": ["2026-01-01"], "Description": ["x"], "Amount": [1.0]})
nothing, *_ = run(empty, ledger)
assert len(nothing.pairs) == 0 and nothing.only_a == [0]
print("PASSED")
