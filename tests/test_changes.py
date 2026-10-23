from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"

import sys
import time
from unittest import mock

sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

import changes as ch
import cleaning
import derive
import rule_engine

STEPS = {"headers": False, "trim": True, "duplicates": False, "empty": False}
ALL = {"headers": True, "trim": True, "duplicates": True, "empty": True, "placeholders": True, "numeric": True, "fills": True}
PRICES = {"Cake": 3.0, "Coffee": 2.0, "Cookie": 1.0, "Juice": 3.0, "Salad": 5.0, "Sandwich": 4.0, "Smoothie": 4.0, "Tea": 1.5}


def make_cafe(rows=300, seed=11, holes=0.08):
    rng = np.random.default_rng(seed)
    item = rng.choice(list(PRICES), rows)
    quantity = rng.integers(1, 6, rows)
    price = np.array([PRICES[i] for i in item])
    frame = pd.DataFrame({"Transaction ID": [f"TXN_{i:07d}" for i in range(rows)], "Item": item, "Quantity": quantity.astype(str), "Price Per Unit": price.astype(str),
                          "Total Spent": (quantity * price).astype(str), "Payment Method": rng.choice(["Cash", "Card"], rows)})
    for column in ["Item", "Quantity", "Price Per Unit", "Total Spent"]:
        hole = rng.random(rows) < holes
        picks = rng.choice(["ERROR", "UNKNOWN", ""], hole.sum())          # "" -> a genuinely blank cell
        frame.loc[hole, column] = [np.nan if pick == "" else pick for pick in picks]
    return frame


print("TEST: values_differ")
D = ch.values_differ
assert not D(np.nan, None) and not D(None, "") and not D("  ", np.nan) and D("ERROR", np.nan) and D(np.nan, 5)
assert not D("3.0", 3.0) and not D(5, 5.0) and not D("1,500", 1500.0) and D(5, 6) and D("007", "7") and D(" Cake", "Cake")
assert D("3", 3.0, strict=True) and not D("3.0", 3.0, strict=True) and D(" 5 ", "5", strict=True) and not D("a", "a", strict=True)
print("PASSED")

print("TEST: trimming is recorded (exact old text, whitespace-only cells too), unchanged cells are not")
df = pd.DataFrame({"Name": [" John ", "Bob", "   ", None, "Cy  "]})
log = ch.ChangeLog()
out, _ = cleaning.clean_dataframe_with_steps(df.copy(), STEPS, {"changes": log})
records = log.finalize(out)
print([(r["row"], r["old"], r["new"], r["reason"]) for r in records])
assert [(r["row"], r["old"], r["reason"]) for r in records] == [(2, " John ", "trimmed"), (4, "   ", "trimmed"), (6, "Cy  ", "trimmed")]
assert records[0]["new"] == "John" and pd.isna(records[1]["new"])
assert ch.comment_text(records[0]) == 'Was: " John "'
print("PASSED")

print("TEST: placeholders are recorded; a placeholder later filled in becomes ONE 'calculated' record that still remembers the original")
frame = make_cafe()
raw = frame.copy()
base, _ = cleaning.clean_dataframe_with_steps(raw.copy(), {**ALL, "fills": False})
rels = derive.detect_products(base)
assert len(rels) == 1
log = ch.ChangeLog()
final, stats = cleaning.clean_dataframe_with_steps(raw.copy(), ALL, {"changes": log, "relationships": rels})
records = log.finalize(final)
by_reason = ch.summarize(records)
print(by_reason)
assert by_reason["calculated"] == sum(stats["fills"]["calculated"].values())
assert by_reason["lookup"] == 0 and by_reason["ai_rule"] == 0
placeholders_in_raw = int(sum(raw[c].isin(["ERROR", "UNKNOWN"]).sum() for c in raw.columns))
calc_from_placeholder = sum(1 for r in records if r["reason"] == "calculated" and isinstance(r["old"], str) and r["old"] in ("ERROR", "UNKNOWN"))
assert by_reason["placeholder"] + calc_from_placeholder == placeholders_in_raw          # every placeholder is accounted for exactly once
example = next(r for r in records if r["reason"] == "calculated" and r["column"] == "Total Spent" and isinstance(r["old"], str) and r["old"] in ("ERROR", "UNKNOWN"))
q, p = final.at[example["index"], "Quantity"], final.at[example["index"], "Price Per Unit"]
assert example["detail"] == f"Quantity {int(q)} x Price Per Unit {float(p)}", example["detail"]
assert example["new"] == q * p
assert ch.comment_text(example) == f"Was: {example['old']}\nCalculated exactly: Quantity {int(q)} x Price Per Unit {float(p)}"
assert example["row"] == list(final.index).index(example["index"]) + 2
blank_filled = next(r for r in records if r["reason"] == "calculated" and pd.isna(r["old"]))
assert ch.comment_text(blank_filled).startswith("Was: (blank)\nCalculated exactly: ")
grey = next(r for r in records if r["reason"] == "placeholder")
assert ch.comment_text(grey) == f"Was: {grey['old']}" and pd.isna(grey["new"])
print("PASSED")

print("TEST: removed rows leave no records; row numbers follow the CLEANED sheet")
df = pd.DataFrame({"A": ["a", "a", "ERROR", "b", "UNKNOWN"], "B": ["ERROR", "ERROR", "ERROR", "x", "y"]})
log = ch.ChangeLog()
final, _ = cleaning.clean_dataframe_with_steps(df.copy(), ALL, {"changes": log, "relationships": []})
print(final.to_string())
records = log.finalize(final)
assert list(final.index) == [0, 3, 4]                       # row 1 (duplicate) and row 2 (all placeholders) are gone
assert not any(r["index"] in (1, 2) for r in records)
assert sorted((r["row"], r["column"], r["old"]) for r in records) == [(2, "B", "ERROR"), (4, "A", "UNKNOWN")]
assert records[0]["position"] == 0
print("PASSED")

print("TEST: AI rule changes are recorded with the rule description; unchanged / skipped cells are not")
df = pd.DataFrame({"Status": ["active", "ACTIVE", "ERROR", np.nan, "Cash", "Active"]})
log = ch.ChangeLog()
rule = {"id": "r", "column": "Status", "rule_type": "unify_casing", "params": {"style": "title"}, "description": "Title-case Status."}
out, st = rule_engine.apply_rule(df.copy(), rule, skip_values=["ERROR"], log=log)
records = log.finalize(out)
print([(r["row"], r["old"], r["new"], r["detail"]) for r in records])
assert st["converted"] == 2 and [(r["row"], r["old"], r["new"]) for r in records] == [(2, "active", "Active"), (3, "ACTIVE", "Active")]
assert all(r["reason"] == "ai_rule" and r["detail"] == "Title-case Status." for r in records)
assert ch.comment_text(records[0]) == "Was: active\nAI rule: Title-case Status."
# AI rule on top of an earlier change keeps the original
df2 = pd.DataFrame({"Status": [" active ", "Active"]})
log2 = ch.ChangeLog()
step, _ = cleaning.clean_dataframe_with_steps(df2.copy(), STEPS, {"changes": log2})
step, _ = rule_engine.apply_rule(step, {**rule, "params": {"style": "upper"}}, log=log2)
rec = log2.finalize(step)
assert [(r["old"], r["new"], r["reason"]) for r in rec] == [(" active ", "ACTIVE", "ai_rule"), ("Active", "ACTIVE", "ai_rule")]
print("PASSED")

print("TEST: numeric conversion logs only visible changes ('1,500' -> 1500), not '5' -> 5")
df = pd.DataFrame({"Amount": ["1,500", "20", "3", "4", "5", "6", "7", "8", "9", "10"]})
log = ch.ChangeLog()
out, _ = cleaning.clean_dataframe_with_steps(df.copy(), {**ALL, "fills": False}, {"changes": log})
records = log.finalize(out)
assert str(out["Amount"].dtype) == "Int64"
assert [(r["row"], r["old"], r["new"], r["reason"], r["detail"]) for r in records] == [(2, "1,500", 1500, "numeric", "thousands separator removed")]
print("PASSED")

print("TEST: lookups are recorded as 'lookup' with a plain-English detail, forward and reverse")
log = ch.ChangeLog()
base, _ = cleaning.clean_dataframe_with_steps(raw.copy(), {**ALL, "fills": False})
detected = derive.detect_relationships(base)
rels_all = detected["products"] + detected["lookups"]
final, stats = cleaning.clean_dataframe_with_steps(raw.copy(), ALL, {"changes": log, "relationships": rels_all})
records = log.finalize(final)
lookups = [r for r in records if r["reason"] == "lookup"]
assert len(lookups) == sum(stats["fills"]["lookup"].values()) > 0
forward = next(r for r in lookups if r["column"] == "Price Per Unit")
assert "-> Price Per Unit" in forward["detail"] and "every other" in forward["detail"], forward["detail"]
reverse = next(r for r in lookups if r["column"] == "Item")
assert "-> Item" in reverse["detail"] and "only" in reverse["detail"], reverse["detail"]
assert ch.comment_text(forward).startswith("Was: ") and "Inferred from lookup: " in ch.comment_text(forward)
assert ch.summarize(records)["lookup"] == len(lookups)
print("PASSED")

print("TEST: nothing is written to disk")
with mock.patch("builtins.open", side_effect=AssertionError("tried to write to disk")), \
        mock.patch("pathlib.Path.write_bytes", side_effect=AssertionError("disk")), mock.patch("pathlib.Path.write_text", side_effect=AssertionError("disk")):
    log = ch.ChangeLog()
    final, _ = cleaning.clean_dataframe_with_steps(raw.copy(), ALL, {"changes": log, "relationships": rels_all})
    assert log.finalize(final)
print("PASSED")

print("TEST: a run without a log behaves exactly as before")
a, sa = cleaning.clean_dataframe_with_steps(raw.copy(), ALL, {"relationships": rels_all})
b, sb = cleaning.clean_dataframe_with_steps(raw.copy(), ALL, {"relationships": rels_all, "changes": ch.ChangeLog()})
pd.testing.assert_frame_equal(a, b)
assert sa == sb
print("PASSED")

print("TEST: real cafe file - every record's 'old' matches the file, every 'new' matches the output")
cafe = _Path.home() / "Downloads" / "archive" / "dirty_cafe_sales.csv"
if cafe.exists():
    original = pd.read_csv(cafe, dtype=str, keep_default_na=False, na_values=[""])
    prepared, _ = cleaning.clean_dataframe_with_steps(original.copy(), {**ALL, "fills": False})
    product = derive.detect_products(prepared)
    log = ch.ChangeLog()
    t = time.time()
    final, stats = cleaning.clean_dataframe_with_steps(original.copy(), ALL, {"changes": log, "relationships": product})
    records = log.finalize(final)
    print(f"[{len(log.events)} events -> {len(records)} cell records in {time.time() - t:.1f}s]", ch.summarize(records))
    counts = ch.summarize(records)
    assert counts["calculated"] == 1398 and counts["lookup"] == 0 and counts["ai_rule"] == 0
    from_placeholder = sum(1 for r in records if r["reason"] == "calculated" and isinstance(r["old"], str))
    assert counts["placeholder"] + from_placeholder == 3256
    for r in records[::7]:                                   # spot-check a spread of records against the source file
        source = original.at[r["index"], r["column"]]
        assert (pd.isna(source) and pd.isna(r["old"])) or source == r["old"], (r, source)
        assert final.at[r["index"], r["column"]] is not None
        assert r["row"] == r["index"] + 2                    # no rows were removed, so rows line up with the original file
    assert all(pd.isna(r["new"]) for r in records if r["reason"] == "placeholder")
    assert all(not pd.isna(r["new"]) for r in records if r["reason"] == "calculated")
    total_check = [r for r in records if r["reason"] == "calculated" and r["column"] == "Total Spent"][:200]
    assert all(abs(r["new"] - final.at[r["index"], "Quantity"] * final.at[r["index"], "Price Per Unit"]) < 1e-9 for r in total_check)
    print("PASSED")
else:
    print("skipped")

print("\nALL CHANGE-TRACKING TESTS PASSED")
