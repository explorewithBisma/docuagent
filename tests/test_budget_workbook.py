from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""budget/workbook.py: the structure of the generated file (read back with openpyxl)."""
import io
import sys
from datetime import date

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import openpyxl
import pandas as pd
from budget import categorize, importer
from budget.model import BudgetConfig, TX_COLUMNS, TYPES
from budget.workbook import build_workbook
from budget_fixtures import messy_budget_bytes

KM = categorize.default_keyword_map()
CONFIG = BudgetConfig(currency="Rs", month_start=date(2026, 6, 1), opening_balance=1000,
                      categories={"Food": 300, "Transport": 100, "Entertainment": 10, "Pets": 50, "Shopping": 0},
                      payment_methods=["Cash", "Card", "Credit card", "Bank transfer"])

raw = importer.read_raw_sheets(messy_budget_bytes())
name, layout = importer.best_sheet(raw)
IMPORT = importer.import_transactions(raw[name], layout, KM, "PK", date(2026, 6, 1))
IMPORTED = build_workbook(CONFIG, KM, IMPORT.rows, IMPORT)
BLANK = build_workbook(CONFIG, KM)
wb = openpyxl.load_workbook(io.BytesIO(IMPORTED))

print("TEST: sheets in order - How to use first, then Dashboard, Transactions, Settings, Keywords, Import Notes")
assert wb.sheetnames == ["How to use", "Dashboard", "Transactions", "Settings", "Keywords", "Import Notes"]
assert openpyxl.load_workbook(io.BytesIO(BLANK)).sheetnames == ["How to use", "Dashboard", "Transactions", "Settings", "Keywords"]
assert wb.active.title == "Dashboard"
print("PASSED")

print("TEST: the six transaction types, and the exact Transactions columns")
tx = wb["Transactions"]
assert [c.value for c in tx[1]] == list(TX_COLUMNS) == ["Date", "Description", "Type", "Category", "Amount", "Payment method"]
assert tx.freeze_panes == "A2"
settings = wb["Settings"]
assert [settings.cell(row=3 + i, column=5).value for i in range(6)] == list(TYPES) and "Card bill payment" in TYPES
print("PASSED")

print("TEST: defined names point at the right ranges")
names = {n: wb.defined_names[n].attr_text for n in wb.defined_names}
assert names["tx_date"] == "Transactions!$A$2:$A$1001" and names["tx_amt"] == "Transactions!$E$2:$E$1001"
assert names["tx_type"] == "Transactions!$C$2:$C$1001" and names["tx_cat"] == "Transactions!$D$2:$D$1001"
assert names["month_start"] == "Settings!$B$3" and names["month_end"] == "Settings!$B$4" and names["opening"] == "Settings!$B$5"
assert names["lst_types"] == "Settings!$E$3:$E$8"
assert names["lst_categories"].startswith("OFFSET(Settings!$G$3,0,0,MAX(1,COUNTA(") and "OFFSET(Settings!$J$3" in names["lst_payment"]
print("PASSED")

print("TEST: dropdowns (data validation) for Type, Category, Payment method; date and amount checks")
validations = {str(dv.sqref): dv for dv in tx.data_validations.dataValidation}
assert validations["C2:C1001"].type == "list" and validations["C2:C1001"].formula1 == "lst_types"
assert validations["D2:D1001"].type == "list" and validations["D2:D1001"].formula1 == "lst_categories"
assert validations["F2:F1001"].type == "list" and validations["F2:F1001"].formula1 == "lst_payment"
assert validations["A2:A1001"].type == "date" and validations["E2:E1001"].type == "decimal"
assert validations["E2:E1001"].operator == "greaterThanOrEqual" and validations["E2:E1001"].formula1 == "0"
print("PASSED")

print("TEST: 1000 rows are formatted (date + currency), the 1001st is not; the currency is baked into the format")
assert tx["A2"].number_format == tx["A1001"].number_format == "dd mmm yyyy"
assert tx["E2"].number_format == tx["E1001"].number_format == '"Rs" #,##0.00'
assert tx["E1002"].number_format == "General" and tx["A1002"].number_format == "General"
assert wb["Dashboard"]["B5"].number_format == '"Rs" #,##0.00'
assert openpyxl.load_workbook(io.BytesIO(build_workbook(BudgetConfig(currency='A"B'), KM)))["Transactions"]["E2"].number_format == '"AB" #,##0.00'
print("PASSED")

print("TEST: the imported rows are in Transactions; filled-down dates are yellow with a comment")
rows = [[c.value for c in tx[r]] for r in range(2, 14)]
assert [r[1] for r in rows][:3] == ["Taxi to office", "Grocery - Carrefour", "Lunch"] and tx["A14"].value is None
assert tx["E6"].value == 1200 and tx["C5"].value == "Income" and tx["D2"].value == "Transport"
filled = [r for r in range(2, 14) if tx.cell(row=r, column=1).comment]
assert len(filled) == IMPORT.stats["fill_down"] == 8
assert all(tx.cell(row=r, column=1).fill.fgColor.rgb.endswith("FEF08A") for r in filled)
assert tx["A2"].value.date() == date(2026, 6, 1) and not tx["A2"].comment
print("PASSED")

print("TEST: conditional formatting on Transactions - grey outside the month, orange expense without category")
cf = {str(rng.sqref): [r.formula for r in rules] for rng, rules in tx.conditional_formatting._cf_rules.items()}
assert any("month_start" in f[0] and "month_end" in f[0] for f in cf["A2:F1001"])
assert any('"Expense"' in f[0] and '$D2=""' in f[0] for f in cf["D2:D1001"])
print("PASSED")

dash = wb["Dashboard"]
print("TEST: the Dashboard has no typed numbers - every number comes from a formula")
numbers = [c.coordinate for row in dash.iter_rows() for c in row if isinstance(c.value, (int, float)) and not isinstance(c.value, bool)]
assert numbers == [], numbers
formulas = [c for row in dash.iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith("=")]
assert len(formulas) > 300
assert any("SUMIFS" in c.value for c in formulas) and all("Transactions!" not in c.value for c in formulas), "uses the defined names"
print("PASSED")

print("TEST: summary labels, savings card, card-bill line, and the formulas behind them")
label = {dash.cell(row=r, column=1).value: dash.cell(row=r, column=2).value for r in range(5, 14)}
assert list(label) == ["Total income", "Total expenses", "Loan payments", "Loans received", "Total saved this month",
                       "Net balance (after savings)", "Opening balance", "Closing balance",
                       "Card bill payments (not counted as spending)"]
assert label["Total saved this month"].startswith('=SUMIFS(tx_amt,tx_type,"Saving"')
assert label["Net balance (after savings)"] == "=B5+B8-B6-B7-B9" and label["Closing balance"] == "=B11+B10"
assert label["Card bill payments (not counted as spending)"].startswith('=SUMIFS(tx_amt,tx_type,"Card bill payment"')
assert label["Total expenses"] == '=SUMIFS(tx_amt,tx_type,"Expense",tx_date,">="&month_start,tx_date,"<="&month_end)'
assert "B13" not in label["Net balance (after savings)"], "card bills are not part of the net balance"
print("PASSED")

print("TEST: the data-checks area explains the credit-card rule")
text = " ".join(str(c.value) for row in dash["D4:G16"] for c in row if c.value)
assert "credit card is an Expense" in text and "Card bill payment" in text and "NOT counted as spending" in text
assert dash["D4"].value == "Data checks"
print("PASSED")

print("TEST: category table - formulas per slot, budget vs spent, red rule when over budget, data bar")
assert dash["A18"].value == '=IF(Settings!G3="","",Settings!G3)' and dash["A47"].value == '=IF(Settings!G32="","",Settings!G32)'
assert dash["C18"].value.startswith('=IF(A18="","",SUMIFS(tx_amt,tx_type,"Expense",tx_cat,A18,')
assert dash["F18"].value == '=IF(A18="","",IF(B18=0,IF(C18>0,"No budget",""),IF(C18>B18,"OVER","OK")))'
rules = {str(rng.sqref): rules for rng, rules in dash.conditional_formatting._cf_rules.items()}
red = rules["A18:F47"][0]
assert red.formula == ['AND($A18<>"",$B18>0,$C18>$B18)'] and red.dxf.fill.bgColor.rgb.endswith("FECACA")
assert rules["E18:E47"][0].type == "dataBar"
print("PASSED")

print("TEST: top 3 and daily totals are formulas; the daily table covers 31 days")
assert dash["J5"].value.startswith("=IF(K5") and "LARGE" in dash["K5"].value and dash["J7"].value and dash["K7"].value
assert dash["I12"].value.startswith("=IF((month_start+") and dash["I42"].value and dash["I43"].value is None
assert 'SUMIFS(tx_amt,tx_type,"Expense",tx_date,I12)' in dash["L12"].value
print("PASSED")

print("TEST: at least one native chart (bar, daily column, doughnut)")
kinds = sorted(type(chart).__name__ for chart in dash._charts)
assert kinds == ["BarChart", "BarChart", "DoughnutChart"], kinds
print("PASSED")

print("TEST: the doughnut shows only name + percentage, hides small and empty slices, has its legend on the right")
import re
import zipfile
doughnut = next(zipfile.ZipFile(io.BytesIO(IMPORTED)).read(n).decode() for n in zipfile.ZipFile(io.BytesIO(IMPORTED)).namelist()
                if n.startswith("xl/charts/chart") and "doughnutChart" in zipfile.ZipFile(io.BytesIO(IMPORTED)).read(n).decode())
labels = re.search(r"<dLbls>.*?</dLbls>", doughnut).group(0)
for element, value in (("showCatName", "1"), ("showPercent", "1"), ("showVal", "0"), ("showSerName", "0"), ("showLegendKey", "0")):
    assert f'<{element} val="{value}"' in labels, (element, labels)
assert 'formatCode="[&lt;0.005]&quot;&quot;;0%"' in labels, "an empty slice gets no '0%' label"
assert labels.index("showPercent") < labels.index("separator") and "showLeaderLines" not in labels, "elements in the order the schema requires"
assert '<legend><legendPos val="r"' in doughnut
rows_in_chart = len(CONFIG.categories) + 1                                  # the ranked categories + the 'Other' slice
assert f"$AA$18:$AA${17 + rows_in_chart}" in doughnut and f"$AB$18:$AB${17 + rows_in_chart}" in doughnut
assert "$A$18" not in doughnut and "$C$18" not in doughnut, "the doughnut reads its own helper table, not the category table"
print("PASSED")

print("TEST: the chart helper is formulas only, visible (a hidden column is not drawn), and its threshold is a named setting")
helper_cells = [c for row in dash["AA17:AC30"] for c in row if c.value is not None]
assert all(not isinstance(c.value, (int, float)) for c in helper_cells)
assert dash["AA18"].value.startswith('=IF($C$48<=0,"",IF(AC18/$C$48>=chart_min_share,') and dash["AB18"].value == '=IF(AA18="",0,AC18)'
assert dash["AA23"].value.startswith('=IF($C$48<=0,"",IF(($C$48-SUM(AB18:AB22))/$C$48>=chart_min_share,"Small categories (under "') and dash["AB23"].value == "=MAX(0,$C$48-SUM(AB18:AB22))"
assert dash["AA16"].value == "Chart data (calculated, do not edit)", "a heading sits right above the helper table"
assert not dash.column_dimensions["AA"].hidden and names["chart_min_share"] == "Settings!$B$7" and wb["Settings"]["B7"].value == 0.03
print("PASSED")

print("TEST: How to use - at most 8 short steps in plain English, covering types, credit cards, colors, next month")
how = wb["How to use"]
steps = [(how.cell(row=r, column=1).value, how.cell(row=r, column=2).value) for r in range(3, 20) if how.cell(row=r, column=1).value]
assert len(steps) == 8 and steps[0][0].startswith("1.") and steps[7][0].startswith("8.")
joined = " ".join(f"{a} {b}" for a, b in steps)
for needed in ("Income", "Expense", "Loan received", "Loan payment", "Saving", "Card bill payment", "credit card",
               "not counted as spending", "Yellow", "Grey", "Orange", "Red", "Next month", "closing balance"):
    assert needed in joined, needed
assert all(len(body) < 700 for _, body in steps)
print("PASSED")

print("TEST: Settings hold the lists and budgets; the keyword map is stored for next month")
assert [settings.cell(row=3 + i, column=7).value for i in range(5)] == ["Food", "Transport", "Entertainment", "Pets", "Shopping"]
assert settings["H3"].value == 300 and settings["B3"].value.date() == date(2026, 6, 1) and settings["B5"].value == 1000
assert settings["B4"].value == "=EDATE(B3,1)-1" and settings["A1"].value == "Budget Builder workbook"
assert [settings.cell(row=3 + i, column=10).value for i in range(4)] == CONFIG.payment_methods
keywords = wb["Keywords"]
assert keywords["A2"].value == "Category" and keywords["B2"].value == "Food" and "grocery" in keywords["C2"].value
assert any(keywords.cell(row=r, column=1).value == "Type" and keywords.cell(row=r, column=2).value == "Card bill payment"
           for r in range(2, 30))
print("PASSED")

print("TEST: Import Notes list the counts, the flagged rows and every dropped row with its reason")
notes = wb["Import Notes"]
text = " ".join(str(c.value) for row in notes.iter_rows() for c in row if c.value is not None)
assert "Dates filled down from the row above (yellow in Transactions): 8" in text
assert "kept: 12" in text and "dropped: 4" in text and "Hardcoded total" in text and "Grand total" in text and "Mystery shop" in text
print("PASSED")

print("TEST: text that looks like a formula is stored as text, never run")
evil = pd.DataFrame([{"Date": date(2026, 6, 2), "Description": '=HYPERLINK("http://evil","x")', "Type": "Expense",
                      "Category": "@Food", "Amount": 5.0, "Payment method": "+cash"}])
book = openpyxl.load_workbook(io.BytesIO(build_workbook(CONFIG, KM, evil)))
cell = book["Transactions"]["B2"]
assert cell.data_type == "s" and cell.value.startswith("=HYPERLINK")
assert book["Transactions"]["D2"].data_type == "s" and book["Transactions"]["F2"].data_type == "s"
print("PASSED")

print("TEST: limits and bad settings are refused with a readable message")
too_many = pd.DataFrame([{"Date": date(2026, 6, 1), "Description": "x", "Type": "Expense", "Category": "Food", "Amount": 1.0,
                          "Payment method": None}] * 1001)
for call, fragment in ((lambda: build_workbook(CONFIG, KM, too_many), "1000 rows"),
                       (lambda: build_workbook(BudgetConfig(month_start=date(2026, 6, 9)), KM), "1st"),
                       (lambda: build_workbook(BudgetConfig(categories={"A": 1, "a": 2}), KM), "unique")):
    try:
        call()
        raise AssertionError("should have been refused")
    except ValueError as error:
        assert fragment in str(error), str(error)
print("PASSED")

print("TEST: a blank start has an empty, still fully formatted Transactions sheet")
blank = openpyxl.load_workbook(io.BytesIO(BLANK))["Transactions"]
assert blank["B2"].value is None and blank["E1001"].number_format == '"Rs" #,##0.00'
assert any(str(dv.sqref) == "C2:C1001" for dv in blank.data_validations.dataValidation)
print("PASSED")
