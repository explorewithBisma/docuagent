from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"

import io
import sys

sys.path.insert(0, str(ROOT))

import numpy as np
import openpyxl
import pandas as pd
from streamlit.testing.v1 import AppTest

import changes as ch
import cleaning
import report
from io_utils import load_sheets
from xlsx_export import export_workbook

ALL = {"headers": True, "trim": True, "duplicates": True, "empty": True, "placeholders": True, "numeric": True, "fills": False}


def run(frame, steps=ALL):
    log = ch.ChangeLog()
    cleaned, stats = cleaning.clean_dataframe_with_steps(frame.copy(), steps, {"changes": log})
    return cleaned, stats, log


print("TEST: removed rows are recorded with original row number, reason, and ORIGINAL content")
frame = pd.DataFrame({
    " Name ": [" Ada ", "Bob", np.nan, "Ada", "ERROR", "Cy", "  Ada  ", "   ", "Bob"],
    "Score": ["1", "2", np.nan, "1", "UNKNOWN", "3", "1", np.nan, "2"],
})
# rows (Excel row = index + 2): 0:' Ada ',1  1:Bob,2  2:empty  3:Ada,1  4:ERROR/UNKNOWN  5:Cy,3  6:'  Ada  ',1  7:'   '/blank  8:Bob,2
cleaned, stats, log = run(frame)
removed = log.finalize_removed()
print([(r["original_row"], r["reason"]) for r in removed])
by_row = {r["original_row"]: r for r in removed}
assert set(by_row) == {4, 5, 6, 8, 9, 10}, set(by_row)
assert by_row[4]["reason"] == "Fully empty row" and by_row[6]["reason"] == "Fully empty row"           # blank row and the ERROR/UNKNOWN row
assert by_row[9]["reason"] == "Fully empty row" and by_row[5]["reason"] == "Duplicate of row 2"
print(sorted((r["original_row"], r["kind"], r["reason"]) for r in removed))
dup_rows = {r["original_row"]: r["reason"] for r in removed if r["kind"] == "duplicate"}
assert dup_rows == {5: "Duplicate of row 2", 8: "Duplicate of row 2", 10: "Duplicate of row 3"}, dup_rows        # all point at the FIRST kept row
assert by_row[8]["values"] == ["  Ada  ", "1"]                      # untouched original text, spaces included
assert by_row[6]["values"] == ["ERROR", "UNKNOWN"]                   # the placeholder-only row shows what it really said
assert log.original_columns == [" Name ", "Score"]                   # headers as uploaded
assert stats["duplicates_removed"] == 3 and stats["empty_rows_removed"] == 3
assert list(cleaned["Name"]) == ["Ada", "Bob", "Cy"] and list(cleaned.index) == [0, 1, 5]
print("PASSED")

print("TEST: nothing removed -> nothing recorded; steps switched off -> nothing recorded")
clean_frame = pd.DataFrame({"a": ["x", "y"], "b": ["1", "2"]})
_, _, log0 = run(clean_frame)
assert log0.removed == [] and log0.finalize_removed() == []
_, _, log1 = run(frame, {**ALL, "duplicates": False, "empty": False})
assert log1.removed == []
_, _, log2 = run(frame, {**ALL, "empty": False})
assert {r["reason"] for r in log2.removed} == {"duplicate"}
print("PASSED")

print("TEST: a row-removing run still records exactly as many removals as the stats report (random data)")
rng = np.random.default_rng(3)
big = pd.DataFrame({"k": rng.integers(0, 40, 400).astype(str), "v": rng.integers(0, 3, 400).astype(str)})
big.loc[rng.random(400) < 0.1, :] = np.nan
cleaned, stats, log = run(big)
kinds = pd.Series([r["kind"] for r in log.finalize_removed()]).value_counts().to_dict()
assert kinds.get("duplicate", 0) == stats["duplicates_removed"] and kinds.get("empty", 0) == stats["empty_rows_removed"]
assert len(big) - len(cleaned) == sum(kinds.values())
for r in log.finalize_removed():                                     # every "duplicate of row N" really is an identical, kept row
    if r["kind"] == "duplicate":
        kept = int(r["reason"].split()[-1]) - 2
        assert kept in cleaned.index and list(big.loc[kept].fillna("<NA>")) == [("<NA>" if pd.isna(v) else v) for v in r["values"]]
print("PASSED")

print("TEST: the Excel gets a 'Removed Rows' sheet AFTER the summary and change log, with the full original rows")
log = ch.ChangeLog()
cleaned, stats = cleaning.clean_dataframe_with_steps(frame.copy(), ALL, {"changes": log})
from quality import profile_dataframe
qb = {"Data": profile_dataframe(frame)}
data = export_workbook({"Data": cleaned}, {"Data": log}, {"Data": {"cleaned": True, "stats": stats}}, qb, [], "rows.csv", {"Data": len(frame)}, True)
wb = openpyxl.load_workbook(io.BytesIO(data))
print(wb.sheetnames)
assert wb.sheetnames == ["Data", "Cleaning Summary", "Change Log", "Removed Rows"]
ws = wb["Removed Rows"]
assert [c.value for c in ws[1]] == ["Original row", "Reason", " Name ", "Score"]
assert ws.max_row == 1 + 6 and ws.freeze_panes == "C2" and ws.auto_filter.ref == "A1:D7" and all(c.font.bold for c in ws[1])
got = {ws.cell(row=r, column=1).value: [ws.cell(row=r, column=c).value for c in range(2, 5)] for r in range(2, ws.max_row + 1)}
assert got[8] == ["Duplicate of row 2", "  Ada  ", "1"] and got[6] == ["Fully empty row", "ERROR", "UNKNOWN"] and got[4] == ["Fully empty row", None, None]
summary_text = [str(c.value) for row in wb["Cleaning Summary"].iter_rows() for c in row if c.value not in (None, "")]
assert "Full content of removed rows" in summary_text and any("Removed Rows" in s and "6 rows" in s for s in summary_text), summary_text
# without the report checkbox: no extra sheets at all
assert openpyxl.load_workbook(io.BytesIO(export_workbook({"Data": cleaned}, {"Data": log}, {"Data": {"cleaned": True, "stats": stats}}, qb, [], "r.csv", {"Data": len(frame)}, False))).sheetnames == ["Data"]
# nothing removed -> no Removed Rows sheet
log_c = ch.ChangeLog()
c2, s2 = cleaning.clean_dataframe_with_steps(clean_frame.copy(), ALL, {"changes": log_c})
wb2 = openpyxl.load_workbook(io.BytesIO(export_workbook({"Data": c2}, {"Data": log_c}, {"Data": {"cleaned": True, "stats": s2}}, {"Data": profile_dataframe(clean_frame)}, [], "r.csv", {"Data": 2}, True)))
assert "Removed Rows" not in wb2.sheetnames
print("PASSED")

print("TEST: the user's own sheets are not touched by the removed rows being listed")
data_ws = wb["Data"]
assert data_ws.max_row == 1 + len(cleaned) and [data_ws.cell(row=r, column=1).value for r in range(2, 5)] == ["Ada", "Bob", "Cy"]
print("PASSED")

print("TEST: cap on the Removed Rows sheet is announced")
import xlsx_export as xe
orig = xe.MAX_LOG_ROWS
xe.MAX_LOG_ROWS = 2
try:
    capped = openpyxl.load_workbook(io.BytesIO(export_workbook({"Data": cleaned}, {"Data": log}, {"Data": {"cleaned": True, "stats": stats}}, qb, [], "r.csv", {"Data": len(frame)}, True)))
finally:
    xe.MAX_LOG_ROWS = orig
assert capped["Removed Rows"].max_row == 3
assert any("shows the first 2 of 6 removed rows" in str(c.value) for row in capped["Cleaning Summary"].iter_rows() for c in row if c.value)
print("PASSED")

print("TEST: multi-sheet - one 'Removed - <sheet>' sheet per sheet that lost rows")
book = load_sheets(io.BytesIO((FIXTURES / "messy_multi_sheet.xlsx").read_bytes()), is_csv=False)
logs, cleaned_sheets, stats_by = {}, {}, {}
for name, df in book.items():
    logs[name] = ch.ChangeLog()
    cleaned_sheets[name], st_ = cleaning.clean_dataframe_with_steps(df.copy(), ALL, {"changes": logs[name]})
    stats_by[name] = {"cleaned": True, "stats": st_}
mdata = export_workbook(cleaned_sheets, logs, stats_by, {n: profile_dataframe(d) for n, d in book.items()}, [], "m.xlsx", {n: len(d) for n, d in book.items()}, True)
mwb = openpyxl.load_workbook(io.BytesIO(mdata))
print(mwb.sheetnames)
assert mwb.sheetnames[:3] == ["Customers", "Orders", "Notes"] and "Removed - Customers" in mwb.sheetnames and "Removed - Orders" in mwb.sheetnames
assert "Removed - Notes" not in mwb.sheetnames                       # Notes lost no rows
print("PASSED")

print("TEST: the .txt report names the removed rows")
text = report.build_report_text("rows.csv", {"Data": {"cleaned": True, "stats": stats}}, removed_by_sheet={"Data": log.finalize_removed()})
print(text)
assert "Removed duplicate rows (row numbers in your file): 5, 8, 10" in text and "Removed fully empty rows (row numbers in your file): 4, 6, 9" in text
assert "'Removed Rows' sheet" in text
print("PASSED")

print("TEST: through the app - table in the Changes tab, line in the report, sheet in the workbook")
at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
at.run()
at.file_uploader[0].upload("rows.csv", frame.to_csv(index=False).encode(), "text/csv")
at.run()
next(b for b in at.button if b.label == "Clean my file").click()
at.run()
assert not at.exception, [e.value for e in at.exception]
tables = [d.value for d in at.dataframe if list(d.value.columns)[:2] == ["Original row", "Reason"]]
assert len(tables) == 1 and sorted(tables[0]["Original row"]) == [4, 5, 6, 8, 9, 10]
assert any(s.value == "Removed rows" for s in at.subheader)
assert "Removed duplicate rows (row numbers in your file): 5, 8, 10" in at.session_state["report_text"]
s = at.session_state
built = openpyxl.load_workbook(io.BytesIO(export_workbook(s["cleaned_sheets"], s["change_logs"], s["sheet_stats"], s["quality_before"], s["placeholder_list"], s["original_filename"], s["original_rows"], True)))
assert built.sheetnames[-1] == "Removed Rows"
print("PASSED")

print("TEST: the real retail file - 110 removed rows, every one checked against the file")
retail = _Path.home() / "Downloads" / "archive" / "retail_sales_dataset.csv"
if retail.exists():
    original = load_sheets(io.BytesIO(retail.read_bytes()), is_csv=True)["Data"]
    cleaned, stats, log = run(original)
    removed = log.finalize_removed()
    kinds = pd.Series([r["kind"] for r in removed]).value_counts().to_dict()
    print(kinds, "| first duplicates:", [(r["original_row"], r["reason"]) for r in removed if r["kind"] == "duplicate"][:3])
    assert kinds == {"duplicate": 80, "empty": 30} and len(removed) == 110
    for r in removed:
        assert [None if pd.isna(v) else v for v in r["values"]] == [None if pd.isna(v) else v for v in original.iloc[r["original_row"] - 2].tolist()]
        if r["kind"] == "duplicate":
            kept = int(r["reason"].split()[-1]) - 2
            assert kept in cleaned.index and kept < r["original_row"] - 2   # an EARLIER row that was kept
            assert original.iloc[kept].fillna("").tolist() == original.iloc[r["original_row"] - 2].fillna("").tolist()
        else:
            assert original.iloc[r["original_row"] - 2].isna().all()
    print("PASSED")
else:
    print("skipped")

print("\nALL REMOVED ROW TESTS PASSED")
