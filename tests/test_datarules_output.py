from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""Check every month: the highlighted copy with a 'Rule report' sheet, and the text report. The data is never changed."""
import io
import sys

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import openpyxl

import datarules_fixtures as fx
from datarules import check as ck, output as out, suggest
from reconcile import columns as rc
from reconcile_helpers import to_raw

RULES = suggest.suggest(rc.table_from_raw(to_raw(fx.sales_month(2026, 4)))[0], "april.xlsx", "PK", name="Monthly sales")
frame, planted = fx.problem_month()


def xlsx_of(df, title_rows=0, sheet="Orders"):
    book = openpyxl.Workbook()
    ws = book.active
    ws.title = sheet
    for i in range(title_rows):
        ws.cell(row=i + 1, column=1, value=f"Report title {i}")
    for c, name in enumerate(df.columns, start=1):
        ws.cell(row=title_rows + 1, column=c, value=name)
    for r, row in enumerate(df.itertuples(index=False), start=title_rows + 2):
        for c, value in enumerate(row, start=1):
            if value is not None:
                ws.cell(row=r, column=c, value=value)
    ws.cell(row=1, column=12, value="a note outside the table")
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def checked(data, sheet="Orders"):
    raw = next(iter(__import__("budget.importer", fromlist=["x"]).read_raw_sheets(data, False).values()))
    table, _ = rc.table_from_raw(raw)
    return ck.check(table, RULES, "may.xlsx", sheet), table


def cells_of(book):
    return {(ws.title, c.coordinate): c.value for ws in book.worksheets for row in ws.iter_rows() for c in row if c.value is not None}


print("TEST: the highlighted copy - the cells that broke a rule are coloured with a note, a 'Rule report' sheet is last, nothing else changed")
for title_rows in (0, 3):
    data = xlsx_of(frame, title_rows)
    result, table = checked(data)
    copy = out.make_highlighted(data, result, "Orders")
    before, after = openpyxl.load_workbook(io.BytesIO(data)), openpyxl.load_workbook(io.BytesIO(copy.data))
    assert after.sheetnames == ["Orders", "Rule report"]
    old, new = cells_of(before), cells_of(after)
    assert all(new.get(k) == v for k, v in old.items()), "a value changed"
    assert {k: v for k, v in new.items() if k[0] == "Orders"} == {k: v for k, v in old.items() if k[0] == "Orders"}          # every cell of the sheet is as it was
    ws = after["Orders"]
    first = title_rows + 1                                                                                           # the header row
    huge = ws.cell(row=first + 51, column=8)
    assert huge.value == 99999.0 and huge.fill.start_color.rgb[-6:] != "000000" and huge.comment is not None and "Number between" in huge.comment.text
    assert "Amount" in huge.comment.text
    header = ws.cell(row=first, column=3)
    assert header.comment is not None and "3 blank cell" in header.comment.text and f"{first + 4}" in header.comment.text        # blanks are marked on the header
    clean_cell = ws.cell(row=first + 2, column=8)
    assert clean_cell.comment is None
    report = after["Rule report"]
    assert [c.value for c in report[1]][:4] == ["#", "Importance", "Where", "What was found"]
    titles = [report.cell(row=r, column=4).value for r in range(2, report.max_row)]
    assert any("values in 'Amount'" in str(t) for t in titles) and any("duplicate value" in str(t) for t in titles)
    assert copy.highlighted == 14 and copy.notes == 14        # 13 cells that broke a rule + the header that carries the blank cells
    assert "No value in your file was changed" in " ".join(str(c.value) for row in report.iter_rows() for c in row if c.value)
print("PASSED")

print("TEST: nothing in the copy differs from the original except colours, notes and the new sheet (the same check as Excel Doctor's copy)")
from toolkit.highlight import validate_patched
data = xlsx_of(frame)
result, _ = checked(data)
copy = out.make_highlighted(data, result, "Orders")
validate_patched(data, copy.data, out.annotations(result, "Orders"))
print("PASSED")

print("TEST: when every rule passes the copy still has the Rule report sheet and says so")
clean = fx.sales_month(2026, 5, 120, 4, first_id=5000)
data = xlsx_of(clean)
result, _ = checked(data)
assert result.status == "PASS"
copy = out.make_highlighted(data, result, "Orders")
report = openpyxl.load_workbook(io.BytesIO(copy.data))["Rule report"]
assert report.cell(row=2, column=4).value == "All 17 rules passed" and copy.highlighted == 0
print("PASSED")

print("TEST: a CSV is turned into a workbook first (title lines, header and data keep their rows and columns), then highlighted the same way")
raw = to_raw(frame, title_rows=2)
book = out.workbook_from_raw(raw, "Data")
sheet = openpyxl.load_workbook(io.BytesIO(book))["Data"]
assert sheet["A1"].value == "Statement line 0"
assert sheet.cell(row=3, column=1).value == "Order ID" and sheet.cell(row=4, column=1).value == "ORD-2000"
table, header = rc.table_from_raw(raw)
result = ck.check(table, RULES, "may.csv", "Data")
copy = out.make_highlighted(book, result, "Data")
ws = openpyxl.load_workbook(io.BytesIO(copy.data))["Data"]
assert ws.cell(row=3 + 51, column=8).value == 99999.0 and ws.cell(row=3 + 51, column=8).comment is not None
print("PASSED")

print("TEST: many problems - the copy stays small (notes are capped) and the report sheet still lists every issue")
bad = fx.sales_month(2026, 5, 3000, 12, first_id=1).astype(object)
bad["Amount"] = 99999.0
data = xlsx_of(bad)
result, _ = checked(data)
assert result.counts()["fail"] == 1 and result.outcomes[[o.rule.id for o in result.outcomes].index("Amount:number_range")].failing_total == 3000
copy = out.make_highlighted(data, result, "Orders")
assert copy.notes <= out.MAX_NOTES and len(result.cells) == ck.MAX_ROWS_LISTED          # 300 cells are marked; the count of 3,000 is in the issue
issue = result.report.issues[0]
assert "3000 values" in issue.title and "and 2996 more" in issue.evidence
print("PASSED")

print("TEST: the text report has the result, every issue, every rule and the notes")
result, _ = checked(xlsx_of(frame))
text = out.report_text(result, "may.xlsx")
for expected in ("RESULT: FAIL", "PLEASE CHECK", "values in 'Amount'", "EVERY RULE", "FAIL     Must not be blank on 'Customer Email'", "FAIL     Number between on 'Quantity'",
                 "never changes your data"):
    assert expected in text, expected
print("PASSED")
