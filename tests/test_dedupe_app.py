from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""The near-duplicate section inside 'Clean my file', driven through Streamlit's AppTest."""
import io
import sys

sys.path.insert(0, str(ROOT))

import openpyxl
import pandas as pd
from streamlit.testing.v1 import AppTest

import changes as ch
import cleaning
from xlsx_export import export_workbook

STEPS_LABEL = "Clean File"
SAMPLE = (ROOT / "test_data" / "messy_sample.csv").read_bytes()
PEOPLE = pd.DataFrame({
    "Name": ["Ali Khan", "ali  KHAN", "Sara Ahmed", "Sara Ahmed", "Omar Farooq", "Bilal Raza", "Mohamed Saleh", "Muhammad Saleh"],
    "Phone": ["0300-1234567", "03001234567", "0321-7654321", "0321 7654321", "0333-1112223", "0345-9998887", "0311-5551212", "0311 5551212"],
    "Email": ["ali@x.com", "", "", "sara@y.com", "omar@z.com", "bilal@w.com", "ms@q.com", ""],
    "City": ["Lahore", "Karachi", "Dubai", "", "Doha", "Multan", "Lahore", "Lahore"],
}).to_csv(index=False).encode()


def new_app(name, content):
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    at.run()
    at.file_uploader[0].upload(name, content, "text/csv")
    at.run()
    next(b for b in at.button if b.label == STEPS_LABEL).click()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def workbook_values(at):
    s = at.session_state
    wb = openpyxl.load_workbook(io.BytesIO(export_workbook(s["cleaned_sheets"], s["change_logs"], s["sheet_stats"], s["quality_before"],
                                                          s["placeholder_list"], s["original_filename"], s["original_rows"], True)))
    return {ws.title: [[c.value for c in row] for row in ws.iter_rows()] for ws in wb if ws.title != "Cleaning Summary"}


def press(at, label):
    next(b for b in at.button if b.label == label).click()
    at.run()
    assert not at.exception, [e.value for e in at.exception]


print("TEST: the cleaner's data, change log and report are unchanged by the new section until the user asks for it")
at = new_app("sample.csv", SAMPLE)
raw = pd.read_csv(io.BytesIO(SAMPLE), dtype=str, keep_default_na=False)
before = {name: df.copy() for name, df in at.session_state["cleaned_sheets"].items()}
report_before, workbook_before = at.session_state["report_text"], workbook_values(at)
assert any(h.value == "Near-duplicates" for h in at.header)
assert any(b.label == "Look for near-duplicates" for b in at.button)
assert not any(log.removed and any(r["reason"] == "near_duplicate" for r in log.removed) for log in at.session_state["change_logs"].values())
assert "near-duplicate" not in at.session_state["report_text"].lower()
print("PASSED")

print("TEST: 'Look for near-duplicates' on a file with none changes nothing and says so")
press(at, "Look for near-duplicates")
after = at.session_state["cleaned_sheets"]
assert all(after[name].equals(df) for name, df in before.items())
assert not any(b.label.startswith("Apply to") for b in at.button)
assert at.session_state["report_text"] == report_before and workbook_values(at) == workbook_before      # downloads identical too
print("   ", [i.value for i in at.info][:3], [c.value for c in at.success][:2])
print("PASSED")

print("TEST: find -> accept all high-confidence -> summary shown first -> apply")
at = new_app("people.csv", PEOPLE)
cleaned_before = at.session_state["cleaned_sheets"]["Sheet1"] if "Sheet1" in at.session_state["cleaned_sheets"] else next(iter(at.session_state["cleaned_sheets"].values()))
sheet = next(iter(at.session_state["cleaned_sheets"]))
rows_before = len(at.session_state["cleaned_sheets"][sheet])
press(at, "Look for near-duplicates")
texts = [i.value for i in at.info] + [s.value for s in at.success]
print("   ", texts)
assert any("group" in t and "possible duplicates" in t for t in texts), texts
press(at, "Accept all high-confidence groups")
summary = [i.value for i in at.info if "rows will be removed" in i.value]
print("   ", summary)
assert summary and "blank cells filled" in summary[0]
assert len(at.session_state["cleaned_sheets"][sheet]) == rows_before                    # nothing changed yet
apply_button = next(b for b in at.button if b.label.startswith("Apply to"))
apply_button.click()
at.run()
assert not at.exception, [e.value for e in at.exception]
final = at.session_state["cleaned_sheets"][sheet]
print("   rows", rows_before, "->", len(final))
assert len(final) < rows_before
log = at.session_state["change_logs"][sheet]
kinds = [r["kind"] for r in log.finalize_removed()]
assert "near_duplicate" in kinds
assert all(r["reason"].startswith("Near-duplicate of row") for r in log.finalize_removed() if r["kind"] == "near_duplicate")
assert "near-duplicate" in at.session_state["report_text"].lower()
assert any("rows removed" in s.value for s in at.success)
print("PASSED")

print("TEST: the file-type switch is on the card itself; an unsure type says so; switching looks again")
sys.path.insert(0, str(ROOT / "tests"))
import dedupe_fixtures as fx
at = new_app("customers.csv", fx.pair_customer_list().to_csv(index=False).encode())
press(at, "Look for near-duplicates")
kind_radio = next(r for r in at.radio if r.key == "dd_kind")
print("   ", kind_radio.label, "|", kind_radio.value)
assert "not sure" in kind_radio.label and kind_radio.value == "Detect automatically"
assert any("7 groups of possible duplicates" in i.value for i in at.info)
assert any("Not sure" in m.value for m in at.markdown) or any("Not sure" in c.value for c in at.caption)
kind_radio.set_value("A list of transactions")
at.run()
assert not at.exception, [e.value for e in at.exception]
assert not any("7 groups" in i.value for i in at.info)                                   # looked again as a transaction list
print("PASSED")
