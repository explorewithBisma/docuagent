from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"

import sys

sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest

import cleaning
import derive
import report

PRICES = {"Cake": 3.0, "Coffee": 2.0, "Cookie": 1.0, "Juice": 3.0, "Salad": 5.0, "Sandwich": 4.0, "Smoothie": 4.0, "Tea": 1.5}


def make_cafe(rows=400, seed=7, holes=0.08):
    """A clean cafe-style frame with Total = Quantity x Price and Price a function of Item, then punched with holes."""
    rng = np.random.default_rng(seed)
    item = rng.choice(list(PRICES), rows)
    quantity = rng.integers(1, 6, rows)
    price = np.array([PRICES[i] for i in item])
    frame = pd.DataFrame({
        "Transaction ID": [f"TXN_{i:07d}" for i in range(rows)],
        "Item": item,
        "Quantity": pd.array(quantity, dtype="Int64"),
        "Price Per Unit": price,
        "Total Spent": quantity * price,
        "Payment Method": rng.choice(["Cash", "Credit Card", "Digital Wallet"], rows),
    })
    truth = frame.copy()
    for column in ["Item", "Quantity", "Price Per Unit", "Total Spent"]:
        frame.loc[rng.random(rows) < holes, column] = np.nan if column != "Quantity" else pd.NA
    return frame, truth


print("TEST: product relationship detection")
frame, truth = make_cafe()
found = derive.detect_products(frame)
print([r["label"] for r in found])
assert len(found) == 1 and found[0]["target"] == "Total Spent" and sorted(found[0]["factors"]) == ["Price Per Unit", "Quantity"]
# one bad row -> no longer exact -> rejected
broken = frame.copy()
broken.loc[broken.index[(broken["Total Spent"].notna()) & broken["Quantity"].notna() & broken["Price Per Unit"].notna()][0], "Total Spent"] += 0.5
assert derive.detect_products(broken) == []
# too few rows
assert derive.detect_products(frame.head(30)) == []
# constant factor / ID-like columns are ignored
const = pd.DataFrame({"A": range(1, 101), "B": [1] * 100, "C": range(1, 101)})
assert derive.detect_products(const) == []
ids = pd.DataFrame({"Order ID": range(1, 101), "Q": [2] * 50 + [3] * 50, "P": [1, 2] * 50})
ids["Total"] = ids["Q"] * ids["P"]
assert derive.detect_products(ids)[0]["factors"] == ["Q", "P"]
ids2 = ids.rename(columns={"Q": "Customer ID"})                      # an ID-named column is never a factor
assert derive.detect_products(ids2) == []
print("PASSED")

print("TEST: lookup detection (Item -> Price) and its guards")
lookups = derive.detect_lookups(frame)
pairs = {(r["key"], r["value"]) for r in lookups}
print(pairs)
assert ("Item", "Price Per Unit") in pairs
item_price = next(r for r in lookups if (r["key"], r["value"]) == ("Item", "Price Per Unit"))
assert item_price["forward"]["Tea"] == 1.5 and len(item_price["forward"]) == 8
assert item_price["reverse"] == {1.0: "Cookie", 1.5: "Tea", 2.0: "Coffee", 5.0: "Salad"}      # 3.0 (Cake/Juice) and 4.0 (Sandwich/Smoothie) are ambiguous
assert ("Price Per Unit", "Item") not in pairs                                                  # price does NOT determine item
assert not any(r["key"] == "Transaction ID" or r["value"] == "Transaction ID" for r in lookups)  # unique IDs are never keys/values
assert not any(r["key"] == "Payment Method" for r in lookups)
# a conflicting key kills the relationship
conflict = frame.copy()
first_tea = conflict.index[conflict["Item"] == "Tea"][0]
conflict.loc[first_tea, "Price Per Unit"] = 9.0
assert ("Item", "Price Per Unit") not in {(r["key"], r["value"]) for r in derive.detect_lookups(conflict)}
# keys that appear < 5 times are left out of the map but don't spoil it
rare = frame.copy()
rare.loc[rare.index[:3], "Item"] = "Typo"
rare.loc[rare.index[:3], "Price Per Unit"] = 7.7
rare_map = next(r for r in derive.detect_lookups(rare) if (r["key"], r["value"]) == ("Item", "Price Per Unit"))
assert "Typo" not in rare_map["forward"]
print("PASSED")

print("TEST: calculated fills - exact, never overwrite, integer-safe, zero-safe")
rel = derive.detect_products(frame)
filled, stats = derive.apply_fills(frame.copy(), rel)
print(stats)
sub = frame.copy()
mask = sub["Quantity"].notna() & sub["Price Per Unit"].notna() & sub["Total Spent"].isna()
assert filled.loc[mask, "Total Spent"].notna().all()
assert np.allclose(filled.loc[mask, "Total Spent"], truth.loc[mask, "Total Spent"])             # values are exactly right
known = frame["Total Spent"].notna()
assert (filled.loc[known, "Total Spent"] == frame.loc[known, "Total Spent"]).all()              # nothing overwritten
q_mask = frame["Quantity"].isna() & frame["Total Spent"].notna() & frame["Price Per Unit"].notna()
assert (filled.loc[q_mask, "Quantity"].astype(int) == truth.loc[q_mask, "Quantity"].astype(int)).all()
assert str(filled["Quantity"].dtype) == "Int64"
assert stats["lookup"] == {} and set(stats["calculated"]) <= {"Total Spent", "Quantity", "Price Per Unit"}
# fractional result for an integer column is refused
frac = pd.DataFrame({"Q": pd.array([2, 4, pd.NA, 3], dtype="Int64"), "P": [3.0, 3.0, 3.0, 3.0], "T": [6.0, 12.0, 7.0, 9.0]})
frac_rel = [{"kind": "product", "target": "T", "factors": ["Q", "P"], "rows": 3, "label": "x"}]
out, st = derive.apply_fills(frac.copy(), frac_rel)
assert pd.isna(out["Q"].iloc[2]) and st["calculated"] == {}                                     # 7/3 is not a whole quantity
# zero divisor does not blow up or fill
zero = pd.DataFrame({"Q": [np.nan, 2.0], "P": [0.0, 3.0], "T": [0.0, 6.0]})
out, st = derive.apply_fills(zero.copy(), [{"kind": "product", "target": "T", "factors": ["Q", "P"], "rows": 1, "label": "x"}])
assert pd.isna(out["Q"].iloc[0])
# inexact division (4 / 3) is refused
inexact = pd.DataFrame({"Q": [np.nan], "P": [3.0], "T": [4.0]})
out, st = derive.apply_fills(inexact.copy(), [{"kind": "product", "target": "T", "factors": ["Q", "P"], "rows": 1, "label": "x"}])
assert pd.isna(out["Q"].iloc[0]) or abs(out["Q"].iloc[0] * 3.0 - 4.0) < 1e-9
print("PASSED")

print("TEST: lookup fills are separate, opt-in, and chain with calculated fills")
only_calc, s1 = derive.apply_fills(frame.copy(), rel)
both, s2 = derive.apply_fills(frame.copy(), rel + [item_price])
print("calc only:", s1, "\nboth:     ", s2)
assert s2["lookup"] and set(s2["lookup"]) <= {"Price Per Unit", "Item"}
assert both.isna().sum().sum() < only_calc.isna().sum().sum()                                   # lookups recovered more
assert both["Item"].isna().sum() <= frame["Item"].isna().sum()
# everything the lookup put in agrees with the truth
for column in ["Item", "Price Per Unit", "Total Spent"]:
    changed = frame[column].isna() & both[column].notna()
    assert (both.loc[changed, column] == truth.loc[changed, column]).all(), column
again, s3 = derive.apply_fills(both.copy(), rel + [item_price])                                   # idempotent
assert s3["calculated"] == {} and s3["lookup"] == {} and again.equals(both)
print("PASSED")

print("TEST: pipeline + report")
raw = frame.astype(object).where(frame.notna(), np.nan).copy()
steps = {"headers": True, "trim": True, "duplicates": True, "empty": True, "placeholders": True, "numeric": True, "fills": True}
cleaned, stats = cleaning.clean_dataframe_with_steps(raw.copy(), steps, {"relationships": rel})
assert stats["fills"]["calculated"] and not stats["fills"]["lookup"]
text = report.build_report_text("f.csv", {"Data": {"cleaned": True, "stats": stats}})
print(text)
assert "Values filled from other columns:" in text and "Calculated exactly:" in text and "Inferred from a lookup" not in text
_, none = cleaning.clean_dataframe_with_steps(raw.copy(), steps, {"relationships": []})
assert "Values filled from other columns: none" in report.build_report_text("f.csv", {"Data": {"cleaned": True, "stats": none}})
_, off = cleaning.clean_dataframe_with_steps(raw.copy(), {**steps, "fills": False}, {"relationships": rel})
assert off["fills"] is None
print("PASSED")

print("TEST: UI - relationships are listed, products ticked, lookups unticked, tick boxes decide")
csv_frame, _ = make_cafe(rows=300, seed=3)
text_frame = csv_frame.astype(object).where(csv_frame.notna(), "")
text_frame["Item"] = text_frame["Item"].where(csv_frame["Item"].notna(), "UNKNOWN")
CSV = text_frame.to_csv(index=False).encode()
def new_app():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    at.run()
    at.file_uploader[0].upload("cafe.csv", CSV, "text/csv")
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at
at = new_app()
rel_boxes = {c.label: c for c in at.checkbox if "holds on" in c.label or "always the same" in c.label}
print(list(rel_boxes))
product_box = next(c for l, c in rel_boxes.items() if l.startswith("Total Spent ="))
lookup_box = next(c for l, c in rel_boxes.items() if l.startswith("Item ->") and "Price Per Unit" in l)
assert product_box.value is True and lookup_box.value is False
next(b for b in at.button if b.label == "Clean File").click()
at.run()
assert not at.exception, [e.value for e in at.exception]
calc_only = at.session_state["cleaned_sheets"]["Data"]
st_fills = at.session_state["sheet_stats"]["Data"]["stats"]["fills"]
assert st_fills["calculated"] and st_fills["lookup"] == {}
at = new_app()
next(c for c in at.checkbox if c.label.startswith("Item ->") and "Price Per Unit" in c.label).check()
at.run()
next(b for b in at.button if b.label == "Clean File").click()
at.run()
with_lookup = at.session_state["cleaned_sheets"]["Data"]
assert at.session_state["sheet_stats"]["Data"]["stats"]["fills"]["lookup"]
assert with_lookup.isna().sum().sum() < calc_only.isna().sum().sum()
assert "Inferred from a lookup in this file" in at.session_state["report_text"]
at = new_app()
next(c for c in at.checkbox if c.label.startswith("Total Spent =")).uncheck()
at.run()
next(b for b in at.button if b.label == "Clean File").click()
at.run()
assert at.session_state["sheet_stats"]["Data"]["stats"]["fills"] == {"calculated": {}, "lookup": {}, "passes": 0}
print("PASSED")

print("TEST: real cafe file (skipped if absent)")
cafe = _Path.home() / "Downloads" / "archive" / "dirty_cafe_sales.csv"
if cafe.exists():
    raw = pd.read_csv(cafe, dtype=str, keep_default_na=False, na_values=[""])
    steps = {"headers": True, "trim": True, "duplicates": True, "empty": True, "placeholders": True, "numeric": True, "fills": False}
    base, _ = cleaning.clean_dataframe_with_steps(raw.copy(), steps)
    detected = derive.detect_relationships(base)
    print([r["label"] for r in detected["products"]], [r["label"] for r in detected["lookups"]])
    assert [r["label"] for r in detected["products"]] == ["Total Spent = Quantity x Price Per Unit  (holds on 8544 of 8544 rows)"] or \
           [r["label"] for r in detected["products"]] == ["Total Spent = Price Per Unit x Quantity  (holds on 8544 of 8544 rows)"]
    assert [(r["key"], r["value"]) for r in detected["lookups"]] == [("Item", "Price Per Unit")]
    before = base.isna().sum().to_dict()
    calc, c_stats = derive.apply_fills(base.copy(), detected["products"])
    full, f_stats = derive.apply_fills(base.copy(), detected["products"] + detected["lookups"])
    print("calc only:", c_stats["calculated"], "| after:", calc.isna().sum().to_dict())
    print("with lookup:", f_stats, "| after:", full.isna().sum().to_dict())
    assert before["Total Spent"] - calc.isna().sum()["Total Spent"] == 462 and c_stats["calculated"]["Total Spent"] == 462
    assert c_stats["calculated"]["Quantity"] == 441 and c_stats["calculated"]["Price Per Unit"] == 495
    after = full.isna().sum().to_dict()
    assert (after["Quantity"], after["Price Per Unit"], after["Total Spent"], after["Item"]) == (23, 6, 23, 480)
    assert (after["Payment Method"], after["Location"], after["Transaction Date"]) == (3178, 3961, 460)    # cannot be known
    # every filled value is consistent with the arithmetic relationship
    ok = full.dropna(subset=["Quantity", "Price Per Unit", "Total Spent"])
    assert np.allclose(ok["Quantity"].astype(float) * ok["Price Per Unit"], ok["Total Spent"])
    print("PASSED")
else:
    print("skipped")

print("\nALL DERIVE TESTS PASSED")
