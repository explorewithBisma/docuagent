from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"
"""Offline unit tests for rule_engine.py - no network, no Streamlit."""
import sys
sys.path.insert(0, str(ROOT))

import pandas as pd
import rule_engine as re_mod

print("=" * 70)
print("TEST: normalize_phone")
print("=" * 70)
df = pd.DataFrame({"Phone": ["0300-1234567", "0321 9876543", "+92-333-1112222", "not a number", None]})
df, stats = re_mod.apply_normalize_phone(df, "Phone", {"country": "PK"})
print(df)
print("stats:", stats)
assert stats["converted"] == 3, f"expected 3 converted, got {stats['converted']}"
assert stats["failed"] == 1, f"expected 1 failed, got {stats['failed']}"
assert df["Phone"].iloc[0] == "+923001234567", df["Phone"].iloc[0]
print("PASSED")

print()
print("=" * 70)
print("TEST: unify_casing")
print("=" * 70)
df = pd.DataFrame({"Status": ["active", "Active", "ACTIVE", "inactive", None, 123]})
df, stats = re_mod.apply_unify_casing(df, "Status", {"style": "title"})
print(df)
print("stats:", stats)
assert list(df["Status"][:4]) == ["Active", "Active", "Active", "Inactive"]
assert df["Status"].iloc[5] == 123  # non-string left alone
print("PASSED")

print()
print("=" * 70)
print("TEST: parse_date")
print("=" * 70)
df = pd.DataFrame({"Date": ["25/12/2024", "01/01/2025", "not a date", None]})
df, stats = re_mod.apply_parse_date(df, "Date", {"day_first": True, "output_format": "%Y-%m-%d"})
print(df)
print("stats:", stats)
assert df["Date"].iloc[0] == "2024-12-25", df["Date"].iloc[0]
assert stats["converted"] == 2, stats
assert stats["failed"] == 1, stats
print("PASSED")

print()
print("=" * 70)
print("TEST: strip_currency_to_number")
print("=" * 70)
df = pd.DataFrame({"Price": ["$1,234.50", "Rs 999", "£20.00", "free", None]})
df, stats = re_mod.apply_strip_currency_to_number(df, "Price", {"decimal_places": 2})
print(df)
print("stats:", stats)
assert df["Price"].iloc[0] == 1234.50, df["Price"].iloc[0]
assert df["Price"].iloc[1] == 999.0, df["Price"].iloc[1]
assert stats["converted"] == 3, stats
assert stats["failed"] == 1, stats
print("PASSED")

print()
print("=" * 70)
print("TEST: validate_rule")
print("=" * 70)
good_rule = {"id": "r1", "column": "Phone", "rule_type": "normalize_phone",
             "params": {"country": "PK"}, "description": "test"}
print("Valid rule error (should be empty):", repr(re_mod.validate_rule(good_rule, ["Phone", "Status"])))
assert re_mod.validate_rule(good_rule, ["Phone", "Status"]) == ""

bad_column = dict(good_rule, column="Nonexistent")
print("Bad column error:", repr(re_mod.validate_rule(bad_column, ["Phone", "Status"])))
assert re_mod.validate_rule(bad_column, ["Phone", "Status"]) != ""

bad_type = dict(good_rule, rule_type="delete_everything")
print("Bad rule_type error:", repr(re_mod.validate_rule(bad_type, ["Phone", "Status"])))
assert re_mod.validate_rule(bad_type, ["Phone", "Status"]) != ""

missing_field = {"id": "r1", "column": "Phone"}
print("Missing fields error:", repr(re_mod.validate_rule(missing_field, ["Phone"])))
assert re_mod.validate_rule(missing_field, ["Phone"]) != ""
print("PASSED")

print()
print("=" * 70)
print("TEST: apply_rule dispatcher")
print("=" * 70)
df = pd.DataFrame({"Status": ["active", "INACTIVE"]})
df, stats = re_mod.apply_rule(df, {"id": "r1", "column": "Status", "rule_type": "unify_casing",
                                     "params": {"style": "upper"}, "description": "x"})
print(df)
assert list(df["Status"]) == ["ACTIVE", "INACTIVE"]
print("PASSED")

print()
print("ALL RULE_ENGINE TESTS PASSED")

print()
print("=" * 70)
print("TEST: parse_date keeps year-first dates intact when day_first=True (regression)")
print("=" * 70)
import warnings
df = pd.DataFrame({"Date": ["2025-04-01", "2025-02-14", "25/12/2024", "03/01/2025", "05 Mar 2025", "30 Jul 2025"]})
with warnings.catch_warnings():
    warnings.simplefilter("error")   # any pandas parsing warning now fails the test
    df, stats = re_mod.apply_parse_date(df, "Date", {"day_first": True, "output_format": "%Y-%m-%d"})
print(df["Date"].tolist())
assert df["Date"].tolist() == ["2025-04-01", "2025-02-14", "2024-12-25", "2025-01-03", "2025-03-05", "2025-07-30"]
assert stats == {"converted": 6, "failed": 0}
print("PASSED")
