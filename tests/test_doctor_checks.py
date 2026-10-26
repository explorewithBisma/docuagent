from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""Excel Doctor: every check on small hand-built workbooks, then the whole report on the synthetic budget-shaped workbook
against its list of planted defects."""
import io
import sys
from datetime import datetime

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

from openpyxl import Workbook

import doctor_fixtures as fx
from doctor.analyze import analyze
from doctor.context import Options


def make(sheets, cached=None):
    """Workbook bytes from {sheet: {coord: value}}; `cached` = {(sheet, coord): stored result} for formulas (like a file saved by Excel)."""
    wb = Workbook()
    wb.remove(wb.active)
    for name, cells in sheets.items():
        ws = wb.create_sheet(name)
        for coord, value in cells.items():
            ws[coord] = value
    buffer = io.BytesIO()
    wb.save(buffer)
    data = buffer.getvalue()
    return fx.inject_stored_results(data, cached) if cached else data


def issues_of(data, kind=None, **options):
    report, _ = analyze(data, Options(**options) if options else None)
    return [i for i in report.issues if kind is None or i.kind == kind]


def column(letter, values, start=1):
    return {f"{letter}{start + i}": v for i, v in enumerate(values)}


# ---------------------------------------------------------------------------------------------------------------------
print("TEST: whole report on the synthetic budget workbook = exactly the planted defects, nothing else")
report, ctx = analyze(fx.build_workbook())
found = {(i.kind, s, c) for i in report.issues for s, c in i.locations}
for planted in fx.PLANTED:
    assert planted in found, ("missed", planted, sorted(found))
assert len(report.issues) == 9, [(i.kind, i.where) for i in report.issues]
assert sorted(i.kind for i in report.issues) == sorted(["criteria_no_match", "cross_total", "cross_total", "cross_total", "formula_error",
                                                       "range_short", "typed_among_formulas", "number_as_text", "budget_conflict"])
clean = {(fx.INCOME, c) for c in ("B4", "B7")} | {(fx.LOAN, "C9"), (fx.DASH, "B4"), (fx.LOG, f"E{fx.LOG_TOTAL_ROW}"), (fx.MONTHLY, "B4"), (fx.MONTHLY, "B7")}
assert not (found & clean), found & clean
print("PASSED")

print("TEST: the same workbook saved WITHOUT Excel's stored results (never calculated) gives the same findings - the Doctor calculates")
uncached, _ = analyze(fx.build_workbook(with_cached_values=False))
assert {(i.kind, s, c) for i in uncached.issues for s, c in i.locations} == found
print("PASSED")

print("TEST: order of importance - serious first, the biggest amount first within a level, small things last")
severities = [i.severity for i in report.issues]
assert severities == sorted(severities, key=["high", "medium", "low"].index) and severities[-1] == "low"
high = [i for i in report.issues if i.severity == "high"]
assert [i.impact for i in high] == sorted([i.impact for i in high], reverse=True)
assert high[0].kind == "cross_total" and high[0].locations == [(fx.ACCOUNT, "B4")], "the typed 11,755 is the biggest gap"
print("PASSED")

print("TEST: the evidence carries the real numbers")
t = fx.truth()
by_kind = {}
for i in report.issues:
    by_kind.setdefault(i.kind, []).append(i)
typed = next(i for i in by_kind["cross_total"] if i.locations == [(fx.ACCOUNT, "B4")])
assert "11,755" in typed.title and "1,080" in typed.title and "typed" in typed.detail and "10,675" in typed.detail
criteria = by_kind["criteria_no_match"][0]
assert '"🍔 Food"' in criteria.title and '"Food/Grocery"' in criteria.detail and "symbols" not in criteria.detail.split("(")[0]
assert f"{t['by_category']['Food/Grocery']:,.2f}".rstrip("0").rstrip(".") in criteria.detail
assert criteria.severity == "high" and abs(criteria.impact - t["by_category"]["Food/Grocery"]) < 0.01
short = by_kind["range_short"][0]
assert "ends at row 30" in short.detail and "3 more row(s)" in short.detail and short.locations == [(fx.DAILY, "B34")]
conflict = by_kind["budget_conflict"][0]
assert "Transport: 0 on" in conflict.detail and "vs 500 on" in conflict.detail
err = by_kind["formula_error"][0]
assert err.title == f"#REF! in {fx.DAILY}!D15" and err.evidence == "D15: =C15-#REF!"
text = by_kind["number_as_text"][0]
assert text.evidence == 'E20: "17.5"' and text.severity == "low"
print("PASSED")

print("TEST: facts - what each sheet does, in plain words, with evidence")
facts = {f.label: f for f in report.facts}
assert facts[fx.LOG].value.startswith("a transaction list: 40 rows") and "header row 3" in facts[fx.LOG].source and facts[fx.LOG].confidence == "high"
assert facts[fx.MONTHLY].value.startswith("a summary that calculates from") and facts[fx.DAILY].value.startswith("a summary that calculates from")
assert facts["This file"].value == "7 sheets, 85 formulas, 1 chart(s)"
assert any("SUMIF/COUNTIF criteria match nothing and have no look-alike" in n for n in report.notes), "Housing: no spending, not reported but counted"
print("PASSED")

# ---------------------------------------------------------------------------------------------------------------------
print("TEST: errors - stored errors are grouped per sheet and kind; missing sheets and links to other files")
data = make({"S": {"A1": 1, "B1": "=A1/0", "B2": "=A1/0", "B3": "=A1+1", "C1": "=Gone!A1", "C2": "=SUM(A1:A2)+[1]Other!A1"}},
            cached={("S", "B1"): "#DIV/0!", ("S", "B2"): "#DIV/0!", ("S", "B3"): 2, ("S", "C1"): "#REF!", ("S", "C2"): 1})
errors = issues_of(data, "formula_error")
assert sorted((i.title, i.locations) for i in errors) == [("#DIV/0! in 2 cell(s) on 'S'", [("S", "B1"), ("S", "B2")]), ("#REF! in S!C1", [("S", "C1")])]
assert "divides by zero" in next(i for i in errors if "#DIV/0!" in i.title).detail
assert issues_of(data, "missing_sheet") == [], "a cell already reported as #REF! is not reported a second time for the same cause"
assert len(issues_of(data, "external_link")) == 1
# a formula naming a sheet that is not there (without a #REF! on show)
data = make({"S": {"A1": 1, "B1": "='Old name'!A1+1"}}, cached={("S", "B1"): 2})
missing = issues_of(data, "missing_sheet")
assert len(missing) == 1 and "'Old name'" in missing[0].title
print("PASSED")

print("TEST: typed totals - a labelled total that is typed: matches (medium) or differs (high); formulas and short blocks are fine")
rows = {**column("A", ["a", "b", "c", "d", "TOTAL"]), **column("B", [10, 20, 30, 40, 100])}
(match,) = issues_of(make({"S": rows}), "typed_total")
assert match.severity == "medium" and "matches the 4 rows above" in match.detail and match.locations == [("S", "B5")]
rows["B5"] = 95
(mismatch,) = issues_of(make({"S": rows}), "typed_total")
assert mismatch.severity == "high" and "add up to 100" in mismatch.detail and "difference of 5" in mismatch.detail and mismatch.impact == 5
rows["B5"] = "=SUM(B1:B4)"
assert issues_of(make({"S": rows}), "typed_total") == []
short_block = {**column("A", ["opening", "income", "Total Expenses"]), **column("B", [100, 50, 999])}
assert issues_of(make({"S": short_block}), "typed_total") == [], "two numbers above are not a block that a total should add up"
emoji = {**column("A", ["a", "b", "c", "✅ TOTAL INCOME"]), **column("B", [1, 2, 3, 7])}
assert issues_of(make({"S": emoji}), "typed_total")[0].severity == "high", "emoji in front of the label is ignored"
print("PASSED")

print("TEST: a range that stops short - rows left out are counted with their amount; clean cases stay quiet")
body = {**column("B", [5, 10, 15, 20, 25, 30]), "B8": "=SUM(B1:B4)"}
(short,) = issues_of(make({"S": body}, cached={("S", "B8"): 30}), "range_short")
assert "ends at row 4" in short.detail and "2 more row(s)" in short.detail and "worth 55" in short.detail and short.impact == 55
assert issues_of(make({"S": {**column("B", [5, 10, 15]), "B5": "=SUM(B1:B3)"}}, cached={("S", "B5"): 30}), "range_short") == []
assert issues_of(make({"S": {**column("B", [5, 10, 15]), "B4": "=SUM(B1:B3)"}}, cached={("S", "B4"): 30}), "range_short") == [], "the total directly below"
assert issues_of(make({"S": {**column("B", [5, 10, 15, 99]), "A4": "Total", "C1": "=SUM(B1:B3)"}}, cached={("S", "C1"): 30}), "range_short") == [], "a total row below is not data"
assert issues_of(make({"S": {**column("B", [5, 10, 15, 20]), "C1": "=SUM(B:B)"}}, cached={("S", "C1"): 50}), "range_short") == []
(start,) = issues_of(make({"S": {**column("B", [100, 5, 10]), "D1": "=SUM(B2:B3)"}}, cached={("S", "D1"): 15}), "range_short")
assert "starts below the first number" in start.title and start.impact == 100
print("PASSED")

print("TEST: siblings - the formula that differs from its column; subtotals counted twice")
rows = {f"A{r}": r for r in range(1, 8)}
rows.update({f"B{r}": f"=A{r}*2" for r in range(1, 8)})
rows["B4"] = "=A4*3"
data = make({"S": rows}, cached={("S", f"B{r}"): r * (3 if r == 4 else 2) for r in range(1, 8)})
(sibling,) = issues_of(data, "inconsistent_formula")
assert sibling.locations == [("S", "B4")] and "6 of the 7" in sibling.detail and "=A4*3" in sibling.evidence
rows = {**column("B", [1, 2, 3, 4]), "B5": "=SUM(B1:B4)", "B6": 7, "B7": "=SUM(B1:B6)"}
(double,) = issues_of(make({"S": rows}, cached={("S", "B5"): 10, ("S", "B7"): 27}), "double_count")
assert double.locations == [("S", "B7"), ("S", "B5")] and double.impact == 10
print("PASSED")

print("TEST: criteria - spaces and different words are found with the closest value; capitals and wildcards are fine")
log = {**column("A", ["Food", "Food ", "Transport", "Health", "Food"]), **column("B", [10, 20, 30, 40, 50])}


def sumif(criterion, rows=None):
    sheets = {"Log": rows or log, "S": {"B1": f"=SUMIF(Log!A1:A5,{criterion},Log!B1:B5)"}}
    return issues_of(make(sheets), "criteria_no_match")


assert sumif('"FOOD"') == [] and sumif('"Fo*"') == [] and sumif('"Transport"') == []
(spaced,) = sumif('"Food  "')
assert "closest value that exists" in spaced.detail and "extra spaces" in spaced.detail
(emoji,) = sumif('"🍔 Food"')
assert '"Food"' in emoji.detail and "symbols, emojis or punctuation" in emoji.detail
assert sumif('"Housing"') == [], "no look-alike: a category with no spending yet is not a mistake"
data = make({"Log": log, "S": {"A1": "🍔 Food", "A2": "🍔 Food", "B1": "=SUMIF(Log!A1:A5,A1,Log!B1:B5)", "B2": "=COUNTIF(Log!A1:A5,A2)"}})
(grouped,) = issues_of(data, "criteria_no_match")
assert grouped.locations == [("S", "B1"), ("S", "B2")], "formulas with the same wrong criterion are one finding"
assert "differ only by symbols, emojis or punctuation" in grouped.detail and '"Food"' in grouped.detail and "60 in total" in grouped.detail
only_count = issues_of(make({"Log": log, "S": {"B1": '=COUNTIFS(Log!A1:A5,"🍔 Food",Log!B1:B5,">5")'}}), "criteria_no_match")
assert len(only_count) == 1
print("PASSED")

print("TEST: stale results - the saved number differs from the formula; matching or missing results are quiet")
data = make({"S": {"A1": 2, "A2": 3, "B1": "=A1+A2"}}, cached={("S", "B1"): 6})
(stale,) = issues_of(data, "stale_result")
assert stale.severity == "low" and "shows 6 but the formula gives 5" in stale.title
assert issues_of(make({"S": {"A1": 2, "A2": 3, "B1": "=A1+A2"}}, cached={("S", "B1"): 5}), "stale_result") == []
assert issues_of(make({"S": {"A1": 2, "A2": 3, "B1": "=A1+A2"}}), "stale_result") == []
print("PASSED")

print("TEST: small things - hidden sheets / rows, text dates, numbers as text (but not phone numbers or codes), low findings can be switched off")
wb = Workbook()
ws = wb.active
ws.title = "Main"
for column, header in enumerate(["Date", "Amount", "Phone number", "Item code"], start=1):
    ws.cell(row=1, column=column, value=header)
for r in range(2, 9):
    ws.cell(row=r, column=1, value=datetime(2026, 6, r))
    ws.cell(row=r, column=2, value=r * 10)
    ws.cell(row=r, column=3, value=r * 10)
    ws.cell(row=r, column=4, value=1000 + r)
ws["A9"], ws["B9"], ws["C9"], ws["D9"] = "03/06/2026", "70", "80", "1008"
ws.row_dimensions[4].hidden = True
secret = wb.create_sheet("Secret")
secret["A1"] = 1
secret.sheet_state = "hidden"
buffer = io.BytesIO()
wb.save(buffer)
data = buffer.getvalue()
kinds = sorted(i.kind for i in issues_of(data))
assert kinds == ["date_as_text", "hidden_data", "hidden_sheet", "number_as_text"], kinds
assert all(i.severity == "low" for i in issues_of(data))
assert issues_of(data, include_low=False) == []
print("PASSED")

print("TEST: the same label with different numbers on two sheets; equal numbers are quiet")
data = make({"A": {"A1": "Total income this month", "B1": 100}, "B": {"A1": "Total Income This Month", "B1": 120}})
(conflict,) = issues_of(data, "label_conflict")
assert conflict.impact == 20 and "100 on 'A'" in conflict.detail and "120 on 'B'" in conflict.detail
assert issues_of(make({"A": {"A1": "Total income", "B1": 100}, "B": {"A1": "TOTAL INCOME", "B1": 100.001}}), "label_conflict") == []
print("PASSED")

print("TEST: formulas the evaluator does not calculate are still read (and listed), never guessed")
data = make({"S": {"A1": 1, "B1": "=IF(A1>0,VLOOKUP(A1,A1:A2,1),0)", "B2": "=AVERAGE(A1:A2)"}}, cached={("S", "B1"): 1, ("S", "B2"): 1})
report, _ = analyze(data)
assert report.issues == [] and any("IF ×1" in n and "VLOOKUP ×1" in n and "not recalculated" in n for n in report.notes), report.notes
print("PASSED")

print("TEST: a workbook that is not readable gives a readable error")
for bad in (b"hello", b"\xd0\xcf\x11\xe0 old"):
    try:
        analyze(bad)
        raise AssertionError("should fail")
    except ValueError as error:
        assert str(error)
print("PASSED")
