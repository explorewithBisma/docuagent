from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""toolkit/highlight.py + doctor/output.py: the highlighted copy changes only colours, notes and one added sheet; everything else
(charts, other sheets, every value and formula) stays identical; and a copy that fails the checks is never handed out."""
import io
import re
import sys
import zipfile

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment

import doctor_fixtures as fx
from doctor.analyze import analyze
from doctor.output import make_highlighted, note_for, report_text
from toolkit.highlight import Annotation, PatchError, ReportRow, patch_workbook, validate_patched
from toolkit.sheets import read_workbook

ORIGINAL = fx.build_workbook()
REPORT, _ = analyze(ORIGINAL)
RESULT = make_highlighted(ORIGINAL, REPORT)
before, after = zipfile.ZipFile(io.BytesIO(ORIGINAL)), zipfile.ZipFile(io.BytesIO(RESULT.data))

print("TEST: every finding's cell is coloured and has a note; nothing was skipped")
assert RESULT.highlighted == 9 and RESULT.notes == 9 and RESULT.skipped == []
copy = load_workbook(io.BytesIO(RESULT.data))
issues_at = {}
for issue in REPORT.issues:
    for location in issue.locations:
        issues_at.setdefault(location, []).append(issue)
by_cell = {location: found[0] for location, found in issues_at.items()}
colours = {"high": "FFFFC7CE", "medium": "FFFFEB9C", "low": "FFDDEBF7"}
for (sheet, coord), found in issues_at.items():
    cell = copy[sheet][coord]
    worst = min(found, key=lambda i: ["high", "medium", "low"].index(i.severity))
    assert cell.fill.fgColor.rgb == colours[worst.severity], (sheet, coord, cell.fill.fgColor.rgb)
    assert cell.comment is not None and cell.comment.author == "Excel Doctor"
    assert all(i.title in cell.comment.text for i in found), "every reason about the cell is in its note"
assert len(issues_at[(fx.DAILY, "B34")]) == 2
print("PASSED")

print("TEST: a cell with two findings gets the more serious colour and both reasons")
two = fx.build_workbook()
notes = [Annotation(fx.DAILY, "B34", "medium", "first reason"), Annotation(fx.DAILY, "B34", "high", "second reason"),
         Annotation(fx.DAILY, "B34", "high", "second reason")]
result = patch_workbook(two, notes, [], "footer")
cell = load_workbook(io.BytesIO(result.data))[fx.DAILY]["B34"]
assert cell.fill.fgColor.rgb == "FFFFC7CE" and cell.comment.text == "first reason | second reason"
print("PASSED")

print("TEST: nothing in the workbook changed - every cell's value, formula, stored result and number format is identical")
was, now = read_workbook(ORIGINAL), read_workbook(RESULT.data)
assert now.order == was.order + ["Doctor report"]
for name, sheet in was.sheets.items():
    assert {k: (c.formula, c.value, c.number_format) for k, c in sheet.cells.items()} == \
           {k: (c.formula, c.value, c.number_format) for k, c in now.sheets[name].cells.items()}, name
assert now.total_formulas() == was.total_formulas()
print("PASSED")

print("TEST: charts, drawings, the other sheets and the properties are byte-identical")
for name in before.namelist():
    if name.startswith(("xl/charts/", "xl/drawings/", "docProps/")) or name in ("xl/worksheets/sheet2.xml", "xl/worksheets/sheet3.xml", "_rels/.rels", "xl/theme/theme1.xml"):
        assert before.read(name) == after.read(name), name
assert after.read("xl/charts/chart1.xml") == before.read("xl/charts/chart1.xml") and "xl/charts/chart1.xml" in after.namelist()
assert {n for n in before.namelist() if before.read(n) != after.read(n)} <= {
    "[Content_Types].xml", "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/styles.xml"} | {n for n in before.namelist() if re.match(r"xl/worksheets/(_rels/)?sheet\d+\.xml(\.rels)?$", n)}
assert load_workbook(io.BytesIO(RESULT.data))[fx.DASH]._charts, "the Dashboard chart is still a chart"
print("PASSED")

print("TEST: only the cells that have a finding got a different style, and only the fill differs")
original_book, copy = load_workbook(io.BytesIO(ORIGINAL)), load_workbook(io.BytesIO(RESULT.data))
flagged = set(by_cell)
for sheet in original_book.worksheets:
    for row in sheet.iter_rows():
        for cell in row:
            twin = copy[sheet.title][cell.coordinate]
            same = lambda x, y: repr(x) == repr(y)          # style objects of two workbooks: compare what they contain
            assert cell.number_format == twin.number_format and same(cell.font, twin.font) and same(cell.alignment, twin.alignment) and same(cell.border, twin.border)
            if (sheet.title, cell.coordinate) not in flagged:
                assert same(cell.fill, twin.fill), (sheet.title, cell.coordinate)
print("PASSED")

print("TEST: the report sheet is last, lists every finding in order, and links to the cell")
sheet = copy["Doctor report"]
assert copy.sheetnames[-1] == "Doctor report"
rows = [[c.value for c in r] for r in sheet.iter_rows(min_row=2, max_row=1 + len(REPORT.issues))]
assert [r[3] for r in rows] == [i.title for i in REPORT.issues] and [r[1] for r in rows] == [i.severity.capitalize() for i in REPORT.issues]
assert [c.value for c in sheet[1]][:4] == ["#", "Importance", "Where", "What was found"]
first = REPORT.issues[0]
assert sheet["C2"].hyperlink.location == "'" + first.locations[0][0] + "'!" + first.locations[0][1]
assert all(sheet.cell(row=2 + i, column=3).hyperlink is not None for i in range(len(REPORT.issues)))
assert "No number, text or formula" in " ".join(str(c.value) for row in sheet.iter_rows() for c in row if c.value)
print("PASSED")

print("TEST: the notes of every sheet are real Excel notes (comments part + VML drawing + relationship + content type)")
types = after.read("[Content_Types].xml").decode()
assert 'Extension="vml"' in types and types.count("spreadsheetml.comments+xml") >= 5
sheet5 = after.read("xl/worksheets/sheet5.xml").decode()
assert sheet5.index("<drawing") < sheet5.index("<legacyDrawing"), "legacyDrawing must come after drawing (the Dashboard has a chart)"
assert "xmlns:r=" in re.search(r"<worksheet\b[^>]*>", sheet5).group(0)
print("PASSED")

print("TEST: sheet names with emojis, spaces and quotes work")
wb = Workbook()
wb.active.title = "It's a 📊 test & more"
wb.active["A1"], wb.active["A2"] = 1, "=A1+1"
buffer = io.BytesIO()
wb.save(buffer)
result = patch_workbook(buffer.getvalue(), [Annotation("It's a 📊 test & more", "A2", "low", "hello <b> & \"quotes\"")],
                        [ReportRow(1, "low", "x", "t", "d", "e", "f", ("It's a 📊 test & more", "A2"))], "f")
copy = load_workbook(io.BytesIO(result.data))
assert copy["It's a 📊 test & more"]["A2"].comment.text == 'hello <b> & "quotes"'
assert copy["Doctor report"]["C2"].hyperlink.location == "'It''s a 📊 test & more'!A2"
print("PASSED")

print("TEST: a sheet that already has notes keeps them; it is still coloured and the report says why no note was added")
wb = Workbook()
ws = wb.active
ws.title = "S"
ws["A1"], ws["A2"] = 5, 6
ws["A1"].comment = Comment("my own note", "me")
buffer = io.BytesIO()
wb.save(buffer)
result = patch_workbook(buffer.getvalue(), [Annotation("S", "A2", "high", "reason")], [], "f")
assert result.highlighted == 1 and result.notes == 0 and "already has notes" in result.skipped[0]
copy = load_workbook(io.BytesIO(result.data))
assert copy["S"]["A1"].comment.text == "my own note" and copy["S"]["A2"].fill.fgColor.rgb == "FFFFC7CE"
print("PASSED")

print("TEST: an empty cell is not coloured (and says so); an unknown sheet is skipped; the rest still works")
result = patch_workbook(ORIGINAL, [Annotation(fx.DAILY, "Z99", "high", "x"), Annotation("No such sheet", "A1", "high", "y"),
                                  Annotation(fx.DAILY, "B12", "medium", "z")], [], "f")
assert result.highlighted == 1 and any("Z99 is an empty cell" in s for s in result.skipped) and any("No such sheet" in s for s in result.skipped)
print("PASSED")

print("TEST: when patching is not safe, a PatchError (never a broken file)")
for broken, fragment in ((b"not a zip", "not a normal .xlsx"),):
    try:
        patch_workbook(broken, [], [], "")
        raise AssertionError("should fail")
    except PatchError as error:
        assert fragment in str(error)
stripped = io.BytesIO()
with zipfile.ZipFile(io.BytesIO(ORIGINAL)) as zin, zipfile.ZipFile(stripped, "w") as zout:
    for item in zin.infolist():
        data = zin.read(item.filename)
        if item.filename == "xl/styles.xml":
            data = data.replace(b"<fills", b"<fillz").replace(b"</fills>", b"</fillz>")
        zout.writestr(item, data)
try:
    patch_workbook(stripped.getvalue(), [Annotation(fx.DAILY, "B12", "high", "x")], [], "")
    raise AssertionError("should fail")
except PatchError as error:
    assert "styles" in str(error)
try:
    make_highlighted(ORIGINAL, analyze(fx.build_workbook())[0].__class__("x"))
    raise AssertionError("should fail")
except PatchError as error:
    assert "nothing to highlight" in str(error)
print("PASSED")

print("TEST: the validator catches a copy that is wrong - a changed value, a lost part, a changed chart, a dangling link")
good = RESULT.data


def tamper(function):
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(good)) as zin, zipfile.ZipFile(out, "w") as zout:
        for item in zin.infolist():
            data = function(item.filename, zin.read(item.filename))
            if data is not None:
                zout.writestr(item, data)
    return out.getvalue()


cases = {
    "a value changed": lambda n, d: d.replace(b"<v>11755</v>", b"<v>11756</v>") if n == "xl/worksheets/sheet6.xml" else d,
    "a part was lost": lambda n, d: None if n == "xl/charts/chart1.xml" else d,
    "the chart changed": lambda n, d: d + b" " if n == "xl/charts/chart1.xml" else d,
    "a link points nowhere": lambda n, d: re.sub(rb"\.\./comments\d+\.xml", b"../comments99.xml", d) if n.startswith("xl/worksheets/_rels/") else d,
    "broken XML": lambda n, d: d[:-10] if n.startswith("xl/comments") else d,
    "unlisted part": lambda n, d: re.sub(rb'<Override PartName="/xl/comments\d+\.xml"[^>]*/>', b"", d) if n == "[Content_Types].xml" else d,
}
for label, function in cases.items():
    try:
        validate_patched(ORIGINAL, tamper(function), [])
        raise AssertionError(f"not caught: {label}")
    except PatchError:
        pass
validate_patched(ORIGINAL, good, [])
print("PASSED")

print("TEST: the text report - always available, in the order of the page")
text = report_text(REPORT, "budget.xlsx")
assert text.startswith("Excel Doctor report for budget.xlsx") and "WHAT I FOUND" in text and "PLEASE CHECK  (5 serious, 3 worth checking, 1 small)" in text
assert text.index("1. [HIGH]") < text.index("9. [LOW]") and "never changes your numbers" in text
assert note_for(REPORT.issues[0]).startswith("[HIGH]") and "Suggestion:" in note_for(REPORT.issues[0])
print("PASSED")
