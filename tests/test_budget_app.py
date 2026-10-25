from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""The budget page end to end (AppTest): blank start, messy import, next month, unreadable file."""
import io
import sys
from datetime import date
from unittest import mock

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import openpyxl
import streamlit as st
from streamlit.testing.v1 import AppTest

from budget import carryover, categorize
from budget.model import BudgetConfig
from budget.workbook import build_workbook
from budget_fixtures import messy_budget_bytes

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_original = st.download_button
captured = {}


def spy(label, data=None, file_name=None, mime=None, key=None, **kwargs):
    captured[label] = {"data": data, "file_name": file_name}
    return _original(label, data=data, file_name=file_name, mime=mime, key=key, **kwargs)


def new_app():
    at = AppTest.from_file(str(ROOT / "budget_app.py"), default_timeout=180)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def run(at):
    with mock.patch.object(st, "download_button", spy):
        at.run()
    assert not at.exception, [e.value for e in at.exception]


def build(at):
    captured.clear()
    next(b for b in at.button if b.label == "Build my budget sheet").click()
    run(at)
    assert "Download budget workbook (.xlsx)" in captured, [e.value for e in at.error]
    return captured["Download budget workbook (.xlsx)"]


def sheet_rows(book):
    ws = book["Transactions"]
    return [[c.value for c in ws[r]] for r in range(2, 1002) if any(c.value is not None for c in ws[r])]


print("TEST: blank start - build straight away, get a usable workbook with the right file name")
at = new_app()
assert any(h.value.startswith("1. Your file") for h in at.header) and not any("Review your transactions" in h.value for h in at.header)
output = build(at)
book = openpyxl.load_workbook(io.BytesIO(output["data"]))
today = date.today().replace(day=1)
assert output["file_name"] == f"Budget_{today:%Y-%m}.xlsx"
assert book.sheetnames == ["How to use", "Dashboard", "Transactions", "Settings", "Keywords"] and sheet_rows(book) == []
assert book["Settings"]["B3"].value.date() == today and book["Settings"]["G3"].value == "Food"
print("PASSED")

print("TEST: blank start - settings typed on the page end up in the file")
at = new_app()
next(n for n in at.number_input if n.label == "Opening balance").set_value(750.0)
next(s for s in at.selectbox if s.label == "Currency").select("AED")
next(t for t in at.text_area if t.label.startswith("Payment methods")).set_value("Cash\nVisa")
run(at)
book = openpyxl.load_workbook(io.BytesIO(build(at)["data"]))
assert book["Settings"]["B5"].value == 750 and book["Settings"]["B6"].value == "AED"
assert [book["Settings"].cell(row=3 + i, column=10).value for i in range(3)] == ["Cash", "Visa", None]
assert book["Transactions"]["E2"].number_format == '"AED" #,##0.00'
print("PASSED")

print("TEST: messy import - counts, month detection, unknown description, a workbook with the 12 rows")
at = new_app()
at.file_uploader[0].upload("june.xlsx", messy_budget_bytes(), XLSX)
run(at)
metrics = {m.label: m.value for m in at.metric}
assert metrics == {"Rows imported": "12", "Dates filled down": "8", "Rows dropped": "4", "Still without category": "1"}, metrics
assert any("did not match any keyword" in w.value for w in at.warning)
assert next(s for s in at.selectbox if s.label == "Month").value == 6 and next(n for n in at.number_input if n.label == "Year").value == 2026
assert any(h.value.startswith("4. Review your transactions") for h in at.header) and any(h.value.startswith("5. Build") for h in at.header)
output = build(at)
book = openpyxl.load_workbook(io.BytesIO(output["data"]))
assert output["file_name"] == "Budget_2026-06.xlsx" and "Import Notes" in book.sheetnames
rows = sheet_rows(book)
assert len(rows) == 12 and rows[0][1] == "Taxi to office" and rows[0][0].date() == date(2026, 6, 1)
categories = [book["Settings"].cell(row=3 + i, column=7).value for i in range(30)]
assert "Pets" in categories and "Transport" in categories, categories
assert book["Settings"]["B5"].value == 0
print("PASSED")

print("TEST: the chosen month is used - June rows are kept when July is chosen (the workbook greys them and the Dashboard ignores them)")
next(s for s in at.selectbox if s.label == "Month").select(7)
run(at)
output = build(at)
book = openpyxl.load_workbook(io.BytesIO(output["data"]))
assert len(sheet_rows(book)) == 12 and book["Settings"]["B3"].value.date() == date(2026, 7, 1)
assert output["file_name"] == "Budget_2026-07.xlsx"
print("PASSED")

print("TEST: next month - upload a workbook this app made; closing balance becomes the opening balance")
km = categorize.add_keyword(categorize.default_keyword_map(), "Food", "shawarma")
june = build_workbook(BudgetConfig(currency="Rs", month_start=date(2026, 6, 1), opening_balance=1000,
                                   categories={"Food": 300, "Transport": 100, "Pets": 50}, payment_methods=["Cash", "Card"]),
                      km, None)
june_with_rows = None
from budget import importer
raw = importer.read_raw_sheets(messy_budget_bytes())
name, layout = importer.best_sheet(raw)
imp = importer.import_transactions(raw[name], layout, km, "PK", date(2026, 6, 1))
june_with_rows = build_workbook(BudgetConfig(currency="Rs", month_start=date(2026, 6, 1), opening_balance=1000,
                                             categories={"Food": 300, "Transport": 100, "Pets": 50, "Entertainment": 10},
                                             payment_methods=["Cash", "Card", "Credit card", "Bank transfer"]), km, imp.rows, imp)
at = new_app()
at.file_uploader[0].upload("Budget_2026-06.xlsx", june_with_rows, XLSX)
run(at)
assert any("Found a workbook made by this app for **June 2026**" in i.value and "3,386.50" in i.value for i in at.info), [i.value for i in at.info]
assert next(n for n in at.number_input if n.label == "Opening balance").value == 3386.5
assert next(s for s in at.selectbox if s.label == "Month").value == 7 and next(s for s in at.selectbox if s.label == "Currency").value == "Rs"
assert not any("Review your transactions" in h.value for h in at.header)
output = build(at)
book = openpyxl.load_workbook(io.BytesIO(output["data"]))
assert output["file_name"] == "Budget_2026-07.xlsx" and sheet_rows(book) == [] and "Import Notes" not in book.sheetnames
again = carryover.read_generated_workbook(output["data"])
assert again.config.month_start == date(2026, 7, 1) and again.config.opening_balance == 3386.5
assert again.config.categories == {"Food": 300, "Transport": 100, "Pets": 50, "Entertainment": 10}
assert again.config.payment_methods == ["Cash", "Card", "Credit card", "Bank transfer"]
assert "shawarma" in again.keyword_map["categories"]["Food"], "the keyword map is kept"
print("PASSED")

print("TEST: a file with no recognisable columns asks the user to choose them instead of crashing")
import pandas as pd
junk = io.BytesIO()
pd.DataFrame({"a": [1, 2], "b": ["x", "y"]}).to_excel(junk, index=False)
at = new_app()
at.file_uploader[0].upload("junk.xlsx", junk.getvalue(), XLSX)
run(at)
assert any("Choose at least a Date or Description column" in w.value for w in at.warning)
assert not [b for b in at.button if b.label == "Build my budget sheet"], "nothing to build until the columns are chosen"
print("PASSED")

print("TEST: a CSV export works too")
at = new_app()
at.file_uploader[0].upload("log.csv", b"Date,Item,Cost\n2026-06-01,Taxi,10\n,Lunch,12\n", "text/csv")
run(at)
assert {m.label: m.value for m in at.metric}["Rows imported"] == "2"
rows = sheet_rows(openpyxl.load_workbook(io.BytesIO(build(at)["data"])))
assert [r[1] for r in rows] == ["Taxi", "Lunch"] and rows[1][3] == "Food"
print("PASSED")
