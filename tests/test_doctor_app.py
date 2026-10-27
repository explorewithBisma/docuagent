from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""'Check my file' page end to end (AppTest): card, Please-check order, downloads, and the safety net when the highlighted copy fails."""
import io
import sys
from unittest import mock

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import openpyxl
import streamlit as st
from streamlit.testing.v1 import AppTest

import doctor.output
import doctor_fixtures as fx
from toolkit.highlight import PatchError

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DATA = fx.build_workbook()
_original = st.download_button
captured = {}


def spy(label, data=None, file_name=None, mime=None, key=None, **kwargs):
    captured[label] = {"data": data, "file_name": file_name, "mime": mime}
    return _original(label, data=data, file_name=file_name, mime=mime, key=key, **kwargs)


def run(at):
    with mock.patch.object(st, "download_button", spy):
        at.run()
    assert not at.exception, [e.value for e in at.exception]


def open_page(data=DATA, name="budget.xlsx"):
    captured.clear()
    at = AppTest.from_file(str(ROOT / "doctor_app.py"), default_timeout=240)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    at.file_uploader[0].upload(name, data, XLSX)
    run(at)
    return at


def text_of(at):
    return " ".join(m.value for m in at.markdown) + " " + " ".join(c.value for c in at.caption)


print("TEST: before an upload the page explains itself and waits")
at = AppTest.from_file(str(ROOT / "doctor_app.py"), default_timeout=120)
at.run()
assert any("Check my file" in t.value for t in at.title) and any("Upload a workbook to start" in i.value for i in at.info)
assert not at.button or all(b.key == "back_to_start" for b in at.button)
print("PASSED")

print("TEST: the card says what each sheet does (with source and confidence) and counts the findings")
at = open_page()
assert any(h.value == "What I found" for h in at.header) and any(h.value == "Please check" for h in at.header)
text = text_of(at)
assert "a transaction list: 40 rows" in text and "from header row 3" in text and "🟢 sure" in text
assert "**11 thing(s) to check:** 7 serious, 3 worth checking, 1 small" in text
assert "I never change your numbers" in text
print("PASSED")

print("TEST: the Please-check list is in order of importance, nothing hidden, each with evidence")
markdown = [m.value for m in at.markdown if m.value.startswith(("🔴", "🟠", "🔵"))]
assert len(markdown) == 11, len(markdown)
icons = [m[0] for m in markdown]
assert icons == sorted(icons, key=["🔴", "🟠", "🔵"].index) and markdown[0].startswith("🔴 **'(-) Total Expenses This Month' says 11,755")
assert any(e.label.endswith("more") for e in at.expander), "the ninth finding is in the 'more' expander, not dropped"
codes = [c.value for c in at.code]
assert any("=C15-#REF!" in c for c in codes) and any("SUMIF(" in c for c in codes)
print("PASSED")

print("TEST: the highlighted copy and the text report are offered; the copy keeps the chart and every number")
assert set(captured) == {"Download highlighted copy (.xlsx)", "Download the report (.txt)"}
copy = captured["Download highlighted copy (.xlsx)"]
assert copy["file_name"] == "budget_checked.xlsx" and copy["mime"] == XLSX
book = openpyxl.load_workbook(io.BytesIO(copy["data"]))
assert book.sheetnames[-1] == "Doctor report" and book[fx.DASH]._charts and book[fx.ACCOUNT]["B4"].value == 11755
assert book[fx.ACCOUNT]["B4"].comment is not None
assert "cell(s) coloured," in text_of(at) and "note(s) added" in text_of(at)
report = captured["Download the report (.txt)"]
assert report["file_name"] == "budget_report.txt" and report["data"].startswith("Excel Doctor report for budget.xlsx")
print("PASSED")

print("TEST: if the highlighted copy cannot be made safely, no copy is offered - the report stays")
with mock.patch.object(doctor.output, "make_highlighted", side_effect=PatchError("A value changed at X!A1.")):
    at = open_page()
    next(s for s in at.slider if s.key == "doctor_tolerance").set_value(0.2)          # a different input, so the cached result is not reused
    captured.clear()
    run(at)
assert "Download highlighted copy (.xlsx)" not in captured and "Download the report (.txt)" in captured
warning = " ".join(w.value for w in at.warning)
assert "I couldn't make a highlighted copy that I'm sure is safe" in warning and "A value changed at X!A1." in warning
assert any(h.value == "Please check" for h in at.header), "the on-page report is complete"
print("PASSED")

print("TEST: 'Also show small things' can be switched off")
at = open_page()
next(c for c in at.checkbox if c.key == "doctor_include_low").uncheck()
run(at)
assert "**10 thing(s) to check:**" in text_of(at) and "1 small" not in text_of(at)
print("PASSED")

print("TEST: a clean workbook says so and offers no highlighted copy")
clean = io.BytesIO()
wb = openpyxl.Workbook()
wb.active["A1"], wb.active["A2"], wb.active["A3"] = 1, 2, "=A1+A2"
wb.save(clean)
at = open_page(clean.getvalue(), "tiny.xlsx")
assert any("No problems were found" in s.value for s in at.success)
assert "Download highlighted copy (.xlsx)" not in captured and "Download the report (.txt)" in captured
assert any("Nothing to highlight" in c.value for c in at.caption)
print("PASSED")

print("TEST: an unreadable file gives a readable message, not a crash")
for bad, fragment in ((b"hello", "doesn't look like an Excel"), (b"\xd0\xcf\x11\xe0 old", "old .xls")):
    at = open_page(bad, "bad.xlsx")
    assert any(fragment in e.value for e in at.error), [e.value for e in at.error]
print("PASSED")
