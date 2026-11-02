from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""dedupe.apply: decisions -> removed rows, filled blanks, change log, export, report."""
import io
import sys

sys.path.insert(0, str(ROOT))

import pandas as pd
from openpyxl import load_workbook

import changes as ch
import cleaning
import report as rp
import xlsx_export as xe
from dedupe import apply as ap
from dedupe.analyze import DedupeOptions, find_near_duplicates

STEPS = {"headers": True, "trim": True, "duplicates": True, "empty": True}


def sample():
    return pd.DataFrame({
        "Name": ["Ali Khan", "Ali  KHAN", "Sara Ahmed", "Sara Ahmed", "Omar Farooq", "Bilal Raza"],
        "Phone": ["0300-1234567", "03001234567", "0321-7654321", "0321 7654321", "0333-1112223", "0345-9998887"],
        "Email": ["ali@x.com", "", "", "sara@y.com", "omar@z.com", "bilal@w.com"],
        "City": ["Lahore", "Karachi", "Dubai", "", "Doha", "Multan"],
    })


def cleaned(df):
    log = ch.ChangeLog()
    out, _ = cleaning.clean_dataframe_with_steps(df, STEPS, {"changes": log})
    return out, log


print("TEST: merge fills blanks only, never overwrites; removed rows get the near-duplicate reason; log and export agree")
out, log = cleaned(sample())
result = find_near_duplicates(out, DedupeOptions(locale="PK"))
assert len(result.groups) == 2 and all(g.level == "high" for g in result.groups), result.summary
decisions = ap.accept_all_high(result.groups)
effect = ap.preview(out, result.groups, decisions)
print("   preview:", effect)
assert effect == {"rows_removed": 2, "cells_filled": 1, "cells_combined": 0, "groups": 2}, effect
new = ap.apply_decisions(out, log, result.groups, decisions)
assert len(new) == 4 and new.loc[0, "City"] == "Lahore" and new.loc[0, "Email"] == "ali@x.com"      # nothing overwritten
sara = new.loc[2]
assert sara["Email"] == "sara@y.com" and sara["City"] == "Dubai"                                      # the blank email filled from the other row
rows = log.finalize_removed()
reasons = {r["original_row"]: r["reason"] for r in rows}
print("  ", reasons)
assert set(reasons) == {3, 5} and reasons[3].startswith("Near-duplicate of row 2 (") and "phone same" in reasons[3]
records = log.finalize(new)
merged = [r for r in records if r["reason"] == ch.MERGED]
assert [(r["column"], r["new"]) for r in merged] == [("Email", "sara@y.com")] and merged[0]["detail"] == "filled from row 5"
assert all(r["old"] in ("", None) or pd.isna(r["old"]) for r in merged)
print("PASSED")

print("TEST: keep_all changes nothing; keep_one removes without filling; a chosen keep row is respected")
out, log = cleaned(sample())
same = ap.apply_decisions(out, log, result.groups, {g.id: ap.Decision(ap.KEEP_ALL) for g in result.groups})
assert same.equals(out) and not log.removed
g = result.groups[0]
other = [r for r in g.rows if r != g.keep][0]
one = ap.apply_decisions(out, log, [g], {g.id: ap.Decision(ap.KEEP_ONE, other)})
assert other in one.index.map(lambda label: out.index.get_loc(label)) and g.keep not in list(one.index) and not any(e["reason"] == ch.MERGED for e in log.events)
print("PASSED")

print("TEST: export Removed Rows sheet, purple merged cells, report text")
raw = sample()
log = ch.ChangeLog()
out, stats = cleaning.clean_dataframe_with_steps(raw, STEPS, {"changes": log})
new = ap.apply_decisions(out, log, result.groups, ap.accept_all_high(result.groups))
from quality import profile_dataframe
data = xe.export_workbook({"Sheet1": new}, {"Sheet1": log}, {"Sheet1": {"cleaned": True, "stats": stats}},
                          {"Sheet1": profile_dataframe(raw, [])}, [], "t.csv", {"Sheet1": len(raw)})
wb = load_workbook(io.BytesIO(data))
assert "Removed Rows" in wb.sheetnames
texts = [str(c.value) for row in wb["Removed Rows"].iter_rows(min_row=2) for c in row if c.value]
assert any(t.startswith("Near-duplicate of row") for t in texts)
fills = {c.fill.start_color.rgb[-6:] for row in wb["Sheet1"].iter_rows(min_row=2) for c in row if c.fill and c.fill.fill_type}
assert "E4DFEC" in fills, fills
joined = "\n".join(map(str, rp._removed_lines(log.finalize_removed())))
assert "near-duplicate" in joined.lower(), joined
assert "filled" in joined.lower()
print("PASSED")

print("TEST: exact duplicates still removed by the cleaner as before (kind 'duplicate'), unaffected by near-duplicate kinds")
frame = pd.DataFrame({"A": ["x", "x", "y"], "B": ["1", "1", "2"]})
_, log = cleaned(frame)
assert [r["kind"] for r in log.finalize_removed()] == ["duplicate"] and log.finalize_removed()[0]["reason"] == "Duplicate of row 2"
print("PASSED")
