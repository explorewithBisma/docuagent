from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""Check every month: a new file against the rules - ground truth on planted problems, renamed / missing / extra columns, speed."""
import sys
import time

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import pandas as pd

import datarules_fixtures as fx
from datarules import check as ck, model, suggest
from reconcile import columns as rc
from reconcile_helpers import to_raw

sample = fx.sales_month(2026, 4)
RULES = suggest.suggest(rc.table_from_raw(to_raw(sample))[0], "april.xlsx", "PK", name="Monthly sales")


def run(frame, title_rows=0, **kw):
    table, _ = rc.table_from_raw(to_raw(frame, title_rows))
    return ck.check(table, RULES, "may.xlsx", "Sheet1", **kw), table


def rows_of(result, rule_id):
    outcome = next(o for o in result.outcomes if o.rule.id == rule_id)
    return outcome.status, sorted(result.report.issues[[i.facts.get("rule") for i in result.report.issues].index(rule_id)].facts["rows"]) if outcome.status == "fail" and rule_id != "columns:column_present" else []


print("TEST: a clean next month passes every rule (different dates, ids, customers - nothing is hard-coded to the sample)")
clean = fx.sales_month(2026, 5, 200, 77, first_id=3000)
result, _ = run(clean)
print("  ", result.status, result.counts(), "|", result.report.facts[0].value)
assert result.status == "PASS" and result.counts() == {"pass": 17, "fail": 0, "skipped": 0} and not result.report.issues
assert result.report.facts[0].value == "PASS - all 17 rules passed"
print("PASSED")

print("TEST: planted problems - every rule fails on exactly the planted rows (Excel row numbers), and on nothing else")
frame, planted = fx.problem_month()
before = frame.copy()
result, table = run(frame)
print("  ", result.status, result.counts())
failing = {o.rule.id: o for o in result.outcomes if o.status == "fail"}
expected = {"Customer Email:required": planted["blank_email"], "Customer Email:valid_email": planted["bad_email"], "Phone:valid_phone": planted["bad_phone"],
            "Status:allowed_values": planted["bad_status"], "Region:allowed_values": planted["bad_region"],
            "Amount:number_range": planted["huge_amount"] + planted["negative_amount"] + planted["amount_text"], "Quantity:number_range": planted["quantity_huge"],
            "Order Date:date_range": planted["date_typo_year"] + planted["date_text"], "Order ID:unique": planted["duplicate_id"]}
assert set(failing) == set(expected), set(failing) ^ set(expected)
source_rows = rc.source_rows(table)
for rule_id, rows in expected.items():
    got = sorted(source_rows[i] for i in failing[rule_id].failing)
    assert got == sorted(rows) and failing[rule_id].failing_total == len(rows), (rule_id, got, rows)
assert result.status == "FAIL" and result.report.facts[0].value == "FAIL - 9 of 17 rules failed"
duplicate = failing["Order ID:unique"].examples[0]
assert duplicate[0] == planted["duplicate_id"][0] and f"duplicate of row {planted['duplicate_id_first'][0]}" in duplicate[2]                  # says which row it repeats
assert next(o for o in result.outcomes if o.rule.id == "Quantity:number_range").status == "fail" and 72 not in [source_rows[i] for i in failing["Quantity:number_range"].failing]
print("PASSED")

print("TEST: the data is never changed")
assert frame.equals(before) and table.equals(rc.table_from_raw(to_raw(before))[0])
print("PASSED")

print("TEST: Please check - worst first, with evidence and where; each issue's cells are the failing cells")
issues = result.report.issues
severities = [i.severity for i in issues]
assert severities == sorted(severities, key=["high", "medium", "low"].index) and issues[0].severity == "high"
by_rule = {i.facts["rule"]: i for i in issues}
assert by_rule["Order ID:unique"].severity == "high" and by_rule["Customer Email:required"].severity == "medium"
amount = by_rule["Amount:number_range"]
print("  ", amount.title, "|", amount.evidence)
assert "3 values in 'Amount'" in amount.title and "row 52: 99999.0" in amount.evidence and "outside 0 .. 523.97" in amount.evidence and amount.where.startswith("Sheet1!")
assert [loc[1] for loc in amount.locations] == [f"H{r}" for r in planted["huge_amount"] + planted["negative_amount"] + planted["amount_text"]]
required = by_rule["Customer Email:required"]
assert required.locations == [("Sheet1", "C1")] and "5, 42, 79" in required.fix                                # blanks cannot be coloured: the header carries them
impacts = [i.impact for i in issues if i.severity == "medium"]
assert impacts == sorted(impacts, reverse=True)
cells = {(row, column) for _, row, column, _ in result.cells}
assert (52, 8) in cells and (1, 3) in cells and (132, 1) in cells
print("PASSED")

print("TEST: renamed columns - same words in another order, a similar name, upper case - are matched and reported; the rules still apply to them")
renamed = frame.rename(columns={"Customer Email": "email customer", "Amount": "Amount (AED)", "Order Date": "ORDER DATE", "Status": "Statuss"})
result, _ = run(renamed)
how = {m.expected: (m.actual, m.how) for m in result.matches}
print("  ", how)
assert how["Customer Email"] == ("email customer", "reordered") and how["Amount"] == ("Amount (AED)", "similar") and how["Order Date"] == ("ORDER DATE", "exact") and how["Status"] == ("Statuss", "similar")
assert {o.rule.id for o in result.outcomes if o.status == "fail"} == set(expected)                              # the same rules fail on the same data
assert any("'Amount' was found as 'Amount (AED)'" in n for n in result.notes) and "renamed" in result.report.facts[3].value
assert next(f for f in result.report.facts if f.label == "Columns").confidence == "medium"
off, _ = run(renamed, allow_similar=False)
assert {m.expected: m.how for m in off.matches}["Amount"] == "missing" and off.status == "FAIL"                  # with similar names switched off it is simply missing
print("PASSED")

print("TEST: a missing column fails the check, its rules are skipped (and say so), and the new file's extra columns are listed")
missing = frame.drop(columns=["Phone", "Region"]).assign(Comment="x", Channel="web")
result, _ = run(missing)
skipped = {o.rule.id: o.note for o in result.outcomes if o.status == "skipped"}
print("  ", sorted(skipped), "| extras:", result.extras)
assert set(skipped) == {"Phone:required", "Phone:valid_phone", "Region:required", "Region:allowed_values"}
assert all("is not in this file" in note for note in skipped.values())
assert next(o for o in result.outcomes if o.rule.type == "column_present").status == "fail"
first = result.report.issues[0]
assert first.kind == "column_missing" and first.severity == "high" and "Phone, Region" in first.title
assert result.extras == ["Comment", "Channel"] and "new, no rules: 'Comment', 'Channel'" in next(f for f in result.report.facts if f.label == "Columns").value
assert any("Skipped" in n and "Phone" in n for n in result.notes)
print("PASSED")

print("TEST: columns in another order, title lines above the header, blank lines inside, and a CSV-style file of text all give the same answers")
shuffled = frame[list(reversed(frame.columns))]
a, _ = run(shuffled)
b, table_b = run(frame, title_rows=3)
c, _ = run(frame.astype(str).replace({"None": None, "nan": None}))
base, _ = run(frame)
for other in (a, b):
    assert {(o.rule.id, o.failing_total) for o in other.outcomes if o.status == "fail"} == {(o.rule.id, o.failing_total) for o in base.outcomes if o.status == "fail"}
assert rc.source_rows(table_b)[0] == 5 and b.report.issues[0].locations                                           # rows keep their number in the file (3 title lines + header)
for other in (c,):                                                                                            # a CSV: every cell is text
    assert {(o.rule.id, o.failing_total) for o in other.outcomes if o.status == "fail"} == {(o.rule.id, o.failing_total) for o in base.outcomes if o.status == "fail"}
print("PASSED")

print("TEST: a rule that is switched off is not run; an empty file fails cleanly")
RULES.rules[[r.id for r in RULES.rules].index("Amount:number_range")].enabled = False
result, _ = run(frame)
assert "Amount:number_range" not in {o.rule.id for o in result.outcomes} and result.counts()["fail"] == 8
RULES.rules[[r.id for r in RULES.rules].index("Amount:number_range")].enabled = True
empty, _ = run(frame.iloc[0:0])
assert empty.status == "FAIL" and empty.rows == 0 and any(i.kind == "no_rows" for i in empty.report.issues)
print("PASSED")

print("TEST: a fixed date range and a date window")
fixed = model.RuleSet(rules=[model.Rule("d", "Order Date", "date_range", {"mode": "fixed", "min": "2026-05-01", "max": "2026-05-31"})], columns=["Order Date"])
table, _ = rc.table_from_raw(to_raw(frame))
outcome = ck.check(table, fixed, "f").outcomes[0]
assert outcome.status == "fail" and sorted(rc.source_rows(table)[i] for i in outcome.failing) == sorted(planted["date_typo_year"] + planted["date_text"])
window = model.RuleSet(rules=[model.Rule("w", "Order Date", "date_range", {"mode": "span", "max_span_days": 31})], columns=["Order Date"])
assert ck.check(table, window, "w").outcomes[0].failing_total == 2
print("PASSED")

print("TEST: speed - 50,000 rows against 17 rules")
big = pd.concat([fx.sales_month(2026, 5, 10000, seed, first_id=100000 * seed) for seed in range(1, 6)], ignore_index=True)
table, _ = rc.table_from_raw(to_raw(big))
started = time.perf_counter()
result = ck.check(table, RULES, "big.xlsx", "Sheet1")
elapsed = time.perf_counter() - started
print(f"   {len(big):,} rows: {elapsed:.1f} s, {result.status}, {result.counts()}")
assert elapsed < 20 and result.status == "PASS"                                                         # (clean data, ids made different on purpose)
print("PASSED")

print("TEST: severity - a number far outside its range (more than 10 x the upper limit) is High; a near miss, a negative, text and other rules keep their usual level")
rule = model.Rule("a", "Amount", "number_range", {"min": 160, "max": 5440})
amounts = pd.DataFrame({"Amount": [200, 6000, 250000, 54401, 54400, 100, -99999, "twelve", 5440]})
table, _ = rc.table_from_raw(to_raw(amounts))
result = ck.check(table, model.RuleSet(rules=[rule], columns=["Amount"]), "x", "Sheet1")
by_row = {row: severity for severity, row, _, _ in result.cells}
print("  ", result.report.issues[0].severity, result.report.issues[0].title, "|", by_row)
assert [ck.severity_of(rule, v) for v in (6000, 250000, 54401, 54400, 100, -99999, "twelve", 5440)] == ["medium", "high", "high", "medium", "medium", "high", "medium", "medium"]
assert by_row == {3: "medium", 4: "high", 5: "high", 6: "medium", 7: "medium", 8: "high", 9: "medium"}                      # exactly 10x (54,400) is not "far"; 54,401 is
issue = result.report.issues[0]
assert issue.severity == "high" and "(3 far outside the range)" in issue.title                                          # 250,000, 54,401 and -99,999
only_near = ck.check(rc.table_from_raw(to_raw(pd.DataFrame({"Amount": [200, 6000, 5441]})))[0], model.RuleSet(rules=[rule], columns=["Amount"]), "x", "S")
assert only_near.report.issues[0].severity == "medium" and "far outside" not in only_near.report.issues[0].title
frame_, planted_ = fx.problem_month()
result_, table_ = run(frame_)
severity = {i.facts["rule"]: i.severity for i in result_.report.issues}
assert severity["Amount:number_range"] == "high" and severity["Quantity:number_range"] == "high"                      # 99,999 against 523.97; 500 against 10
assert severity["Customer Email:required"] == "medium" and severity["Status:allowed_values"] == "medium" and severity["Order ID:unique"] == "high"
assert result_.report.issues[0].severity == "high"
cells = {(row, column): sev for sev, row, column, _ in result_.cells}
assert cells[(52, 8)] == "high" and cells[(62, 8)] == "medium" and cells[(67, 8)] == "medium"                            # 99,999 high; -45 and 'twelve' medium
print("PASSED")
