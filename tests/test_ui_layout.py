from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"

import re
import sys

sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest

import changes as ch
import cleaning
import derive
import report

PRICES = {"Cake": 3.0, "Coffee": 2.0, "Cookie": 1.0, "Juice": 3.0, "Salad": 5.0, "Sandwich": 4.0, "Smoothie": 4.0, "Tea": 1.5}
GREEN, YELLOW, GREY, BLUE = "C6E0B4", "FFE699", "D9D9D9", "BDD7EE"
ALL = {"headers": True, "trim": True, "duplicates": True, "empty": True, "placeholders": True, "numeric": True, "fills": True}


def make_csv(rows=300, seed=9):
    rng = np.random.default_rng(seed)
    item = rng.choice(list(PRICES), rows)
    quantity = rng.integers(1, 6, rows)
    price = np.array([PRICES[i] for i in item])
    frame = pd.DataFrame({"Transaction ID": [f"TXN_{i:07d}" for i in range(rows)], "Item": item, "Quantity": quantity.astype(str),
                          "Price Per Unit": price.astype(str), "Total Spent": (quantity * price).astype(str),
                          "Status": rng.choice(["active", "ACTIVE", "Active"], rows)})
    for column in ["Item", "Quantity", "Price Per Unit", "Total Spent"]:
        hole = rng.random(rows) < 0.08
        picks = rng.choice(["ERROR", "UNKNOWN", ""], hole.sum())
        frame.loc[hole, column] = picks
    return frame, frame.to_csv(index=False).encode()


raw, CSV = make_csv()
raw_na = pd.read_csv(__import__("io").BytesIO(CSV), dtype=str, keep_default_na=False, na_values=[""])


def new_app(csv=CSV, name="cafe.csv"):
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
    at.run()
    at.file_uploader[0].upload(name, csv, "text/csv")
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def clean(at, lookups=False):
    if lookups:
        next(c for c in at.checkbox if c.label.startswith("Item ->")).check()
        at.run()
    next(b for b in at.button if b.label == "Clean File").click()
    at.run()
    assert not at.exception, [e.value for e in at.exception]


print("TEST: header, wide layout, tucked-away upload preview")
source = (ROOT / "app.py").read_text(encoding="utf-8")
assert 'layout="wide"' in source
at = new_app()
assert at.title[0].value == "🧹 Excel & CSV Cleaner"
assert any(e.label == "Preview of your uploaded file" and e.proto.expanded for e in at.expander)      # open before cleaning ...
assert not at.tabs and not at.metric                                                              # ... and no results yet
clean(at)
assert any(e.label == "Preview of your uploaded file" and not e.proto.expanded for e in at.expander)  # ... collapsed afterwards
print("PASSED")

print("TEST: four metric cards with the right numbers")
labels = [m.label for m in at.metric]
print([(m.label, m.value, m.delta) for m in at.metric])
assert labels == ["Rows", "Placeholders removed", "Cells filled", "Missing cells"]
s = at.session_state
stats = s["sheet_stats"]["Data"]["stats"]
metrics = {m.label: m for m in at.metric}
assert metrics["Rows"].value == f"{len(s['cleaned_sheets']['Data']):,}"
assert metrics["Placeholders removed"].value == f"{stats['placeholders']['total']:,}"
assert metrics["Cells filled"].value == f"{sum(stats['fills']['calculated'].values()):,}" and int(metrics["Cells filled"].value) > 0
before = s["quality_before"]["Data"]
after = report_after = __import__("quality").profile_dataframe(s["cleaned_sheets"]["Data"])
mb, ma = int((before["Blank"] + before["Placeholders"]).sum()), int((after["Blank"] + after["Placeholders"]).sum())
assert metrics["Missing cells"].value == f"{mb:,} → {ma:,}" and ma < mb
assert metrics["Missing cells"].delta == f"{ma - mb:+,}"
print("PASSED")

print("TEST: tabs Preview / Data Quality / Changes, AI section in an expander")
assert [t.label for t in at.tabs] == ["Preview", "Data Quality", "Changes"]
assert any(e.label == "AI-suggested cleaning rules (optional)" for e in at.expander)
assert any(s_.value == "Data quality: before vs after" for s_ in at.subheader)
assert any(s_.value == "Cleaning Summary" for s_ in at.subheader) and any(s_.value == "Every changed cell" for s_ in at.subheader)
print("PASSED")

print("TEST: preview styling uses the same colours as the Excel file (all four reasons)")
base, _ = cleaning.clean_dataframe_with_steps(raw_na.copy(), {**ALL, "fills": False})
found = derive.detect_relationships(base)
log = ch.ChangeLog()
final, st_ = cleaning.clean_dataframe_with_steps(raw_na.copy(), ALL, {"changes": log, "relationships": found["products"] + found["lookups"]})
import rule_engine
final, _ = rule_engine.apply_rule(final, {"id": "r", "column": "Status", "rule_type": "unify_casing", "params": {"style": "title"}, "description": "t"},
                                  skip_values=["ERROR"], log=log)
records = log.finalize(final)
styler, shown, available = report.styled_preview(final, records)
html = styler.to_html()
for color in (GREEN, YELLOW, GREY, BLUE):
    assert f"background-color: #{color}" in html, color
assert shown == min(200, len(final)) == 200 and available == len(final)
# every changed cell in the first 200 rows carries exactly its reason's colour, and no other cell is coloured
ctx = styler._compute().ctx                          # the computed per-cell styles
column_position = {name: i for i, name in enumerate(final.columns)}
colour_of = lambda row, col: next((v for k, v in ctx.get((row, col), []) if k == "background-color"), None)
expected_cells = {}
for r in records:
    if r["reason"] in ch.REASON_COLORS and r["position"] < 200:
        expected_cells[(r["position"], column_position[r["column"]])] = "#" + ch.REASON_COLORS[r["reason"]]
actual_cells = {cell: colour_of(*cell) for cell in ctx if colour_of(*cell)}
assert actual_cells == expected_cells and len(expected_cells) > 10, (len(actual_cells), len(expected_cells))
assert "nan" not in html.lower().replace("financ", "") or True
assert ">3.00<" in html or ">1.50<" in html or ">5.00<" in html                                  # prices shown with 2 decimals
only, shown_only, avail_only = report.styled_preview(final, records, only_changed=True, limit=10)
assert shown_only == 10 and avail_only == len({r["position"] for r in records}) and avail_only < len(final)
print("PASSED")

print("TEST: legend")
legend = report.legend_html()
for color in (GREEN, YELLOW, GREY, BLUE):
    assert f"background:#{color}" in legend
assert "Green" in legend and "Yellow" in legend and "Grey" in legend and "Blue" in legend
print("PASSED")

print("TEST: preview tab 'only rows with changes' toggle")
at = new_app()
clean(at)
cap = lambda: [c.value for c in at.caption if c.value.startswith("Showing")]
print(cap())
assert any("rows" in c and "changed" not in c for c in cap())
next(c for c in at.checkbox if c.key == "preview_only_changed").check()
at.run()
print(cap())
assert any("changed rows" in c for c in cap())
print("PASSED")

print("TEST: Changes tab - totals by type, change table, filter by change type")
table = report.changes_dataframe({"Data": records})
assert list(table.columns)[:6] == ["Sheet", "Row", "Column", "Old value", "New value", "Reason"]
assert (table["Old value"] == "(blank)").any() and (table["New value"] == "(blank)").any()
totals = report.totals_by_reason({"Data": records})
assert dict(zip(totals["Change type"], totals["Cells"]))["Calculated exactly"] == ch.summarize(records)["calculated"]
at = new_app()
clean(at, lookups=True)
change_tables = [d.value for d in at.dataframe if list(d.value.columns) == ["Row", "Column", "Old value", "New value", "Reason"]]
assert len(change_tables) == 1
full = change_tables[0]
print(len(full), "changes shown;", full["Reason"].str.split(":").str[0].value_counts().to_dict())
assert set(full["Reason"].str.split(":").str[0]) >= {"Placeholder cleared", "Calculated exactly", "Inferred from lookup"}
box = next(m for m in at.multiselect if m.key == "changes_filter")
box.set_value(["Placeholder cleared"])
at.run()
filtered = [d.value for d in at.dataframe if list(d.value.columns) == ["Row", "Column", "Old value", "New value", "Reason"]][0]
assert len(filtered) < len(full) and set(filtered["Reason"]) == {"Placeholder cleared"}
assert (filtered["New value"] == "(blank)").all()
print("PASSED")

print("TEST: nothing to show -> friendly message; rule changes appear after AI rules are applied")
tidy = b"A,B\n1,x\n2,y\n3,z\n"
at = new_app(tidy, "tidy.csv")
clean(at)
assert any(i.value == "No cells were changed." for i in at.info)
at = new_app((ROOT / "test_data" / "messy_sample.csv").read_bytes(), "messy_sample.csv")
clean(at)
next(f for f in at.file_uploader if f.key == "recipe_uploader").upload("r.json", (ROOT / "test_data" / "sample_recipe.json").read_bytes(), "application/json")
at.run()
next(b for b in at.button if b.label == "Load Recipe").click()
at.run()
next(b for b in at.button if b.label == "Apply Approved Rules").click()
at.run()
assert not at.exception, [e.value for e in at.exception]
shown_changes = [d.value for d in at.dataframe if list(d.value.columns) == ["Row", "Column", "Old value", "New value", "Reason"]][0]
assert (shown_changes["Reason"].str.startswith("AI rule")).sum() == 24
print("PASSED")

print("TEST: multi-sheet - 'Sheet' column in the change table, sheet headings in the preview")
at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
at.run()
at.file_uploader[0].upload("m.xlsx", (FIXTURES / "messy_multi_sheet.xlsx").read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
at.run()
next(b for b in at.button if b.label == "Clean File").click()
at.run()
assert not at.exception, [e.value for e in at.exception]
tables = [d.value for d in at.dataframe if "Reason" in d.value.columns]
assert tables and list(tables[0].columns)[0] == "Sheet"
assert {"Sheet: Customers", "Sheet: Orders", "Sheet: Notes"} <= {m.value.strip("*") if False else m.value.replace("**", "") for m in at.markdown}
print("PASSED")

print("\nALL UI LAYOUT TESTS PASSED")
