from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""budget/detect.py: currency, month, opening balance, budgets, income and loan payments found without being told."""
import sys
from datetime import date

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import pandas as pd
from budget import categorize, detect, importer
from budget_fixtures import messy_budget_bytes, personal_style_bytes

KM = categorize.default_keyword_map()
JUNE = date(2026, 6, 1)
SHEETS = importer.read_raw_sheets(personal_style_bytes())
MAIN, LAYOUT = importer.best_sheet(SHEETS)

print("TEST: the daily log is found among seven sheets")
assert MAIN == "📝 Daily Log" and LAYOUT["columns"]["amount"] == 4
print("PASSED")

print("TEST: currency - from the amount header (high), with the locale default as the last resort")
found = detect.detect_currency(SHEETS, MAIN, LAYOUT, "PK")
assert (found.value, found.confidence) == ("AED", "high") and "Amount (AED)" in found.source and "Daily Log" in found.source
assert detect.locale_for_currency("AED", "PK") == "AE" and detect.locale_for_currency("XYZ", "PK") == "PK"
plain = importer.read_raw_sheets(messy_budget_bytes())
name, layout = importer.best_sheet(plain)
fallback = detect.detect_currency(plain, name, layout, "PK")
assert (fallback.value, fallback.confidence) == ("Rs", "low") and "Pakistan" in fallback.source
print("PASSED")

print("TEST: currency - symbols in the cells (medium), then headings of other sheets (medium)")
symbols = pd.DataFrame([["Date", "Description", "Amount"], ["2026-06-01", "a", "$5"], ["2026-06-02", "b", "$6.50"],
                        ["2026-06-03", "c", "$7"], ["2026-06-04", "d", "$8"]], dtype=object)
layout = importer.detect_layout(symbols)
found = detect.detect_currency({"Log": symbols}, "Log", layout, "PK")
assert (found.value, found.confidence) == ("$", "medium") and "4 amounts" in found.source
titled = {"Log": pd.DataFrame([["Date", "Description", "Amount"], ["2026-06-01", "a", 5]], dtype=object),
          "Other": pd.DataFrame([["Budget (EUR)", "Spent (EUR)"], [1, 2]], dtype=object)}
found = detect.detect_currency(titled, "Log", importer.detect_layout(titled["Log"]), "PK")
assert (found.value, found.confidence) == ("EUR", "medium") and "headings of the other sheets" in found.source
print("PASSED")

print("TEST: month - the most common month, with its evidence")
rows = pd.DataFrame({"Date": [date(2026, 6, d) for d in range(1, 9)] + [date(2026, 5, 31)]})
found = detect.detect_month(rows)
assert found.value == date(2026, 6, 1) and found.confidence == "high" and found.source == "8 of 9 dated rows are in June 2026"
assert found.alternatives[0][0] == date(2026, 5, 1)
split = pd.DataFrame({"Date": [date(2026, 6, 1)] * 3 + [date(2026, 7, 1)] * 2})
assert detect.detect_month(split).confidence == "medium"
none = detect.detect_month(pd.DataFrame({"Date": [None, None]}), today=date(2026, 10, 9))
assert none.value == date(2026, 10, 1) and none.confidence == "low"
print("PASSED")

print("TEST: opening balance - the previous month's closing balance wins; this month's estimated closing is rejected")
opening = detect.detect_opening_balance(SHEETS, MAIN, JUNE)
assert opening.value == 11236 and opening.confidence == "high", opening
assert "Opening Balance (from May closing)" in opening.source and "Account Balance" in opening.source
assert "2 other label(s) give the same number" in opening.source
assert opening.alternatives == []
print("PASSED")

print("TEST: opening balance - disagreeing candidates give medium confidence and are listed")
disagree = {"Main": SHEETS[MAIN], "Balance": pd.DataFrame([["April closing balance", 900], ["Previous month balance", 1000],
                                                           ["Estimated closing balance", 5]], dtype=object)}
found = detect.detect_opening_balance(disagree, "Main", JUNE)
assert found.confidence == "medium" and {value for value, _ in found.alternatives} | {found.value} == {900, 1000}, found
nothing = detect.detect_opening_balance({"Main": SHEETS[MAIN], "X": pd.DataFrame([["Total balance", 5]], dtype=object)}, "Main", JUNE)
assert nothing.value == 0 and nothing.confidence == "low"
assert detect.detect_opening_balance({"Main": SHEETS[MAIN], "B": pd.DataFrame([["Opening balance", 250]], dtype=object)}, "Main", JUNE).value == 250
print("PASSED")

print("TEST: the file's own estimated closing balance is found for the sanity check")
own = detect.detect_own_closing(SHEETS, MAIN, JUNE)
assert own.value == 1463.29 and "Estimated Closing Balance" in own.source
assert detect.detect_own_closing({"Main": SHEETS[MAIN]}, "Main", JUNE) is None
print("PASSED")

print("TEST: budgets - the table that adds up to its own total wins, the other is listed as a conflict")
budgets = detect.detect_budgets(SHEETS, MAIN)
assert budgets.value == {"Housing": 2000, "Food": 1000, "Transport": 0, "Health": 500}, budgets.value
assert "Monthly Summary" in budgets.source and "add up to its own total" in budgets.source
assert budgets.confidence == "medium" and len(budgets.alternatives) == 1
assert "Transport" in budgets.alternatives[0][0] and "500" in budgets.alternatives[0][1] and "Dashboard" in budgets.alternatives[0][1]
only_one = {k: v for k, v in SHEETS.items() if "Dashboard" not in k}
assert detect.detect_budgets(only_one, MAIN).confidence == "high"
assert detect.detect_budgets({"Main": SHEETS[MAIN]}, "Main").value == {}
print("PASSED")

print("TEST: income - the list becomes Income rows dated the 1st (flagged); zero and empty sources are skipped")
income, found = detect.detect_income(SHEETS, MAIN, JUNE, KM)
assert list(income["Description"]) == ["Primary Salary"] and list(income["Amount"]) == [7225] and list(income["Type"]) == ["Income"]
assert income["Date"].iloc[0] == JUNE and "date not given" in income["Flags"].iloc[0] and "💵" not in income["Flags"].iloc[0]
assert found.value == 7225 and found.confidence == "high" and "adds up to its own total" in found.source
dated = {"Main": SHEETS[MAIN], "Income": pd.DataFrame([["Income Source", "Amount", "Date Received"], ["Salary", 100, date(2026, 6, 25)],
                                                       ["Gift", 20, None]], dtype=object)}
rows, found = detect.detect_income(dated, "Main", JUNE, KM)
assert list(rows["Date"]) == [date(2026, 6, 25), JUNE] and "date not given" not in rows["Flags"].iloc[0] and found.confidence == "medium"
no_income, found = detect.detect_income({"Main": SHEETS[MAIN]}, "Main", JUNE, KM)
assert no_income.empty and found.confidence == "low"
print("PASSED")

print("TEST: loan / card payment log - dated payments become Loan payment rows; zero rows and the total line are skipped")
payments, found = detect.detect_payments(SHEETS, MAIN, JUNE)
assert list(payments["Amount"]) == [500, 500.5] and set(payments["Type"]) == {"Loan payment"}
assert list(payments["Date"]) == [date(2026, 6, 15), date(2026, 6, 17)] and payments["Payment method"].iloc[0] == "Debit card"
assert payments["Description"].iloc[0] == "Loan / card payment (ENBD)" and payments["Description"].iloc[1] == "Loan / card payment"
assert found.value == 1000.5 and found.confidence == "high" and "Credit Card Loan" in found.source
assert detect.detect_payments({"Main": SHEETS[MAIN]}, "Main", JUNE)[0].empty
print("PASSED")
