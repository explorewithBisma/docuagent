from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""Next month: read a workbook this app generated, carry the closing balance and settings over."""
import io
import sys
from datetime import date

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import openpyxl
import pandas as pd
from budget import carryover, categorize, importer
from budget.model import BudgetConfig
from budget.workbook import build_workbook
from budget_calc import expected_totals
from budget_fixtures import messy_budget_bytes

KM = categorize.default_keyword_map()
KM = categorize.add_keyword(KM, "Food", "shawarma")
CONFIG = BudgetConfig(currency="Rs", month_start=date(2026, 6, 1), opening_balance=1000,
                      categories={"Food": 300, "Transport": 100, "Pets": 50}, payment_methods=["Cash", "Card", "Credit card"])
raw = importer.read_raw_sheets(messy_budget_bytes())
name, layout = importer.best_sheet(raw)
IMPORT = importer.import_transactions(raw[name], layout, KM, "PK", date(2026, 6, 1))
GENERATED = build_workbook(CONFIG, KM, IMPORT.rows, IMPORT)

print("TEST: a generated workbook is recognised; other files are not")
assert carryover.is_generated_workbook(GENERATED) and carryover.is_generated_workbook(build_workbook(CONFIG, KM))
assert not carryover.is_generated_workbook(messy_budget_bytes())
assert not carryover.is_generated_workbook(b"not an excel file")
book = openpyxl.load_workbook(io.BytesIO(GENERATED))
del book["Dashboard"]
buffer = io.BytesIO()
book.save(buffer)
assert not carryover.is_generated_workbook(buffer.getvalue()), "a missing sheet means it is not ours"
book = openpyxl.load_workbook(io.BytesIO(GENERATED))
book["Settings"]["A1"] = "something else"
buffer = io.BytesIO()
book.save(buffer)
assert not carryover.is_generated_workbook(buffer.getvalue()), "the marker must match"
print("PASSED")

print("TEST: settings, keyword map and transactions are read back exactly")
generated = carryover.read_generated_workbook(GENERATED)
assert generated.config.currency == "Rs" and generated.config.month_start == date(2026, 6, 1)
assert generated.config.opening_balance == 1000
assert generated.config.categories == CONFIG.categories and generated.config.payment_methods == CONFIG.payment_methods
assert generated.keyword_map == KM and "shawarma" in generated.keyword_map["categories"]["Food"]
assert len(generated.transactions) == 12 and generated.transactions["Date"].iloc[0] == date(2026, 6, 1)
assert generated.transactions["Amount"].sum() == IMPORT.rows["Amount"].sum()
print("PASSED")

print("TEST: the closing balance equals the pandas calculation (savings reduce it; the card bill does not)")
want = expected_totals(IMPORT.rows, date(2026, 6, 1), 1000)
assert abs(generated.closing_balance - want["closing"]) < 0.005 and abs(generated.closing_balance - 3386.5) < 0.005
print("PASSED")

print("TEST: next month - the closing balance is the new opening balance; settings carry over; Transactions start empty")
following = carryover.next_month_config(generated)
assert following.month_start == date(2026, 7, 1) and following.opening_balance == 3386.5
assert following.categories == CONFIG.categories and following.payment_methods == CONFIG.payment_methods and following.currency == "Rs"
assert following.categories is not generated.config.categories
new_file = build_workbook(following, generated.keyword_map)
again = carryover.read_generated_workbook(new_file)
assert len(again.transactions) == 0 and again.keyword_map == KM and again.closing_balance == 3386.5
assert again.config.month_start == date(2026, 7, 1)
print("PASSED")

print("TEST: December rolls over to January of the next year; an edited opening balance is respected")
december = build_workbook(BudgetConfig(month_start=date(2026, 12, 1), opening_balance=10, categories={"Food": 5}), KM)
assert carryover.next_month_config(carryover.read_generated_workbook(december)).month_start == date(2027, 1, 1)
book = openpyxl.load_workbook(io.BytesIO(GENERATED))
book["Settings"]["B5"] = 0
book["Transactions"]["E2"] = 125                      # the user changed the first row's amount from 25 to 125
buffer = io.BytesIO()
book.save(buffer)
edited = carryover.read_generated_workbook(buffer.getvalue())
assert abs(edited.closing_balance - (3386.5 - 1000 - 100)) < 0.005
print("PASSED")

print("TEST: rows outside the month, without a date or a Type, do not move the balance (as on the Dashboard)")
rows = pd.DataFrame([
    {"Date": date(2026, 5, 31), "Description": "x", "Type": "Expense", "Category": "Food", "Amount": 99.0, "Payment method": None},
    {"Date": None, "Description": "y", "Type": "Expense", "Category": "Food", "Amount": 98.0, "Payment method": None},
    {"Date": date(2026, 6, 5), "Description": "z", "Type": None, "Category": "Food", "Amount": 97.0, "Payment method": None},
    {"Date": date(2026, 6, 6), "Description": "w", "Type": "Income", "Category": None, "Amount": 40.0, "Payment method": None}])
read = carryover.read_generated_workbook(build_workbook(CONFIG, KM, rows))
assert abs(read.closing_balance - 1040) < 0.005
print("PASSED")

print("TEST: a broken or missing Keywords sheet falls back to the defaults, with a note")
book = openpyxl.load_workbook(io.BytesIO(GENERATED))
del book["Keywords"]
buffer = io.BytesIO()
book.save(buffer)
fallback = carryover.read_generated_workbook(buffer.getvalue())
assert fallback.keyword_map == categorize.default_keyword_map() and any("Keywords sheet is missing" in n for n in fallback.notes)
book = openpyxl.load_workbook(io.BytesIO(GENERATED))
book["Keywords"]["A3"], book["Keywords"]["B3"] = "Type", "Nonsense"
buffer = io.BytesIO()
book.save(buffer)
assert any("problem" in n for n in carryover.read_generated_workbook(buffer.getvalue()).notes)
print("PASSED")

print("TEST: reading a file that is not ours raises a readable error")
try:
    carryover.read_generated_workbook(messy_budget_bytes())
    raise AssertionError("should fail")
except ValueError as error:
    assert "isn't a workbook made by" in str(error)
print("PASSED")
