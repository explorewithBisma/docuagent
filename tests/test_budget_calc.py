from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""The Dashboard formulas, recalculated without Excel (formulas package) and compared with plain pandas."""
import sys
from datetime import date

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import pandas as pd
from budget import categorize, importer
from budget.model import BudgetConfig
from budget.workbook import build_workbook
from budget_calc import SUMMARY_CELLS, excel_serial, expected_totals, recalc
from budget_fixtures import messy_budget_bytes

KM = categorize.default_keyword_map()
CATS = {"Food": 300, "Transport": 100, "Entertainment": 10, "Pets": 50, "Shopping": 0}


def tx(day, description, kind, category, amount, pay=None):
    return {"Date": day, "Description": description, "Type": kind, "Category": category, "Amount": amount, "Payment method": pay}


def close(a, b):
    return abs(float(a) - float(b)) < 0.005


def check_summary(values, expected, label):
    for key, cell in SUMMARY_CELLS.items():
        assert close(values["Dashboard!" + cell], expected[key]), (label, key, values["Dashboard!" + cell], expected[key])


def category_rows(values):
    rows = {}
    for row in range(18, 48):
        name = values.get(f"Dashboard!A{row}")
        if name:
            rows[name] = {"budget": values[f"Dashboard!B{row}"], "spent": values[f"Dashboard!C{row}"],
                          "remaining": values[f"Dashboard!D{row}"], "pct": values[f"Dashboard!E{row}"],
                          "status": values[f"Dashboard!F{row}"]}
    return rows


# ---------------------------------------------------------------------------------------------
print("TEST: imported messy workbook - every Dashboard number equals the pandas calculation")
raw = importer.read_raw_sheets(messy_budget_bytes())
name, layout = importer.best_sheet(raw)
imp = importer.import_transactions(raw[name], layout, KM, "PK", date(2026, 6, 1))
config = BudgetConfig(month_start=date(2026, 6, 1), opening_balance=1000, categories=dict(CATS))
values = recalc(build_workbook(config, KM, imp.rows, imp))
want = expected_totals(imp.rows, date(2026, 6, 1), 1000)
check_summary(values, want, "imported")
assert close(values["Dashboard!B6"], 1513.5) and close(values["Dashboard!B12"], 3386.5)
cats = category_rows(values)
for category in CATS:
    assert close(cats[category]["spent"], want["by_category"].get(category, 0)), category
assert close(cats["Food"]["spent"], 168) and close(cats["Transport"]["spent"], 1225)
assert close(values["Dashboard!C49"], 45), "the Mystery shop expense has no category"            # 'Expenses with no category'
print("   summary:", {k: values["Dashboard!" + c] for k, c in SUMMARY_CELLS.items()})
print("PASSED")

print("TEST: savings have their own card; savings reduce the closing balance; card bills are not in the net balance")
assert close(values["Dashboard!B9"], 300) and close(values["Dashboard!B13"], 600)
assert close(values["Dashboard!B10"], 5000 + 0 - 1513.5 - 800 - 300)
print("PASSED")

print("TEST: top 3 categories and daily totals")
assert [values[f"Dashboard!J{r}"] for r in (5, 6, 7)] == ["Transport", "Food", "Pets"]
assert [round(values[f"Dashboard!K{r}"], 2) for r in (5, 6, 7)] == [1225.0, 168.0, 60.0]
assert [values[f"Dashboard!I{r}"] for r in (5, 6, 7)] == ["#1", "#2", "#3"]
for day, amount in want["daily_expenses"].items():
    row = 12 + (day - date(2026, 6, 1)).days
    assert values[f"Dashboard!I{row}"] == excel_serial(day) and close(values[f"Dashboard!L{row}"], amount), day
for day, amount in want["daily_income"].items():
    assert close(values[f"Dashboard!K{12 + (day - date(2026, 6, 1)).days}"], amount)
assert close(values["Dashboard!M12"], values["Dashboard!K12"] - values["Dashboard!L12"])
assert values["Dashboard!I41"] == excel_serial(date(2026, 6, 30)) and values["Dashboard!I42"] in ("", None)   # June has 30 days
print("PASSED")

print("TEST: data checks on the imported file")
assert values["Dashboard!F5"] == 0 and values["Dashboard!F6"] == 0 and values["Dashboard!F7"] == 0
assert values["Dashboard!F8"] == 1 and close(values["Dashboard!F9"], 45)
print("PASSED")

# ---------------------------------------------------------------------------------------------
print("TEST: month boundaries, rows without a date or a Type, credit-card / loan / saving rules")
d = date
rows = pd.DataFrame([
    tx(d(2026, 5, 31), "last day of May", "Expense", "Food", 11),          # before the month
    tx(d(2026, 6, 1), "first day", "Expense", "Food", 20),
    tx(d(2026, 6, 30), "last day", "Expense", "Food", 30),
    tx(d(2026, 7, 1), "first day of July", "Expense", "Food", 99),         # after the month
    tx(d(2026, 6, 10), "Salary", "Income", None, 3000),
    tx(d(2026, 6, 11), "Borrowed", "Loan received", None, 500),
    tx(d(2026, 6, 12), "Car EMI", "Loan payment", None, 200),
    tx(d(2026, 6, 13), "To savings", "Saving", None, 400),
    tx(d(2026, 6, 14), "TV on the credit card", "Expense", "Shopping", 100, "Credit card"),
    tx(d(2026, 6, 28), "Pay the Visa bill", "Card bill payment", None, 100, "Bank transfer"),
    tx(None, "no date", "Expense", "Food", 7),                              # no date: ignored, counted in the checks
    tx(d(2026, 6, 15), "no type", None, "Food", 8),                         # no Type: ignored, counted in the checks
    tx(d(2026, 6, 16), "uncategorised", "Expense", None, 5),
])
config = BudgetConfig(month_start=date(2026, 6, 1), opening_balance=250, categories=dict(CATS))
values = recalc(build_workbook(config, KM, rows))
want = expected_totals(rows, date(2026, 6, 1), 250)
check_summary(values, want, "boundaries")
assert close(values["Dashboard!B6"], 20 + 30 + 100 + 5), "June 1st and June 30th count, May 31st and July 1st do not"
assert close(values["Dashboard!B13"], 100) and close(values["Dashboard!B9"], 400)
assert close(values["Dashboard!B10"], 3000 + 500 - 155 - 200 - 400), "the card bill (100) is not subtracted again"
assert close(values["Dashboard!B12"], 250 + 3000 + 500 - 155 - 200 - 400)
assert values["Dashboard!F5"] == 2 and values["Dashboard!F6"] == 1 and values["Dashboard!F7"] == 1 and values["Dashboard!F8"] == 1
assert close(values["Dashboard!F9"], 5)
print("PASSED")

print("TEST: over budget / on budget / no budget, percentages, and the over-budget status")
config = BudgetConfig(month_start=date(2026, 6, 1), categories={"Food": 100, "Transport": 100, "Shopping": 0, "Pets": 50})
rows = pd.DataFrame([tx(d(2026, 6, 2), "a", "Expense", "Food", 150), tx(d(2026, 6, 3), "b", "Expense", "Transport", 40),
                     tx(d(2026, 6, 4), "c", "Expense", "Shopping", 25), tx(d(2026, 6, 5), "d", "Expense", "Pets", 50)])
cats = category_rows(recalc(build_workbook(config, KM, rows)))
assert cats["Food"]["status"] == "OVER" and close(cats["Food"]["remaining"], -50) and close(cats["Food"]["pct"], 1.5)
assert cats["Transport"]["status"] == "OK" and close(cats["Transport"]["pct"], 0.4)
assert cats["Shopping"]["status"] == "No budget" and cats["Shopping"]["pct"] in ("", None)
assert cats["Pets"]["status"] == "OK" and close(cats["Pets"]["pct"], 1.0), "exactly on budget is not over"
print("PASSED")

print("TEST: top 3 with ties picks distinct categories in list order; fewer than 3 spenders leaves blanks")
config = BudgetConfig(month_start=date(2026, 6, 1), categories={"Food": 0, "Transport": 0, "Pets": 0, "Shopping": 0})
rows = pd.DataFrame([tx(d(2026, 6, 2), "a", "Expense", c, 50) for c in ("Food", "Transport", "Pets", "Shopping")])
values = recalc(build_workbook(config, KM, rows))
assert [values[f"Dashboard!J{r}"] for r in (5, 6, 7)] == ["Food", "Transport", "Pets"], [values[f"Dashboard!J{r}"] for r in (5, 6, 7)]
rows = pd.DataFrame([tx(d(2026, 6, 2), "a", "Expense", "Pets", 9)])
values = recalc(build_workbook(config, KM, rows))
assert values["Dashboard!J5"] == "Pets" and values["Dashboard!J6"] in ("", None) and values["Dashboard!K7"] in ("", None)
print("PASSED")

print("TEST: February in a leap year has 29 daily rows; the 29th is counted")
config = BudgetConfig(month_start=date(2028, 2, 1), categories={"Food": 100})
rows = pd.DataFrame([tx(d(2028, 2, 29), "leap day", "Expense", "Food", 12), tx(d(2028, 3, 1), "next month", "Expense", "Food", 99)])
values = recalc(build_workbook(config, KM, rows))
assert values["Dashboard!I40"] == excel_serial(date(2028, 2, 29)) and values["Dashboard!I41"] in ("", None)
assert close(values["Dashboard!L40"], 12) and close(values["Dashboard!B6"], 12) and values["Dashboard!A2"] == "Month: February 2028"
print("PASSED")

print("TEST: a blank start - everything is zero and the closing balance is the opening balance")
values = recalc(build_workbook(BudgetConfig(month_start=date(2026, 9, 1), opening_balance=1234.5), KM))
for key in ("income", "expenses", "loan_payments", "loans_received", "saved", "net", "card_bills"):
    assert close(values["Dashboard!" + SUMMARY_CELLS[key]], 0), key
assert close(values["Dashboard!B12"], 1234.5) and values["Dashboard!J5"] in ("", None)
assert values["Dashboard!F5"] == 0 and values["Dashboard!F8"] == 0
print("PASSED")


def helper(values, count):
    """(label, value) of the doughnut helper table: `count` ranked categories + the 'Other' row."""
    return [(values.get(f"Dashboard!AA{18 + i}"), values.get(f"Dashboard!AB{18 + i}")) for i in range(count + 1)]


print("TEST: doughnut helper - ranked by spending; a slice under the chosen share has no name (so no label) and joins 'Other'")
config = BudgetConfig(month_start=date(2026, 6, 1), categories={"Food": 0, "Transport": 0, "Pets": 0, "Shopping": 0})
rows = pd.DataFrame([tx(d(2026, 6, 2), "a", "Expense", "Pets", 2), tx(d(2026, 6, 3), "b", "Expense", "Food", 100),
                     tx(d(2026, 6, 4), "c", "Expense", "Transport", 50)])
values = recalc(build_workbook(config, KM, rows))
got = helper(values, 4)
# total 152: Food 65.8%, Transport 32.9%, Pets 1.3% (hidden); the hidden 2 would be 'Other' but 1.3% is also under 3%: no name either
assert got[0] == ("Food", 100) and got[1] == ("Transport", 50), got
assert got[2][0] in ("", None) and got[2][1] == 0 and got[3][0] in ("", None) and got[3][1] == 0, got
assert got[4][0] in ("", None) and close(got[4][1], 2), got
assert close(sum(v for _, v in got), 152), "nothing is lost: the slices add up to the total spending"
print("PASSED")

print("TEST: doughnut helper - several small slices together become a labelled 'Other (under 3%)' slice; ties keep list order")
config = BudgetConfig(month_start=date(2026, 6, 1), categories={"Food": 0, "A": 0, "B": 0, "C": 0, "D": 0, "E": 0})
rows = pd.DataFrame([tx(d(2026, 6, 2), "x", "Expense", "Food", 95)] + [tx(d(2026, 6, 3), "y", "Expense", c, 1) for c in "ABCDE"])
values = recalc(build_workbook(config, KM, rows))
got = helper(values, 6)
assert got[0] == ("Food", 95) and all(label in ("", None) and value == 0 for label, value in got[1:6]), got
assert got[6] == ("Other (under 3%)", 5), got
print("PASSED")

print("TEST: doughnut helper - the threshold is a setting; no spending gives an empty chart table")
import io as _io
import openpyxl
book = openpyxl.load_workbook(_io.BytesIO(build_workbook(BudgetConfig(month_start=date(2026, 6, 1), categories={"Food": 0, "Transport": 0}), KM,
                                                         pd.DataFrame([tx(d(2026, 6, 2), "a", "Expense", "Food", 70),
                                                                       tx(d(2026, 6, 3), "b", "Expense", "Transport", 30)]))))
assert book["Settings"]["B7"].value == 0.03 and book["Settings"]["A7"].value == "Doughnut chart: hide slices under"
book["Settings"]["B7"] = 0.5
buffer = _io.BytesIO()
book.save(buffer)
got = helper(recalc(buffer.getvalue()), 2)
assert got[0] == ("Food", 70) and got[1][0] in ("", None) and got[1][1] == 0 and got[2][0] in ("", None) and close(got[2][1], 30), got
empty = helper(recalc(build_workbook(BudgetConfig(month_start=date(2026, 9, 1), categories={"Food": 0, "Transport": 0}), KM)), 2)
assert all(label in ("", None) and value == 0 for label, value in empty), empty
print("PASSED")
