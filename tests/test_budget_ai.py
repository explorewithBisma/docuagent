from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""budget/ai_categories.py with a mocked provider: only descriptions go out, answers are validated, no key = skipped."""
import json
import sys
from unittest import mock

sys.path.insert(0, str(ROOT))

import ai_rules
from budget import ai_categories as ai

CATEGORIES = ["Food", "Transport", "Health", "Bills"]
sent_prompts = []


def fake_groq(answer):
    def call(prompt):
        sent_prompts.append(prompt)
        return json.dumps(answer) if not isinstance(answer, str) else answer
    return call


print("TEST: ai_available - needs a key; a missing key is a reason, never an error")
assert ai.ai_available({}) == (False, "No API key is set (ANTHROPIC_API_KEY), so the AI step is skipped.")
assert ai.ai_available({"GROQ_API_KEY": "k"}) == (True, "groq")
assert ai.ai_available({"AI_PROVIDER": "groq"})[0] is False and ai.ai_available({"ANTHROPIC_API_KEY": "k"}) == (True, "anthropic")
assert ai.ai_available({"AI_PROVIDER": "nonsense"})[0] is False
print("PASSED")

print("TEST: only distinct description texts and our category names are sent - nothing else")
texts = ["malabar", "Malabar ", "Nesto", "  Aster   pharmacy ", "", None, "x" * 200]
sent = ai.clean_descriptions(texts)
assert sent == ["malabar", "Nesto", "Aster pharmacy", "x" * ai.MAX_DESCRIPTION_LENGTH]
prompt = ai.build_prompt(sent, CATEGORIES)
for needed in ('"malabar"', '"Nesto"', '"Aster pharmacy"', '["Food", "Transport", "Health", "Bills"]'):
    assert needed in prompt, needed
assert len(ai.clean_descriptions([f"shop {i}" for i in range(500)])) == ai.MAX_DESCRIPTIONS
import inspect
assert list(inspect.signature(ai.suggest_categories).parameters) == ["descriptions", "categories", "provider"], \
    "the function cannot even receive amounts, dates or other columns"
print("PASSED")

print("TEST: a good answer becomes assignments with confidence; the call goes through the existing provider code")
answer = {"categories": [{"description": "malabar", "category": "food", "confidence": "medium"},
                         {"description": "Aster pharmacy", "category": "Health", "confidence": "high"},
                         {"description": "Nesto", "category": "Food", "confidence": "HIGH"}]}
with mock.patch.object(ai_rules, "_call_groq", fake_groq(answer)):
    result = ai.suggest_categories(["malabar", "Nesto", "Aster pharmacy"], CATEGORIES, "groq")
assert result["assignments"] == {"malabar": ("Food", "medium"), "Aster pharmacy": ("Health", "high"), "Nesto": ("Food", "high")}
assert result["provider"] == "groq" and result["model"] == ai_rules.model_for("groq") and result["dropped"] == []
assert result["sent"] == ["malabar", "Nesto", "Aster pharmacy"]
print("PASSED")

print("TEST: the Anthropic path passes the category schema, not the rules schema")
calls = []


def fake_anthropic(prompt, schema=None):
    calls.append(schema)
    return json.dumps({"categories": []})


with mock.patch.object(ai_rules, "_call_anthropic", fake_anthropic):
    ai.suggest_categories(["x"], CATEGORIES, "anthropic")
assert calls == [ai.CATEGORY_SCHEMA]
print("PASSED")

print("TEST: invalid answers are dropped with a reason - invented categories, unsent descriptions, unsure, duplicates")
answer = {"categories": [
    {"description": "malabar", "category": "Groceries", "confidence": "high"},           # not our category
    {"description": "Secret shop", "category": "Food", "confidence": "high"},            # never sent
    {"description": "Nesto", "category": "", "confidence": "low"},                       # unsure
    {"description": "Aster", "category": "Health", "confidence": "certain"},             # bad confidence -> low
    {"description": "Aster", "category": "Bills", "confidence": "high"},                 # repeated
    "not an object"]}
with mock.patch.object(ai_rules, "_call_groq", fake_groq(answer)):
    result = ai.suggest_categories(["malabar", "Nesto", "Aster"], CATEGORIES, "groq")
assert result["assignments"] == {"Aster": ("Health", "low")}
reasons = {item["description"]: item["reason"] for item in result["dropped"]}
assert "not one of your categories" in reasons["malabar"] and "were sent" in reasons["Secret shop"]
assert reasons["Nesto"] == "the AI was unsure" and reasons["Aster"] == "answered twice"
print("PASSED")

print("TEST: unreadable or wrongly shaped answers raise a readable error")
for bad in ("not json at all", json.dumps({"rules": []}), json.dumps([1, 2])):
    with mock.patch.object(ai_rules, "_call_groq", fake_groq(bad)):
        try:
            ai.suggest_categories(["x"], CATEGORIES, "groq")
            raise AssertionError("should fail")
        except ai_rules.AISuggestionError as error:
            assert str(error)
fenced = "```json\n" + json.dumps({"categories": []}) + "\n```"
with mock.patch.object(ai_rules, "_call_groq", fake_groq(fenced)):
    assert ai.suggest_categories(["x"], CATEGORIES, "groq")["assignments"] == {}
print("PASSED")

print("TEST: provider errors (like a missing key) surface as AISuggestionError, not a crash")
with mock.patch.dict("os.environ", {}, clear=True):
    try:
        ai.suggest_categories(["x"], CATEGORIES, "groq")
        raise AssertionError("should fail")
    except ai_rules.AISuggestionError as error:
        assert "GROQ_API_KEY" in str(error)
print("PASSED")
