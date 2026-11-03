from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"

import io
import json
import os
import sys
from unittest import mock

sys.path.insert(0, str(ROOT))

import numpy as np
import openpyxl
import pandas as pd
from streamlit.testing.v1 import AppTest

import ai_rules
import changes as ch
import cleaning
import quality
import recipes
import report
import rule_engine as re_
from io_utils import load_sheets
from xlsx_export import export_workbook

GENDER_MAP = {"m": "Male", "male": "Male", "f": "Female", "female": "Female"}
RULE = {"id": "rule_1", "column": "gender", "rule_type": "map_values", "params": {"mapping": GENDER_MAP},
        "description": "Standardize gender to Male / Female.", "confidence": "high"}


def apply(values, mapping=GENDER_MAP, skip=()):
    df = pd.DataFrame({"gender": values})
    out, stats = re_.apply_map_values(df, "gender", {"mapping": mapping}, quality.normalize_placeholders(skip))
    return out["gender"].tolist(), stats


print("TEST: mapping is case-insensitive, ignores surrounding spaces, and counts only real changes")
values = ["m", "M", "male", "MALE", " Male ", "Male", "f", "F", "Female", "FEMALE", "female"]
out, stats = apply(values)
print(out, stats)
assert out == ["Male", "Male", "Male", "Male", "Male", "Male", "Female", "Female", "Female", "Female", "Female"]
assert stats == {"converted": 9, "already_correct": 2, "failed": 0, "unmapped": 0}   # "Male" and "Female" were already standard: reported separately
print("PASSED")

print("TEST: values not in the map are left unchanged and COUNTED; blanks / placeholders / non-text are ignored")
out, stats = apply(["m", "Other", "other", "Prefer not to say", np.nan, "", "  ", "ERROR", 5, "f"], skip=["ERROR"])
print(out, stats)
assert out[:4] == ["Male", "Other", "other", "Prefer not to say"]
assert pd.isna(out[4]) and out[5] == "" and out[6] == "  " and out[7] == "ERROR" and out[8] == 5 and out[9] == "Female"
assert stats == {"converted": 2, "already_correct": 0, "failed": 0, "unmapped": 3}
_, stats_no_skip = apply(["ERROR"], skip=[])
assert stats_no_skip["unmapped"] == 1                                    # without the skip list a placeholder is just another unmapped value
print("PASSED")

print("TEST: validate_rule - structure")
cols = ["gender", "city"]
assert re_.validate_rule(RULE, cols) == ""
bad = lambda mapping: re_.validate_rule({**RULE, "params": {"mapping": mapping}}, cols)
assert "non-empty" in bad({}) and "non-empty" in re_.validate_rule({**RULE, "params": {"mapping": "m=Male"}}, cols)
assert "text -> text" in bad({"m": ""}) and "text -> text" in bad({"": "Male"}) and "text -> text" in bad({"m": 5}) and "text -> text" in bad({"m": None})
assert "appears twice" in bad({"m": "Male", "M": "Man"})                  # same key ignoring case, different targets
assert bad({"m": "Male", "M": "Male"}) == ""                              # same key ignoring case, SAME target: fine
assert "missing required params" in re_.validate_rule({**RULE, "params": {}}, cols)
assert "too many" in bad({f"k{i}": "v" for i in range(201)})
print("PASSED")

print("TEST: validate_rule - every mapping key must exist in the column")
series = pd.Series(["m", "Male", "MALE", "F", "female", "Other", np.nan, "ERROR"])
known = {"gender": quality.casefolded_values(series, ["ERROR"])}
assert known["gender"] == {"m", "male", "f", "female", "other"}
assert re_.validate_rule(RULE, cols, known) == ""
message = re_.validate_rule({**RULE, "params": {"mapping": {**GENDER_MAP, "nonbinary": "Other", "x": "Other"}}}, cols, known)
print(message)
assert message == "These mapping keys do not exist in column 'gender': 'nonbinary', 'x'."
assert re_.validate_rule({**RULE, "params": {"mapping": {"error": "Male"}}}, cols, known) != ""      # a placeholder is not a value
assert re_.validate_rule({**RULE, "params": {"mapping": {"  M ": "Male"}}}, cols, known) == ""        # keys are matched trimmed + case-insensitively
assert re_.validate_rule({**RULE, "params": {"mapping": {"zzz": "Male"}}}, cols, None) == ""          # no data supplied -> structure only
print("PASSED")

print("TEST: the AI gate - only valid map_values rules reach the UI; mapping beats unify_casing")
profiles = ai_rules.build_column_profile(pd.DataFrame({"gender": ["m", "Male", "f", "FEMALE", "male"], "city": ["Pune"] * 5}), mask=False)
frame = pd.DataFrame({"gender": ["m", "Male", "f", "FEMALE", "male"], "city": ["Pune"] * 5})
kv = {c: quality.casefolded_values(frame[c]) for c in frame.columns}
response = json.dumps({"rules": [
    {**RULE, "id": "good"},
    {**RULE, "id": "badkey", "description": "Bad key.", "params": {"mapping": {"m": "Male", "unknown": "Other"}}},
    {"id": "casing", "column": "gender", "rule_type": "unify_casing", "params": {"style": "title"}, "description": "Title-case gender.", "confidence": "medium"},
    {"id": "casing_city", "column": "city", "rule_type": "unify_casing", "params": {"style": "title"}, "description": "Title-case city.", "confidence": "low"},
]})
with mock.patch.object(ai_rules, "_call_groq", lambda p: response):
    result = ai_rules.suggest_rules(profiles, "IN", "groq", inconsistent_casing_columns=quality.columns_with_inconsistent_casing(frame), known_values=kv)
print([r["id"] for r in result["rules"]], [(d["description"], d["reason"]) for d in result["dropped"]])
assert [r["id"] for r in result["rules"]] == ["good"]
reasons = {d["description"]: d["reason"] for d in result["dropped"]}
assert "do not exist in column 'gender': 'unknown'" in reasons["Bad key."]
assert reasons["Title-case gender."] == "A map_values rule already standardizes this column."
assert "consistent casing" in reasons["Title-case city."]
# an INVALID mapping does not kill the casing rule that was only dropped because of it
only_bad = json.dumps({"rules": [response and json.loads(response)["rules"][1], json.loads(response)["rules"][2]]})
with mock.patch.object(ai_rules, "_call_groq", lambda p: only_bad):
    result2 = ai_rules.suggest_rules(profiles, "IN", "groq", inconsistent_casing_columns={"gender"}, known_values=kv)
assert [r["id"] for r in result2["rules"]] == ["casing"]
print("PASSED")

print("TEST: the prompt prefers map_values for abbreviations / variants and lists it as a rule type")
prompt = ai_rules.build_prompt(profiles, "IN")
for needle in ['"map_values": params', "PREFER map_values", "abbreviations", "INCLUDING the standard value itself", "never invent a key",
               "masked", "use \"map_values\"", "one of the 5", "5 rule types"]:
    assert needle in prompt, needle
assert "map_values" in ai_rules.RULE_TYPES and len(ai_rules.RULE_TYPES) == 5
assert "map_values" in json.dumps(ai_rules.RESPONSE_SCHEMA)
print("PASSED")

print("TEST: recipes round-trip a map_values rule and apply it to a new file")
recipe = recipes.build_recipe("Gender", "IN", [{**RULE, "approved": True}])
loaded = recipes.load_recipe(io.BytesIO(recipes.save_recipe_bytes(recipe)))
assert loaded["rules"][0]["params"]["mapping"] == GENDER_MAP
other = pd.DataFrame({"gender": ["M", "female", "Other"]})
assert re_.validate_rule(loaded["rules"][0], list(other.columns), {"gender": quality.casefolded_values(other["gender"])}) != ""     # 'f' and 'male'... not in THIS file
partial = {**loaded["rules"][0], "params": {"mapping": {"m": "Male", "female": "Female"}}}
assert re_.validate_rule(partial, ["gender"], {"gender": quality.casefolded_values(other["gender"])}) == ""
out, stats = re_.apply_rule(other.copy(), partial)
assert out["gender"].tolist() == ["Male", "Female", "Other"] and stats["unmapped"] == 1
print("PASSED")

print("TEST: changed cells are recorded as AI-rule changes (blue), unmapped ones are not")
log = ch.ChangeLog()
frame2 = pd.DataFrame({"gender": ["m", "Male", "F", "Other"]})
out, _ = re_.apply_rule(frame2.copy(), RULE, log=log)
records = log.finalize(out)
assert [(r["row"], r["old"], r["new"], r["reason"]) for r in records] == [(2, "m", "Male", "ai_rule"), (4, "F", "Female", "ai_rule")]
assert ch.REASON_COLORS["ai_rule"] == "BDD7EE"
print("PASSED")

print("TEST: report wording")
line = report._format_rule_outcome_line({"rule": RULE, "stats": {"converted": 4, "already_correct": 9, "failed": 0, "unmapped": 7}})
assert line.endswith("-> converted 4, already correct 9, not in the map (left unchanged) 7"), line
assert report._format_rule_outcome_line({"rule": RULE, "stats": {"converted": 4, "already_correct": 9, "failed": 1}}).endswith(
    "converted 4, already correct 9, failed 1")
print("PASSED")

print("TEST: through the app - suggest, review, approve, apply; keys checked; blue cells in the Excel")
csv = ("order_id,gender,city\n" + "\n".join(
    f"O{i},{g},{c}" for i, (g, c) in enumerate(
        [("m", "Pune"), ("Male", "Pune"), ("F", "Delhi"), ("female", "Delhi"), ("FEMALE", "Delhi"), ("Other", "Pune"), ("MALE", "Pune")] * 3)
)).encode()
at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
at.run()
at.file_uploader[0].upload("g.csv", csv, "text/csv")
at.run()
next(b for b in at.button if b.label == "Clean my file").click()
at.run()
ai = json.dumps({"rules": [
    {**RULE, "description": "Standardize gender to Male / Female (m, f, male, female)."},
    {**RULE, "id": "rule_2", "description": "Invented key rule.", "params": {"mapping": {"m": "Male", "ghost": "Male"}}},
    {"id": "rule_3", "column": "gender", "rule_type": "unify_casing", "params": {"style": "title"}, "description": "Title-case gender.", "confidence": "low"},
]})
with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_fake"}), mock.patch.object(ai_rules, "_call_groq", lambda p: ai):
    next(b for b in at.button if b.label == "Suggest Cleaning Rules").click()
    at.run()
assert not at.exception, [e.value for e in at.exception]
labels = [c.label for c in at.checkbox]
assert "Standardize gender to Male / Female (m, f, male, female)." in labels
assert "Invented key rule." not in labels and "Title-case gender." not in labels
assert any("2 suggestion(s) were discarded" in e.label for e in at.expander)
next(c for c in at.checkbox if c.label.startswith("Standardize gender")).check()
at.run()
next(b for b in at.button if b.label == "Apply Approved Rules").click()
at.run()
assert not at.exception, [e.value for e in at.exception]
cleaned = at.session_state["cleaned_sheets"]["Data"]
print(cleaned["gender"].value_counts().to_dict())
assert cleaned["gender"].value_counts().to_dict() == {"Male": 9, "Female": 9, "Other": 3}
text = at.session_state["report_text"]
assert "converted 15, already correct 3, not in the map (left unchanged) 3" in text, text
s = at.session_state
wb = openpyxl.load_workbook(io.BytesIO(export_workbook(s["cleaned_sheets"], s["change_logs"], s["sheet_stats"], s["quality_before"],
                                                       s["placeholder_list"], s["original_filename"], s["original_rows"], True)))
ws = wb["Data"]
blue = [r for r in range(2, ws.max_row + 1) if (ws.cell(row=r, column=2).fill.fgColor.rgb or "")[-6:] == "BDD7EE"]
unchanged_male = [r for r in range(2, ws.max_row + 1) if ws.cell(row=r, column=2).value == "Male" and ws.cell(row=r, column=2).fill.fill_type is None]
assert len(blue) == 15 and len(unchanged_male) == 3                       # 15 changed (blue); the 3 already-standard "Male" cells are not
assert ws.cell(row=blue[0], column=2).comment.text.startswith("Was: ") and "AI rule: Standardize gender" in ws.cell(row=blue[0], column=2).comment.text
print("PASSED")

print("TEST: the real retail file's gender column")
retail = _Path.home() / "Downloads" / "archive" / "retail_sales_dataset.csv"
if retail.exists():
    original = load_sheets(io.BytesIO(retail.read_bytes()), is_csv=True)["Data"]
    cleaned, _ = cleaning.clean_dataframe_with_steps(original.copy(), {"headers": True, "trim": True, "duplicates": True, "empty": True,
                                                                        "placeholders": True, "numeric": True, "fills": False})
    known_retail = {"gender": quality.casefolded_values(cleaned["gender"])}
    full = {"mapping": {"m": "Male", "male": "Male", "f": "Female", "female": "Female"}}
    rule = {**RULE, "params": full}
    assert re_.validate_rule(rule, list(cleaned.columns), known_retail) == ""
    before = cleaned["gender"].value_counts().to_dict()
    out, stats = re_.apply_rule(cleaned.copy(), rule)
    expect_converted = sum(before.get(v, 0) for v in ["m", "M", "male", "MALE", "f", "F", "FEMALE", "female"])
    print(before, "->", out["gender"].value_counts().to_dict(), stats)
    assert stats["converted"] == expect_converted and stats["unmapped"] == before["Other"]
    assert set(out["gender"].dropna().unique()) == {"Male", "Female", "Other"}
    print("PASSED")
else:
    print("skipped")

print("\nALL MAP VALUES TESTS PASSED")
