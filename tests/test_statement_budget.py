from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""Bank statement -> budget: the statement is recognised from its running balance, credits become Income, debits Expenses, card bills and
transfers between own accounts are not spending, and the closing balance is checked against the statement's last balance."""
import sys
from datetime import date

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import pandas as pd

import statement_fixtures as sf
from budget import autopilot, carryover, categorize
from budget.model import BudgetConfig
from budget.workbook import build_workbook
from budget_calc import recalc

KM = categorize.default_keyword_map()
SHOP = "Mazaya Trading LLC"


def analyse(shape, xlsx=False, **kw):
    return autopilot.analyze(sf.statement_bytes(shape, xlsx=xlsx), is_csv=not xlsx, **kw)


def check_budget(A, label):
    rows = A.rows
    assert len(rows) == 28, (label, len(rows))
    types = dict(zip(rows["Description"], rows["Type"]))
    for description, expected in sf.EXPECTED_TYPES.items():
        assert types[description] == expected, (label, description, types[description])
    assert float(rows.loc[rows["Type"] == "Income", "Amount"].sum()) == sf.INCOME_TOTAL                           # credits are Income
    assert (rows.loc[rows["Type"] == "Expense", "Amount"] > 0).all()
    cats = dict(zip(rows["Description"], rows["Category"]))
    for description, expected in sf.EXPECTED_CATEGORIES.items():
        assert cats[description] == expected, (label, description, cats[description])
    assert "category guessed" in rows.loc[rows["Description"] == "Resturant Al Mallah", "Flags"].iloc[0]               # the typo was matched, and says so
    assert pd.isna(cats[SHOP]) and SHOP in list(A.unmatched["Description"])                                       # nothing knows this shop
    assert A.month.value == date(2026, 5, 1) and A.month.confidence == "high"
    assert A.opening.value == sf.OPENING and A.opening.confidence == "high" and "balance column" in A.opening.source
    check = A.statement_check
    assert check["theirs"] == sf.CLOSING and abs(check["difference"]) < 0.005 and check["excluded_rows"] == 4, check
    assert abs(check["excluded"] - sf.NET_OF_CARD_BILL_ROWS) < 0.005
    assert abs(A.closing_balance - (sf.CLOSING - sf.NET_OF_CARD_BILL_ROWS)) < 0.005          # the Dashboard's own closing leaves the 4 moves out
    assert A.closing_difference[2] == check["difference"] and not any(c.kind in ("opening", "closing") for c in A.checks)


print("TEST: shape 1 - debit/credit columns, a balance column, title rows above the header (CSV)")
A = analyse("debit_credit")
print("  ", A.statement.describe())
assert A.statement is not None and A.statement.money_mode == "debit_credit" and A.statement.order == "oldest_first" and A.statement.chain_share == 1.0
assert A.main_sheet == "Sheet1" and A.layout["header_row"] == 3 and sorted(A.layout["columns"]) == ["credit", "date", "debit", "description"]
assert "Balance" not in A.layout["ignored"]                                              # the balance is not an amount
check_budget(A, "debit/credit")
print("PASSED")

print("TEST: the same statement as an Excel file, and newest transaction first")
check_budget(analyse("debit_credit", xlsx=True), "xlsx")
newest = analyse("newest_first")
assert newest.statement.order == "newest_first" and any("newest transaction first" in n for n in newest.notes)
check_budget(newest, "newest first")
assert list(newest.rows["Date"]) == sorted(newest.rows["Date"])                              # the budget is in date order
print("PASSED")

print("TEST: shape 2 - one signed amount column (negative = money out) with a running balance")
signed = analyse("signed")
assert signed.statement.money_mode == "signed" and signed.currency.value == "AED" and "one amount column" in signed.statement.describe()
check_budget(signed, "signed")
print("PASSED")

print("TEST: a file that writes money OUT as positive numbers - the balance proves it and the signs are turned around")
opposite = analyse("signed_positive_debits")
print("  ", opposite.statement.source(), "|", opposite.notes[0][:80])
assert opposite.statement.sign == "opposite" and "turned around" in opposite.statement.source() and any("opposite way" in n for n in opposite.notes)
check_budget(opposite, "positive debits")
print("PASSED")

print("TEST: the card shows the statement, sources and confidence; the Please-check list has the unclear transfer and no balance question")
lines = {label: (value, source, confidence) for label, value, source, confidence in autopilot.summary_lines(A)}
assert lines["Bank statement"][2] == "high" and "running balance" in lines["Bank statement"][0] and "adds up on 100%" in lines["Bank statement"][1]
assert lines["Opening balance"][2] == "high" and "5,000.00" in lines["Opening balance"][0]
kinds = [c.kind for c in A.checks]
print("  ", kinds)
transfer = next(c for c in A.checks if c.kind == "transfer")
assert "IBFT transfer Ali Khan" in transfer.text and transfer.amount == 300.0 and "Card bill payment" in transfer.text
assert "opening" not in kinds and "closing" not in kinds
print("PASSED")

print("TEST: card bills and own-account transfers are not spending - the workbook's Dashboard agrees with pandas")
config = BudgetConfig(currency="AED", month_start=A.month.value, opening_balance=float(A.opening.value), categories=dict(A.categories))
data = build_workbook(config, KM, A.rows, autopilot.import_info(A, A.rows))
values = recalc(data)
expenses = float(A.rows.loc[A.rows["Type"] == "Expense", "Amount"].sum())
print("   dashboard:", {k: values.get(f"Dashboard!{c}") for k, c in {"income": "B5", "expenses": "B6", "opening": "B11", "closing": "B12", "card bills": "B13"}.items()})
assert abs(values["Dashboard!B5"] - sf.INCOME_TOTAL) < 0.005 and abs(values["Dashboard!B6"] - expenses) < 0.005
assert abs(values["Dashboard!B13"] - 5300.0) < 0.005                                     # three card bills / transfers out (2500 + 800 + 1500) plus the 500 transferred in: all on their own line
assert abs(values["Dashboard!B11"] - sf.OPENING) < 0.005 and abs(values["Dashboard!B12"] - A.closing_balance) < 0.005
imported = carryover.read_generated_workbook(data)
assert abs(imported.closing_balance - A.closing_balance) < 0.005
print("PASSED")

print("TEST: closing balance check - a missing transaction shows up as a difference, with the amount")
frame = sf.statement_frame("debit_credit")
dropped_row = next(i for i, r in enumerate(frame) if r[1] == "Cafe breakfast")           # lose a 42.00 expense from the file
broken = pd.DataFrame(frame[:dropped_row] + frame[dropped_row + 1:]).to_csv(index=False, header=False).encode()
B = autopilot.analyze(broken, is_csv=True)
check = B.statement_check
print("  ", round(check["difference"], 2), "|", [c.text[:90] for c in B.checks if c.kind == "closing"])
assert B.statement is not None and B.statement.chain_share < 1.0                         # the balance chain shows the break
assert abs(check["difference"] - 42.00) < 0.005                                          # we are 42.00 higher than the bank: the missing expense
closing_item = next(c for c in B.checks if c.kind == "closing")
assert "difference 42.00" in closing_item.text and closing_item.amount == 42.00
assert any("difference 42.00" in n for n in autopilot.import_info(B, B.rows).notes)     # also in the workbook's Import Notes
print("PASSED")

print("TEST: a statement without a balance column still works through the normal flow (credits = Income, card bills not spending)")
plain = analyse("signed_no_balance")
assert plain.statement is None and plain.opening.confidence == "low"
types = plain.rows["Type"].value_counts().to_dict()
assert types == {"Expense": 20, "Card bill payment": 4, "Income": 3, "Loan payment": 1}, types
assert plain.closing_difference is None
print("PASSED")

print("TEST: an ordinary budget log is not mistaken for a statement")
from budget_fixtures import messy_budget_bytes
assert autopilot.analyze(messy_budget_bytes()).statement is None
print("PASSED")
