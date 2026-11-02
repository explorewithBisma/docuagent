from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""Check every month, driven through Streamlit's AppTest: make rules from a sample, then check a new file against them."""
import io
import sys
from unittest import mock

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import openpyxl
import streamlit as st
from streamlit.testing.v1 import AppTest

import datarules_fixtures as fx
from datarules import model

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_original = st.download_button
captured = {}


def spy(label, data=None, file_name=None, mime=None, key=None, **kwargs):
    captured[key] = {"label": label, "data": data, "file_name": file_name, "disabled": kwargs.get("disabled")}
    return _original(label, data=data, file_name=file_name, mime=mime, key=key, **kwargs)


def run(at):
    with mock.patch.object(st, "download_button", spy):
        at.run()
    assert not at.exception, [e.value for e in at.exception]


def uploader(at, key):
    return next(u for u in at.file_uploader if u.key == key)


def xlsx_of(df, sheet="Orders"):
    book = openpyxl.Workbook()
    ws = book.active
    ws.title = sheet
    for c, name in enumerate(df.columns, start=1):
        ws.cell(row=1, column=c, value=name)
    for r, row in enumerate(df.itertuples(index=False), start=2):
        for c, value in enumerate(row, start=1):
            if value is not None:
                ws.cell(row=r, column=c, value=value)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def new_app():
    at = AppTest.from_file(str(ROOT / "monthly_app.py"), default_timeout=120)
    run(at)
    return at


def text_of(at):
    return " ".join(m.value for m in at.markdown) + " " + " ".join(c.value for c in at.caption) + " " + " ".join(i.value for i in at.info)


print("TEST: first screen - two choices, and an invitation to upload a sample")
at = new_app()
assert [t.value for t in at.title] == ["✅ Check every month"] and at.radio[0].options == ["Make rules from a clean sample file", "Check this month's file"]
assert any("Upload a clean sample file" in i.value for i in at.info)
print("PASSED")

print("TEST: Make rules - a sample CSV gives the card and 17 suggested rules; the download is a valid rules file with every rule on")
at = new_app()
sample_csv = fx.sales_month(2026, 4).to_csv(index=False).encode()
uploader(at, "monthly_upload_sample").upload("april.csv", sample_csv, "text/csv")
captured.clear()
run(at)
assert [h.value for h in at.header] == ["What I found", "Your rules"]
text = text_of(at)
assert "240 rows, 9 columns" in text and "Rules suggested" in text and "17: " in text and "Untick what you do not want" in text
rules_download = captured["monthly_download_rules"]
rules = model.from_json(rules_download["data"])
print("  ", rules_download["label"], "|", rules_download["file_name"], "|", len(rules.rules), "rules")
assert len(rules.rules) == 17 and all(r.enabled for r in rules.rules) and rules.source == "april.csv" and rules_download["file_name"] == "Checks_for_april.csv.json"
assert "17 rules on" in rules_download["label"] and not rules_download["disabled"]
print("PASSED")

print("TEST: Check - the rules file + a month with planted problems -> FAIL card, Please check worst first, highlighted copy and report")
frame, planted = fx.problem_month()
at = new_app()
at.radio[0].set_value("Check this month's file")
run(at)
assert any("Upload the rules file" in i.value for i in at.info)
uploader(at, "monthly_upload_rules").upload("rules.json", rules_download["data"], "application/json")
uploader(at, "monthly_upload_month").upload("may.xlsx", xlsx_of(frame), XLSX)
captured.clear()
run(at)
assert any("FAIL - 9 of 17 rules failed" in e.value for e in at.error), [e.value for e in at.error]
headers = [h.value for h in at.header]
print("  ", headers)
assert headers == ["What I found", "Please check", "Download"]
text = text_of(at)
for expected in ("**Result**", "FAIL - 9 of 17 rules failed", "9 of 9 expected columns found", "values in &#39;Amount&#39;".replace("&#39;", "'"), "duplicate value", "blank cell"):
    assert expected in text, expected
copy = captured["monthly_download_copy"]
book = openpyxl.load_workbook(io.BytesIO(copy["data"]))
assert copy["file_name"] == "may_checked.xlsx" and book.sheetnames == ["Orders", "Rule report"]
assert book["Orders"].cell(row=52, column=8).value == 99999.0 and book["Orders"].cell(row=52, column=8).comment is not None
assert "RESULT: FAIL" in captured["monthly_download_text"]["data"] and captured["monthly_download_text"]["file_name"] == "may_rule_report.txt"
print("PASSED")

print("TEST: a clean month passes, with a green result and a copy that says so")
clean = fx.sales_month(2026, 5, 150, 31, first_id=7000)
at = new_app()
at.radio[0].set_value("Check this month's file")
run(at)
uploader(at, "monthly_upload_rules").upload("rules.json", rules_download["data"], "application/json")
uploader(at, "monthly_upload_month").upload("may_clean.csv", clean.to_csv(index=False).encode(), "text/csv")
captured.clear()
run(at)
assert any("PASS - all 17 rules passed" in s.value for s in at.success) and not at.error
assert any("Every rule passed." in i.value for i in at.info) and "Please check" not in [h.value for h in at.header]
csv_copy = openpyxl.load_workbook(io.BytesIO(captured["monthly_download_copy"]["data"]))
assert csv_copy.sheetnames == ["Data", "Rule report"] and captured["monthly_download_copy"]["file_name"] == "may_clean_checked.xlsx"
print("PASSED")

print("TEST: a renamed column is accepted and reported; switching that off treats it as missing")
renamed = frame.rename(columns={"Amount": "Amount (AED)"})
at = new_app()
at.radio[0].set_value("Check this month's file")
run(at)
uploader(at, "monthly_upload_rules").upload("rules.json", rules_download["data"], "application/json")
uploader(at, "monthly_upload_month").upload("may.xlsx", xlsx_of(renamed), XLSX)
run(at)
text = text_of(at)
assert "'Amount' was found as 'Amount (AED)'" in text and "renamed: 'Amount' is 'Amount (AED)'" in text
assert any("9 of 17 rules failed" in e.value for e in at.error)
next(c for c in at.checkbox if c.key == "monthly_similar").set_value(False)
run(at)
assert any("FAIL - 9 of 15 rules failed" in e.value for e in at.error)                      # the 2 Amount rules are skipped; 'expected columns' now fails too
assert "missing: 'Amount'" in text_of(at) and "Skipped" in text_of(at)
print("PASSED")

print("TEST: a file that is not a rules file, or has the wrong version, says so")
at = new_app()
at.radio[0].set_value("Check this month's file")
run(at)
uploader(at, "monthly_upload_rules").upload("notrules.json", b'{"version": 9, "rules": []}', "application/json")
uploader(at, "monthly_upload_month").upload("may.csv", clean.to_csv(index=False).encode(), "text/csv")
run(at)
assert any("version 9" in e.value for e in at.error) and not at.get("download_button")
print("PASSED")
