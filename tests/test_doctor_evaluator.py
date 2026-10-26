from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""doctor/formulas.py (reading formula text) and doctor/evaluator.py (SUM, SUMIF(S), COUNTIF(S)), including a comparison with
the `formulas` package as an independent oracle."""
import io
import os
import random
import sys
import tempfile
import warnings
from datetime import datetime

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

from openpyxl import Workbook

import doctor_fixtures as fx
from doctor import formulas as fm
from doctor.evaluator import Evaluator, ExcelError, Unsupported, make_matcher, serial
from toolkit.sheets import read_workbook


def book(**sheets):
    """A workbook from {sheet: {coord: value}} (strings starting with = are formulas); read back as WorkbookData."""
    wb = Workbook()
    wb.remove(wb.active)
    for name, cells in sheets.items():
        ws = wb.create_sheet(name.replace("_", " "))
        for coord, value in cells.items():
            ws[coord] = value
    buffer = io.BytesIO()
    wb.save(buffer)
    return read_workbook(buffer.getvalue())


print("TEST: formula reader - references, sheets with emojis and spaces, operators, functions")
tree = fm.parse("SUMIF('📝 Daily Log'!$D$4:$D$43,A8,'📝 Daily Log'!$E$4:$E$43)+Total!B2*2%")
references = fm.refs(tree)
assert [r.text for r in references] == ["'📝 Daily Log'!$D$4:$D$43", "A8", "'📝 Daily Log'!$E$4:$E$43", "Total!B2"]
first = references[0]
assert (first.sheet, first.kind, first.r1, first.c1, first.r2, first.c2, first.abs_r1, first.abs_c1) == ("📝 Daily Log", "range", 4, 4, 43, 4, True, True)
assert [c.name for c in fm.calls(tree)] == ["SUMIF"]
column = fm.parse_ref("Data!B:B")
assert (column.kind, column.r1, column.r2, column.c1, column.c2) == ("col", 1, None, 2, 2)
assert fm.parse_ref("'It''s'!A1").sheet == "It's"
assert fm.parse('"a ""quoted"" text"').value == 'a "quoted" text'
for bad in ("SUM(A1", "A1 B1", "{1,2}", "SUM(A1))", "1+"):
    try:
        fm.parse(bad)
        raise AssertionError(bad)
    except fm.FormulaSyntax:
        pass
print("PASSED")

print("TEST: relative form - filled-down formulas have the same text, a different range does not")
assert fm.to_relative("SUM(B4:B30)", 34, 2) == "SUM(R[-30]C[0]:R[-4]C[0])"
assert fm.to_relative("C5-B5", 5, 4) == fm.to_relative("C6-B6", 6, 4) == "R[0]C[-1]-R[0]C[-2]"
assert fm.to_relative("SUMIF($A$1:$A$9,A4,B$1:B$9)", 4, 3) == "SUMIF(R1C1:R9C1,R[0]C[-2],R1C[-1]:R9C[-1])"
assert fm.to_relative("'My sheet'!A1+1", 2, 2) == "'My sheet'!R[-1]C[-1]+1" and fm.to_relative("{1}+A1", 1, 1) == "{1}+A1"
print("PASSED")

print("TEST: arithmetic, text and comparisons")
wb = book(S={"A1": 10, "A2": 4, "A3": "5", "A4": None, "B1": "=A1*A2-3^2", "B2": '=A1&"-"&A2', "B3": "=A1/0", "B4": "=A1>A2",
              "B5": "=-A2%", "B6": "=A3+1", "B7": '="x"+1', "B8": "=A1=10"})
ev = Evaluator(wb)
cases = {"A1*A2-3^2": 31, "A1&\"-\"&A2": "10-4", "A1>A2": True, "-A2%": -0.04, "A3+1": 6, "A1=10": True, "(A1+A2)*2": 28,
         "A1&A4": "10", "A4+1": 1}
for formula, expected in cases.items():
    got = ev.evaluate(formula, "S")
    assert got == expected or (isinstance(expected, float) and abs(got - expected) < 1e-9), (formula, got, expected)
assert ev.evaluate("A1/0", "S") == "#DIV/0!" and isinstance(ev.evaluate("A1/0", "S"), ExcelError)
assert ev.evaluate('"x"+1', "S") == "#VALUE!" and ev.evaluate("#REF!+1", "S") == "#REF!" and ev.evaluate("Nope!A1", "S") == "#REF!"
print("PASSED")

print("TEST: SUM - ranges, numbers, text and blanks ignored, errors propagate, whole columns, other sheets")
wb = book(S={"A1": 1, "A2": 2.5, "A3": "text", "A4": None, "A5": "7", "A6": True, "B1": "=1/0", "C1": 10, "C9": 5},
          Other_sheet={"A1": 100, "A2": 200})
ev = Evaluator(wb)
assert ev.evaluate("SUM(A1:A6)", "S") == 3.5, "text, blanks and TRUE in a range are ignored"
assert ev.evaluate("SUM(A1,A2,10)", "S") == 13.5 and ev.evaluate('SUM("3",A1)', "S") == 4
assert ev.evaluate("SUM(B1:B2)", "S") == "#DIV/0!"
assert ev.evaluate("SUM(C:C)", "S") == 15 and ev.evaluate("SUM('Other sheet'!A1:A2)", "S") == 300
assert ev.evaluate("SUM(A1:A2)+SUM(C1:C9)", "S") == 18.5
print("PASSED")

print("TEST: SUMIF / COUNTIF criteria follow Excel: case, wildcards, operators, numbers as text, blanks")
data = {"A1": "Food", "A2": "food ", "A3": "FOOD", "A4": "Food/Grocery", "A5": "Transport", "A6": None, "A7": 5, "A8": "5", "A9": "Fruit",
        "B1": 1, "B2": 2, "B3": 4, "B4": 8, "B5": 16, "B6": 32, "B7": 64, "B8": 128, "B9": 256}
ev = Evaluator(book(S=data))


def sumif(criterion, **kw):
    return ev.evaluate(f"SUMIF(A1:A9,{criterion},B1:B9)", "S")


assert sumif('"food"') == 1 + 4, "case is ignored, a trailing space is NOT"
assert sumif('"Food*"') == 1 + 2 + 4 + 8 and sumif('"*food*"') == 1 + 4 + 2 + 8 and sumif('"F??d"') == 1 + 4
assert sumif('"<>Food"') == 2 + 8 + 16 + 32 + 64 + 128 + 256, "'<>' also counts blanks and other types"
assert sumif("5") == 64 + 128, "a number matches a number and numeric text"
assert sumif('"5"') == 64 + 128 and sumif('">3"') == 64 and sumif('">=5"') == 64
assert sumif('""') == 32 and sumif('"="') == 32, "an empty criterion means a blank cell"
assert sumif('"~*"') == 0 and sumif('"Fruit"') == 256 and sumif('"fr*t"') == 256
assert ev.evaluate('COUNTIF(A1:A9,"food*")', "S") == 4 and ev.evaluate("COUNTIF(A1:A9,\">0\")", "S") == 1
assert ev.evaluate('COUNTIF(A1:A9,"🍔 Food")', "S") == 0, "the emoji makes it a different text"
assert ev.evaluate('SUMIF(A1:A9,"Fruit")', "S") == 0 and ev.evaluate('SUMIF(A1:A3,"food",B4:B9)', "S") == 8 + 32, "sum range starts at its own first cell"
print("PASSED")

print("TEST: criteria taken from a cell, built with &, and dates")
wb = book(S={"A1": datetime(2026, 6, 1), "A2": datetime(2026, 6, 2), "A3": datetime(2026, 6, 2), "B1": 1, "B2": 10, "B3": 100,
             "D1": datetime(2026, 6, 2), "D2": "Fo*", "D3": 5, "C1": "Food", "C2": "Foxy", "C3": "x", "E1": 1, "E2": 2, "E3": 3})
ev = Evaluator(wb)
assert ev.evaluate("SUMIF(A1:A3,D1,B1:B3)", "S") == 110, "a date criterion matches the dates (they are numbers)"
assert ev.evaluate('SUMIF(A1:A3,">"&D1-1,B1:B3)', "S") == 110 and ev.evaluate('COUNTIF(A1:A3,"<"&D1)', "S") == 1
assert ev.evaluate("SUMIF(C1:C3,D2,E1:E3)", "S") == 3 and ev.evaluate("COUNTIF(B1:B3,D3)", "S") == 0
assert serial(datetime(2026, 6, 2)) == 46175
print("PASSED")

print("TEST: SUMIFS / COUNTIFS combine all the conditions")
data = {"A1": "a", "A2": "a", "A3": "b", "A4": "a", "B1": 1, "B2": 2, "B3": 1, "B4": 1, "C1": 10, "C2": 20, "C3": 40, "C4": 80}
ev = Evaluator(book(S=data))
assert ev.evaluate('SUMIFS(C1:C4,A1:A4,"a",B1:B4,1)', "S") == 90 and ev.evaluate('COUNTIFS(A1:A4,"a",B1:B4,">1")', "S") == 1
assert ev.evaluate('COUNTIFS(A1:A4,"a")', "S") == 3 and ev.evaluate('SUMIFS(C1:C4,A1:A4,"zzz")', "S") == 0
assert ev.evaluate('SUMIFS(C1:C4,A1:A3,"a")', "S") == "#VALUE!" and ev.evaluate('COUNTIF(A1:A4)', "S") == "#VALUE!"
print("PASSED")

print("TEST: a formula without a stored result is calculated from the cells it uses; the stored result wins when there is one")
wb = book(S={"A1": 2, "A2": 3, "B1": "=A1*A2", "B2": "=B1+1", "B3": "=SUM(B1:B2)"})
ev = Evaluator(wb)
assert ev.cell_value("S", 2, 2) == 7 and ev.cell_value("S", 3, 2) == 13 and ev.evaluate("B3*2", "S") == 26
loaded = read_workbook(fx.build_workbook())
assert Evaluator(loaded).cell_value(fx.MONTHLY, 5, 2) == 0 and Evaluator(loaded).cell_value(fx.DAILY, 15, 4) == "#REF!"
print("PASSED")

print("TEST: what is not supported raises Unsupported (never a guess); circular references too")
ev = Evaluator(book(S={"A1": 1, "B1": "=A1+B2", "B2": "=B1+1"}))
for formula in ("IF(A1>0,1,2)", "VLOOKUP(A1,A1:B2,2)", "SUM(A1:B2)+MyName", "AVERAGE(A1:A2)", "SUM(1:1)", "A1:B2"):
    try:
        ev.evaluate(formula, "S")
        raise AssertionError(formula)
    except Unsupported:
        pass
try:
    ev.cell_value("S", 1, 2)
    raise AssertionError("circular")
except Unsupported:
    pass
print("PASSED")

print("TEST: make_matcher on its own")
assert make_matcher("a*")("Apple") and not make_matcher("a*")("banana") and make_matcher(">1")(2) and not make_matcher(">1")("x")
assert make_matcher(None)(0) and make_matcher(True)(True) and not make_matcher(ExcelError("#N/A"))("#N/A")
print("PASSED")

print("TEST: the evaluator agrees with the `formulas` package on random data and criteria (independent oracle)")
# The oracle is NOT a perfect copy of Excel: it treats wildcard patterns as case-sensitive, counts numeric text ("7") as numbers in
# ranges, and handles "<>" on blanks, "*" on blanks and "?" differently. Those Excel rules are checked above from Excel's documented behaviour. Here the
# data and criteria are chosen where the oracle and Excel are known to agree (lower-case text, real numbers), so any difference
# found is a real bug in this evaluator.
import formulas  # test-only dependency (requirements-dev.txt)

random.seed(7)
words = ["food", "food/grocery", "transport", "health", "fruit", "fruit salad", None, "x*y", "food"]
rows = 30
cells = {}
for r in range(1, rows + 1):
    cells[f"A{r}"] = random.choice(words)
    cells[f"B{r}"] = random.choice([None, 0, 1, 2.5, 10, -3, 7])
    cells[f"C{r}"] = random.choice([1, 2, 3, 4, 5, None])
text_criteria = ['"food"', '"food*"', '"*oo*"', '"=health"', '"fr*"', '"x~*y"', '"zzz"']
number_criteria = ['">2"', '"<=2.5"', '">=0"', "5", '"5"', "2.5", '"<0"']
formulas_list = []
for crit in text_criteria:
    formulas_list += [f"SUMIF(A1:A{rows},{crit},C1:C{rows})", f"COUNTIF(A1:A{rows},{crit})",
                      f"SUMIFS(C1:C{rows},A1:A{rows},{crit},C1:C{rows},\">1\")"]
for crit in number_criteria:
    formulas_list += [f"SUMIF(B1:B{rows},{crit},C1:C{rows})", f"COUNTIF(B1:B{rows},{crit})", f"COUNTIFS(B1:B{rows},{crit},C1:C{rows},\">2\")"]
formulas_list += [f"SUM(B1:B{rows})", f"SUM(B1:C{rows})", f"SUM(C1:C{rows},B1)", f"COUNTIFS(A1:A{rows},\"food\",C1:C{rows},3)"]
wb = Workbook()
ws = wb.active
ws.title = "S"
for coord, value in cells.items():
    if value is not None:
        ws[coord] = value
for i, formula in enumerate(formulas_list, start=1):
    ws[f"F{i}"] = "=" + formula
folder = tempfile.mkdtemp()
path = os.path.join(folder, "oracle.xlsx")
wb.save(path)
warnings.filterwarnings("ignore")
solution = formulas.ExcelModel().loads(path).finish().calculate()
mine = Evaluator(read_workbook(open(path, "rb").read()))
checked = disagreements = 0
for i, formula in enumerate(formulas_list, start=1):
    key = next(k for k in solution if k.upper().endswith(f"S'!F{i}"))
    oracle = solution[key].value[0][0]
    ours = mine.evaluate(formula, "S")
    checked += 1
    same = abs(float(oracle) - float(ours)) < 1e-9 if not isinstance(oracle, str) and not isinstance(ours, str) else str(oracle) == str(ours)
    if not same:
        disagreements += 1
        print("   DISAGREE:", formula, "oracle", oracle, "ours", ours)
assert checked == len(formulas_list) > 40 and disagreements == 0
print(f"PASSED ({checked} formulas compared)")
