from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"

import io
import sys

sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest

import cleaning
import quality
import report

ROWS = (
    "Item,Quantity,Price Per Unit,Total Spent,Payment Method\n"
    + "\n".join(
        f"{item},{q},{p},{t},{pay}"
        for item, q, p, t, pay in [
            ("Cake", "2", "3.0", "6.0", "Cash"), ("Tea", "4", "1.5", "6.0", "ERROR"), ("Cake", "1", "3.0", "3.0", ""),
            ("Tea", "2", "1.5", "3.0", "Cash"), ("Cake", "5", "3.0", "15.0", "UNKNOWN"), ("Tea", "3", "1.5", "4.5", "Cash"),
            ("Cake", "2", "3.0", "6.0", "Cash"), ("Tea", "ERROR", "1.5", "7.5", "Cash"), ("ERROR", "3", "3.0", "9.0", "Cash"),
            ("Cake", "UNKNOWN", "3.0", "12.0", "Cash"), ("Tea", "5", "ERROR", "7.5", "Cash"), ("Cake", "1", "3.0", "UNKNOWN", "Cash"),
        ] * 12  # repeat so relationships have enough rows (rows differ only by position, so make them unique below)
    ) + "\n"
)
# make the rows unique so dedup does not collapse them: tweak Item with a counter suffix is not allowed (would break the lookup),
# so instead append a unique id column
lines = ROWS.strip().split("\n")
ROWS = "Order,Item,Quantity,Price Per Unit,Total Spent,Payment Method\n" + "\n".join(f"{i},{line}" for i, line in enumerate(lines[1:], start=1)) + "\n"
CSV = ROWS.encode()

raw = pd.read_csv(io.BytesIO(CSV), dtype=str, keep_default_na=False, na_values=[""])
steps = {"headers": True, "trim": True, "duplicates": True, "empty": True, "placeholders": True, "numeric": True, "fills": True}

print("TEST: before/after profiles reflect the cleaning")
before = quality.profile_dataframe(raw)
cleaned, stats = cleaning.clean_dataframe_with_steps(raw.copy(), steps, {})
after = quality.profile_dataframe(cleaned)
b, a = before.set_index("Column"), after.set_index("Column")
print(b.to_string()); print(a.to_string())
assert b.loc["Quantity", "Placeholders"] == 24 and b.loc["Quantity", "Type"] == "numeric"
assert a.loc["Quantity", "Placeholders"] == 0 and a.loc["Quantity", "Blank"] == 24     # placeholders became blanks
assert b.loc["Item", "Placeholders"] == 12 and a.loc["Item", "Blank"] == 12
assert a["Placeholders"].sum() == 0 and b["Placeholders"].sum() > 0
print("PASSED")

print("TEST: report text contains both tables, the totals line and the other sections")
quality_by_sheet = {"Data": {"before": before, "after": after}}
text = report.build_report_text("f.csv", {"Data": {"cleaned": True, "stats": stats}}, quality_by_sheet=quality_by_sheet)
print(text)
assert "Data quality - before cleaning:" in text and "Data quality - after cleaning:" in text
assert "Missing total" in text and "Placeholders" in text
missing_before = int((before["Blank"] + before["Placeholders"]).sum())
missing_after = int((after["Blank"] + after["Placeholders"]).sum())
assert f"Missing cells: {missing_before} before -> {missing_after} after" in text
assert missing_after <= missing_before                                                   # nothing got worse
# without quality data the report is unchanged
assert "Data quality" not in report.build_report_text("f.csv", {"Data": {"cleaned": True, "stats": stats}})
# sheets that weren't cleaned get no quality section
mixed = report.build_report_text("f.xlsx", {"A": {"cleaned": True, "stats": stats}, "B": {"cleaned": False, "stats": None}},
                                 quality_by_sheet={"A": quality_by_sheet["Data"]})
assert mixed.count("Data quality - before cleaning:") == 1
print("PASSED")

print("TEST: UI shows before/after tables; exact fills lower the missing count")
at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
at.run()
at.file_uploader[0].upload("c.csv", CSV, "text/csv")
at.run()
next(b_ for b_ in at.button if b_.label == "Clean my file").click()
at.run()
assert not at.exception, [e.value for e in at.exception]
assert any(s.value == "Data quality: before vs after" for s in at.subheader)
tables = [d.value for d in at.dataframe if "Missing total" in d.value.columns]
assert len(tables) == 2, len(tables)
ui_before, ui_after = tables
print(ui_before.to_string()); print(ui_after.to_string())
assert ui_before.set_index("Column").loc["Quantity", "Placeholders"] == 24
assert ui_after.set_index("Column").loc["Quantity", "Placeholders"] == 0
assert any("Missing cells (blank + placeholder):" in c.value for c in at.caption)
total_before = int((ui_before["Blank"] + ui_before["Placeholders"]).sum())
total_after = int((ui_after["Blank"] + ui_after["Placeholders"]).sum())
assert total_after < total_before, (total_before, total_after)          # Total = Quantity x Price filled some gaps
assert at.session_state["sheet_stats"]["Data"]["stats"]["fills"]["calculated"]
report_text = at.session_state["report_text"]
assert "Data quality - before cleaning:" in report_text and "Data quality - after cleaning:" in report_text
print("PASSED")

print("\nALL QUALITY REPORT TESTS PASSED")
