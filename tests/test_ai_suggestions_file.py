from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"

import copy
import io
import json
import os
import sys
from datetime import date
from unittest import mock

sys.path.insert(0, str(ROOT))

import streamlit as st
from streamlit.testing.v1 import AppTest

import ai_rules
import recipes

SAMPLE = (ROOT / "test_data" / "messy_sample.csv").read_bytes()
SAMPLE_RECIPE = (ROOT / "test_data" / "sample_recipe.json").read_bytes()

RULES = [
    {"id": "rule_1", "column": "Phone", "rule_type": "normalize_phone", "params": {"country": "PK"},
     "description": "Normalize phone numbers to E.164.", "confidence": "high", "approved": True},
    {"id": "rule_2", "column": "Status", "rule_type": "unify_casing", "params": {"style": "title"},
     "description": "Title-case Status.", "confidence": "medium", "approved": False},
    {"id": "rule_3", "column": "Signup Date", "rule_type": "parse_date", "params": {"output_format": "%Y-%m-%d", "day_first": True},
     "description": "Write Signup Date as YYYY-MM-DD.", "confidence": "low", "approved": True},
]

print("TEST: build_suggestions_file - all rules, none approved, with who/where/when")
rules_by_sheet = {"Data": copy.deepcopy(RULES)}
snapshot = copy.deepcopy(rules_by_sheet)
data = recipes.build_suggestions_file(rules_by_sheet, "groq", "openai/gpt-oss-120b", "PK", "messy_sample.csv")
print({k: v for k, v in data.items() if k != "rules"})
assert rules_by_sheet == snapshot, "the in-memory suggestions must not be touched"
assert len(data["rules"]) == 3 and all(r["approved"] is False for r in data["rules"])              # even the two that were ticked
assert [r["id"] for r in data["rules"]] == ["rule_1", "rule_2", "rule_3"] and data["rules"][1]["description"] == "Title-case Status."
assert (data["provider"], data["model"], data["locale"], data["file_name"]) == ("groq", "openai/gpt-oss-120b", "PK", "messy_sample.csv")
assert data["date"] == date.today().isoformat() and data["recipe_name"] == "AI suggestions for messy_sample.csv"
assert recipes.build_suggestions_file(rules_by_sheet, "x", "y", "PK", "f.csv", created="2026-01-02")["date"] == "2026-01-02"
print("PASSED")

print("TEST: same recipe format - loads with load_recipe; a rule suggested for several sheets is saved once")
loaded = recipes.load_recipe(io.BytesIO(recipes.save_recipe_bytes(data)))
assert loaded == data and set(["recipe_name", "locale", "rules"]) <= set(loaded)
two_sheets = recipes.build_suggestions_file({"A": copy.deepcopy(RULES), "B": copy.deepcopy(RULES[:1])}, "anthropic", "m", "IN", "x.xlsx")
assert len(two_sheets["rules"]) == 3
print("PASSED")

print("TEST: is_ticked follows the rule's own flag (recipes pre-ticked, suggestion files not, hand-written rules ticked)")
assert recipes.is_ticked({"approved": True}) and not recipes.is_ticked({"approved": False}) and recipes.is_ticked({})
assert not recipes.is_ticked({"approved": "false"}) and not recipes.is_ticked({"approved": 0})
assert all(recipes.is_ticked(r) for r in json.loads(SAMPLE_RECIPE)["rules"])                          # the shipped sample recipe stays pre-ticked
print("PASSED")

# ---------------------------------------------------------------------------------------------
# UI tests: capture exactly what the app hands to st.download_button
# ---------------------------------------------------------------------------------------------
AI_RESPONSE = json.dumps({"rules": [
    {k: v for k, v in r.items() if k != "approved"} for r in RULES
]})
_original = st.download_button
captured = {}


def spy(label, data=None, file_name=None, mime=None, key=None, **kwargs):
    captured[label] = {"data": data, "file_name": file_name, "mime": mime, "key": key}
    return _original(label, data=data, file_name=file_name, mime=mime, key=key, **kwargs)


def new_app():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
    at.run()
    at.file_uploader[0].upload("sample.csv", SAMPLE, "text/csv")
    at.run()
    next(b for b in at.button if b.label == "Clean File").click()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def flatten(block):
    items = []
    for child in block.children.values():
        items.append(child)
        if getattr(child, "children", None):
            items.extend(flatten(child))
    return items


def ai_panel(at):
    return next(e for e in at.expander if e.label == "AI-suggested cleaning rules (optional)")


def run_with_spy(at):
    with mock.patch.object(st, "download_button", spy):
        at.run()
    assert not at.exception, [e.value for e in at.exception]


with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_fake", "AI_PROVIDER": "groq"}), mock.patch.object(ai_rules, "_call_groq", lambda p: AI_RESPONSE):
    print("TEST: no suggestions yet -> no suggestions download")
    at = new_app()
    assert "Download AI suggestions (.json)" not in [b.label for b in at.download_button]
    print("PASSED")

    print("TEST: right after the suggestions come back - before anything is ticked or applied - the button is there, in the right place")
    captured.clear()
    next(b for b in at.button if b.label == "Suggest Cleaning Rules").click()
    run_with_spy(at)
    assert "Download AI suggestions (.json)" in [b.label for b in at.download_button]
    order = [(c.type, getattr(c, "label", None)) for c in flatten(ai_panel(at))]
    names = [label for _, label in order]
    print([n for n in names if n and ("Download" in n or "Suggest" in n or "Apply" in n or "Normalize" in n)])
    banner = next(i for i, (kind, _) in enumerate(order) if kind == "info")
    download = names.index("Download AI suggestions (.json)")
    first_rule = names.index("Normalize phone numbers to E.164.")
    apply_button = names.index("Apply Approved Rules")
    assert names.index("Suggest Cleaning Rules") < banner < download < first_rule < apply_button, "banner -> download -> review boxes -> Apply"
    assert not at.session_state["rule_outcomes_by_sheet"]                                               # nothing has been applied
    assert not any(c.value for c in at.checkbox if c.label in {r["description"] for r in RULES})         # nothing has been ticked
    print("PASSED")

    print("TEST: the file it saves - every suggestion, none approved, with provider / model / locale / file / date")
    info = captured["Download AI suggestions (.json)"]
    assert info["file_name"] == "sample_ai_suggestions.json" and info["mime"] == "application/json"
    saved = json.loads(info["data"].decode("utf-8"))
    print({k: v for k, v in saved.items() if k != "rules"}, [(r["id"], r["approved"]) for r in saved["rules"]])
    assert [r["id"] for r in saved["rules"]] == ["rule_1", "rule_2", "rule_3"] and all(r["approved"] is False for r in saved["rules"])
    assert (saved["provider"], saved["model"], saved["locale"], saved["file_name"], saved["date"]) == (
        "groq", "openai/gpt-oss-120b", "PK", "sample.csv", date.today().isoformat())
    print("PASSED")

    print("TEST: ticking rules does not change the saved file (still everything, still unapproved)")
    next(c for c in at.checkbox if c.label == "Normalize phone numbers to E.164.").check()
    next(c for c in at.checkbox if c.label == "Title-case Status.").check()
    captured.clear()
    run_with_spy(at)
    saved2 = json.loads(captured["Download AI suggestions (.json)"]["data"].decode("utf-8"))
    assert [(r["id"], r["approved"]) for r in saved2["rules"]] == [("rule_1", False), ("rule_2", False), ("rule_3", False)]
    print("PASSED")

    print("TEST: loading the file later shows the same review screen with NOTHING pre-ticked")
    fresh = new_app()
    next(f for f in fresh.file_uploader if f.key == "recipe_uploader").upload("sample_ai_suggestions.json", info["data"], "application/json")
    fresh.run()
    next(b for b in fresh.button if b.label == "Load Recipe").click()
    fresh.run()
    assert not fresh.exception, [e.value for e in fresh.exception]
    message = [s.value for s in fresh.success]
    print(message)
    assert any("Loaded AI suggestions 'AI suggestions for sample.csv' with 3 rule(s)" in m and "nothing is ticked" in m for m in message)
    boxes = {c.label: c.value for c in fresh.checkbox if c.label in {r["description"] for r in RULES}}
    assert set(boxes) == {r["description"] for r in RULES} and not any(boxes.values()), boxes
    banner_text = [i.value for i in fresh.info]
    assert any("Groq" in b and "openai/gpt-oss-120b" in b and "loaded from a saved file" in b and date.today().isoformat() in b for b in banner_text), banner_text
    print("PASSED")

    print("TEST: applying with nothing ticked changes nothing and offers no recipe; ticking one then applying works")
    before = fresh.session_state["cleaned_sheets"]["Data"].copy()
    next(b for b in fresh.button if b.label == "Apply Approved Rules").click()
    fresh.run()
    assert not fresh.exception
    assert fresh.session_state["cleaned_sheets"]["Data"].equals(before)
    assert fresh.session_state["rule_outcomes_by_sheet"] == {"Data": []}
    assert not [b for b in fresh.download_button if b.label == "Download Recipe (.json)"]
    next(c for c in fresh.checkbox if c.label == "Title-case Status.").check()
    fresh.run()
    next(b for b in fresh.button if b.label == "Apply Approved Rules").click()
    fresh.run()
    after = fresh.session_state["cleaned_sheets"]["Data"]
    assert set(after["Status"].dropna()) == {"Active", "Inactive", "Pending"} and list(after["Phone"]) == list(before["Phone"])    # only the ticked rule ran
    print("PASSED")

    print("TEST: a plain recipe still loads PRE-TICKED, with the original wording")
    plain = new_app()
    next(f for f in plain.file_uploader if f.key == "recipe_uploader").upload("sample_recipe.json", SAMPLE_RECIPE, "application/json")
    plain.run()
    next(b for b in plain.button if b.label == "Load Recipe").click()
    plain.run()
    assert any(s.value.startswith("Loaded recipe 'Messy Sample Recipe' with 4 rule(s) - review them below before applying.") for s in plain.success)
    assert all(c.value for c in plain.checkbox if c.label in {r["description"] for r in json.loads(SAMPLE_RECIPE)["rules"]})
    assert not [i for i in plain.info if "Suggestions generated by" in i.value]                          # a recipe has no provider
    print("PASSED")

    print("TEST: the Download Recipe button appears in the AI panel right after Apply succeeds (and still at the bottom)")
    at = new_app()
    next(b for b in at.button if b.label == "Suggest Cleaning Rules").click()
    at.run()
    assert not [b for b in at.download_button if b.label == "Download Recipe (.json)"], "nothing applied yet -> no recipe button"
    next(c for c in at.checkbox if c.label == "Normalize phone numbers to E.164.").check()
    next(c for c in at.checkbox if c.label == "Title-case Status.").check()
    at.run()
    captured.clear()
    next(b for b in at.button if b.label == "Apply Approved Rules").click()
    run_with_spy(at)
    assert any(s.value == "Approved rules applied!" for s in at.success)
    names = [getattr(c, "label", None) for c in flatten(ai_panel(at))]
    assert "Download Recipe (.json)" in names, "the recipe button must be INSIDE the AI panel"
    assert names.index("Apply Approved Rules") < names.index("Download Recipe (.json)")
    assert names.index("Download AI suggestions (.json)") < names.index("Apply Approved Rules")
    recipe_buttons = [b for b in at.download_button if b.label == "Download Recipe (.json)"]
    assert len(recipe_buttons) == 2                                                                      # panel + bottom of the page
    saved_recipe = json.loads(captured["Download Recipe (.json)"]["data"].decode("utf-8"))
    assert [(r["id"], r["approved"]) for r in saved_recipe["rules"]] == [("rule_1", True), ("rule_2", True)]   # only what was approved
    assert "provider" not in saved_recipe                                                                # a recipe is not a suggestions file
    print("PASSED")

    print("TEST: recipe name typed in the panel is used by both recipe buttons")
    next(t for t in at.text_input if t.key == "recipe_name").set_value("My Test")
    captured.clear()
    at.run()
    run_with_spy(at)
    assert captured["Download Recipe (.json)"]["file_name"] == "My_Test_recipe.json"
    assert json.loads(captured["Download Recipe (.json)"]["data"].decode("utf-8"))["recipe_name"] == "My Test"
    print("PASSED")

print("TEST: no suggestions came back -> nothing to download")
with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_fake", "AI_PROVIDER": "groq"}), mock.patch.object(ai_rules, "_call_groq", lambda p: '{"rules": []}'):
    empty = new_app()
    next(b for b in empty.button if b.label == "Suggest Cleaning Rules").click()
    empty.run()
    assert not empty.exception and "Download AI suggestions (.json)" not in [b.label for b in empty.download_button]
print("PASSED")

print("TEST: a suggestions file from an unknown provider still loads (shown as written)")
odd = dict(data, provider="somewhere", model="m1")
at3 = new_app()
next(f for f in at3.file_uploader if f.key == "recipe_uploader").upload("odd.json", json.dumps(odd).encode(), "application/json")
at3.run()
next(b for b in at3.button if b.label == "Load Recipe").click()
at3.run()
assert not at3.exception and any("somewhere" in i.value and "m1" in i.value for i in at3.info)
print("PASSED")

print("\nALL AI SUGGESTIONS FILE TESTS PASSED")
