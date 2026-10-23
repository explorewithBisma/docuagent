from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"

import io
import sys

sys.path.insert(0, str(ROOT))

import openpyxl
import pandas as pd
from streamlit.testing.v1 import AppTest

import changes as ch
from xlsx_export import export_workbook

SAMPLE_CSV = (ROOT / "test_data" / "messy_sample.csv").read_bytes()
SAMPLE_RECIPE = (ROOT / "test_data" / "sample_recipe.json").read_bytes()
XLSX = (FIXTURES / "messy_multi_sheet.xlsx").read_bytes()
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def new_app(name, content, mime):
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    at.run()
    at.file_uploader[0].upload(name, content, mime)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def clean(at):
    next(b for b in at.button if b.label == "Clean File").click()
    at.run()
    assert not at.exception, [e.value for e in at.exception]


def workbook_from(at, include_report=True):
    """Build exactly what the download button would deliver, from the app's own session state."""
    s = at.session_state
    data = export_workbook(s["cleaned_sheets"], s["change_logs"], s["sheet_stats"], s["quality_before"],
                           s["placeholder_list"], s["original_filename"], s["original_rows"], include_report)
    return openpyxl.load_workbook(io.BytesIO(data))


def solid(cell):
    return (cell.fill.fgColor.rgb or "")[-6:] if cell.fill.fill_type == "solid" else None


print("TEST: CSV upload -> plain CSV button + 'Download as Excel (with highlights)' with an explanatory tooltip")
at = new_app("messy_sample.csv", SAMPLE_CSV, "text/csv")
clean(at)
buttons = {b.label: b for b in at.download_button}
print(list(buttons))
assert "Download Cleaned File" in buttons and "Download as Excel (with highlights)" in buttons
assert "can't store colors or comments" in buttons["Download as Excel (with highlights)"].help
assert "Download Cleaning Report (.txt)" in buttons
box = next(c for c in at.checkbox if c.label.startswith("Add a \"Cleaning Summary\""))
assert box.value is True
print("PASSED")

print("TEST: the Excel built from a CSV clean has highlights, comments and the two extra sheets at the end")
wb = workbook_from(at)
assert wb.sheetnames == ["Data", "Cleaning Summary", "Change Log"]
ws = wb["Data"]
assert ws.freeze_panes == "A2" and ws.auto_filter.ref == "A1:E9"
log = wb["Change Log"]
assert log.max_row - 1 == sum(ch.summarize(at.session_state["change_logs"]["Data"].finalize(at.session_state["cleaned_sheets"]["Data"])).values())
no_report = workbook_from(at, include_report=False)
assert no_report.sheetnames == ["Data"]
print("PASSED")

print("TEST: after approved AI rules are applied, their cells are light blue with an 'AI rule' comment")
rec_uploader = next(f for f in at.file_uploader if f.key == "recipe_uploader")
rec_uploader.upload("sample_recipe.json", SAMPLE_RECIPE, "application/json")
at.run()
next(b for b in at.button if b.label == "Load Recipe").click()
at.run()
next(b for b in at.button if b.label == "Apply Approved Rules").click()
at.run()
assert not at.exception, [e.value for e in at.exception]
wb = workbook_from(at)
ws = wb["Data"]
cols = {ws.cell(row=1, column=c).value: c for c in range(1, ws.max_column + 1)}
blue_cells = [(r, name) for name in ("Phone", "Status", "Signup Date", "Amount Paid") for r in range(2, ws.max_row + 1) if solid(ws.cell(row=r, column=cols[name])) == "BDD7EE"]
print(len(blue_cells), "blue cells")
assert len(blue_cells) >= 20
phone = ws.cell(row=2, column=cols["Phone"])
assert phone.value == "+923001234567" and phone.comment.text == "Was: 0300-1234567\nAI rule: Normalize Pakistani phone numbers to E.164 (+92...)."
assert ws.cell(row=2, column=cols["Phone"]).number_format == "@"
assert ws.cell(row=2, column=cols["Amount Paid"]).value == 1500 and ws.cell(row=2, column=cols["Amount Paid"]).comment.text.startswith("Was: Rs. 1,500/-")
summary = wb["Cleaning Summary"]
texts = [str(c.value) for row in summary.iter_rows() for c in row if c.value not in (None, "")]
ai_row = next(r for r in range(1, summary.max_row + 1) if summary.cell(row=r, column=2).value == ch.COLOR_MEANINGS["ai_rule"])
assert summary.cell(row=ai_row, column=3).value == len(blue_cells) or summary.cell(row=ai_row, column=3).value >= len(blue_cells)
print("PASSED")

print("TEST: .xlsx upload -> ONE 'Download Cleaned File' button (the highlighted workbook), no CSV-style second button")
at = new_app("messy_multi_sheet.xlsx", XLSX, XLSX_MIME)
clean(at)
labels = [b.label for b in at.download_button]
print(labels)
assert labels.count("Download Cleaned File") == 1 and "Download as Excel (with highlights)" not in labels
print("PASSED")

print("TEST: multi-sheet, clean ONE sheet -> others untouched and uncoloured, order kept, extras last")
at = new_app("messy_multi_sheet.xlsx", XLSX, XLSX_MIME)
next(r for r in at.radio).set_value("Customers")
at.run()
clean(at)
wb = workbook_from(at)
print(wb.sheetnames)
assert wb.sheetnames == ["Customers", "Orders", "Notes", "Cleaning Summary", "Change Log"]
orders = wb["Orders"]
original_orders = pd.read_excel(io.BytesIO(XLSX), sheet_name="Orders", dtype=str, keep_default_na=False)
assert [orders.cell(row=1, column=c).value for c in range(1, 4)] == list(original_orders.columns)         # header text untouched
assert orders.max_row - 1 == len(original_orders)                                                            # duplicate row still there
assert not any(c.comment or solid(c) in ("C6E0B4", "FFE699", "D9D9D9", "BDD7EE") for row in orders.iter_rows(min_row=2) for c in row)
notes = wb["Notes"]
assert notes.cell(row=2, column=1).value == "  keep this exactly as-is  "                                    # not even trimmed
customers = wb["Customers"]
assert any(c.comment for row in customers.iter_rows(min_row=2) for c in row)                                # the cleaned one is annotated
summary_texts = [str(c.value) for row in wb["Cleaning Summary"].iter_rows() for c in row if c.value not in (None, "")]
assert "Sheet: Customers" in summary_texts and "Sheet: Orders" not in summary_texts
print("PASSED")

print("TEST: multi-sheet, clean ALL -> a 'Sheet' column in the Change Log")
at = new_app("messy_multi_sheet.xlsx", XLSX, XLSX_MIME)
clean(at)
wb = workbook_from(at)
log = wb["Change Log"]
assert [c.value for c in log[1]][:3] == ["Sheet", "Row", "Column"]
assert {log.cell(row=r, column=1).value for r in range(2, log.max_row + 1)} <= {"Customers", "Orders", "Notes"}
print("PASSED")

print("TEST: a repeated Clean File starts a fresh log (no changes carried over)")
at = new_app("messy_sample.csv", SAMPLE_CSV, "text/csv")
clean(at)
first = len(at.session_state["change_logs"]["Data"].events)
clean(at)
assert len(at.session_state["change_logs"]["Data"].events) == first
print("PASSED")

print("\nALL DOWNLOAD TESTS PASSED")
