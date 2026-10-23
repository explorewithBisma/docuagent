from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"
"""Offline unit tests for ai_rules.py - no network call, tests masking,
profiling, prompt building, and response parsing only."""
import sys
sys.path.insert(0, str(ROOT))

import json
import pandas as pd
import ai_rules

print("=" * 70)
print("TEST: mask_value preserves structure, never leaks real content")
print("=" * 70)
assert ai_rules.MODEL_NAME == "claude-sonnet-5-5", ai_rules.MODEL_NAME
cases = {
    "0300-1234567": "9999-9999999",
    "John Smith": "Xxxx Xxxxx",
    "Active": "Xxxxxx",
    "ACTIVE": "XXXXXX",
    "active": "xxxxxx",
    "$1,234.50": "$9,999.99",
    "jane@email.com": "xxxx@xxxxx.xxx",
    "+92-333-1112222": "+99-999-9999999",
}
for original, expected in cases.items():
    masked = ai_rules.mask_value(original)
    print(f"{original!r:25} -> {masked!r}")
    assert masked == expected, f"expected {expected!r}, got {masked!r}"
    # The real value must never appear verbatim inside the masked output
    # (skip this check for values that are only punctuation, which mask to themselves)
    if any(ch.isalnum() for ch in original):
        assert original not in masked
print("PASSED")

print()
print("=" * 70)
print("TEST: build_column_profile - unmasked")
print("=" * 70)
df = pd.DataFrame({
    "Phone": ["0300-1234567", "0321-9876543", "0333-1112222", None],
    "Status": ["active", "ACTIVE", "inactive", "Active"],
})
profiles = ai_rules.build_column_profile(df, mask=False)
for p in profiles:
    print(p)
phone_profile = next(p for p in profiles if p["column"] == "Phone")
assert phone_profile["non_null_count"] == 3
assert phone_profile["null_count"] == 1
assert len(phone_profile["sample_values"]) == 3
assert "0300-1234567" in phone_profile["sample_values"]  # unmasked: real values visible
print("PASSED")

print()
print("=" * 70)
print("TEST: build_column_profile - masked (no real phone value should appear)")
print("=" * 70)
masked_profiles = ai_rules.build_column_profile(df, mask=True)
phone_masked = next(p for p in masked_profiles if p["column"] == "Phone")
print(phone_masked["sample_values"])
for value in phone_masked["sample_values"]:
    assert value not in ["0300-1234567", "0321-9876543", "0333-1112222"]
    assert all(ch in "9-" for ch in value), f"unexpected char in masked phone: {value!r}"
print("PASSED")

print()
print("=" * 70)
print("TEST: build_prompt includes profiles and locale, excludes nothing unexpected")
print("=" * 70)
prompt = ai_rules.build_prompt(profiles, "PK")
assert "Pakistan" in prompt
assert "normalize_phone" in prompt
assert "0300-1234567" in prompt  # the (unmasked, in this case) sample value is in the prompt
print("Prompt length:", len(prompt), "chars")
print("PASSED")

print()
print("=" * 70)
print("TEST: parse_rules_response (hand-written fake API response, no network)")
print("=" * 70)
fake_response_text = json.dumps({
    "rules": [
        {
            "id": "rule_1",
            "column": "Phone",
            "rule_type": "normalize_phone",
            "params": {"country": "PK"},
            "description": "Normalize phone numbers to E.164 using Pakistan as the default country.",
            "confidence": "high",
        },
        {
            "id": "rule_2",
            "column": "Status",
            "rule_type": "unify_casing",
            "params": {"style": "title"},
            "description": "Make all status values use consistent Title Case.",
            "confidence": "medium",
        },
    ]
})
rules = ai_rules.parse_rules_response(fake_response_text)
print(rules)
assert len(rules) == 2
assert rules[0]["rule_type"] == "normalize_phone"
print("PASSED")

print()
print("=" * 70)
print("TEST: parsed rules pass rule_engine.validate_rule and actually run")
print("=" * 70)
import rule_engine
available_columns = list(df.columns)
for rule in rules:
    error = rule_engine.validate_rule(rule, available_columns)
    print(f"{rule['id']}: validation error = {error!r}")
    assert error == ""

cleaned_df = df.copy()
for rule in rules:
    cleaned_df, stats = rule_engine.apply_rule(cleaned_df, rule)
    print(f"Applied {rule['id']} ({rule['rule_type']}): {stats}")
print(cleaned_df)
print("PASSED")

print()
print("ALL AI_RULES OFFLINE TESTS PASSED (no network calls made)")
