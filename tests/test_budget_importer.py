from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""budget/importer.py on a synthetic messy daily-expense workbook plus the layout variations people use."""
import sys
from datetime import date

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import pandas as pd
from budget import categorize, importer
from budget_fixtures import messy_budget_bytes

KM = categorize.default_keyword_map()
JUNE = date(2026, 6, 1)


def run(raw, **kwargs):
    layout = importer.detect_layout(raw)
    assert layout, "no layout found"
    return importer.import_transactions(raw, layout, KM, kwargs.get("locale", "PK"), kwargs.get("month", JUNE))


print("TEST: the messy workbook - the right sheet and header row are found, extra columns are listed as ignored")
raw_sheets = importer.read_raw_sheets(messy_budget_bytes())
name, layout = importer.best_sheet(raw_sheets)
assert name == "June" and layout["header_row"] == 2
assert layout["columns"] == {"date": 0, "description": 1, "amount": 2, "category": 3, "payment": 4}
assert layout["ignored"] == ["Daily Total"]
print("PASSED")

result = importer.import_transactions(raw_sheets[name], layout, KM, "PK", JUNE)
rows, dropped = result.rows, result.dropped

print("TEST: every source row is accounted for - kept, dropped, or blank")
assert result.stats["source_rows"] == len(raw_sheets[name]) - 3 == 17
assert len(rows) + len(dropped) + result.stats["blank_rows"] == 17 and (len(rows), len(dropped), result.stats["blank_rows"]) == (12, 4, 1)
print("PASSED")

print("TEST: dates - filled down (and flagged), serial numbers, day-first text; nothing left undated")
assert result.stats["fill_down"] == 8 and result.stats["no_date"] == 0
assert list(rows["Date"]) == [date(2026, 6, 1)] * 3 + [date(2026, 6, 2)] * 3 + [date(2026, 6, 3)] * 4 + [date(2026, 6, 13)] * 2
flagged = rows["Flags"].str.contains("date filled down")
assert flagged.sum() == 8 and not flagged.iloc[[0, 3, 6, 10]].any()          # rows that carried their own date
assert any("day-first" in note for note in result.notes)
print("PASSED")

print("TEST: dropped rows - labelled total, unlabelled hardcoded total, a note without amount, grand total; each says why")
by_row = dropped.set_index("Source row")
assert list(by_row.index) == [7, 11, 17, 20]
assert "total" in by_row.loc[7, "Reason"].lower() and "sum of the rows above" in by_row.loc[11, "Reason"]
assert by_row.loc[17, "Reason"].startswith("No amount") and by_row.loc[11, "Amount"] == 1245
assert 99999 not in rows["Amount"].values and 175 not in rows["Amount"].values
print("PASSED")

print("TEST: amounts - text with currency, decimals; signs removed")
assert rows.set_index("Description").loc["Metro card top-up", "Amount"] == 1200 and rows["Amount"].iloc[-1] == 15.5
assert (rows["Amount"] > 0).all()
print("PASSED")

print("TEST: types from keywords - salary, EMI, credit card bill, savings; the rest default to Expense (not flagged)")
types = dict(zip(rows["Description"], rows["Type"]))
assert types["Salary June"] == "Income" and types["Car EMI"] == "Loan payment"
assert types["Credit card bill - Visa"] == "Card bill payment" and types["Transfer to savings"] == "Saving"
assert types["Taxi to office"] == "Expense" and result.stats["defaulted_to_expense"] == 8
assert not rows["Flags"].str.contains("type").any()
print("PASSED")

print("TEST: categories - keywords, the file's own category (capitals fixed, unknown ones reported), unknowns listed")
cats = dict(zip(rows["Description"], rows["Category"]))
assert cats["Taxi to office"] == "Transport" and cats["Metro card top-up"] == "Transport" and cats["Netflix"] == "Entertainment"
assert cats["Grocery - Carrefour"] == "Food" and cats["Lunch"] == "Food"        # the file said 'food'
assert cats["Dog food"] == "Pets" and result.new_categories == ["Pets"]           # file's own category beats the 'food' keyword
assert cats["Mystery shop"] is None and result.stats["uncategorised"] == 1
assert cats["Salary June"] is None and cats["Car EMI"] is None                    # only expenses need a category
assert list(result.unknown["Description"]) == ["Mystery shop"] and result.unknown["Total"].iloc[0] == 45
assert "no category" in rows.set_index("Description").loc["Mystery shop", "Flags"]
print("PASSED")

print("TEST: assigning an unknown description / editing the map categorizes the rows and clears the flag")
assigned = importer.assign_category(rows, "mystery SHOP", "Shopping")
assert assigned.set_index("Description").loc["Mystery shop", "Category"] == "Shopping"
assert "no category" not in assigned.set_index("Description").loc["Mystery shop", "Flags"]
assert rows.set_index("Description").loc["Mystery shop", "Category"] is None, "the input frame is not modified"
better = importer.categorize_rows(rows, categorize.add_keyword(KM, "Shopping", "mystery"))
assert better.set_index("Description").loc["Mystery shop", "Category"] == "Shopping"
assert (better["Category"].fillna("") == rows["Category"].fillna("")).sum() == len(rows) - 1, "only the unknown row changes"
assert importer.unknown_descriptions(better).empty
print("PASSED")

print("TEST: month detection and the 'outside month' flag")
assert importer.most_common_month(rows) == date(2026, 6, 1)
july = importer.flag_outside_month(rows, date(2026, 7, 1))
assert july["Flags"].str.contains("outside month").all()
june = importer.flag_outside_month(july, JUNE)
assert not june["Flags"].str.contains("outside month").any() and (june["Flags"] == rows["Flags"]).all()
print("PASSED")


def sheet(*lines):
    return pd.DataFrame(list(lines), dtype=object).where(lambda frame: frame.notna(), None)


print("TEST: debit / credit columns - credit becomes Income, debit Expense")
raw = sheet(["Date", "Details", "Debit", "Credit"],
            ["2026-06-01", "Rent", 900, None], ["2026-06-02", "Refund", None, 40], ["2026-06-03", "Coffee", 4, None])
out = run(raw).rows
assert list(out["Type"]) == ["Expense", "Income", "Expense"] and list(out["Amount"]) == [900, 40, 4]
print("PASSED")

print("TEST: one signed amount column - negative = Expense, positive = Income; an all-positive log is all Expense")
raw = sheet(["Date", "Description", "Amount"], ["2026-06-01", "Coffee", -4], ["2026-06-02", "Pay", 100])
out = run(raw)
assert list(out.rows["Type"]) == ["Expense", "Income"] and list(out.rows["Amount"]) == [4, 100]
assert any("negative = Expense" in note for note in out.notes)
raw = sheet(["Date", "Description", "Amount"], ["2026-06-01", "Coffee", 4], ["2026-06-02", "Tea", 3])
assert set(run(raw).rows["Type"]) == {"Expense"}
print("PASSED")

print("TEST: a Type column is understood (synonyms); an unknown word is flagged")
raw = sheet(["Date", "Description", "Amount", "Type"], ["2026-06-01", "x", 5, "Spent"], ["2026-06-01", "y", 6, "Income"],
            ["2026-06-01", "z", 7, "Borrowed"], ["2026-06-01", "w", 8, "Whatever"])
out = run(raw).rows
assert list(out["Type"]) == ["Expense", "Income", "Loan received", "Expense"] and "not understood" in out["Flags"].iloc[3]
print("PASSED")

print("TEST: a date column of day numbers uses the chosen month; without a month the rows are flagged")
raw = sheet(["Day", "Item", "Cost"], [1, "Taxi", 10], [None, "Tea", 2], [15, "Lunch", 9])
out = run(raw)
assert list(out.rows["Date"]) == [date(2026, 6, 1), date(2026, 6, 1), date(2026, 6, 15)]
nomonth = run(raw, month=None)
assert nomonth.rows["Flags"].str.contains("needs month").sum() == 2
print("PASSED")

print("TEST: the header 'Expense' over a column of numbers is the amount, not the description")
raw = sheet(["Date", "Item", "Expense"], ["2026-06-01", "Taxi", 10], ["2026-06-02", "Tea", 2])
layout = importer.detect_layout(raw)
assert layout["columns"] == {"date": 0, "description": 1, "amount": 2}
print("PASSED")

print("TEST: the very first row has no date -> left undated and flagged; text dates without a year get the month's year")
raw = sheet(["Date", "Description", "Amount"], [None, "Taxi", 10], ["3 Jun", "Tea", 2])
out = run(raw)
assert out.rows["Date"].iloc[0] is None and "no date" in out.rows["Flags"].iloc[0] and out.stats["no_date"] == 1
assert out.rows["Date"].iloc[1] == date(2026, 6, 3)
print("PASSED")

print("TEST: month-first text dates are recognised from the data, ambiguous ones use the locale default")
raw = sheet(["Date", "Description", "Amount"], ["06/25/2026", "a", 1], ["06/02/2026", "b", 1])
assert list(run(raw).rows["Date"]) == [date(2026, 6, 25), date(2026, 6, 2)]
raw = sheet(["Date", "Description", "Amount"], ["06/02/2026", "a", 1])
assert run(raw, locale="PK").rows["Date"].iloc[0] == date(2026, 2, 6) and run(raw, locale="US").rows["Date"].iloc[0] == date(2026, 6, 2)
print("PASSED")

print("TEST: no usable header -> None; the page has to ask the user; a CSV can be read too")
assert importer.detect_layout(sheet(["hello", "world"], [1, 2])) is None
assert importer.best_sheet({"x": sheet(["a", "b"])}) == (None, None)
csv = importer.read_raw_sheets(b"Date,Description,Amount\n2026-06-01,Taxi,10\n,Tea,2\n", is_csv=True)["Sheet1"]
assert list(run(csv).rows["Date"]) == [date(2026, 6, 1)] * 2
print("PASSED")

print("TEST: money parsing")
parse = importer.parse_amount
assert (parse("1,200"), parse("Rs. 1,200/-"), parse("$45.50"), parse("(45)"), parse("-45"), parse(12), parse("AED 30")) == \
       (1200, 1200, 45.5, -45, -45, 12, 30)
assert parse("abc") is None and parse(None) is None and parse("") is None and parse(True) is None
print("PASSED")
