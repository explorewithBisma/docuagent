from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"

import sys

sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest
sys.path.insert(0, str(_Path(__file__).resolve().parent))
from clean_page_helpers import box, boxes, include_box

import cleaning
import report

print("TEST: convert_placeholders - whole-cell, case-insensitive, trimmed, counted per column")
df = pd.DataFrame({
    "Item": ["Cake", "ERROR", " unknown ", "Tea", "Error code 5", "N/A", "-", "Cake"],
    "Qty": [1, 2, 3, 4, 5, 6, 7, 8],                       # numeric column must be untouched
    "Note": ["ok", "Unknown", "UNKNOWN", np.nan, "ok", "ok", "ok", "ok"],
})
out, info = cleaning.convert_placeholders(df.copy())
print(out.to_string())
print(info)
assert out["Item"].isna().sum() == 4 and out["Item"].tolist()[4] == "Error code 5"      # partial match survives
assert info["by_column"]["Item"] == {"ERROR": 1, "UNKNOWN": 1, "N/A": 1, "-": 1}
assert info["by_column"]["Note"] == {"UNKNOWN": 2}
assert "Qty" not in info["by_column"] and out["Qty"].tolist() == list(range(1, 9))
assert info["total"] == 6
print("PASSED")

print("TEST: custom list, empty list, nothing to convert")
out, info = cleaning.convert_placeholders(df.copy(), ["error"])
assert info["total"] == 1 and out["Item"].isna().sum() == 1 and out["Item"].iloc[2] == " unknown "
out, info = cleaning.convert_placeholders(df.copy(), [])
assert info == {"total": 0, "by_column": {}} and out.equals(df)
out, info = cleaning.convert_placeholders(pd.DataFrame({"A": ["x", "y"]}))
assert info["total"] == 0
print("PASSED")

print("TEST: pipeline order - placeholders make duplicates/empty rows visible")
df = pd.DataFrame({
    "A": ["x", "x", "ERROR", "UNKNOWN", "y"],
    "B": ["ERROR", np.nan, "ERROR", "unknown", "z"],
})
steps = {"headers": True, "trim": True, "duplicates": True, "empty": True, "placeholders": True}
cleaned, stats = cleaning.clean_dataframe_with_steps(df.copy(), steps, {"placeholders": ["ERROR", "UNKNOWN"]})
print(cleaned.to_string()); print(stats["placeholders"], stats["duplicates_removed"], stats["empty_rows_removed"])
assert stats["placeholders"]["total"] == 5
assert stats["duplicates_removed"] == 1          # row1 duplicates row0 once ERROR == blank
assert stats["empty_rows_removed"] == 2          # rows 2 and 3 are all-placeholder -> empty (they are not "duplicates" of each other)
assert cleaned["A"].tolist() == ["x", "y"]
# without the step, nothing changes (and old callers that omit the key still work)
_, stats_off = cleaning.clean_dataframe_with_steps(df.copy(), {**steps, "placeholders": False})
assert stats_off["placeholders"] is None
_, stats_old = cleaning.clean_dataframe_with_steps(df.copy(), {"headers": True, "trim": True, "duplicates": True, "empty": True})
assert "placeholders" not in stats_old
print("PASSED")

print("TEST: report lines")
sheet_stats = {"Data": {"cleaned": True, "stats": stats}}
text = report.build_report_text("f.csv", sheet_stats)
print(text)
assert "Placeholders converted to missing: 5" in text and "A: ERROR 1, UNKNOWN 1" in text
text_off = report.build_report_text("f.csv", {"Data": {"cleaned": True, "stats": stats_off}})
assert "Placeholders converted to missing: skipped (step turned off)" in text_off
assert "Placeholders" not in report.build_report_text("f.csv", {"Data": {"cleaned": True, "stats": stats_old}})
print("PASSED")

print("TEST: UI - checkbox + editable list")
CSV = (b"Item,Quantity,Price\nCake,2,3.0\nERROR,UNKNOWN,4.0\nTea,,1.5\nUNKNOWN,3,ERROR\nCake,2,3.0\n")


def new_app():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    at.file_uploader[0].upload("c.csv", CSV, "text/csv")
    at.run()
    return at


def clean(at):
    next(b for b in at.button if b.label == "Clean my file").click()
    at.run()
    assert not at.exception, [e.value for e in at.exception]


at = new_app()
clean(at)
data = at.session_state["cleaned_sheets"]["Data"]
print(data.to_string())
assert data["Item"].isna().sum() == 2 and "ERROR" not in data.to_string() and "UNKNOWN" not in data.to_string()
assert "Placeholders converted to missing: 4" in at.session_state["report_text"], at.session_state["report_text"]
assert at.session_state["placeholder_list"][:2] == ["ERROR", "UNKNOWN"]

at = new_app()
next(t for t in at.text_area if t.label.startswith("Placeholder values")).set_value("ERROR")
at.run()
clean(at)
data = at.session_state["cleaned_sheets"]["Data"]
assert data["Item"].tolist()[2] == "UNKNOWN" or "UNKNOWN" in data["Item"].tolist()      # UNKNOWN is no longer a placeholder
assert at.session_state["placeholder_list"] == ["ERROR"]

at = new_app()
box(at, "placeholders").uncheck()
at.run()
clean(at)
assert "ERROR" in at.session_state["cleaned_sheets"]["Data"]["Item"].tolist()
assert "Placeholders converted to missing: skipped" in at.session_state["report_text"]
print("PASSED")

print("\nALL PLACEHOLDER TESTS PASSED")
