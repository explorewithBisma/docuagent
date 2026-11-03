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
sys.path.insert(0, str(_Path(__file__).resolve().parent))
from clean_page_helpers import box, boxes, include_box

import changes as ch
import cleaning
import flags as fl
import report
from io_utils import load_sheets
from quality import profile_dataframe
from xlsx_export import export_workbook

ALL = {"headers": True, "trim": True, "duplicates": True, "empty": True, "placeholders": True, "numeric": True, "fills": False}
rng = np.random.default_rng(4)


def by_column(result):
    return {(f["column"], f["row"]): f["reasons"] for f in result["flags"]}


print("TEST: negative values are flagged only in columns that should not be negative")
df = pd.DataFrame({
    "age": [30, 45, -7, 52] * 10, "quantity": [1, 2, 3, -1] * 10, "days_to_ship": [2, 3, -1, 4] * 10,
    "unit_price": [10.5, 20.0, -3.0, 15.0] * 10, "profit": [5.0, -20.0, 10.0, -1.0] * 10,        # profit may legitimately be negative
    "temperature": [10, -5, 3, 4] * 10,
})
result = fl.find_flags(df)
flagged = by_column(result)
assert ("age", 4) in flagged and "age -7 is negative" in flagged[("age", 4)][0]                  # row = position + 2
assert any("quantity -1 is negative (-1 is often used as a placeholder code)" in r for r in flagged[("quantity", 5)])
assert ("days_to_ship", 4) in flagged and ("unit_price", 4) in flagged
assert not any(column in ("profit", "temperature") for column, _ in flagged), "profit / temperature may be negative"
print("PASSED")

print("TEST: age must be 10-100 (the limits themselves are fine)")
ages = pd.DataFrame({"age": [10, 100, 9, 101, 0, 150, 999, 35, 36, 37] + [40] * 25})
flagged = by_column(fl.find_flags(ages))
# positions 2..6 hold 9, 101, 0, 150, 999 -> Excel rows 4..8; 10 and 100 (rows 2, 3) are plausible
assert sorted(row for (_, row) in flagged) == [4, 5, 6, 7, 8], flagged
assert ("age", 2) not in flagged and ("age", 3) not in flagged
assert flagged[("age", 4)] == ["age 9 looks invalid (expected 10-100)"]
assert "age 999 looks invalid (expected 10-100)" in flagged[("age", 8)]
print("PASSED")

print("TEST: sentinel codes (999, 9999, -999 ...) are flagged when RARE; a frequent value is just a value")
s = pd.DataFrame({"score": list(rng.integers(1, 50, 200)) + [999, 9999, -999], "price_x": list(rng.integers(1, 50, 203)),
                  "ratio": list(rng.random(203))})
s.loc[0, "ratio"] = 999.37                                                                              # not a whole number: not a code
result = fl.find_flags(s)
flagged = by_column(result)
assert ("score", 202) in flagged and any("999 looks like a placeholder code" in r for r in flagged[("score", 202)])
assert any("9999 looks like a placeholder code" in r for r in flagged[("score", 203)])
assert not any(c == "ratio" and "placeholder" in " ".join(r) for (c, _), r in flagged.items())
common = pd.DataFrame({"x": [999] * 20 + list(rng.integers(1, 5, 80))})                                # 20% are 999 -> a real value, not a code
assert not any("placeholder code" in " ".join(r) for r in by_column(fl.find_flags(common)).values())
print("PASSED")

print("TEST: statistical outliers (beyond 3x IQR) are flagged; wide-ranging columns are skipped and reported")
tight = pd.DataFrame({"measure": list(rng.normal(100, 5, 300)) + [400.0, -200.0]})
result = fl.find_flags(tight)
flagged = by_column(result)
assert ("measure", 302) in flagged and ("measure", 303) in flagged and len(flagged) == 2, flagged
assert "is far outside the usual range" in flagged[("measure", 302)][0]
assert result["skipped_statistical"] == {}
wide = pd.DataFrame({"sales": np.concatenate([rng.uniform(100, 1000, 220), rng.uniform(50000, 90000, 30)])})   # 12% huge: naturally wide
result = fl.find_flags(wide)
assert result["flags"] == [] and list(result["skipped_statistical"]) == ["sales"] and result["skipped_statistical"]["sales"] > 0.05
few = pd.DataFrame({"v": [1.0, 2.0, 3.0, 1000.0]})                                                       # too few values to judge a "usual range"
assert fl.find_flags(few)["flags"] == []
flat = pd.DataFrame({"v": [5.0] * 100})                                                                  # no spread at all
assert fl.find_flags(flat)["flags"] == []
print("PASSED")

print("TEST: flagging never changes the data; ID-like and text columns are ignored; text numbers are understood")
frame = pd.DataFrame({"age": ["30", "-7", "999", "ERROR", "", "41"] * 8, "customer_id": [-5, 999, 1, 2, 3, 4] * 8, "name": ["Ada"] * 48})
before = frame.copy()
result = fl.find_flags(frame)
pd.testing.assert_frame_equal(frame, before)                                                             # untouched, even the raw text
assert {f["column"] for f in result["flags"]} == {"age"}
assert sorted({f["row"] for f in result["flags"]}) == sorted({i + 2 for i in range(48) if i % 6 in (1, 2)})
assert fl.flag_counts(result) == {"age": 16}
print("PASSED")

print("TEST: flags become change-log style records; merging keeps a cell's change before its flag")
log = ch.ChangeLog()
frame = pd.DataFrame({"qty": ["5", "-1", "ERROR", "3"] * 10, "price": ["2.5", "2.5", "x", "2.5"] * 10})
cleaned, _ = cleaning.clean_dataframe_with_steps(frame.copy(), {**ALL, "duplicates": False}, {"changes": log})
records = log.finalize(cleaned)
result = fl.find_flags(cleaned)
merged = fl.merge_flag_records(records, result, list(cleaned.columns))
kinds = [(r["position"], r["column"], r["reason"]) for r in merged if r["column"] == "qty" and r["position"] in (1, 2)]
print(kinds)
assert (1, "qty", "flagged") in kinds and (2, "qty", "placeholder") in kinds
flag_record = next(r for r in merged if r["reason"] == "flagged")
assert flag_record["old"] == flag_record["new"] and ch.reason_text(flag_record).startswith("Flagged (not changed): qty -1 is negative")
assert ch.comment_text(flag_record).startswith("Flagged: qty -1 is negative")
assert ch.summarize(merged)["flagged"] == 10 and "flagged" in ch.REASON_ORDER
print("PASSED")

print("TEST: the highlighted Excel - border + light red fill + comment, change colours kept, Change Log rows, quality column, off switch")
raw = pd.DataFrame({
    "order_id": [f"O{i}" for i in range(40)],
    "age": ["30", "-7", "999", "ERROR", "35", "8", "41", "29"] * 5,
    "quantity": ["2", "3", "-1", "4", "5", "2", "3", "4"] * 5,
    "gender": ["m", "F", "male", "Other", "f", "M", "female", "Other"] * 5,
})
raw["order_id"] = [f"O{i}" for i in range(40)]
log = ch.ChangeLog()
cleaned, stats = cleaning.clean_dataframe_with_steps(raw.copy(), ALL, {"changes": log})
import rule_engine
map_rule = {"id": "m", "column": "gender", "rule_type": "map_values", "params": {"mapping": {"m": "Male", "male": "Male", "f": "Female", "female": "Female"}},
            "description": "Standardize gender."}
cleaned, _ = rule_engine.apply_rule(cleaned, map_rule, log=log)
args = ({"Data": cleaned}, {"Data": log}, {"Data": {"cleaned": True, "stats": stats}}, {"Data": profile_dataframe(raw)}, [], "x.csv", {"Data": 40})
fb = {"Data": fl.flag_counts(fl.find_flags(raw))}
wb = openpyxl.load_workbook(io.BytesIO(export_workbook(*args, True, True, fb)))
ws = wb["Data"]
cols = {ws.cell(row=1, column=c).value: c for c in range(1, ws.max_column + 1)}
negative_age = ws.cell(row=3, column=cols["age"])
assert negative_age.value == -7                                                                       # value untouched
assert negative_age.border.left.style == "medium" and (negative_age.border.left.color.rgb or "")[-6:] == "E26B0A"
assert (negative_age.fill.fgColor.rgb or "")[-6:] == "F8D7DA"                                           # light red where nothing else is colored
assert negative_age.comment.text == "Flagged: age -7 is negative; age -7 looks invalid (expected 10-100)"
sentinel_age = ws.cell(row=4, column=cols["age"])
assert sentinel_age.value == 999 and sentinel_age.border.left.style == "medium"
assert ws.cell(row=5, column=cols["age"]).value is None                                                 # ERROR was cleared ...
assert ws.cell(row=5, column=cols["age"]).border.left.style == "thin"                                   # ... and a blank is not "flagged"
young = ws.cell(row=7, column=cols["age"])
assert young.value == 8 and "age 8 looks invalid" in young.comment.text
# a cell that is BOTH changed and flagged keeps its change color and gets both comment lines
qty = ws.cell(row=4, column=cols["quantity"])
assert qty.value == -1 and qty.border.left.style == "medium"
gender_changed = ws.cell(row=2, column=cols["gender"])
assert (gender_changed.fill.fgColor.rgb or "")[-6:] == "BDD7EE" and gender_changed.border.left.style == "thin"       # a plain AI change: no flag border
log_ws = wb["Change Log"]
flag_rows = [r for r in range(2, log_ws.max_row + 1) if str(log_ws.cell(row=r, column=5).value).startswith("Flagged (not changed)")]
assert flag_rows and all(log_ws.cell(row=r, column=3).value == log_ws.cell(row=r, column=4).value for r in flag_rows)       # old == new
summary = wb["Cleaning Summary"]
texts = {str(c.value): c.row for row in summary.iter_rows() for c in row if c.value not in (None, "") and c.column == 2}
flag_summary_row = texts[ch.FLAG_MEANING]
assert summary.cell(row=flag_summary_row, column=3).value == len(flag_rows)
assert (summary.cell(row=flag_summary_row, column=1).fill.fgColor.rgb or "")[-6:] == "F8D7DA"
total_changed = next(c for row in summary.iter_rows() for c in row if c.value == "Total cells changed")
n_changes = sum(1 for r in range(2, log_ws.max_row + 1) if not str(log_ws.cell(row=r, column=5).value).startswith("Flagged"))
assert summary.cell(row=total_changed.row, column=3).value == n_changes                                  # flagged cells are NOT "changes"
header = [c.value for c in summary[next(r for r in range(1, summary.max_row) if summary.cell(row=r, column=1).value == "Column")]]
assert "Flagged (not changed)" in header
off = openpyxl.load_workbook(io.BytesIO(export_workbook(*args, True, False)))                            # flagging OFF
assert off["Data"].cell(row=3, column=cols["age"]).comment is None and off["Data"].cell(row=3, column=cols["age"]).border.left.style == "thin"
assert not any(str(c.value).startswith("Flagged") for row in off["Change Log"].iter_rows() for c in row if c.value)
print("PASSED")

print("TEST: the data in the Excel equals the cleaned data - flagged values included")
assert [ws.cell(row=r, column=cols["age"]).value for r in (3, 4, 7)] == [-7, 999, 8]
print("PASSED")

print("TEST: through the app - checkbox on by default, Data Quality 'Flagged' column, red-border preview, report line, off switch")
csv = raw.to_csv(index=False).encode()
def new_app():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
    at.run()
    at.file_uploader[0].upload("f.csv", csv, "text/csv")
    at.run()
    return at
at = new_app()
flag_box = box(at, "flags")
assert flag_box.value is True and "unusual value" in flag_box.label
next(b for b in at.button if b.label == "Clean my file").click()
at.run()
assert not at.exception, [e.value for e in at.exception]
tables = [d.value for d in at.dataframe if "Flagged" in d.value.columns]
assert len(tables) == 2, "before and after quality tables both show the Flagged column"
after = tables[1].set_index("Column")
print(after[["Flagged"]].T.to_string())
assert after.loc["age", "Flagged"] == 15 and after.loc["quantity", "Flagged"] == 5 and after.loc["gender", "Flagged"] == 0
assert "Flagged values (not changed, just flagged for a look)" in at.session_state["report_text"]
change_tables = [d.value for d in at.dataframe if "Old value" in d.value.columns]
assert change_tables and change_tables[0]["Reason"].str.startswith("Flagged (not changed)").any()
styler, _, _ = report.styled_preview(at.session_state["cleaned_sheets"]["Data"], at.session_state["records_cache"]["records"]["Data"])
assert "border: 2px solid #E26B0A" in styler.to_html()
assert "Red border: flagged" in report.legend_html()
at2 = new_app()
box(at2, "flags").uncheck()
at2.run()
next(b for b in at2.button if b.label == "Clean my file").click()
at2.run()
assert not at2.exception, [e.value for e in at2.exception]
assert not [d for d in at2.dataframe if "Flagged" in d.value.columns]
assert "Flagged values" not in at2.session_state["report_text"]
assert not any(r["reason"] == "flagged" for r in at2.session_state["records_cache"]["records"]["Data"])
print("PASSED")

print("TEST: quality-table helpers")
prof = profile_dataframe(pd.DataFrame({"a": [1, 2], "b": ["x", "y"]}))
assert "Flagged" not in report.with_missing_total(prof).columns
shown = report.with_missing_total(prof, {"a": 3})
assert shown.set_index("Column")["Flagged"].to_dict() == {"a": 3, "b": 0} and list(shown.columns).index("Flagged") == list(shown.columns).index("Unique") - 1
print("PASSED")

print("TEST: real retail file (skipped if absent)")
retail = _Path.home() / "Downloads" / "archive" / "retail_sales_dataset.csv"
if retail.exists():
    original = load_sheets(io.BytesIO(retail.read_bytes()), is_csv=True)["Data"]
    cleaned, _ = cleaning.clean_dataframe_with_steps(original.copy(), ALL)
    result = fl.find_flags(cleaned)
    counts = fl.flag_counts(result)
    print(counts, "| skipped:", {k: f"{v:.1%}" for k, v in result["skipped_statistical"].items()})
    # age: 13 negative + ages 0-9 + 150/999; quantity: -1 and 999; days_to_ship: -1, 45, 100; shipping_cost: 2 negatives
    assert counts == {"age": 96, "days_to_ship": 11, "quantity": 14, "shipping_cost": 2}, counts
    assert set(result["skipped_statistical"]) == {"unit_price", "sales_amount", "profit"}
    pd.testing.assert_frame_equal(cleaned, cleaning.clean_dataframe_with_steps(original.copy(), ALL)[0])      # flagging touched nothing
    print("PASSED")
else:
    print("skipped")

print("\nALL FLAG TESTS PASSED")
