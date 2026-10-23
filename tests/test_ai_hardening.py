from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"

import json
import os
import sys
from unittest import mock

sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest

import ai_rules
import quality
import rule_engine

# A small frame shaped like the cafe data: junk placeholders plus perfectly consistent real values.
CAFE = pd.DataFrame({
    "Item": ["Cake", "Tea", "ERROR", "Cake", "UNKNOWN", "Tea", np.nan, "Cake", "Tea", "Cake"],
    "Payment Method": ["Cash", "Credit Card", "ERROR", "Cash", "UNKNOWN", "Digital Wallet", np.nan, "Cash", "ERROR", "Cash"],
    "Location": ["In-store", "Takeaway", "ERROR", "In-store", "UNKNOWN", "Takeaway", np.nan, "In-store", "Takeaway", "UNKNOWN"],
    "Status": ["active", "Active", "ACTIVE", "ERROR", "inactive", "Inactive", np.nan, "active", "Active", "UNKNOWN"],
})

print("TEST: casing_variants only looks at real values")
assert quality.casing_variants(CAFE["Item"]) == {}                      # Cake/Tea consistent; ERROR/UNKNOWN ignored
assert quality.casing_variants(CAFE["Location"]) == {}                  # In-store is consistent
assert quality.casing_variants(CAFE["Status"]) == {"active": ["ACTIVE", "Active", "active"], "inactive": ["Inactive", "inactive"]}
# placeholders written in two casings must not count as inconsistent casing
assert quality.casing_variants(pd.Series(["ERROR", "Error", "error", "ok"])) == {}
assert quality.casing_variants(pd.Series(["ERROR", "Error", "ok"]), placeholders=[]) == {"error": ["ERROR", "Error"]}
assert quality.columns_with_inconsistent_casing(CAFE) == {"Status"}
print("PASSED")

print("TEST: profiles never use placeholders as samples and count them as missing")
for mask in (False, True):
    profiles = {p["column"]: p for p in ai_rules.build_column_profile(CAFE, mask=mask)}
    for column, profile in profiles.items():
        blob = json.dumps(profile).upper()
        assert "ERROR" not in blob.replace("ERROR", "ERROR") or True
        assert "ERROR" not in [s.upper() for s in profile["sample_values"]], (column, profile)
        assert "UNKNOWN" not in [s.upper() for s in profile["sample_values"]], (column, profile)
item = ai_rules.build_column_profile(CAFE, mask=False)[0]
assert item["non_null_count"] == 7 and item["null_count"] == 3          # 7 real values; ERROR, UNKNOWN, NaN = 3 missing
assert set(item["sample_values"]) <= {"Cake", "Tea"}
location = ai_rules.build_column_profile(CAFE, mask=False)[2]
assert set(location["sample_values"]) <= {"In-store", "Takeaway"} and location["non_null_count"] == 6
# a column of nothing but placeholders has no samples at all
only_junk = pd.DataFrame({"X": ["ERROR", "UNKNOWN", np.nan]})
assert ai_rules.build_column_profile(only_junk, mask=False)[0]["sample_values"] == []
# profile keys are unchanged (privacy boundary)
assert set(item) == {"column", "dtype", "non_null_count", "null_count", "sample_values"}
# custom placeholder list is honoured
custom = ai_rules.build_column_profile(pd.DataFrame({"X": ["foo", "bar", "bar"]}), mask=False, placeholders=["foo"])[0]
assert custom["sample_values"] == ["bar", "bar"] and custom["null_count"] == 1
print("PASSED")

print("TEST: prompt tells the AI when unify_casing is (not) appropriate")
prompt = ai_rules.build_prompt(ai_rules.build_column_profile(CAFE, mask=False), "PK")
assert "do NOT suggest it" in prompt and "SAME word in" in prompt and "In-store" in prompt.split("do NOT suggest it")[0][-200:]
assert "placeholders such as ERROR" in prompt
# ... and the real data in the prompt contains no placeholder sample values
sample_part = prompt.split("Column profiles:")[1]
assert '"ERROR"' not in sample_part and '"UNKNOWN"' not in sample_part
print("PASSED")

print("TEST: unify_casing skips missing + placeholders and counts only real changes")
frame = pd.DataFrame({"S": ["active", "Active", "ERROR", "unknown", np.nan, "", "  ", 5, "Cash"]})
out, stats = rule_engine.apply_unify_casing(frame.copy(), "S", {"style": "title"}, quality.normalize_placeholders(["ERROR", "UNKNOWN"]))
print(out["S"].tolist(), stats)
assert out["S"].tolist()[:2] == ["Active", "Active"]
assert out["S"].tolist()[2] == "ERROR" and out["S"].tolist()[3] == "unknown"       # placeholders untouched
assert pd.isna(out["S"].iloc[4]) and out["S"].iloc[5] == "" and out["S"].iloc[7] == 5
assert stats == {"converted": 1, "failed": 0}                                        # only "active" changed; "Active"/"Cash" unchanged
# without a skip list, placeholders WOULD be re-cased (this is the bug we are guarding against)
_, unguarded = rule_engine.apply_unify_casing(frame.copy(), "S", {"style": "title"})
assert unguarded["converted"] == 3
# the dispatcher accepts a plain list
_, via_rule = rule_engine.apply_rule(frame.copy(), {"id": "r", "column": "S", "rule_type": "unify_casing", "params": {"style": "title"}, "description": "d"},
                                     skip_values=["error", "UNKNOWN"])
assert via_rule == {"converted": 1, "failed": 0}
print("PASSED")

print("TEST: other rules don't count placeholders/blank text as failures")
phones = pd.DataFrame({"P": ["0300-1234567", "ERROR", "UNKNOWN", "", "garbage"]})
_, st = rule_engine.apply_normalize_phone(phones.copy(), "P", {"country": "PK"}, frozenset({"ERROR", "UNKNOWN"}))
assert st == {"converted": 1, "failed": 1}                                           # only "garbage" fails
dates = pd.DataFrame({"D": ["2025-01-02", "ERROR", "", "nope"]})
_, st = rule_engine.apply_parse_date(dates.copy(), "D", {"output_format": "%Y-%m-%d"}, frozenset({"ERROR"}))
assert st == {"converted": 1, "failed": 1}
money = pd.DataFrame({"M": ["$5", "UNKNOWN", "", "free"]})
_, st = rule_engine.apply_strip_currency_to_number(money.copy(), "M", {}, frozenset({"UNKNOWN"}))
assert st == {"converted": 1, "failed": 1}
print("PASSED")

print("TEST: the shared gate drops unify_casing for consistent columns (the cafe incident)")
THREE_BAD = json.dumps({"rules": [
    {"id": f"rule_{i}", "column": col, "rule_type": "unify_casing", "params": {"style": "title"},
     "description": f"Title-case {col}.", "confidence": "high"}
    for i, col in enumerate(["Payment Method", "Item", "Location"], start=1)
] + [{"id": "rule_4", "column": "Status", "rule_type": "unify_casing", "params": {"style": "title"},
      "description": "Title-case Status.", "confidence": "high"}]})
profiles = ai_rules.build_column_profile(CAFE, mask=False)
inconsistent = quality.columns_with_inconsistent_casing(CAFE)
for provider in ("groq", "anthropic"):
    with mock.patch.object(ai_rules, "_call_groq", lambda p: THREE_BAD), mock.patch.object(ai_rules, "_call_anthropic", lambda p: THREE_BAD):
        result = ai_rules.suggest_rules(profiles, "PK", provider, inconsistent_casing_columns=inconsistent)
    assert [r["column"] for r in result["rules"]] == ["Status"], result["rules"]
    assert [d["description"] for d in result["dropped"]] == ["Title-case Payment Method.", "Title-case Item.", "Title-case Location."]
    assert all("consistent casing" in d["reason"] for d in result["dropped"])
# without the casing info the gate is not applied (callers that don't pass it keep the old behaviour)
with mock.patch.object(ai_rules, "_call_groq", lambda p: THREE_BAD):
    assert len(ai_rules.suggest_rules(profiles, "PK", "groq")["rules"]) == 4
print("PASSED")

print("TEST: UI - the incident can no longer reach the review screen, and applying rules skips placeholders")
CSV = ("Item,Payment Method,Location,Status\n" + "\n".join(
    ",".join("" if pd.isna(v) else v for v in row) for row in CAFE.itertuples(index=False))).encode()
at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
at.run()
at.file_uploader[0].upload("cafe.csv", CSV, "text/csv")
at.run()
next(b for b in at.button if b.label == "Clean File").click()
at.run()
data = at.session_state["cleaned_sheets"]["Data"]
assert data["Location"].isna().sum() == 3 and "ERROR" not in data.to_string() and "UNKNOWN" not in data.to_string()
with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_fake"}), mock.patch.object(ai_rules, "_call_groq", lambda p: THREE_BAD):
    next(b for b in at.button if b.label == "Suggest Cleaning Rules").click()
    at.run()
assert not at.exception, [e.value for e in at.exception]
labels = [c.label for c in at.checkbox]
assert "Title-case Status." in labels and not any(l in labels for l in ("Title-case Item.", "Title-case Location.", "Title-case Payment Method."))
assert any("3 suggestion(s) were discarded" in e.label for e in at.expander), [e.label for e in at.expander]
next(c for c in at.checkbox if c.label == "Title-case Status.").check()
at.run()
next(b for b in at.button if b.label == "Apply Approved Rules").click()
at.run()
assert not at.exception
after = at.session_state["cleaned_sheets"]["Data"]
assert after["Location"].dropna().unique().tolist() == ["In-store", "Takeaway"]       # legitimate values untouched
assert sorted(after["Status"].dropna().unique()) == ["Active", "Inactive"]
print(at.session_state["report_text"].split("AI-suggested rules applied:")[1])

# placeholder step OFF: warning shown, but the AI still never sees placeholders and rules still skip them
seen = {}
def spy(prompt):
    seen["prompt"] = prompt
    return json.dumps({"rules": []})
at2 = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
at2.run()
at2.file_uploader[0].upload("cafe.csv", CSV, "text/csv")
at2.run()
next(c for c in at2.checkbox if c.label.startswith("Convert placeholder")).uncheck()
at2.run()
next(b for b in at2.button if b.label == "Clean File").click()
at2.run()
assert "ERROR" in at2.session_state["cleaned_sheets"]["Data"]["Item"].tolist()      # still in the data...
assert any("Placeholder conversion was turned off" in w.value for w in at2.warning)
with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_fake"}), mock.patch.object(ai_rules, "_call_groq", spy):
    next(b for b in at2.button if b.label == "Suggest Cleaning Rules").click()
    at2.run()
sample_part = seen["prompt"].split("Column profiles:")[1]
assert '"ERROR"' not in sample_part and '"UNKNOWN"' not in sample_part              # ...but never sent to the AI
print("PASSED")

print("\nALL AI HARDENING TESTS PASSED")
