from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""The date-order check on LOADED rules (recipes and saved AI-suggestion files):
never changed silently - unticked, warned, and fixable with one click."""
import copy
import json
import re
import sys

sys.path.insert(0, str(ROOT))

import pandas as pd
import rule_checks as rc
from streamlit.testing.v1 import AppTest

PH = ["ERROR", "UNKNOWN"]


def date_rule(**params):
    return {"id": "r1", "column": "d", "rule_type": "parse_date",
            "params": {"output_format": "%Y-%m-%d", **params},
            "description": "Write d as YYYY-MM-DD.", "confidence": "high", "approved": True}


day_first_data = pd.Series(["19/07/2023", "20/01/2023", "02/03/2024", None], dtype=object)
month_first_data = pd.Series(["07/19/2023", "01/20/2023", "02/03/2024"], dtype=object)
ambiguous_data = pd.Series(["01/02/2024", "03/04/2024"], dtype=object)

print("TEST: saved month-first, data proves day-first -> rule returned exactly as saved, with a warning and the fix")
saved = date_rule(day_first=False)
snapshot = copy.deepcopy(saved)
rule, info = rc.check_loaded_date_rule(saved, day_first_data, "IN", PH)
assert rule == snapshot and saved == snapshot, "a loaded rule must not be changed"
assert info["kind"] == "contradicted" and info["fix_params"]["day_first"] is True
assert info["note"] == ("This rule says month-first, but 2 values like 19/07/2023 can only be day-first. "
                        "It has been unticked. Use day-first instead?"), info["note"]
print("PASSED")

print("TEST: the reverse (saved day-first, data month-first); input_format counts as the saved order and is fixed too")
rule, info = rc.check_loaded_date_rule(date_rule(day_first=True), month_first_data, "IN", PH)
assert info["kind"] == "contradicted" and info["fix_params"]["day_first"] is False and "says day-first" in info["note"]
rule, info = rc.check_loaded_date_rule(date_rule(input_format="%d/%m/%Y"), month_first_data, "IN", PH)
assert info["kind"] == "contradicted" and info["fix_params"]["input_format"] == "%m/%d/%Y"
print("PASSED")

print("TEST: data agrees, or cannot tell, or is inconsistent -> kept as saved, and it says so")
_, info = rc.check_loaded_date_rule(date_rule(day_first=True), day_first_data, "IN", PH)
assert info["kind"] == "kept" and info["note"].startswith("Kept as saved (day-first): the data agrees (2 values"), info
_, info = rc.check_loaded_date_rule(date_rule(day_first=False), ambiguous_data, "IN", PH)
assert info["kind"] == "kept" and info["note"] == "Kept as saved (month-first): the data cannot tell day-first from month-first.", info
_, info = rc.check_loaded_date_rule(date_rule(day_first=True), pd.Series(["25/12/2024", "12/25/2024"], dtype=object), "IN", PH)
assert info["kind"] == "kept" and "inconsistent" in info["note"], info
other = {"id": "c", "column": "d", "rule_type": "unify_casing", "params": {"style": "title"}, "description": "x"}
assert rc.check_loaded_date_rule(other, day_first_data, "IN", PH) == (other, None)
print("PASSED")

print("TEST: review_loaded_rules - only the contradicted rule is unticked; others stay ticked; odd rules do not crash")
frame = pd.DataFrame({"d": day_first_data, "e": ambiguous_data})
rules = [date_rule(day_first=False), {**date_rule(day_first=True), "id": "r2", "column": "e"}, {**other, "approved": True},
         {"rule_type": "parse_date", "column": "d", "params": {}}, {**date_rule(), "id": "r5", "column": "missing"}]
before = copy.deepcopy(rules)
reviewed, notes = rc.review_loaded_rules(frame, rules, "IN", PH)
assert rules == before, "the loaded list is not modified in place"
assert [r.get("approved") for r in reviewed[:3]] == [False, True, True]
assert reviewed[0]["params"]["day_first"] is False, "still as saved"
assert set(notes) == {"r1", "r2"} and "fix_params" in notes["r1"] and "fix_params" not in notes["r2"]
print("PASSED")

# ---------------------------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------------------------
over12 = [f"{13 + i % 15:02d}/{1 + i % 12:02d}/2023" for i in range(12)]
CSV = ("order_date,amount\n" + "\n".join(f"{d},{10 + i}" for i, d in enumerate(["2024-08-09"] + over12 + ["02/04/2024"] * 3))
       + "\n").encode()
AMBIGUOUS_CSV = b"order_date,amount\n01/02/2024,1\n03/04/2024,2\n05/06/2024,3\n"


def saved_file(day_first, with_provider):
    data = {"recipe_name": "Retail", "locale": "IN", "rules": [{
        "id": "rule_1", "column": "order_date", "rule_type": "parse_date",
        "params": {"output_format": "%Y-%m-%d", "day_first": day_first},
        "description": "Write order_date as YYYY-MM-DD.", "confidence": "high", "approved": not with_provider}]}
    if with_provider:
        data.update({"provider": "groq", "model": "openai/gpt-oss-120b", "file_name": "retail.csv", "date": "2026-10-20"})
    return json.dumps(data).encode()


def open_app(csv_bytes, name="orders.csv"):
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=240)
    at.run()
    at.file_uploader[0].upload(name, csv_bytes, "text/csv")
    at.run()
    next(b for b in at.button if b.label == "Clean my file").click()
    at.run()
    return at


def load(at, file_bytes):
    next(fu for fu in at.file_uploader if fu.key == "recipe_uploader").upload("saved.json", file_bytes, "application/json")
    at.run()
    next(b for b in at.button if b.label == "Load Recipe").click()
    at.run()
    assert not at.exception, [e.value for e in at.exception]


def rule_box(at):
    return next(c for c in at.checkbox if c.label == "Write order_date as YYYY-MM-DD.")


def stored_rule(at):
    return next(iter(at.session_state["suggested_rules_by_sheet"].values()))[0]


for with_provider, label in ((False, "recipe"), (True, "saved AI-suggestions file")):
    print(f"TEST: UI - a {label} with day_first=false on day-first data: unticked, warned, rule untouched until the fix is clicked")
    at = open_app(CSV)
    load(at, saved_file(False, with_provider))
    assert rule_box(at).value is False
    warnings = [w.value for w in at.warning]
    assert any(w.startswith("This rule says month-first, but 12 values like 13/01/2023 can only be day-first.") and
               w.endswith("Use day-first instead?") for w in warnings), warnings
    assert stored_rule(at)["params"]["day_first"] is False and stored_rule(at)["approved"] is False
    assert any("nothing is ticked yet" in s.value for s in at.success), [s.value for s in at.success]
    fix = next(b for b in at.button if b.label == "Use the corrected date order")
    fix.click()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert stored_rule(at)["params"]["day_first"] is True and rule_box(at).value is True
    assert not [b for b in at.button if b.label == "Use the corrected date order"], "the warning goes away once fixed"
    assert any("Corrected to the date order the data proves (day-first)" in c.value for c in at.caption)
    next(b for b in at.button if b.label == "Apply Approved Rules").click()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    cleaned = next(iter(at.session_state["cleaned_sheets"].values()))
    assert "2023-01-13" in set(cleaned["order_date"]) and not any("/" in str(d) for d in cleaned["order_date"])
    print("PASSED")

print("TEST: UI - data agrees with the saved rule -> stays ticked, kept as saved, no warning, no fix button")
at = open_app(CSV)
load(at, saved_file(True, False))
assert rule_box(at).value is True and not any("This rule says" in w.value for w in at.warning)
assert any(c.value.startswith("🔎 Kept as saved (day-first): the data agrees") for c in at.caption)
assert not [b for b in at.button if b.label == "Use the corrected date order"]
print("PASSED")

print("TEST: UI - ambiguous data -> kept as saved (even day_first=false), stays ticked, says so")
at = open_app(AMBIGUOUS_CSV, "amb.csv")
load(at, saved_file(False, False))
assert rule_box(at).value is True and stored_rule(at)["params"]["day_first"] is False
assert any(c.value == "🔎 Kept as saved (month-first): the data cannot tell day-first from month-first." for c in at.caption)
print("PASSED")

print("TEST: UI - the stored rule (and so the recipe download) carries the fixed order")
at = open_app(CSV)
load(at, saved_file(False, False))
next(b for b in at.button if b.label == "Use the corrected date order").click()
at.run()
assert stored_rule(at)["params"]["day_first"] is True
print("PASSED")

retail_file = _Path.home() / "Downloads" / "archive" / "retail_sales_dataset.csv"
print("TEST: the real retail file - saved day_first=false loaded, preview uses the cleaned data and matches the applied result")
if retail_file.exists():
    at = open_app(retail_file.read_bytes(), "retail_sales_dataset.csv")
    cleaned_rows = len(next(iter(at.session_state["cleaned_sheets"].values())))
    cleaned_dates = next(iter(at.session_state["cleaned_sheets"].values()))["order_date"]
    real_dates = int(cleaned_dates.notna().sum())
    load(at, saved_file(False, True))
    assert rule_box(at).value is False
    # The check reads the CLEANED data (what the rule will run on): the Clean step removed 3 duplicate rows that held
    # day-first dates, so it finds 83 of the raw file's 86.
    raw = pd.read_csv(retail_file, dtype=str, keep_default_na=False).replace("", None)
    assert rc.detect_date_order(raw["order_date"], PH)["day_first_count"] == 86
    in_cleaned = rc.detect_date_order(cleaned_dates, PH)["day_first_count"]
    assert in_cleaned == 83, in_cleaned
    assert any(f"{in_cleaned} values like" in w.value and "can only be day-first" in w.value for w in at.warning), [w.value for w in at.warning]
    before_fix = next(c.value for c in at.caption if c.value.startswith("Preview: "))
    assert before_fix.endswith("would fail 0") or "would fail" in before_fix
    next(b for b in at.button if b.label == "Use the corrected date order").click()
    at.run()
    preview_line = next(c.value for c in at.caption if c.value.startswith("Preview: ") and "→" in c.value and "·" in c.value
                        and "would convert" in c.value and "/" in c.value)
    numbers = [int(n) for n in re.findall(r"would convert (\d+), already correct (\d+), would fail (\d+)", preview_line)[0]]
    print("rows in the cleaned data:", cleaned_rows, "| non-blank order_date:", real_dates, "| preview:", numbers)
    assert sum(numbers) == real_dates, "the preview must cover exactly the cleaned data's real dates"
    assert sum(numbers) <= cleaned_rows
    next(b for b in at.button if b.label == "Apply Approved Rules").click()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    outcome = next(iter(at.session_state["rule_outcomes_by_sheet"].values()))[0]["stats"]
    assert [outcome["converted"], outcome["already_correct"], outcome["failed"]] == numbers, (outcome, numbers)
    print("PASSED")
else:
    print("SKIPPED")

print()
print("ALL LOADED-RULE-CHECK TESTS PASSED")
