from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"
"""UI-level test: Suggest Cleaning Rules with a patched Groq call, using
test_data/messy_sample.csv. No network, no keys."""
import json
import os
import sys
from unittest import mock

sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest
import ai_rules

APP = str(ROOT / "app.py")
SAMPLE = str(ROOT / "test_data/messy_sample.csv")

RESPONSE = json.dumps({"rules": [
    {"id": "rule_1", "column": "Phone", "rule_type": "normalize_phone", "params": {"country": "PK"},
     "description": "Normalize Pakistani phone numbers to E.164.", "confidence": "high"},
    {"id": "rule_2", "column": "Status", "rule_type": "unify_casing", "params": {"style": "title"},
     "description": "Use Title Case for Status.", "confidence": "high"},
    {"id": "rule_3", "column": "Signup Date", "rule_type": "parse_date",
     "params": {"output_format": "%Y-%m-%d", "day_first": True},
     "description": "Write Signup Date as YYYY-MM-DD.", "confidence": "medium"},
    {"id": "rule_4", "column": "Amount Paid", "rule_type": "strip_currency_to_number",
     "params": {"currency_symbol": "Rs", "decimal_places": 2},
     "description": "Convert Amount Paid to numbers.", "confidence": "high"},
    {"id": "rule_5", "column": "Ghost Column", "rule_type": "unify_casing", "params": {"style": "upper"},
     "description": "Refers to a column that doesn't exist.", "confidence": "high"},
]})


def no_exception(at, label):
    assert not at.exception, [e.value for e in at.exception]
    print("OK -", label)


def fresh_app():
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    with open(SAMPLE, "rb") as f:
        at.file_uploader[0].upload("messy_sample.csv", f.read(), "text/csv")
    at.run()
    next(b for b in at.button if b.label == "Clean File").click()
    at.run()
    no_exception(at, "upload + clean messy_sample.csv")
    return at


print("=" * 70)
print("TEST 1: Groq suggestions -> banner, discarded note, approve, apply")
print("=" * 70)
at = fresh_app()
with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_fake"}), \
        mock.patch.object(ai_rules, "_call_groq", lambda prompt: RESPONSE):
    next(b for b in at.button if b.label == "Suggest Cleaning Rules").click()
    at.run()
no_exception(at, "Suggest Cleaning Rules (Groq)")

banner = [i.value for i in at.info]
print("Banner:", banner)
assert any("Groq" in b and "openai/gpt-oss-120b" in b for b in banner)
assert any("1 suggestion(s) were discarded" in e.label for e in at.expander), [e.label for e in at.expander]
checkboxes = [c for c in at.checkbox if c.label in (
    "Normalize Pakistani phone numbers to E.164.", "Use Title Case for Status.",
    "Write Signup Date as YYYY-MM-DD.", "Convert Amount Paid to numbers.")]
assert len(checkboxes) == 4 and not any(c.value for c in checkboxes), "fresh suggestions must start unchecked"
assert not any("Ghost Column" in c.label or "doesn't exist" in c.label for c in at.checkbox)
print("Confirmed: 4 valid rules shown unchecked, the invalid one never reached the UI")

for c in checkboxes:
    c.check()
at.run()
next(b for b in at.button if b.label == "Apply Approved Rules").click()
at.run()
no_exception(at, "Apply Approved Rules")
cleaned = at.session_state["cleaned_sheets"]["Data"]
print(cleaned.to_string())
print(at.session_state["report_text"])

print()
print("=" * 70)
print("TEST 2: Groq rate limit -> clear message, no crash")
print("=" * 70)
at2 = fresh_app()
def rate_limited(prompt):
    raise ai_rules.AISuggestionError("Groq's rate limit was reached. Try again in about 30 seconds.")
with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_fake"}), mock.patch.object(ai_rules, "_call_groq", rate_limited):
    next(b for b in at2.button if b.label == "Suggest Cleaning Rules").click()
    at2.run()
no_exception(at2, "rate-limited Suggest click")
errors = [e.value for e in at2.error]
print(errors)
assert any("rate limit" in e.lower() for e in errors)

print()
print("=" * 70)
print("TEST 3: Groq selected but no GROQ_API_KEY -> clear message, no crash")
print("=" * 70)
at3 = fresh_app()
with mock.patch.dict(os.environ, {"AI_PROVIDER": "groq"}, clear=False):
    os.environ.pop("GROQ_API_KEY", None)
    next(b for b in at3.button if b.label == "Suggest Cleaning Rules").click()
    at3.run()
no_exception(at3, "missing-key Suggest click")
errors = [e.value for e in at3.error]
print(errors)
assert any("GROQ_API_KEY" in e for e in errors)

print()
print("=" * 70)
print("TEST 4: invalid AI_PROVIDER -> clear message, no crash")
print("=" * 70)
at4 = fresh_app()
with mock.patch.dict(os.environ, {"AI_PROVIDER": "openai"}):
    next(b for b in at4.button if b.label == "Suggest Cleaning Rules").click()
    at4.run()
no_exception(at4, "invalid AI_PROVIDER Suggest click")
assert any("AI_PROVIDER" in e.value for e in at4.error)

print()
print("ALL PROVIDER UI TESTS PASSED")
