from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""Check every month: rules suggested from a clean sample file, per column, with the evidence."""
import sys

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

from datetime import date

import pandas as pd

import datarules_fixtures as fx
from datarules import model, suggest
from reconcile import columns as rc
from reconcile_helpers import to_raw

sample = fx.sales_month(2026, 4)
table, _ = rc.table_from_raw(to_raw(sample))
rules = suggest.suggest(table, "april.xlsx", "PK")
by = {(r.column, r.type): r for r in rules.rules}

print("TEST: the suggested rules for the sample month - exactly these, nothing more")
got = sorted((r.column, r.type) for r in rules.rules)
expected = sorted([("", "column_present")] + [(c, "required") for c in ("Order ID", "Order Date", "Customer Email", "Phone", "Region", "Status", "Quantity", "Amount")] + [
    ("Order ID", "unique"), ("Order Date", "date_range"), ("Customer Email", "valid_email"), ("Phone", "valid_phone"), ("Region", "allowed_values"),
    ("Status", "allowed_values"), ("Quantity", "number_range"), ("Amount", "number_range")])
print("  ", len(got), "rules")
assert got == expected, set(got) ^ set(expected)
assert rules.name == "Checks for april.xlsx" and rules.source == "april.xlsx" and rules.region == "PK" and rules.columns == list(sample.columns)
assert all(r.why and r.enabled for r in rules.rules) and len({r.id for r in rules.rules}) == len(rules.rules)
print("PASSED")

print("TEST: the parameters - allowed values, number ranges with a margin, date span, expected columns")
assert by[("Region", "allowed_values")].params == {"values": ["East", "North", "South", "West"]}
assert by[("Status", "allowed_values")].params == {"values": ["Paid", "Pending", "Refunded"]}
assert by[("", "column_present")].params == {"columns": list(sample.columns)}
low, high = sample["Amount"].min(), sample["Amount"].max()
amount = by[("Amount", "number_range")].params
assert amount["min"] == 0 and amount["max"] == round(high + 0.1 * (high - low), 2) and "10% margin" in by[("Amount", "number_range")].why
assert by[("Quantity", "number_range")].params == {"min": 0, "max": 10}                                  # 1..9 plus a margin, whole numbers, not below zero
days = (max(sample["Order Date"]) - min(sample["Order Date"])).days
assert by[("Order Date", "date_range")].params == {"mode": "span", "max_span_days": -(-days * 125 // 100) + 3}
assert model.setting_text(by[("Order Date", "date_range")]) == "within 40 days" and "next month" in by[("Order Date", "date_range")].why
print("PASSED")

print("TEST: no rule that would make no sense - an id has no range, free optional text has no value list, a phone is not a number")
assert ("Order ID", "number_range") not in by and ("Notes", "allowed_values") not in by and ("Notes", "required") not in by and ("Phone", "number_range") not in by
assert ("Amount", "unique") not in by and ("Customer Email", "unique") not in by
print("PASSED")

print("TEST: other shapes - a fully blank column, a short sample, an id that is a plain number, a column with some blanks, mostly-wrong emails")
odd = pd.DataFrame({"Empty": [None] * 12, "Postcode": list(range(54000, 54012)), "Customer ID": list(range(1, 13)), "Score": [1.5, 2.5, None] * 4,
                    "Email": ["a@x.com", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l"], "Tag": ["x", "y"] * 6, "Joined": [date(2026, 1, d) for d in range(1, 13)]})
odd_rules = {(r.column, r.type) for r in suggest.suggest(odd).rules}
print("  ", sorted(odd_rules))
assert not any(c == "Empty" for c, _ in odd_rules)
assert ("Postcode", "number_range") not in odd_rules and ("Customer ID", "number_range") not in odd_rules and ("Customer ID", "unique") in odd_rules
assert ("Score", "required") not in odd_rules and ("Score", "number_range") in odd_rules
assert ("Email", "valid_email") not in odd_rules                                                           # 1 of 12 looks like an email
assert ("Tag", "allowed_values") in odd_rules and ("Joined", "date_range") in odd_rules
short = suggest.suggest(pd.DataFrame({"Status": ["a", "b", "a", "b", "a"]}))
assert ("Status", "allowed_values") not in {(r.column, r.type) for r in short.rules}                       # 5 rows: too few to call the values a list
print("PASSED")

print("TEST: the suggestion reads CSV text too (everything arrives as text) and Excel dates")
text = sample.astype(str).replace({"None": None, "nan": None})
csv_table, _ = rc.table_from_raw(to_raw(text))
csv_rules = {(r.column, r.type) for r in suggest.suggest(csv_table, "april.csv").rules}
assert csv_rules == {(c, t) for c, t in expected}, csv_rules ^ set(expected)
print("PASSED")
