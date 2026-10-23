from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"

import io
import sys
import time
from datetime import datetime

sys.path.insert(0, str(ROOT))

import numpy as np
import openpyxl
import pandas as pd

import changes as ch
import cleaning
import derive
import quality
import rule_engine
import xlsx_export as xe

PRICES = {"Cake": 3.0, "Coffee": 2.0, "Cookie": 1.0, "Juice": 3.0, "Salad": 5.0, "Sandwich": 4.0, "Smoothie": 4.0, "Tea": 1.5}
ALL = {"headers": True, "trim": True, "duplicates": True, "empty": True, "placeholders": True, "numeric": True, "fills": True}
GREEN, YELLOW, GREY, BLUE = "C6E0B4", "FFE699", "D9D9D9", "BDD7EE"


def make_raw(rows=300, seed=5):
    rng = np.random.default_rng(seed)
    item = rng.choice(list(PRICES), rows)
    quantity = rng.integers(1, 6, rows)
    price = np.array([PRICES[i] for i in item])
    frame = pd.DataFrame({
        "Transaction ID": [f"TXN_{i:07d}" for i in range(rows)], "Item": item, "Quantity": quantity.astype(str),
        "Price Per Unit": price.astype(str), "Total Spent": (quantity * price).astype(str),
        "Phone": [f"0300-{1000000 + i}" for i in range(rows)], "Status": rng.choice(["active", "ACTIVE", "Active"], rows),
    })
    for column in ["Item", "Quantity", "Price Per Unit", "Total Spent"]:
        hole = rng.random(rows) < 0.08
        picks = rng.choice(["ERROR", "UNKNOWN", ""], hole.sum())
        frame.loc[hole, column] = [np.nan if pick == "" else pick for pick in picks]
    frame.loc[3, "Item"] = f"  {item[3]}  "                           # a trimmed cell (no colour, comment only)
    return frame


def run_pipeline(raw, with_lookup=True, with_rule=True):
    base, _ = cleaning.clean_dataframe_with_steps(raw.copy(), {**ALL, "fills": False})
    found = derive.detect_relationships(base)
    rels = found["products"] + (found["lookups"] if with_lookup else [])
    log = ch.ChangeLog()
    final, stats = cleaning.clean_dataframe_with_steps(raw.copy(), ALL, {"changes": log, "relationships": rels})
    if with_rule:
        rule = {"id": "r1", "column": "Status", "rule_type": "unify_casing", "params": {"style": "title"}, "description": "Title-case Status."}
        final, _ = rule_engine.apply_rule(final, rule, skip_values=quality.DEFAULT_PLACEHOLDERS, log=log)
    return final, stats, log.finalize(final)


raw = make_raw()
item_of_row3 = raw.at[3, "Item"].strip()
final, stats, records = run_pipeline(raw)
counts = ch.summarize(records)
print(counts)
assert all(counts[r] > 0 for r in ("placeholder", "calculated", "lookup", "ai_rule", "trimmed"))      # every colour is exercised

quality_by_sheet = {"Data": {"before": quality.profile_dataframe(raw), "after": quality.profile_dataframe(final)}}
report = {"file_name": "cafe.csv", "generated": datetime(2026, 10, 24, 9, 30), "sheet_stats": {"Data": {"cleaned": True, "stats": stats}},
          "quality_by_sheet": quality_by_sheet, "original_rows": {"Data": len(raw)}, "final_rows": {"Data": len(final)}}
t = time.time()
data = xe.build_highlighted_workbook({"Data": final}, {"Data": records}, report)
print(f"[workbook with {len(records)} changes built in {time.time() - t:.1f}s, {len(data) / 1024:.0f} KB]")
wb = openpyxl.load_workbook(io.BytesIO(data))
ws = wb["Data"]
col = {name: i + 1 for i, name in enumerate(final.columns)}

print("TEST: sheets - user's sheet first and unchanged, extra sheets at the END")
assert wb.sheetnames == ["Data", "Cleaning Summary", "Change Log"], wb.sheetnames
back = pd.read_excel(io.BytesIO(data), sheet_name="Data", dtype=str, keep_default_na=False, na_values=[""])
assert list(back.columns) == list(final.columns) and len(back) == len(final)
assert back["Transaction ID"].tolist() == final["Transaction ID"].tolist()
assert back["Item"].fillna("").tolist() == final["Item"].fillna("").astype(str).tolist()
print("PASSED")

print("TEST: fills - green / yellow / grey / blue on exactly the right cells, none on trimmed or untouched cells")
rgb = lambda cell: (cell.fill.fgColor.rgb or "")[-6:] if cell.fill.fill_type == "solid" else None
expected = {"calculated": GREEN, "lookup": YELLOW, "placeholder": GREY, "ai_rule": BLUE}
changed_cells = set()
for r in records:
    cell = ws.cell(row=r["row"], column=col[r["column"]])
    changed_cells.add((r["row"], col[r["column"]]))
    assert rgb(cell) == expected.get(r["reason"]), (r["reason"], rgb(cell))
for row in range(2, len(final) + 2):
    for c in range(1, len(final.columns) + 1):
        if (row, c) not in changed_cells:
            assert rgb(ws.cell(row=row, column=c)) is None and ws.cell(row=row, column=c).comment is None
trimmed = next(r for r in records if r["reason"] == "trimmed")
assert rgb(ws.cell(row=trimmed["row"], column=col[trimmed["column"]])) is None                  # trimmed: comment only
print("PASSED")

print("TEST: comments - 'Was: ...' and the explanation, on every changed cell")
for r in records:
    comment = ws.cell(row=r["row"], column=col[r["column"]]).comment
    assert comment is not None and comment.text == ch.comment_text(r) and comment.author == "Excel Cleaner"
sample = {reason: next(r for r in records if r["reason"] == reason) for reason in expected}
print({k: ws.cell(row=v["row"], column=col[v["column"]]).comment.text for k, v in sample.items()})
assert ws.cell(row=sample["placeholder"]["row"], column=col[sample["placeholder"]["column"]]).comment.text.startswith("Was: ")
assert "Calculated exactly: " in ws.cell(row=sample["calculated"]["row"], column=col[sample["calculated"]["column"]]).comment.text
assert "Inferred from lookup: " in ws.cell(row=sample["lookup"]["row"], column=col[sample["lookup"]["column"]]).comment.text
assert "AI rule: Title-case Status." in ws.cell(row=sample["ai_rule"]["row"], column=col[sample["ai_rule"]["column"]]).comment.text
assert ws.cell(row=trimmed["row"], column=col["Item"]).comment.text == f'Was: "  {item_of_row3}  "'
print("PASSED")

print("TEST: polish - bold header with background, borders, freeze, autofilter, widths, phone text, number formats")
header = [ws.cell(row=1, column=c) for c in range(1, len(final.columns) + 1)]
assert all(c.font.bold and c.fill.fill_type == "solid" and c.border.left.style == "thin" for c in header)
assert (header[0].fill.fgColor.rgb or "")[-6:] not in (GREEN, YELLOW, GREY, BLUE)                 # header colour is not a highlight colour
assert ws.freeze_panes == "A2"
assert ws.auto_filter.ref == f"A1:{openpyxl.utils.get_column_letter(len(final.columns))}{len(final) + 1}"
assert all(ws.cell(row=r, column=c).border.left.style == "thin" and ws.cell(row=r, column=c).border.bottom.style == "thin"
           for r in (2, 50, len(final) + 1) for c in range(1, len(final.columns) + 1))
assert ws.column_dimensions["A"].width >= len("Transaction ID") + 2 and ws.column_dimensions["B"].width >= 8
phone = ws.cell(row=2, column=col["Phone"])
assert phone.number_format == "@" and phone.data_type == "s" and phone.value.startswith("0300-")
q = [ws.cell(row=r, column=col["Quantity"]) for r in range(2, 40) if ws.cell(row=r, column=col["Quantity"]).value is not None]
p = [ws.cell(row=r, column=col["Price Per Unit"]) for r in range(2, 40) if ws.cell(row=r, column=col["Price Per Unit"]).value is not None]
assert all(c.number_format == "0" for c in q), {c.number_format for c in q}                         # integers without decimals
assert all(c.number_format == "#,##0.00" for c in p), {c.number_format for c in p}                  # prices with 2 decimals
assert ws.cell(row=2, column=col["Total Spent"]).number_format == "#,##0.00"
assert ws.cell(row=2, column=col["Item"]).number_format == "General"
print("PASSED")

print("TEST: Change Log sheet")
log = wb["Change Log"]
assert [c.value for c in log[1]] == ["Row", "Column", "Old value", "New value", "Reason"]
assert log.max_row == len(records) + 1 and log.freeze_panes == "A2" and log.auto_filter.ref == f"A1:E{len(records) + 1}"
first = records[0]
row2 = [c.value for c in log[2]]
print(row2)
assert row2[0] == first["row"] and row2[1] == first["column"] and row2[4] == ch.reason_text(first)
assert all(c.font.bold for c in log[1])
reasons = {log.cell(row=r, column=5).value.split(":")[0] for r in range(2, log.max_row + 1)}
assert reasons == {"Placeholder cleared", "Calculated exactly", "Inferred from lookup", "AI rule", "Whitespace trimmed"}, reasons
blank_new = next(r for r in range(2, log.max_row + 1) if log.cell(row=r, column=5).value == "Placeholder cleared")
assert log.cell(row=blank_new, column=4).value == "(blank)" and log.cell(row=blank_new, column=3).value in ("ERROR", "UNKNOWN")
print("PASSED")

print("TEST: Cleaning Summary sheet - file, date, rows, totals by type, legend colours, before/after table")
summ = wb["Cleaning Summary"]
cells = {(c.row, c.column): c for row in summ.iter_rows() for c in row if c.value not in (None, "")}
texts = [str(c.value) for c in cells.values()]
assert "Cleaning Summary" in texts and "cafe.csv" in texts and "2026-10-24 09:30" in texts
assert any(t_.startswith("Rows in your file") for t_ in texts)
label_row = {str(c.value): c.row for c in cells.values() if c.column == 2}
for reason in ("calculated", "lookup", "placeholder", "ai_rule"):
    meaning = ch.COLOR_MEANINGS[reason]
    r = label_row[meaning]
    assert (summ.cell(row=r, column=1).fill.fgColor.rgb or "")[-6:] == expected[reason]            # legend swatch
    assert summ.cell(row=r, column=3).value == counts[reason]                                       # totals by change type
total_row = next(c.row for c in cells.values() if c.value == "Total cells changed" and c.column == 1)
assert summ.cell(row=total_row, column=3).value == len(records)
assert any(t_ == "Data quality by column - Data" for t_ in texts)
qrow = next(c.row for c in cells.values() if c.value == "Missing before")
qualities = {summ.cell(row=r, column=1).value: [summ.cell(row=r, column=c).value for c in range(2, 8)] for r in range(qrow + 1, qrow + 1 + len(final.columns))}
assert qualities["Quantity"][0] == "numeric" and isinstance(qualities["Quantity"][1], int)
assert qualities["Quantity"][2] <= qualities["Quantity"][1]                                          # fills lowered missing
print("PASSED")

print("TEST: without the report checkbox only the user's sheets are written")
plain = openpyxl.load_workbook(io.BytesIO(xe.build_highlighted_workbook({"Data": final}, {"Data": records}, None)))
assert plain.sheetnames == ["Data"] and rgb(plain["Data"].cell(row=records[0]["row"], column=col[records[0]["column"]])) is not None
print("PASSED")

print("TEST: caps - Change Log limited with a note, comments limited, colours unaffected")
orig_log, orig_comments = xe.MAX_LOG_ROWS, xe.MAX_COMMENTS
xe.MAX_LOG_ROWS, xe.MAX_COMMENTS = 7, 5
try:
    capped = openpyxl.load_workbook(io.BytesIO(xe.build_highlighted_workbook({"Data": final}, {"Data": records}, report)))
finally:
    xe.MAX_LOG_ROWS, xe.MAX_COMMENTS = orig_log, orig_comments
assert capped["Change Log"].max_row == 8
ctexts = [str(c.value) for row in capped["Cleaning Summary"].iter_rows() for c in row if c.value]
assert f"The Change Log shows the first 7 of {len(records):,} changes." in ctexts, [t_ for t_ in ctexts if "Change Log" in t_]
assert any(t_.startswith("Cell comments were added to the first 5 of") for t_ in ctexts)
wcap = capped["Data"]
assert sum(1 for row in wcap.iter_rows() for c in row if c.comment) == 5
assert sum(1 for r in records if rgb(wcap.cell(row=r["row"], column=col[r["column"]])) is not None) == sum(1 for r in records if r["reason"] in expected)
print("PASSED")

print("TEST: multi-sheet - order kept, uncleaned sheet untouched, 'Sheet' column in the log, name clashes avoided")
other = pd.DataFrame({"Keep": [" as is ", "ERROR"], "N": [1, 2]})
sheets = {"Zeta": final, "Alpha": other, "Change Log": pd.DataFrame({"x": [1]})}
multi = xe.build_highlighted_workbook(
    sheets, {"Zeta": records, "Change Log": []},
    {**report, "sheet_stats": {"Zeta": {"cleaned": True, "stats": stats}, "Alpha": {"cleaned": False, "stats": None}, "Change Log": {"cleaned": True, "stats": stats}},
     "quality_by_sheet": {}, "original_rows": {"Zeta": len(raw)}, "final_rows": {"Zeta": len(final)}})
mwb = openpyxl.load_workbook(io.BytesIO(multi))
print(mwb.sheetnames)
assert mwb.sheetnames == ["Zeta", "Alpha", "Change Log", "Cleaning Summary", "Change Log (2)"]
assert [c.value for c in mwb["Alpha"][2]] == [" as is ", 1] and [c.value for c in mwb["Alpha"][3]] == ["ERROR", 2]     # values exactly as given
assert not any(c.comment or c.fill.fill_type == "solid" for row in mwb["Alpha"].iter_rows(min_row=2) for c in row)
assert [c.value for c in mwb["Change Log (2)"][1]][:2] == ["Sheet", "Row"]
assert mwb["Change Log (2)"].cell(row=2, column=1).value == "Zeta"
print("PASSED")

print("TEST: nothing is lost - the data values equal the cleaned DataFrame")
fresh = pd.read_excel(io.BytesIO(data), sheet_name="Data")
for column in ["Quantity", "Price Per Unit", "Total Spent"]:
    assert np.allclose(fresh[column].astype(float).fillna(-1), final[column].astype(float).fillna(-1)), column
print("PASSED")

print("\nALL HIGHLIGHTED XLSX TESTS PASSED")
