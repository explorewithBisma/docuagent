from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""Merge rules: totals add up, 'last ... date' takes the latest, 'first ... date' the earliest; contact lists only."""
import io
import sys
from datetime import date

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import pandas as pd
from openpyxl import load_workbook

import changes as ch
import cleaning
import dedupe_fixtures as fx
import report as rp
import xlsx_export as xe
from dedupe import apply as ap, columns as cols
from dedupe.analyze import DedupeOptions, find_near_duplicates
from quality import profile_dataframe

STEPS = {"headers": True, "trim": True, "duplicates": True, "empty": True}


def ali_khan():
    return pd.DataFrame({
        "Customer Name": ["Ali Khan", "ali  khan", "Sara Ahmed", "Omar Farooq"],
        "Phone": ["0300-1234567", "03001234567", "0321-7654321", "0333-1112223"],
        "Email": ["ali@x.com", "", "sara@y.com", "omar@z.com"],
        "City": ["Lahore", "Lahore", "Dubai", "Doha"],
        "Last Order Date": ["2026-09-01", "2026-10-15", "2026-08-01", "2026-07-01"],
        "Total Spent": [4500, 1200, 300, 80],
    })


def run(df, rules="suggested", action=ap.MERGE):
    log = ch.ChangeLog()
    out, stats = cleaning.clean_dataframe_with_steps(df, STEPS, {"changes": log})
    result = find_near_duplicates(out, DedupeOptions(locale="PK"))
    if rules == "suggested":
        rules = ap.suggest_rules(out, result.profile)
    decisions = {g.id: ap.Decision(action, g.keep) for g in result.groups}
    new = ap.apply_decisions(out, log, result.groups, decisions, rules)
    return out, new, log, result, rules, stats


print("TEST: rules are suggested from the header and the type")
out, new, log, result, rules, _ = run(ali_khan())
print("  ", rules)
assert rules == {"Last Order Date": ap.LATEST, "Total Spent": ap.SUM}, rules
sample = pd.DataFrame({"Name": ["a b"], "Total Orders": [3], "Visits": [2], "Loyalty Points": [10], "Balance": [5.5], "Average Order Value": [9.0], "Unit Price": [2.0],
                       "First Order Date": ["2026-01-01"], "Joined": ["2025-01-01"], "Created At": ["2025-02-02"], "Most Recent Visit Date": ["2026-03-03"],
                       "Date of Birth": ["1980-01-02"], "Postcode": ["54000"], "Customer ID": ["C1"]})
suggested = ap.suggest_rules(sample, cols.build_profile(sample))
print("  ", suggested)
assert suggested == {"Total Orders": ap.SUM, "Visits": ap.SUM, "Loyalty Points": ap.SUM, "Balance": ap.SUM, "First Order Date": ap.EARLIEST, "Joined": ap.EARLIEST,
                     "Created At": ap.EARLIEST, "Most Recent Visit Date": ap.LATEST}, suggested        # no average / price / DOB / postcode / id
print("PASSED")

print("TEST: the Ali Khan example - the merged row says 2026-10-15 and 5700; the removed row goes to Removed Rows; everything else as before")
assert result.profile.kind == "contacts" and len(result.groups) == 1
row = new[new["Customer Name"] == "Ali Khan"].iloc[0]
assert row["Total Spent"] == 5700 and row["Last Order Date"] == "2026-10-15" and row["Email"] == "ali@x.com", row.to_dict()
assert len(new) == 3 and new["Total Spent"].tolist().count(5700) == 1
assert ap.describe_merge(out, result.groups[0], result.groups[0].keep, rules) == "Total Spent 4500 + 1200 = 5700; Last Order Date -> 2026-10-15"
assert ap.preview(out, result.groups, {result.groups[0].id: ap.Decision(ap.MERGE)}, rules) == {"rows_removed": 1, "cells_filled": 0, "cells_combined": 2, "groups": 1}
combined = [r for r in log.finalize(new) if r["reason"] == ch.COMBINED]
print("  ", [(r["column"], r["old"], r["new"], r["detail"]) for r in combined])
assert {(r["column"], r["old"], r["new"]) for r in combined} == {("Total Spent", 4500, 5700), ("Last Order Date", "2026-09-01", "2026-10-15")}
assert all("rows 2, 3" in r["detail"] for r in combined) and ch.REASON_LABELS[ch.COMBINED] == "Combined from near-duplicates"
assert log.finalize_removed()[0]["reason"].startswith("Near-duplicate of row 2 (") and "phone same" in log.finalize_removed()[0]["reason"]
print("PASSED")

print("TEST: the combined cells have their own colour in the Excel file, the legend explains it, the report counts them")
raw = ali_khan()
log = ch.ChangeLog()
out, stats = cleaning.clean_dataframe_with_steps(raw, STEPS, {"changes": log})
res = find_near_duplicates(out, DedupeOptions(locale="PK"))
final = ap.apply_decisions(out, log, res.groups, ap.accept_all_high(res.groups), rules)
data = xe.export_workbook({"Sheet1": final}, {"Sheet1": log}, {"Sheet1": {"cleaned": True, "stats": stats}}, {"Sheet1": profile_dataframe(raw, [])},
                          [], "t.csv", {"Sheet1": len(raw)})
wb = load_workbook(io.BytesIO(data))
fills = {c.fill.start_color.rgb[-6:] for r in wb["Sheet1"].iter_rows(min_row=2) for c in r if c.fill and c.fill.fill_type}
assert "F8CBAD" in fills, fills
log_text = " ".join(str(c.value) for r in wb["Change Log"].iter_rows() for c in r if c.value)
assert "Combined from near-duplicates" in log_text and "5700" in log_text
text = "\n".join(rp._removed_lines(log.finalize_removed()))
assert "Values combined" in text and ": 2" in text, text
print("PASSED")

print("TEST: the order of the rows does not matter, three rows are added up, the kept row can be the later one")
three = pd.DataFrame({"Customer Name": ["Ali Khan", "Ali Khan", "ALI KHAN"], "Phone": ["0300-1234567"] * 3, "Email": ["a@x.com", "", ""], "City": ["Lahore", "", ""],
                      "Last Order Date": ["2026-10-15", "2026-09-01", "2026-12-24"], "Total Spent": [1200.5, 4500, 10]})
out, new, log, result, rules, _ = run(three)
assert len(new) == 1 and new.iloc[0]["Total Spent"] == 5710.5 and new.iloc[0]["Last Order Date"] == "2026-12-24"
earliest = ap.apply_decisions(*[out, ch.ChangeLog(), result.groups, ap.accept_all_high(result.groups)], rules={"Last Order Date": ap.EARLIEST})
assert earliest.iloc[0]["Last Order Date"] == "2026-09-01" and earliest.iloc[0]["Total Spent"] == out.iloc[0]["Total Spent"]       # a rule not asked for: untouched
print("PASSED")

print("TEST: a column that cannot be combined is left alone (and says why); 'keep' rules, Keep one and Keep all combine nothing")
bad = ali_khan().assign(**{"Total Spent": [4500, "n/a", 300, 80]})
out, new, log, result, rules, _ = run(bad, rules={"Total Spent": ap.SUM})
plan, skipped = ap.plan_merge(out, result.groups[0].keep, [r for r in result.groups[0].rows if r != result.groups[0].keep], {"Total Spent": ap.SUM})
assert not plan and skipped == [("Total Spent", "not every value is a number")]
assert new[new["Customer Name"] == "Ali Khan"].iloc[0]["Total Spent"] == 4500
for variant in ({"Total Spent": ap.KEEP, "Last Order Date": ap.KEEP}, {}, None):
    _, new, log, *_ = run(ali_khan(), rules=variant)
    assert new[new["Customer Name"] == "Ali Khan"].iloc[0]["Total Spent"] == 4500 and not any(e["reason"] == ch.COMBINED for e in log.events)
_, new, log, *_ = run(ali_khan(), action=ap.KEEP_ONE)
assert new[new["Customer Name"] == "Ali Khan"].iloc[0]["Total Spent"] == 4500 and not any(e["reason"] in (ch.COMBINED, ch.MERGED) for e in log.events)
_, new, log, *_ = run(ali_khan(), action=ap.KEEP_ALL)
assert len(new) == 4
print("PASSED")

print("TEST: no change is recorded when the kept row already has the latest date or the other value is blank")
same = ali_khan().assign(**{"Last Order Date": ["2026-10-15", "2026-09-01", "2026-08-01", "2026-07-01"], "Total Spent": [4500, None, 300, 80]})
out, new, log, result, rules, _ = run(same)
assert not any(e["reason"] == ch.COMBINED for e in log.events), log.events
print("PASSED")

print("TEST: never in a transaction list")
orders = pd.DataFrame({"Customer": [f"Person {i % 8}" for i in range(80)], "Email": [f"p{i % 8}@mail.test" for i in range(80)],
                       "Item": [("Tea", "Cake", "Coffee")[i % 3] for i in range(80)], "Order Date": [date(2026, 1, 1 + i % 28) for i in range(80)],
                       "Total Spent": [3.5 + i for i in range(80)], "Last Order Date": [date(2026, 2, 1 + i % 28) for i in range(80)]})
profile = cols.build_profile(orders)
assert profile.kind == "transactions" and ap.suggest_rules(orders, profile) == {}
forced = cols.build_profile(ali_khan(), mode="transactions")
assert ap.suggest_rules(ali_khan(), forced) == {}
print("PASSED")
