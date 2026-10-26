from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""budget/autopilot.py on a synthetic template-style workbook (numbers worked out by hand in the comments)."""
import io
import sys
from datetime import date

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import openpyxl
import pandas as pd
from budget import autopilot, categorize
from budget_fixtures import messy_budget_bytes, personal_style_bytes

DATA = personal_style_bytes()
A = autopilot.analyze(DATA)

print("TEST: everything is detected from the file alone, each with confidence and source")
assert (A.currency.value, A.currency.confidence) == ("AED", "high") and A.locale == "AE"
assert (A.month.value, A.month.confidence) == (date(2026, 6, 1), "high")
assert (A.opening.value, A.opening.confidence) == (11236, "high")
assert A.main_sheet == "📝 Daily Log" and A.income.value == 7225 and A.payments.value == 1000.5
assert A.budgets.value == {"Housing": 2000, "Food": 1000, "Transport": 0, "Health": 500}
print("PASSED")

print("TEST: rows - 8 expenses, income and the loan payments added from their sheets, the TOTAL line dropped")
types = A.rows["Type"].value_counts().to_dict()
assert types == {"Expense": 8, "Income": 1, "Loan payment": 2}, types
assert list(A.dropped["Description"]) == ["TOTAL"] and A.rows["Amount"][A.rows["Type"] == "Expense"].sum() == 2105.5
salary = A.rows[A.rows["Type"] == "Income"].iloc[0]
assert salary["Date"] == date(2026, 6, 1) and "date not given" in salary["Flags"]
dates = [d for d in A.rows["Date"]]
assert dates == sorted(dates), "chronological"
print("PASSED")

print("TEST: categories - own categories merged ('Food/Grocery' -> Food), keywords, typo match, the rest unmatched")
by_description = A.rows[A.rows["Type"] == "Expense"].set_index("Description")
assert A.merges == {"Food/Grocery": "Food"}
assert A.rows.loc[A.rows["Description"] == "Grocery", "Category"].iloc[0] == "Food"
assert by_description.loc["Taxi", "Category"] == "Transport" and by_description.loc["Room rent", "Category"] == "Bills"
resturant = A.rows[A.rows["Description"] == "Resturant"]
assert list(resturant["Category"]) == ["Food", "Food"]
assert resturant["Flags"].str.contains("category guessed: looks like 'restaurant'").sum() == 1, "only the typo-matched row says so"
assert sorted(A.unmatched["Description"]) == ["Aster", "malabar"] and A.unmatched["Total"].sum() == 63.5
assert any("Merged your categories into ours: Food/Grocery → Food" in n for n in A.notes)
print("PASSED")

print("TEST: settings - budgets matched by name (emojis ignored), new categories added, currency/month/opening/payment methods")
cfg = A.config
assert cfg.currency == "AED" and cfg.month_start == date(2026, 6, 1) and cfg.opening_balance == 11236
assert cfg.categories["Food"] == 1000 and cfg.categories["Health"] == 500 and cfg.categories["Housing"] == 2000
assert cfg.categories["Transport"] == 0 and "Bills" in cfg.categories and "🏠" not in "".join(cfg.categories)
assert any("Added the category 'Housing'" in n for n in A.notes)
assert "Debit Card" in cfg.payment_methods and "Cash" in cfg.payment_methods
print("PASSED")

print("TEST: sanity check against the file's own estimated closing balance (information only)")
# 11236 + 7225 - 2105.5 - 1000.5 = 15355
assert abs(A.closing_balance - 15355) < 0.005
theirs, ours, difference = A.closing_difference
assert theirs == 1463.29 and abs(ours - 15355) < 0.005 and abs(difference - (15355 - 1463.29)) < 0.005
assert autopilot.analyze(messy_budget_bytes()).closing_difference is None
print("PASSED")

print("TEST: the Please-check list - payments first (with the one-click switch), then biggest uncertain categories")
kinds = [c.kind for c in A.checks]
assert kinds[0] == "payments" and A.checks[0].action == "switch_payments_to_card"
assert "Loan payment" in A.checks[0].text and "Card bill payment" in A.checks[0].text and "1,000.50" in A.checks[0].text
categories = [c for c in A.checks if c.kind == "category"]
assert [c.amount for c in categories] == sorted([c.amount for c in categories], reverse=True)
assert categories[0].text.startswith("'Aster' has no category") and any("looks like 'restaurant'" in c.text for c in categories)
assert any(c.kind == "unusual" and "Room rent" in c.text for c in A.checks)
assert any(c.kind == "budget" and "Transport" not in c.text and "disagree" in c.text for c in A.checks)
assert not any(c.kind in ("opening", "currency", "month") for c in A.checks), "confident findings are not listed"
print("PASSED")

print("TEST: summary lines - currency, month, opening balance, income, expenses, payments, categories, with sources")
lines = {label: (value, source, confidence) for label, value, source, confidence in autopilot.summary_lines(A)}
assert list(lines) == ["Currency", "Month", "Opening balance", "Income", "Expenses", "Loan / card payments", "Categories"]
assert lines["Currency"][0] == "AED" and "Amount (AED)" in lines["Currency"][1]
assert lines["Opening balance"][0] == "AED 11,236.00" and lines["Income"][0].startswith("AED 7,225.00")
assert lines["Expenses"][0] == "8 rows, AED 2,105.50" and lines["Loan / card payments"][0].endswith("(recorded as Loan payment)")
print("PASSED")

print("TEST: AI answers are applied only to uncategorised expenses, flagged 'AI suggested', and move out of the unmatched list")
snapshot = A.rows.copy(deep=True)
B = autopilot.apply_ai(A, {"malabar": ("Food", "medium"), "ASTER": ("Health", "high"), "Taxi": ("Bills", "high")})
assert A.rows.equals(snapshot), "the original analysis is not modified"
assert B.ai_assigned == 2 and B.unmatched.empty
ai_rows = B.rows[B.rows["Flags"].str.contains("AI suggested")]
assert sorted(ai_rows["Description"]) == ["Aster", "malabar"] and set(ai_rows["Flags"].str.extract(r"AI suggested \((\w+)\)")[0]) == {"medium", "high"}
assert B.rows.loc[B.rows["Description"] == "Taxi", "Category"].iloc[0] == "Transport", "an existing category is never overwritten"
assert not ai_rows["Flags"].str.contains("no category").any()
assert any("AI suggested (medium)" in c.text for c in B.checks if c.kind == "category")
print("PASSED")

print("TEST: switching the loan-card payments to Card bill payment - closing balance follows, the choice stays visible")
C = autopilot.switch_payments(A, "Card bill payment")
paid = C.rows[C.rows["Flags"].str.contains("payment log")]
assert set(paid["Type"]) == {"Card bill payment"} and all("switched to Card bill payment" in f for f in paid["Flags"])
assert abs(C.closing_balance - (15355 + 1000.5)) < 0.005
assert C.checks[0].kind == "payments" and C.checks[0].action == "switch_payments_to_loan" and "are recorded as Card bill payment" in C.checks[0].text
D = autopilot.switch_payments(C, "Loan payment")
assert abs(D.closing_balance - 15355) < 0.005 and D.checks[0].action == "switch_payments_to_card"
print("PASSED")

print("TEST: rows from other sheets that the log already has are not added twice")
book = openpyxl.load_workbook(io.BytesIO(DATA))
log = book["📝 Daily Log"]
log.insert_rows(4)
for column, value in enumerate([None, pd.Timestamp("2026-06-01").to_pydatetime(), "Salary June", None, 7225, "Bank"], start=1):
    log.cell(row=4, column=column, value=value)
buffer = io.BytesIO()
book.save(buffer)
E = autopilot.analyze(buffer.getvalue())
assert E.rows["Type"].value_counts()["Income"] == 1 and any("already in the log" in n for n in E.notes)
print("PASSED")

print("TEST: a file with no income list or other sheets still works (plain messy log)")
P = autopilot.analyze(messy_budget_bytes())
assert P.currency.confidence == "low" and P.month.value == date(2026, 6, 1) and P.opening.confidence == "low" and P.opening.value == 0
assert any(c.kind == "opening" for c in P.checks) and any(c.kind == "currency" for c in P.checks)
assert not any(c.kind == "payments" for c in P.checks)
print("PASSED")

print("TEST: a workbook with no transaction log gives a readable error")
empty = io.BytesIO()
pd.DataFrame({"a": [1]}).to_excel(empty, index=False)
try:
    autopilot.analyze(empty.getvalue())
    raise AssertionError("should fail")
except ValueError as error:
    assert "No transaction log was found" in str(error)
print("PASSED")
