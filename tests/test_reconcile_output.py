from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""Match two files: the card, the Please-check list and the Excel result."""
import io
import sys

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import numpy as np
from openpyxl import load_workbook

import reconcile_fixtures as fx
from reconcile import columns as rc, output as ro
from reconcile.pipeline import Settings, analyze
from reconcile_helpers import to_raw

bank, ledger, truth = fx.bank_vs_ledger()
bank_before, ledger_before = bank.copy(), ledger.copy()
table_a, header_a = rc.table_from_raw(to_raw(bank, 2))
table_b, header_b = rc.table_from_raw(to_raw(ledger))
analysis = analyze("bank.csv", table_a, header_a, "ledger.xlsx", table_b, header_b, Settings())
result, report, A, B = analysis.result, analysis.report, analysis.A, analysis.B

print("TEST: the summary sentence and the money totals of every bucket")
print("  ", analysis.summary)
assert analysis.summary == "226 matched (196 exact, 30 close), 10 to confirm, 21 only in A, 15 only in B, 10 amount mismatches"
totals = report.stats["totals"]
assert totals["exact"]["count"] == 196 and totals["only_a"]["count"] == 21 and totals["mismatch"]["count"] == 10
assert abs(totals["exact"]["a"] - totals["exact"]["b"]) < 0.01                             # exact matches have the same money on both sides
assert abs(totals["only_a"]["a"] - float(np.nansum([A.amounts[i] for i in result.only_a]))) < 1e-6
assert totals["mismatch"]["difference"] != 0 and abs(totals["mismatch"]["difference"]) > 10 * 50 / 100
print("PASSED")

print("TEST: the card says where every column came from, with confidence; the sign reading and the rules are listed")
labels = {f.label: f for f in report.facts}
print("  ", [f"{f.label}={f.value}" for f in report.facts][:6])
assert labels["File A · date"].value == "'Date'" and labels["File A · date"].confidence == "high" and "dates" in labels["File A · date"].source
assert "File A · money out" in labels and "File A · money in" in labels and "File B · amount" in labels
assert labels["File B · reference / id"].value == "'Doc No'" and labels["File A · description"].value == "'Description'"
assert "Money in / out" in labels and "Matching" in labels and "always a 'maybe'" in labels["Matching"].value
print("PASSED")

print("TEST: Please check - mismatches and the biggest unmatched amounts first, each with the evidence and the row numbers; capped, with a note")
issues = report.issues
assert len(issues) == 10 + 21 + 15 + 10 and report.stats["all_issues"] == 56
for severity in ("high", "medium", "low"):
    impacts = [x.impact for x in issues if x.severity == severity]
    assert impacts == sorted(impacts, reverse=True), severity                          # within a level the biggest amount first
unmatched = [x for x in issues if x.kind in ("only_in_a", "only_in_b")]
assert [x.impact for x in unmatched] == sorted((x.impact for x in unmatched), reverse=True)
from unittest import mock
from reconcile import report as rr
with mock.patch.object(rr, "LIST_LIMIT", 20):
    capped = analyze("bank.csv", table_a, header_a, "ledger.xlsx", table_b, header_b, Settings()).report
assert len(capped.issues) == 20 and any("most important of 56" in n for n in capped.notes) and capped.issues == issues[:20]
first = issues[0]
print("  ", first.severity, first.title, "|", first.evidence.splitlines()[0])
assert first.severity in ("high", "medium") and first.evidence and first.locations and first.locations[0][0] in ("File A", "File B")
assert {i.kind for i in issues} <= {"amount_mismatch", "only_in_a", "only_in_b", "maybe_match"}
severities = [i.severity for i in issues]
assert severities == sorted(severities, key=["high", "medium", "low"].index)
print("PASSED")

print("TEST: the Excel result - sheets in order, side by side with the reason, one colour per bucket, correct counts")
data = ro.build_workbook("bank.csv", "ledger.xlsx", table_a, table_b, A, B, analysis.map_a, analysis.map_b, result, report)
wb = load_workbook(io.BytesIO(data))
print("  ", wb.sheetnames)
assert wb.sheetnames == ["Summary", "Matched", "Maybe", "Only in A", "Only in B", "Mismatches"]
count = lambda name: wb[name].max_row - 1
assert (count("Matched"), count("Maybe"), count("Only in A"), count("Only in B"), count("Mismatches")) == (226, 10, 21, 15, 10)
matched = wb["Matched"]
headers = [c.value for c in matched[1]]
assert headers[:3] == ["Result", "Why", "Row in A"] and "A: Description" in headers and "B: Memo" in headers and headers[-1] == "Amount difference (B - A)"
fill = lambda ws, row: ws.cell(row=row, column=1).fill.start_color.rgb[-6:]
colors = {sheet: {fill(wb[sheet], r) for r in range(2, wb[sheet].max_row + 1)} for sheet in wb.sheetnames[1:]}
print("  ", colors)
assert colors["Matched"] == {ro.COLORS["exact"], ro.COLORS["close"]} and colors["Maybe"] == {ro.COLORS["maybe"]} and colors["Only in A"] == {ro.COLORS["only_a"]}
assert colors["Only in B"] == {ro.COLORS["only_b"]} and colors["Mismatches"] == {ro.COLORS["mismatch"]}
reasons = [matched.cell(row=r, column=2).value for r in range(2, 8)]
assert all(isinstance(r, str) and r for r in reasons)
a_col, b_col = headers.index("Row in A") + 1, headers.index("Row in B") + 1
row_a = {matched.cell(row=r, column=a_col).value: r for r in range(2, matched.max_row + 1)}
assert min(row_a) >= 4 and A.row_numbers[0] == 4                                        # rows are numbered as in the user's file (2 title rows + header)
summary = [[c.value for c in r] for r in wb["Summary"].iter_rows()]
text = " ".join(str(v) for r in summary for v in r if v is not None)
assert "bank.csv" in text and "ledger.xlsx" in text and "were not changed" in text and "Matched (exact)" in text and "Net amount difference" in text
print("PASSED")

print("TEST: maybes the user confirms move to Matched ('Confirmed by you'); the rest stay under Maybe")
accepted = {(p.a, p.b) for p in result.bucket("maybe")[:4]}
report2 = analyze("bank.csv", table_a, header_a, "ledger.xlsx", table_b, header_b, Settings(), accepted).report
data = ro.build_workbook("bank.csv", "ledger.xlsx", table_a, table_b, A, B, analysis.map_a, analysis.map_b, result, report2, accepted)
wb = load_workbook(io.BytesIO(data))
assert wb["Matched"].max_row - 1 == 226 + 4 and wb["Maybe"].max_row - 1 == 6
confirmed = [r for r in range(2, wb["Matched"].max_row + 1) if wb["Matched"].cell(row=r, column=1).value == "Confirmed by you"]
assert len(confirmed) == 4 and str(wb["Matched"].cell(row=confirmed[0], column=2).value).startswith("Confirmed by you:")
assert sum(1 for i in report2.issues if i.kind == "maybe_match") == 6
print("PASSED")

print("TEST: the user's files are never changed")
assert bank.equals(bank_before) and ledger.equals(ledger_before)
print("PASSED")
