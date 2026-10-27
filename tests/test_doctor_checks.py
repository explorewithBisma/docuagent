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
assert len(report.issues) == 11, [(i.kind, i.where) for i in report.issues]
assert sorted(i.kind for i in report.issues) == sorted(["criteria_no_match", "cross_total", "cross_total", "cross_total", "formula_error",
                                                       "range_short", "typed_among_formulas", "number_as_text", "budget_conflict",
                                                       "blank_criteria", "blank_criteria"])
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
assert facts["This file"].value == "7 sheets, 86 formulas, 1 chart(s)"
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


# ---------------------------------------------------------------------------------------------------------------------
# Regressions found by running the Doctor on a real budget file (the structure is reproduced here with invented data)
# ---------------------------------------------------------------------------------------------------------------------
def tracker(rows=12, shop="Grand barsha"):
    """A daily log (header row 3, rows 4..) whose last data row is followed by an UNLABELLED =SUM total, plus a summary sheet
    that reads the log with SUMIFs and a date label next to a number."""
    log = {"A3": "Date", "B3": "Description", "C3": "Category", "D3": "Amount (AED)"}
    total = 0
    for i in range(rows):
        row = 4 + i
        log[f"A{row}"] = datetime(2026, 6, 1 + i // 3)
        log[f"B{row}"] = shop if i % 4 == 0 else f"item {i}"
        log[f"C{row}"] = "Food" if i % 2 else "Transport"
        log[f"D{row}"] = 10 + i
        total += 10 + i
    last = 3 + rows
    log[f"D{last + 1}"] = f"=SUM(D4:D{last})"                    # the total row has no label at all
    cached = {("Log", f"D{last + 1}"): total}
    summary = {"A1": "Loan Start Date", "B1": datetime(2026, 6, 19), "A2": "Category", "B2": "Spent (AED)"}
    for n, category in enumerate(["Food", "Transport"]):
        summary[f"A{3 + n}"] = category
        summary[f"B{3 + n}"] = f"=SUMIF(Log!C4:C{last},A{3 + n},Log!D4:D{last})"
        stored = sum(10 + i for i in range(rows) if (i % 2 == 1) == (category == "Food"))
        cached[("Summary", f"B{3 + n}")] = stored
    return {"Log": log, "Summary": summary}, cached, total, last


print("TEST: a shop called 'Grand barsha' is not a total (only 'total', 'subtotal', 'sum' and 'grand total' are), a date next to a label is not a figure")
sheets, cached, total, last = tracker()
report, ctx = analyze(make(sheets, cached))
assert report.issues == [], [(i.kind, i.title) for i in report.issues]
assert ctx.tx_tables[0].amount_total == total and len(ctx.tx_tables[0].rows) == 12, "all 12 rows are in the list - the shop did not end it"
print("PASSED")

print("TEST: an UNLABELLED total row (=SUM of the rows above, or a typed amount that equals them) ends the list; it is not more data")
assert ctx.tx_tables[0].last_row == last
sheets["Log"][f"D{last + 1}"] = total                            # typed instead of a formula, still no label
report, ctx = analyze(make(sheets))
assert ctx.tx_tables[0].amount_total == total and ctx.tx_tables[0].last_row == last
print("PASSED")

print("TEST: 10 formulas that read a range whose total sits directly below it are NOT 'ranges that stop short'")
sheets, cached, total, last = tracker()
for n in range(10):
    sheets["Summary"][f"B{10 + n}"] = f"=SUMIF(Log!C4:C{last},A{3},Log!D4:D{last})"
    cached[("Summary", f"B{10 + n}")] = 0
assert issues_of(make(sheets, cached), "range_short") == []
print("PASSED")

print("TEST: a range that really stops short is ONE finding, however many formulas use it")
many = {"A1": "Item", "B1": "Amount"}
many.update({f"B{r}": r for r in range(2, 12)})                  # data in B2:B11
for n in range(8):
    many[f"D{2 + n}"] = "=SUM(B2:B9)"                              # eight formulas that all stop at row 9
(grouped,) = issues_of(make({"S": many}, cached={("S", f"D{2 + n}"): 44 for n in range(8)}), "range_short")
assert len(grouped.locations) == 8 and grouped.title.startswith("8 formulas:") and "2 more row(s)" in grouped.detail and grouped.impact == 21
print("PASSED")

print("TEST: typed numbers among formulas are one finding per column; a total row at the end of a formula column is not 'different'")
rows = {f"A{r}": r for r in range(1, 9)}
rows.update({f"B{r}": f"=A{r}*2" for r in range(1, 8)})
rows["B3"], rows["B5"] = 99, 77                                   # two numbers typed over formulas
rows["B8"] = "=SUM(B1:B7)"                                        # the total row: a different formula by design
rows["A8"] = "Total"
data = make({"S": rows}, cached={**{("S", f"B{r}"): r * 2 for r in range(1, 8) if r not in (3, 5)}, ("S", "B8"): 0})
typed = issues_of(data, "typed_among_formulas")
assert len(typed) == 1 and typed[0].locations == [("S", "B3"), ("S", "B5")] and "2 typed numbers between formulas in column B" in typed[0].title
assert issues_of(data, "inconsistent_formula") == []
print("PASSED")


# ---------------------------------------------------------------------------------------------------------------------
# One cause, one finding; criteria columns with blanks
# ---------------------------------------------------------------------------------------------------------------------
print("TEST: a Dashboard total that only reads the Monthly Summary figures is ONE finding with the Monthly Summary, not two")
report, _ = analyze(fx.build_workbook())
joined = [i for i in report.issues if i.kind == "cross_total" and (fx.MONTHLY, "B8") in i.locations]
assert len(joined) == 1 and joined[0].locations == [(fx.MONTHLY, "B8"), (fx.DASH, "B12")]
assert joined[0].title == f"{fx.MONTHLY}!B8 is short by 532.50; {fx.DASH}!B12 shows the same number", joined[0].title
assert "just reads it" in joined[0].detail and "fixing" in joined[0].detail and joined[0].evidence.endswith(f"{fx.DASH}!B12 = 547.50")
assert len([i for i in report.issues if i.kind == "cross_total"]) == 3, "the typed 11,755, the Daily Summary, and the joined pair"
print("PASSED")


def two_summaries(second_formula, second_value):
    log = {"A3": "Date", "B3": "Description", "C3": "Category", "D3": "Amount (AED)"}
    for i in range(8):
        log.update({f"A{4 + i}": datetime(2026, 6, 1 + i), f"B{4 + i}": f"item {i}", f"C{4 + i}": "Food", f"D{4 + i}": 100})
    sheets = {"Log": log,
              "First": {"A1": "Category", "B1": "Spent (AED)", "A2": "Food", "B2": "=SUMIF(Log!C4:C11,A2,Log!D4:D11)*0+500", "A3": "Total", "B3": "=SUM(B2:B2)"},
              "Second": {"A1": "Category", "B1": "Spent (AED)", "A2": "Food", "B2": second_formula, "A3": "Total", "B3": "=SUM(B2:B2)"}}
    return make(sheets, cached={("First", "B2"): 500, ("First", "B3"): 500, ("Second", "B2"): second_value, ("Second", "B3"): second_value})


print("TEST: not joined when the second total does not come from the first (a coincidence of numbers), or the numbers differ")
report, _ = analyze(two_summaries("=250+250", 500))                           # also 500, but typed arithmetic, not read from 'First'
assert len([i for i in report.issues if i.kind == "cross_total"]) == 2
report, _ = analyze(two_summaries("=First!B2+1", 501))                        # reads 'First' but shows a different number
assert len([i for i in report.issues if i.kind == "cross_total"]) == 2
print("PASSED")

print("TEST: joined when the second total reads the first through a chain of cells; the first one never joins the second")
report, _ = analyze(two_summaries("=First!B2", 500))
(one,) = [i for i in report.issues if i.kind == "cross_total"]
assert one.locations == [("First", "B3"), ("Second", "B3")] and "is short by 300" in one.title and "Second!B3 shows the same number" in one.title
print("PASSED")


def dated_log(blank_rows, categories=False, amount=10):
    """A log of 12 rows where `blank_rows` of them have an empty date (or category) cell, and a summary that looks up by that column."""
    log = {"A3": "Date" if not categories else "Category", "B3": "Amount (AED)"}
    for i in range(12):
        value = None if i in blank_rows else (datetime(2026, 6, 1 + i) if not categories else f"cat{i % 2}")
        log[f"A{4 + i}"] = value
        log[f"B{4 + i}"] = amount
    summary = {"A1": "Key", "B1": "Spent"}
    cached = {}
    for n in range(3):
        key = datetime(2026, 6, 1 + n) if not categories else f"cat{n % 2}"
        summary[f"A{2 + n}"] = key
        summary[f"B{2 + n}"] = f"=SUMIF(Log!A4:A15,A{2 + n},Log!B4:B15)"
        cached[("Summary", f"B{2 + n}")] = amount
    return make({"Log": log, "Summary": summary}, cached)


print("TEST: dates only on some rows - the rows and the money a SUMIF by date can never reach, and the suggestion to fill dates down")
(blank,) = issues_of(dated_log(blank_rows={1, 2, 4, 5, 7, 8, 10, 11}), "blank_criteria")
assert blank.title == "3 formulas skip 8 rows because Log!A4:A15 is blank on them" and blank.impact == 80
assert "8 of the 12 rows that have an amount (80 of 120) have no date" in blank.detail and "written only on the first row of each day" in blank.detail
assert blank.fix == "Fill the dates down in Log!A4:A15 (every row repeats the date above it)."
assert blank.severity == "high" and blank.locations == [("Summary", "B2"), ("Summary", "B3"), ("Summary", "B4")]
assert "blank rows: 5, 6, 8, 9, 11, 12, 14, 15" in blank.evidence
print("PASSED")

print("TEST: the same for a category column (no date suggestion), and severity follows the share of the money")
(cat,) = issues_of(dated_log(blank_rows={0, 3, 6}, categories=True), "blank_criteria")
assert cat.severity == "high", "exactly a quarter of the money is the line between medium and high"
assert "are blank there" in cat.detail and "Give those rows a value" in cat.fix and "fill the dates" not in cat.fix.lower()
assert cat.impact == 30
sheets = {"Log": {"A3": "Cat", "B3": "Amt", **{f"A{4 + i}": (None if i < 3 else "x") for i in range(20)}, **{f"B{4 + i}": 10 for i in range(20)}},
          "S": {"A1": '=SUMIF(Log!A4:A23,"x",Log!B4:B23)'}}
(small,) = issues_of(make(sheets, {("S", "A1"): 170}), "blank_criteria")
assert small.severity == "medium" and small.impact == 30, "3 of 20 rows (15% of the money) is worth checking, not serious"
print("PASSED")

print("TEST: stray gaps and criteria that ask for blanks on purpose are not reported; COUNTIF has no amount to lose")
assert issues_of(dated_log(blank_rows={1, 2}), "blank_criteria") == [], "two blank rows are a gap, not a pattern"
assert issues_of(dated_log(blank_rows=set()), "blank_criteria") == []
by_hand = {"Log": {"A3": "Cat", "B3": "Amt", **{f"A{4 + i}": (None if i % 2 else "x") for i in range(10)}, **{f"B{4 + i}": 5 for i in range(10)}},
           "S": {"A1": '=SUMIF(Log!A4:A13,"",Log!B4:B13)'}}
assert issues_of(make(by_hand, {("S", "A1"): 25}), "blank_criteria") == [], "asking for the blank rows is deliberate"
counting = {"Log": by_hand["Log"], "S": {"A1": '=COUNTIF(Log!A4:A13,"x")'}}
assert issues_of(make(counting, {("S", "A1"): 5}), "blank_criteria") == []
whole_column = {"Log": by_hand["Log"], "S": {"A1": '=SUMIF(Log!A:A,"x",Log!B:B)'}}
(wc,) = issues_of(make(whole_column, {("S", "A1"): 25}), "blank_criteria")
assert wc.impact == 25 and "5 rows" in wc.title.replace("skips ", "skips ")
print("PASSED")

print("TEST: SUMIFS - each criteria column is checked against the sum column")
sheets = {"Log": {"A3": "Date", "B3": "Cat", "C3": "Amt", **{f"A{4 + i}": (None if i % 2 else datetime(2026, 6, 1)) for i in range(10)},
                  **{f"B{4 + i}": "x" for i in range(10)}, **{f"C{4 + i}": 10 for i in range(10)}},
          "S": {"A1": '=SUMIFS(Log!C4:C13,Log!B4:B13,"x",Log!A4:A13,DATE(2026,6,1))'}}
assert issues_of(make(sheets, {("S", "A1"): 50}), "blank_criteria") == [], "DATE() is not calculated here, so nothing is claimed"
sheets["S"]["A1"] = '=SUMIFS(Log!C4:C13,Log!B4:B13,"x",Log!A4:A13,">0")'
(multi,) = issues_of(make(sheets, {("S", "A1"): 50}), "blank_criteria")
assert multi.impact == 50 and "5 rows" in multi.title and "Log!A4:A13" in multi.title
print("PASSED")


# ---------------------------------------------------------------------------------------------------------------------
# False positives seen on the Doctor's own Budget-mode output
# ---------------------------------------------------------------------------------------------------------------------
print("TEST: a category name shared by a spending figure and a budget is not 'the same figure'; a measure label still is")
sheets = {"Spending": {"A1": "Family / Remittance", "B1": 155}, "Settings": {"A1": "Family / Remittance", "B1": 0},
          "Totals A": {"A1": "Total expenses this month", "B1": 10}, "Totals B": {"A1": "Total expenses this month", "B1": 12}}
conflicts = issues_of(make(sheets), "label_conflict")
assert [i.title for i in conflicts] == ["'Total expenses this month' has different numbers on different sheets"]
assert issues_of(make({"A": {"A1": "Food budget total", "B1": 10}, "B": {"A1": "Food budget total", "B1": 99}}), "label_conflict") == [], "budgets are settings"
print("PASSED")

print("TEST: a row of the transaction list is never compared with a figure on another sheet")
log = {"A3": "Date", "B3": "Description", "C3": "Amount (AED)", **{f"A{4 + i}": datetime(2026, 6, 1 + i) for i in range(6)},
       **{f"B{4 + i}": f"item {i}" for i in range(6)}, **{f"C{4 + i}": 10 for i in range(6)}}
log["B4"] = "Net spent today"                                      # an item whose text looks like a measure
assert issues_of(make({"Log": log, "Other": {"A1": "Net spent today", "B1": 999}}), "label_conflict") == []
print("PASSED")

print("TEST: a summary row after the end of a table, or at its edge, is not 'different from the formulas around it'; a slip in the middle is")
rows = {f"A{r}": r for r in range(1, 9)}
rows.update({f"B{r}": f"=A{r}*2" for r in range(1, 9)})
rows["B9"], rows["A9"] = "=B1-B8", "Left over"                       # a separate summary row below the table, with its own label
cached = {("S", f"B{r}"): r * 2 for r in range(1, 9)}
cached[("S", "B9")] = -14
assert issues_of(make({"S": rows}, cached), "inconsistent_formula") == []
rows["B9"] = "=A9*2"
assert issues_of(make({"S": rows}, {**cached, ("S", "B9"): 18}), "inconsistent_formula") == [], "the last cell of a run is an edge"
rows["B9"], rows["B4"] = "=A9*2", "=A4*3"
(slip,) = issues_of(make({"S": rows}, {**cached, ("S", "B9"): 18, ("S", "B4"): 12}), "inconsistent_formula")
assert slip.locations == [("S", "B4")], "a formula in the middle that differs is still found"
after_total = {f"A{r}": r for r in range(1, 9)}
after_total.update({f"B{r}": f"=A{r}*2" for r in range(1, 6)})
after_total.update({"B6": "=SUM(B1:B5)", "A6": "Total", "B7": "=B6/2", "B8": "=B6*3", "B9": "=B6+1"})
cached2 = {("S", f"B{r}"): r * 2 for r in range(1, 6)}
cached2.update({("S", "B6"): 30, ("S", "B7"): 15, ("S", "B8"): 90, ("S", "B9"): 31})
assert issues_of(make({"S": after_total}, cached2), "inconsistent_formula") == [], "formulas below a total are their own summary rows"
print("PASSED")
