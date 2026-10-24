from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""Local safety checks on suggested rules (date order, local casing detection, before -> after previews)."""
import copy
import json
import os
import sys
from unittest import mock

sys.path.insert(0, str(ROOT))

import pandas as pd
import ai_rules
import rule_checks as rc
from rule_engine import apply_rule

PH = ["ERROR", "UNKNOWN"]


def date_rule(**params):
    return {"id": "r1", "column": "d", "rule_type": "parse_date",
            "params": {"output_format": "%Y-%m-%d", **params}, "description": "dates", "confidence": "high", "approved": False}


def retail_dates():
    """The retail shape: ISO dates, 'August 17, 2024' dates, and 150 dd/mm/yyyy dates -
    86 with a first part above 12, 64 where both parts are 12 or less - and no second part above 12."""
    over12 = [f"{13 + i % 18:02d}/{1 + i % 12:02d}/2023" for i in range(86)]
    under12 = [f"{1 + i % 12:02d}/{1 + (i * 5) % 12:02d}/2024" for i in range(64)]
    return pd.Series(["2024-08-09", "August 17, 2024", None] + over12 + under12 + ["ERROR"], dtype=object)


print("TEST: detect_date_order - the exact retail case: 86 values can only be day-first")
found = rc.detect_date_order(retail_dates(), PH)
print(found)
assert found["order"] == "day_first" and found["day_first_count"] == 86 and found["month_first_count"] == 0
assert found["day_example"] == "13/01/2023"
print("PASSED")

print("TEST: the AI said day_first=false -> corrected to true, with a note")
series = retail_dates()
rule, info = rc.check_date_rule(date_rule(day_first=False), series, "IN", PH)
assert rule["params"]["day_first"] is True
assert info["note"] == "Corrected to day-first: 86 values like 13/01/2023 can only be day-first.", info["note"]
print("PASSED")

print("TEST: the AI was right -> unchanged, note says it was confirmed; the original rule dict is never mutated")
original = date_rule(day_first=True)
snapshot = copy.deepcopy(original)
rule, info = rc.check_date_rule(original, series, "IN", PH)
assert original == snapshot and rule["params"]["day_first"] is True
assert info["note"].startswith("Checked against the data: day-first confirmed (86 values like")
print("PASSED")

print("TEST: month-first data corrects a day-first rule; swapping also fixes a contradicting input_format")
us = pd.Series(["12/25/2024", "01/03/2024", "07/04/2024"], dtype=object)
rule, info = rc.check_date_rule(date_rule(day_first=True), us, "IN", PH)
assert rule["params"]["day_first"] is False and info["note"].startswith("Corrected to month-first: 1 values like 12/25/2024")
rule, _ = rc.check_date_rule(date_rule(input_format="%d/%m/%Y"), us, "IN", PH)
assert rule["params"]["input_format"] == "%m/%d/%Y" and rule["params"]["day_first"] is False
rule, info = rc.check_date_rule(date_rule(input_format="%m/%d/%Y"), us, "IN", PH)
assert rule["params"]["input_format"] == "%m/%d/%Y" and info["note"].startswith("Checked against the data")
print("PASSED")

print("TEST: ambiguous data -> the locale default is used, and the note says so")
ambiguous = pd.Series(["01/02/2024", "03/04/2024", "2024-05-06", None], dtype=object)
rule, info = rc.check_date_rule(date_rule(day_first=False), ambiguous, "IN", PH)
assert rule["params"]["day_first"] is True and "cannot tell" in info["note"] and "India default (day-first)" in info["note"], info
rule, info = rc.check_date_rule(date_rule(day_first=True), ambiguous, "US", PH)
assert rule["params"]["day_first"] is False and "United States default (month-first)" in info["note"], info
print("PASSED")

print("TEST: conflicting data (both orders prove themselves) -> locale default, flagged as inconsistent")
mixed = pd.Series(["25/12/2024", "12/25/2024"], dtype=object)
rule, info = rc.check_date_rule(date_rule(day_first=False), mixed, "GB", PH)
assert rule["params"]["day_first"] is True and "inconsistent" in info["note"], info
print("PASSED")

print("TEST: other rule types are left alone; year-first-only columns and placeholders are not read as day/month dates")
casing = {"id": "c", "column": "d", "rule_type": "unify_casing", "params": {"style": "title"}, "description": "x"}
assert rc.check_date_rule(casing, series, "IN", PH) == (casing, None)
assert rc.detect_date_order(pd.Series(["2024-12-25", "ERROR", None], dtype=object), PH)["order"] == "ambiguous"
print("PASSED")

print("TEST: preview of the date rule - counts and examples; the wrong order would give different dates")
frame = pd.DataFrame({"d": series})
good = rc.preview_rule(frame["d"], date_rule(day_first=True), PH)
print(rc.preview_text(good))
assert good["failed"] == 0 and len(good["examples"]) == 3
assert good["converted"] + good["already_correct"] == 3 + 86 + 64 - 1       # every real value; the blank and ERROR are skipped
fixed = apply_rule(frame.copy(), date_rule(day_first=True), PH)[0]["d"]
swapped = apply_rule(frame.copy(), date_rule(day_first=False), PH)[0]["d"]
assert fixed.iloc[3] == "2023-01-13"
assert (swapped != fixed).sum() > 0, "month-first would silently read some dates differently"
print("PASSED")

print("TEST: preview of every rule type - examples (max 3, distinct), counts, nothing modified")
df = pd.DataFrame({
    "status": ["delivered", "Delivered", "delivered", "Shipped", "shipped", "pending", "Pending", None],
    "price": ["$20", "1500", "Rs. 1,500", "abc", None, "7", "8", "9"],
    "gender": ["m", "Male", "MALE", "f", "x", None, "Female", "female"],
    "phone": ["0300-1234567", "+923001234567", "nope", None, "0321 9876543", "0321 9876543", "x", "y"],
})
frozen = df.copy()
p = rc.preview_rule(df["status"], {"column": "status", "rule_type": "unify_casing", "params": {"style": "title"}})
assert p["examples"] == [("delivered", "Delivered"), ("shipped", "Shipped"), ("pending", "Pending")], p
assert (p["converted"], p["already_correct"], p["failed"]) == (4, 3, 0), p
p = rc.preview_rule(df["price"], {"column": "price", "rule_type": "strip_currency_to_number", "params": {}})
assert p["examples"][0] == ("$20", 20.0) and p["failed"] == 1, p
p = rc.preview_rule(df["gender"], {"column": "gender", "rule_type": "map_values",
                                   "params": {"mapping": {"m": "Male", "male": "Male", "f": "Female", "female": "Female"}}})
assert p["unmapped"] == 1 and p["converted"] == 4 and "not in the map 1" in rc.preview_text(p), p
p = rc.preview_rule(df["phone"], {"column": "phone", "rule_type": "normalize_phone", "params": {"country": "PK"}})
assert p["examples"][0] == ("0300-1234567", "+923001234567") and p["failed"] == 3, p
none = rc.preview_rule(pd.Series(["Delivered", "Shipped"], dtype=object),
                       {"column": "c", "rule_type": "unify_casing", "params": {"style": "title"}})
assert none["examples"] == [] and rc.preview_text(none).startswith("no value would change")
assert df.equals(frozen), "a preview must never change the data"
print("PASSED")

print("TEST: local casing detection - order_status delivered/Delivered is added, marked local")
retail = pd.DataFrame({
    "order_id": [f"ORD{i}" for i in range(40)],
    "order_status": ["Delivered"] * 20 + ["delivered"] * 5 + ["Shipped"] * 8 + ["shipped"] * 2 + ["Pending"] * 4 + [None],
})
rules, notes = rc.review_rules(retail, [], "IN", PH)
assert [(r["column"], r["rule_type"], r["params"]) for r in rules] == [("order_status", "unify_casing", {"style": "title"})], rules
assert rules[0]["approved"] is False and rules[0]["id"] == "local_casing_1"
assert notes["local_casing_1"]["source"] == "local" and "did not suggest" in notes["local_casing_1"]["note"]
print("PASSED")

print("TEST: not added when map_values or unify_casing already covers the column; never for ids / names / emails")
mapping = {"id": "m", "column": "order_status", "rule_type": "map_values", "params": {"mapping": {"delivered": "Delivered"}},
           "description": "map"}
assert rc.review_rules(retail, [mapping], "IN", PH)[0] == [mapping]
upper = {"id": "u", "column": "order_status", "rule_type": "unify_casing", "params": {"style": "upper"}, "description": "u"}
assert rc.review_rules(retail, [upper], "IN", PH)[0] == [upper]
people = pd.DataFrame({"customer_name": ["alice smith", "Alice Smith"] * 10, "email": ["a@x.com", "A@x.com"] * 10,
                       "order_id": ["abc1", "ABC1"] * 10, "region": ["north", "North"] * 10})
rules, _ = rc.review_rules(people, [], "IN", PH)
assert [r["column"] for r in rules] == ["region"], rules
consistent = pd.DataFrame({"s": ["Delivered", "Shipped"] * 10})
assert rc.review_rules(consistent, [], "IN", PH)[0] == []
print("PASSED")

print("TEST: the best style follows the data (UPPER majority)")
shout = pd.DataFrame({"s": ["ACTIVE"] * 8 + ["active"] * 2 + ["INACTIVE"] * 6})
assert rc.review_rules(shout, [], "IN", PH)[0][0]["params"] == {"style": "upper"}
print("PASSED")

retail_file = _Path.home() / "Downloads" / "archive" / "retail_sales_dataset.csv"
print("TEST: the real retail file (skipped if absent)")
if retail_file.exists():
    real = pd.read_csv(retail_file, dtype=str, keep_default_na=False).replace("", None)
    found = rc.detect_date_order(real["order_date"], PH)
    assert found["order"] == "day_first" and found["day_first_count"] == 86, found
    wrong = {**date_rule(day_first=False), "column": "order_date"}
    rules, notes = rc.review_rules(real, [wrong], "IN", PH)
    assert rules[0]["params"]["day_first"] is True and notes["r1"]["note"].startswith("Corrected to day-first: 86 values like")
    status = [r for r in rules if r["column"] == "order_status"]
    assert status and status[0]["rule_type"] == "unify_casing" and notes[status[0]["id"]]["source"] == "local"
    preview = rc.preview_rule(real["order_status"], status[0], PH)
    assert preview["converted"] == 121 + 16 + 13 + 6 and preview["failed"] == 0, preview
    print("PASSED")
else:
    print("SKIPPED")

# ---------------------------------------------------------------------------------------------
# UI: notes, the "detected locally" suggestion and the preview line show up under the rules
# ---------------------------------------------------------------------------------------------
from streamlit.testing.v1 import AppTest

dates = ["2024-08-09"] * 3 + [f"{13 + i % 15:02d}/{1 + i % 12:02d}/2023" for i in range(12)] + ["02/04/2024"] * 5
statuses = ["Delivered"] * 12 + ["delivered"] * 4 + ["Shipped"] * 4
csv = "order_date,order_status\n" + "\n".join(f"{d},{s}" for d, s in zip(dates, statuses)) + "\n"
AI = json.dumps({"rules": [{"id": "rule_1", "column": "order_date", "rule_type": "parse_date",
                            "params": {"output_format": "%Y-%m-%d", "day_first": False},
                            "description": "Write order_date as YYYY-MM-DD.", "confidence": "high"}]})

print("TEST: UI - corrected date rule with note + preview, local casing rule marked, applying both works")
with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_fake", "AI_PROVIDER": "groq"}), mock.patch.object(ai_rules, "_call_groq", lambda p: AI):
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
    at.run()
    at.file_uploader[0].upload("orders.csv", csv.encode(), "text/csv")
    at.run()
    next(b for b in at.button if b.label == "Clean File").click()
    at.run()
    next(b for b in at.button if b.label == "Suggest Cleaning Rules").click()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    captions = [c.value for c in at.caption]
    print([c for c in captions if "Corrected" in c or "locally" in c or c.startswith("Preview")])
    assert any("Corrected to day-first: 12 values like" in c for c in captions), captions
    assert any(c.startswith("🔎 **Detected locally.**") for c in captions)
    previews = [c for c in captions if c.startswith("Preview: ")]
    assert len(previews) == 2 and any("delivered → Delivered" in c for c in previews), previews
    assert all("would fail 0" in c for c in previews)
    assert not any(c.value for c in at.checkbox if c.label.startswith(("Write order_date", "Make capitalisation")))
    rules = next(iter(at.session_state["suggested_rules_by_sheet"].values()))
    assert rules[0]["params"]["day_first"] is True and rules[1]["id"] == "local_casing_1"
    assert all(set(rule) == {"id", "column", "rule_type", "params", "description", "confidence", "approved"} for rule in rules), \
        "notes must not travel inside the rules (they would end up in recipes)"
    for box in at.checkbox:
        if box.label.startswith(("Write order_date", "Make capitalisation")):
            box.check()
    at.run()
    next(b for b in at.button if b.label == "Apply Approved Rules").click()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    cleaned = next(iter(at.session_state["cleaned_sheets"].values()))
    assert set(cleaned["order_status"]) == {"Delivered", "Shipped"}     # (the Clean step already removed the duplicate rows)
    assert "2023-01-13" in set(cleaned["order_date"]) and not any("/" in d for d in cleaned["order_date"]), list(cleaned["order_date"])
print("PASSED")
print()
print("ALL RULE-CHECK TESTS PASSED")
