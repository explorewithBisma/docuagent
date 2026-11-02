from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""The 'Match two files' page, driven through Streamlit's AppTest: upload two files, read the card, change a setting, confirm the maybes."""
import io
import sys

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import pandas as pd
from openpyxl import load_workbook
from streamlit.testing.v1 import AppTest

import reconcile_fixtures as fx
from reconcile import output as ro

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
bank, ledger, truth = fx.bank_vs_ledger()
bank_csv = ("Account statement,,,,,\nPeriod: 2026,,,,,\n" + bank.to_csv(index=False)).encode()           # two title rows before the header
buffer = io.BytesIO()
with pd.ExcelWriter(buffer) as writer:
    pd.DataFrame({"Note": ["Prepared by accounts"]}).to_excel(writer, sheet_name="Cover", index=False)
    ledger.to_excel(writer, sheet_name="Ledger", index=False)
ledger_xlsx = buffer.getvalue()


def new_app():
    at = AppTest.from_file(str(ROOT / "match_app.py"), default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def upload_both(at):
    at.file_uploader[0].upload("bank.csv", bank_csv, "text/csv")
    at.run()
    at.file_uploader[1].upload("ledger.xlsx", ledger_xlsx, XLSX_MIME)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


print("TEST: nothing happens until both files are there")
at = new_app()
assert any("Upload both files" in i.value for i in at.info) and not at.header
at.file_uploader[0].upload("bank.csv", bank_csv, "text/csv")
at.run()
assert any("Upload both files" in i.value for i in at.info)
print("PASSED")

print("TEST: both files -> the card with the columns found, the summary, the numbers, the groups")
at = upload_both(new_app())
headers = [h.value for h in at.header]
print("  ", headers)
assert "What I found" in headers and "Please check" in headers and "Possible matches to confirm" in headers and "Download" in headers
assert any(s.label == "Sheet of ledger.xlsx" and s.value == "Ledger" for s in at.selectbox)            # the sheet with the data is chosen
analysis = at.session_state["match_cache"]["analysis"]
assert analysis.summary == "226 matched (196 exact, 30 close), 10 to confirm, 21 only in A, 15 only in B, 10 amount mismatches"
metrics = {m.label: m.value for m in at.metric}
print("  ", metrics)
assert metrics == {"Matched": "226", "To confirm": "10", "Only in A": "21", "Only in B": "15", "Amount mismatches": "10"}
assert analysis.A.row_numbers[0] == 4                                                                    # two title rows + the header row, then the first record
text = " ".join(m.value for m in at.markdown)
sources = " ".join(c.value for c in at.caption)
assert "**File A · date**" in text and "'Debit'" in text and "🟢 sure" in text
assert "from header 'Date' and the values are dates" in sources and "from header 'Debit' and the values are numbers" in sources
print("PASSED")

print("TEST: Edit details - a smaller day window removes the close matches; choosing 'no reference column' removes the mismatches")
at = upload_both(new_app())
days = next(n for n in at.number_input if n.key == "match_days")
days.set_value(0)
at.run()
assert not at.exception, [e.value for e in at.exception]
tight = at.session_state["match_cache"]["analysis"]
print("  ", tight.summary)
assert len(tight.result.bucket("close")) == 0
at = upload_both(new_app())
for box in at.selectbox:
    if str(box.key).startswith("match_a_reference_") or str(box.key).startswith("match_b_reference_"):
        box.set_value("(none)")
at.run()
assert not at.exception, [e.value for e in at.exception]
no_ref = at.session_state["match_cache"]["analysis"]
print("  ", no_ref.summary)
assert len(no_ref.result.mismatches) == 0 and "set by you" not in no_ref.summary
assert any(f.label == "File A · reference / id" and f.value == "none" for f in no_ref.report.facts)
print("PASSED")

print("TEST: 'Tick all' confirms every maybe - they count as matched and the card stops asking")
at = upload_both(new_app())
next(b for b in at.button if b.key == "match_tick_all").click()
at.run()
assert not at.exception, [e.value for e in at.exception]
metrics = {m.label: m.value for m in at.metric}
print("  ", metrics)
assert metrics["Matched"] == "236" and metrics["To confirm"] == "0"
cache_key = at.session_state["match_cache"]["key"]
assert len(at.session_state["match_accepted"][cache_key]) == 10
print("PASSED")

print("TEST: a file that cannot be read is reported, not a crash")
at = new_app()
at.file_uploader[0].upload("broken.xlsx", b"this is not an excel file", XLSX_MIME)
at.run()
assert not at.exception and any("couldn't read broken.xlsx" in e.value for e in at.error)
print("PASSED")
