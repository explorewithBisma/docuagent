from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""toolkit: shared report shapes, ranking and the workbook reader."""
import io
import sys

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

from openpyxl import Workbook

import doctor_fixtures as fx
from toolkit.ranking import rank
from toolkit.report import Fact, Finding, Issue, Report
from toolkit.sheets import a1, read_workbook

print("TEST: Issue.where and Report.counts")
issue = Issue("k", "high", "t", "d", locations=[("Sheet A", "B5"), ("Sheet A", "B6"), ("Sheet B", "C1")])
assert issue.where == "Sheet A!B5 (+2 more)" and Issue("k", "low", "t", "d").where == ""
report = Report("x", issues=[issue, Issue("k", "low", "t", "d"), Issue("k", "low", "t", "d")])
assert report.counts() == {"high": 1, "medium": 0, "low": 2}
assert Finding(1, "high", "src").alternatives == [] and Fact("a", "b", "c").confidence == "high"
print("PASSED")

print("TEST: ranking - severity first, then amount, then more cells, then sheet order")
issues = [Issue("a", "low", "low big", "", impact=9999, locations=[("S2", "A1")]),
          Issue("b", "high", "high small", "", impact=1, locations=[("S2", "A1")]),
          Issue("c", "high", "high big", "", impact=500, locations=[("S2", "A1")]),
          Issue("d", "medium", "medium", "", impact=50, locations=[("S1", "A1")]),
          Issue("e", "high", "high big many cells", "", impact=500, locations=[("S2", "A1"), ("S2", "A2")]),
          Issue("f", "high", "high big earlier sheet", "", impact=500, locations=[("S1", "A1")])]
assert [i.title for i in rank(issues, ["S1", "S2"])] == ["high big many cells", "high big earlier sheet", "high big", "high small",
                                                       "medium", "low big"]
assert [i.kind for i in rank(issues)][:1] == ["e"] and issues[0].kind == "a", "the input list is not reordered"
assert rank([]) == []
print("PASSED")

print("TEST: read_workbook - formulas, stored results, constants, hidden state, charts, parts")
wb = read_workbook(fx.build_workbook())
assert wb.order == [fx.LOG, fx.INCOME, fx.LOAN, fx.MONTHLY, fx.DASH, fx.ACCOUNT, fx.DAILY]
assert wb.charts == 1 and wb.images == 0 and not wb.has_external_links and not wb.has_macros
monthly = wb.sheets[fx.MONTHLY]
food = monthly.get(5, 2)
assert food.is_formula and food.coord == "B5" and food.formula.startswith("SUMIF(") and food.value == 0
assert monthly.get(4, 3).is_formula is False and monthly.get(4, 3).value == 2000
assert wb.sheets[fx.DAILY].get(15, 4).is_error and wb.sheets[fx.DAILY].get(15, 4).value == "#REF!"
assert wb.sheets[fx.ACCOUNT].get(4, 2).value == 11755 and not wb.sheets[fx.ACCOUNT].get(4, 2).is_formula
assert wb.total_formulas() == sum(len(s.formula_cells()) for s in wb.sheets.values()) > 80
assert "xl/charts/chart1.xml" in wb.parts and a1(34, 2) == "B34" and a1(1, 27) == "AA1"
print("PASSED")

print("TEST: a file never calculated by Excel has no stored results (value None); the formulas are still read")
raw = read_workbook(fx.build_workbook(with_cached_values=False))
assert raw.sheets[fx.LOG].get(45, 5).is_formula and raw.sheets[fx.LOG].get(45, 5).value is None
print("PASSED")

print("TEST: to_frame gives the values (row 0 = Excel row 1)")
frame = wb.sheets[fx.LOG].to_frame()
assert frame.iat[2, 1] == "Date" and frame.iat[3, 2] == "item 1" and frame.shape[0] == 45
print("PASSED")

print("TEST: hidden sheets / rows / columns and comments are noticed")
book = Workbook()
ws = book.active
ws["A1"], ws["B1"] = 1, 2
ws.row_dimensions[3].hidden = True
ws.column_dimensions["C"].hidden = True
ws["A3"] = 5
from openpyxl.comments import Comment
ws["A1"].comment = Comment("hello", "me")
second = book.create_sheet("Secret")
second["A1"] = 1
second.sheet_state = "hidden"
buffer = io.BytesIO()
book.save(buffer)
data = read_workbook(buffer.getvalue())
sheet = data.sheets[ws.title]
assert sheet.hidden_rows == {3} and sheet.hidden_cols == {3} and sheet.has_comments and data.sheets["Secret"].state == "hidden"
print("PASSED")

print("TEST: unreadable files give readable errors")
for bad, fragment in ((b"\xd0\xcf\x11\xe0 old excel", "old .xls"), (b"hello", "doesn't look like an Excel"),
                      (b"PK\x03\x04 not really a zip", "doesn't look like an Excel")):
    try:
        read_workbook(bad)
        raise AssertionError("should fail")
    except ValueError as error:
        assert fragment in str(error), (bad, str(error))
print("PASSED")
