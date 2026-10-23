from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"

import sys

sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

import quality as q

print("TEST: parse_number accepts plain numbers, rejects identifiers")
good = {"5": 5.0, " 3.5 ": 3.5, "-2": -2.0, "1,500": 1500.0, "1,234,567.89": 1234567.89, ".5": 0.5, "0": 0.0, "0.75": 0.75, 7: 7.0, 2.5: 2.5}
for text, expected in good.items():
    assert q.parse_number(text) == expected, (text, q.parse_number(text))
for text in ["007", "0300-1234567", "03001234567", "+923001234567", "12345678901", "Rs. 1,500/-", "$20", "ERROR",
             "", "  ", "1,5", "12,34", "1e5", "TXN_1961373", None, np.nan, True]:
    assert q.parse_number(text) is None, (text, q.parse_number(text))
print("PASSED")

print("TEST: placeholder + blank helpers")
ph = q.normalize_placeholders(["error", " Unknown ", "", "n/a", "-"])
assert ph == frozenset({"ERROR", "UNKNOWN", "N/A", "-"})
assert q.is_placeholder(" error ", ph) and q.is_placeholder("-", ph) and not q.is_placeholder("Errors", ph)
assert not q.is_placeholder(5, ph) and not q.is_placeholder(np.nan, ph)
assert q.is_blank(np.nan) and q.is_blank(None) and q.is_blank("   ") and q.is_blank(pd.NA) and not q.is_blank("0") and not q.is_blank(0)
assert q.parse_placeholder_text("ERROR, unknown\nN/A\n\n  -  ,") == ["ERROR", "unknown", "N/A", "-"]
s = pd.Series(["a", "ERROR", np.nan, " ", "b", "unknown"])
assert list(q.real_values(s, ph)) == ["a", "b"]
print("PASSED")

print("TEST: identifier/phone name + value detection")
for name in ["Customer ID", "customer_id", "CustomerID", "ZIP", "Postal Code", "Phone", "Mobile Number", "Account No", "txn id"]:
    assert q.has_identifier_name(name), name
for name in ["Quantity", "Price Per Unit", "Total Spent", "Amount Paid", "Item", "Age", "Idea", "Valid"]:
    assert not q.has_identifier_name(name), name
phones = pd.Series(["0300-1234567", "+92 321 9876543", "03331112222", "0345-7654321"])
assert q.is_phone_like("Contact", phones) and q.is_phone_like("anything", phones)
assert not q.is_phone_like("Amount", pd.Series(["1500", "20", "15.5", "1500000000"]))
assert q.has_phone_name("Mobile") and not q.has_phone_name("Total")
print("PASSED")

print("TEST: detect_column_type")
assert q.detect_column_type(pd.Series([1, 2, 3]), "Qty") == "numeric"
assert q.detect_column_type(pd.Series([1.5, np.nan]), "Price") == "numeric"
assert q.detect_column_type(pd.Series([np.nan, np.nan], dtype=float), "Empty") == "empty"
assert q.detect_column_type(pd.Series(["1", "2", "ERROR", "", "4"]), "Qty", ph) == "numeric"
assert q.detect_column_type(pd.Series(["ERROR", "UNKNOWN", ""]), "Qty", ph) == "empty"
assert q.detect_column_type(pd.Series(["Cake", "Tea", "1"]), "Item") == "text"
assert q.detect_column_type(pd.Series(["1", "2", "3"]), "Customer ID") == "text"            # identifier name
assert q.detect_column_type(pd.Series(["0300-1234567"] * 5), "Anything") == "text"          # phone values
assert q.detect_column_type(pd.Series(["007", "008", "009"]), "Thing") == "text"            # leading zeros
# 90% boundary: 9 of 10 parse -> numeric ; 8 of 10 -> text
nine = pd.Series(["1", "2", "3", "4", "5", "6", "7", "8", "9", "x"])
eight = pd.Series(["1", "2", "3", "4", "5", "6", "7", "8", "x", "y"])
assert q.detect_column_type(nine, "N") == "numeric" and q.detect_column_type(eight, "N") == "text"
print("PASSED")

print("TEST: profile_dataframe counts blank / placeholder / invalid / unique")
df = pd.DataFrame({
    "Item": ["Cake", "ERROR", np.nan, "Tea", "UNKNOWN", "Cake", "", "Cake", "Tea", "Cake", "Tea", "Cake"],
    "Qty": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "bad", "ERROR", ""],
    "Price": [1.0, np.nan, 2.0, 2.0, 3.0, np.nan, 1.0, 1.0, 2.0, 2.0, 3.0, 1.0],
})
profile = q.profile_dataframe(df).set_index("Column")
print(profile.to_string())
assert profile.loc["Item"].to_dict() == {"Type": "text", "Blank": 2, "Placeholders": 2, "Invalid": 0, "Unique": 2}
assert profile.loc["Qty"].to_dict() == {"Type": "numeric", "Blank": 1, "Placeholders": 1, "Invalid": 1, "Unique": 10}
assert profile.loc["Price"].to_dict() == {"Type": "numeric", "Blank": 2, "Placeholders": 0, "Invalid": 0, "Unique": 3}
custom = q.profile_dataframe(df, placeholders=["bad"]).set_index("Column")
assert custom.loc["Qty", "Placeholders"] == 1 and custom.loc["Item", "Placeholders"] == 0
print("PASSED")

print("TEST: profile on the real cafe file (skipped if absent)")
cafe = _Path.home() / "Downloads" / "archive" / "dirty_cafe_sales.csv"
if cafe.exists():
    raw = pd.read_csv(cafe, dtype=str, keep_default_na=False, na_values=[""])
    p = q.profile_dataframe(raw).set_index("Column")
    print(p.to_string())
    assert p.loc["Item", "Placeholders"] == 636 and p.loc["Item", "Blank"] == 333
    assert p.loc["Quantity", "Placeholders"] == 341 and p.loc["Quantity", "Type"] == "numeric"
    assert p.loc["Location", "Placeholders"] == 696 and p.loc["Location", "Blank"] == 3265
    assert p.loc["Transaction ID", "Type"] == "text" and p.loc["Transaction ID", "Unique"] == 10000
    print("PASSED")
else:
    print("skipped")

print("\nALL QUALITY TESTS PASSED")
