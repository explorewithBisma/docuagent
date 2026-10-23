from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"
"""Offline tests for provider selection, the shared validation pipeline,
privacy parity and Groq/Anthropic error handling. Fake clients only - no
network and no real keys."""
import json
import os
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(ROOT))

import groq
import httpx
import pandas as pd
import ai_rules
import rule_engine

print("=" * 70)
print("TEST: constants")
print("=" * 70)
assert ai_rules.MODEL_NAME == "claude-sonnet-5-5"
assert ai_rules.GROQ_MODEL_NAME == "openai/gpt-oss-120b"
assert ai_rules.GROQ_MAX_COMPLETION_TOKENS >= 16000
print("PASSED")

print("=" * 70)
print("TEST: model_for - defaults and GROQ_MODEL / ANTHROPIC_MODEL overrides")
print("=" * 70)
assert ai_rules.model_for("groq", {}) == "openai/gpt-oss-120b"
assert ai_rules.model_for("anthropic", {}) == "claude-sonnet-5-5"
assert ai_rules.model_for("groq", {"GROQ_MODEL": "some/new-model"}) == "some/new-model"
assert ai_rules.model_for("groq", {"GROQ_MODEL": "  some/new-model  "}) == "some/new-model"
assert ai_rules.model_for("groq", {"GROQ_MODEL": "   "}) == "openai/gpt-oss-120b"        # blank = unset
assert ai_rules.model_for("anthropic", {"ANTHROPIC_MODEL": "claude-future"}) == "claude-future"
assert ai_rules.model_for("anthropic", {"GROQ_MODEL": "x"}) == "claude-sonnet-5-5"       # variables don't cross over
assert ai_rules.model_for("groq", {"ANTHROPIC_MODEL": "x"}) == "openai/gpt-oss-120b"
with mock.patch.dict(os.environ, {"GROQ_MODEL": "env/model"}):
    assert ai_rules.model_for("groq") == "env/model"                                    # reads os.environ by default
print("PASSED")

print("=" * 70)
print("TEST: select_provider")
print("=" * 70)
sel = ai_rules.select_provider
assert sel({}) == "anthropic"                                              # nothing set
assert sel({"GROQ_API_KEY": "gsk_x"}) == "groq"                            # Groq key only
assert sel({"GROQ_API_KEY": "   "}) == "anthropic"                         # blank key ignored
assert sel({"ANTHROPIC_API_KEY": "a", "GROQ_API_KEY": "g"}) == "groq"      # both keys, no AI_PROVIDER -> Groq
assert sel({"AI_PROVIDER": "anthropic", "GROQ_API_KEY": "g"}) == "anthropic"  # explicit wins
assert sel({"AI_PROVIDER": "groq"}) == "groq"                              # explicit, even without key
assert sel({"AI_PROVIDER": " GROQ "}) == "groq"                            # case/space tolerant
assert sel({"AI_PROVIDER": "", "GROQ_API_KEY": "g"}) == "groq"             # empty = unset
try:
    sel({"AI_PROVIDER": "openai"})
    raise AssertionError("expected AISuggestionError")
except ai_rules.AISuggestionError as error:
    print("invalid AI_PROVIDER ->", error)
with mock.patch.dict(os.environ, {"AI_PROVIDER": "groq"}, clear=False):
    assert sel() == "groq"                                                 # reads os.environ by default
assert ai_rules.model_for("groq", {}) == "openai/gpt-oss-120b"
assert ai_rules.model_for("anthropic", {}) == "claude-sonnet-5-5"
print("PASSED")

# ---------------------------------------------------------------------------
# A response mixing valid and invalid rules, to prove the shared filter works
# ---------------------------------------------------------------------------
GOOD_PHONE = {"id": "rule_1", "column": "Phone", "rule_type": "normalize_phone",
              "params": {"country": "PK"}, "description": "Normalize phones.", "confidence": "high"}
GOOD_CASE = {"id": "rule_1", "column": "Status", "rule_type": "unify_casing",   # duplicate id on purpose
             "params": {"style": "title"}, "description": "Title-case status.", "confidence": "medium"}
BAD_COLUMN = {"id": "rule_3", "column": "Nope", "rule_type": "unify_casing",
              "params": {"style": "title"}, "description": "Bad column.", "confidence": "high"}
BAD_TYPE = {"id": "rule_4", "column": "Phone", "rule_type": "delete_everything",
            "params": {}, "description": "Bad type.", "confidence": "high"}
BAD_COUNTRY = {"id": "rule_5", "column": "Phone", "rule_type": "normalize_phone",
               "params": {"country": "ZZ"}, "description": "Bad country.", "confidence": "high"}
BAD_STYLE = {"id": "rule_6", "column": "Status", "rule_type": "unify_casing",
             "params": {"style": "shouting"}, "description": "Bad style.", "confidence": "high"}
BAD_PLACES = {"id": "rule_7", "column": "Amount", "rule_type": "strip_currency_to_number",
              "params": {"decimal_places": "two"}, "description": "Bad places.", "confidence": "high"}
MISSING = {"id": "rule_8", "column": "Phone"}
RESPONSE = json.dumps({"rules": [GOOD_PHONE, GOOD_CASE, BAD_COLUMN, BAD_TYPE, BAD_COUNTRY,
                                 BAD_STYLE, BAD_PLACES, MISSING, "not even a dict"]})

df = pd.DataFrame({
    "Phone": [f"0300-{1000000 + i}" for i in range(20)],
    "Status": ["active", "ACTIVE", "Active", "inactive"] * 5,
    "Amount": ["Rs. 1,500/-", "$20"] * 10,
})
profiles = ai_rules.build_column_profile(df, mask=True)

print("=" * 70)
print("TEST: both providers run the same parsing + validation; only valid rules come out")
print("=" * 70)
prompts_seen = {}

def fake_groq(prompt):
    prompts_seen["groq"] = prompt
    return RESPONSE

def fake_anthropic(prompt):
    prompts_seen["anthropic"] = prompt
    return RESPONSE

results = {}
with mock.patch.object(ai_rules, "_call_groq", fake_groq), mock.patch.object(ai_rules, "_call_anthropic", fake_anthropic):
    for provider in ("groq", "anthropic"):
        results[provider] = ai_rules.suggest_rules(profiles, "PK", provider)

for provider, result in results.items():
    print(provider, "->", [r["id"] for r in result["rules"]], "| dropped:", len(result["dropped"]))
    assert result["provider"] == provider
    assert result["model"] == ai_rules.model_for(provider, {})
    assert [r["column"] for r in result["rules"]] == ["Phone", "Status"]
    ids = [r["id"] for r in result["rules"]]
    assert len(set(ids)) == len(ids), f"duplicate ids survived: {ids}"        # duplicate id was repaired
    assert all(r["approved"] is False for r in result["rules"])
    assert len(result["dropped"]) == 7, result["dropped"]
    for rule in result["rules"]:                                              # everything that survives is valid
        assert rule_engine.validate_rule(rule, ["Phone", "Status", "Amount"]) == ""
assert results["groq"]["rules"] == results["anthropic"]["rules"]
reasons = " | ".join(d["reason"] for d in results["groq"]["dropped"])
print("drop reasons:", reasons)
print("PASSED")

print("=" * 70)
print("TEST: privacy parity - identical prompt, only profile data, masked, <= 8 samples")
print("=" * 70)
assert prompts_seen["groq"] == prompts_seen["anthropic"]
prompt = prompts_seen["groq"]
assert "0300-1000000" not in prompt and "Rs. 1,500/-" not in prompt          # real values never sent when masked
assert "9999-9999999" in prompt and "Xx. 9,999/-" in prompt                  # masked shapes are
for profile in profiles:
    base_keys = {"column", "dtype", "non_null_count", "null_count"}
    if "distinct_values" in profile:                                      # few distinct values: the complete list, with counts
        assert set(profile) == base_keys | {"distinct_values"}
        assert sum(count for _, count in profile["distinct_values"]) == profile["non_null_count"]
        assert len(profile["distinct_values"]) <= ai_rules.MAX_DISTINCT_VALUES == 30
    else:                                                                 # otherwise: up to 8 random samples
        assert set(profile) == base_keys | {"sample_values"}
        assert len(profile["sample_values"]) <= ai_rules.SAMPLE_SIZE == 8
masked_lists = [p for p in profiles if "distinct_values" in p]
assert {p["column"] for p in masked_lists} == {"Status", "Amount"} and "Phone" in {p["column"] for p in profiles if "sample_values" in p}
assert all(not any(v in ("active", "Active", "ACTIVE", "inactive", "$20") for v, _ in p["distinct_values"]) for p in masked_lists)   # masked lists hold shapes only
print("PASSED")

print("=" * 70)
print("TEST: unusable responses become a clear error, not a crash")
print("=" * 70)
for bad_text in ["this is not json", '{"nothing": 1}', '{"rules": "oops"}', "", '{"rules": [{"id": "r1"']:
    with mock.patch.object(ai_rules, "_call_groq", lambda p, t=bad_text: t):
        try:
            ai_rules.suggest_rules(profiles, "PK", "groq")
            raise AssertionError("expected AISuggestionError")
        except ai_rules.AISuggestionError as error:
            assert "Groq" in str(error)
print("PASSED")

print("=" * 70)
print("TEST: Groq - missing key")
print("=" * 70)
with mock.patch.dict(os.environ, {}, clear=True):
    try:
        ai_rules._call_groq("hi")
        raise AssertionError("expected AISuggestionError")
    except ai_rules.AISuggestionError as error:
        print(error)
        assert "GROQ_API_KEY" in str(error)
print("PASSED")


def make_groq_error(cls, status, headers=None):
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(status, headers=headers or {}, request=request)
    return cls("boom", response=response, body=None)


class FakeGroq:
    """Stands in for groq.Groq; records the request and returns/raises as told."""
    def __init__(self, outcome):
        self.outcome = outcome
        self.kwargs = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.outcome))])


print("=" * 70)
print("TEST: Groq - request shape (model, JSON mode, same prompt)")
print("=" * 70)
fake = FakeGroq(RESPONSE)
with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_test"}, clear=True), \
        mock.patch.object(groq, "Groq", lambda api_key: fake):
    text = ai_rules._call_groq("THE PROMPT")
assert text == RESPONSE
assert fake.kwargs["model"] == "openai/gpt-oss-120b"
assert fake.kwargs["response_format"] == {"type": "json_object"}
assert fake.kwargs["messages"][-1] == {"role": "user", "content": "THE PROMPT"}
assert fake.kwargs["max_completion_tokens"] >= 16000 and "max_tokens" not in fake.kwargs
assert fake.kwargs["reasoning_effort"] == "low" and fake.kwargs["include_reasoning"] is False
print("PASSED")

print("=" * 70)
print("TEST: GROQ_MODEL override is used, and reasoning options only go to gpt-oss models")
print("=" * 70)
fake2 = FakeGroq(RESPONSE)
with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_test", "GROQ_MODEL": "meta/some-llama"}, clear=True), \
        mock.patch.object(groq, "Groq", lambda api_key: fake2):
    ai_rules._call_groq("P")
assert fake2.kwargs["model"] == "meta/some-llama"
assert "reasoning_effort" not in fake2.kwargs and "include_reasoning" not in fake2.kwargs
assert fake2.kwargs["max_completion_tokens"] >= 16000
fake3 = FakeGroq(RESPONSE)
with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_test", "GROQ_MODEL": "openai/gpt-oss-20b"}, clear=True), \
        mock.patch.object(groq, "Groq", lambda api_key: fake3):
    ai_rules._call_groq("P")
assert fake3.kwargs["model"] == "openai/gpt-oss-20b" and fake3.kwargs["reasoning_effort"] == "low"
print("PASSED")

print("=" * 70)
print("TEST: only the final answer is parsed - reasoning text is ignored")
print("=" * 70)
class Msg:
    def __init__(self, content, reasoning=None):
        self.content, self.reasoning = content, reasoning
def groq_returning(message, finish="stop"):
    client = FakeGroq(None)
    client._create = None
    def create(**kwargs):
        client.kwargs = kwargs
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)])
    client.chat = SimpleNamespace(completions=SimpleNamespace(create=create))
    return client
bait = '{"rules": [{"id": "evil", "column": "Phone"}]} and then some thinking {not json}'
with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_test"}, clear=True):
    with mock.patch.object(groq, "Groq", lambda api_key: groq_returning(Msg(RESPONSE, reasoning=bait))):
        assert ai_rules._call_groq("P") == RESPONSE                      # reasoning field never used
    wrapped = "<think>let me think {\"rules\": []}</think>\n```json\n" + RESPONSE + "\n```"
    with mock.patch.object(groq, "Groq", lambda api_key: groq_returning(Msg(wrapped))):
        assert ai_rules._call_groq("P") == RESPONSE                      # inline <think> + code fence stripped
    with mock.patch.object(groq, "Groq", lambda api_key: groq_returning(Msg(None, reasoning=bait))):
        assert ai_rules._call_groq("P") == ""                            # empty content is NOT replaced by reasoning
print("PASSED")

print("=" * 70)
print("TEST: cut-off answer (finish_reason=length) gives a clear message")
print("=" * 70)
with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_test"}, clear=True), \
        mock.patch.object(groq, "Groq", lambda api_key: groq_returning(Msg('{"rules": [{"id":'), finish="length")):
    try:
        ai_rules._call_groq("P")
        raise AssertionError("expected AISuggestionError")
    except ai_rules.AISuggestionError as error:
        print(error)
        assert "cut off" in str(error)
print("PASSED")

print("=" * 70)
print("TEST: model_not_found (404) -> friendly message naming GROQ_MODEL, not the raw error")
print("=" * 70)
raw_404 = ("Error code: 404 - {'error': {'message': 'The model `llama-3.3-70b-versatile` does not exist or you do "
           "not have access to it.', 'type': 'invalid_request_error', 'code': 'model_not_found'}}")
request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
not_found = groq.NotFoundError(raw_404, response=httpx.Response(404, request=request), body=None)
with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_test", "GROQ_MODEL": "old/retired-model"}, clear=True), \
        mock.patch.object(groq, "Groq", lambda api_key: FakeGroq(not_found)):
    try:
        ai_rules._call_groq("P")
        raise AssertionError("expected AISuggestionError")
    except ai_rules.AISuggestionError as error:
        message = str(error)
        print(message)
        assert "old/retired-model" in message and "retired" in message and "GROQ_MODEL" in message
        assert "Error code: 404" not in message and "invalid_request_error" not in message
# a non-404 status carrying model_not_found is also recognised
forbidden = groq.PermissionDeniedError("model_not_found: no access", response=httpx.Response(403, request=request), body=None)
with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_test"}, clear=True), mock.patch.object(groq, "Groq", lambda api_key: FakeGroq(forbidden)):
    try:
        ai_rules._call_groq("P")
        raise AssertionError("expected AISuggestionError")
    except ai_rules.AISuggestionError as error:
        assert "GROQ_MODEL" in str(error) and "openai/gpt-oss-120b" in str(error)
print("PASSED")

print("=" * 70)
print("TEST: reasoning options rejected with 400 -> retried once without them")
print("=" * 70)
calls = []
class PickyGroq:
    def __init__(self):
        def create(**kwargs):
            calls.append(dict(kwargs))
            if "include_reasoning" in kwargs:
                raise groq.BadRequestError("include_reasoning is not supported with json mode",
                                           response=httpx.Response(400, request=request), body=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=Msg(RESPONSE), finish_reason="stop")])
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))
with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_test"}, clear=True), mock.patch.object(groq, "Groq", lambda api_key: PickyGroq()):
    assert ai_rules._call_groq("P") == RESPONSE
assert len(calls) == 2 and "include_reasoning" in calls[0] and "include_reasoning" not in calls[1]
print("PASSED")

print("=" * 70)
print("TEST: Groq - rate limit, auth, connection and server errors give clear messages")
print("=" * 70)
cases = [
    (make_groq_error(groq.RateLimitError, 429, {"retry-after": "30"}), ["rate limit", "30 seconds"]),
    (make_groq_error(groq.RateLimitError, 429), ["rate limit", "try again"]),
    (make_groq_error(groq.AuthenticationError, 401), ["rejected", "GROQ_API_KEY"]),
    (groq.APIConnectionError(request=httpx.Request("POST", "https://api.groq.com")), ["Could not reach Groq"]),
    (make_groq_error(groq.InternalServerError, 500), ["Groq returned an error", "500"]),
]
for error, expected_bits in cases:
    with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk_test"}, clear=True), \
            mock.patch.object(groq, "Groq", lambda api_key, e=error: FakeGroq(e)):
        try:
            ai_rules._call_groq("hi")
            raise AssertionError("expected AISuggestionError")
        except ai_rules.AISuggestionError as caught:
            message = str(caught)
            print(f"{type(error).__name__}: {message}")
            for bit in expected_bits:
                assert bit.lower() in message.lower(), (bit, message)
print("PASSED")

print("=" * 70)
print("TEST: Anthropic path still works (request shape + response read) and maps errors")
print("=" * 70)
captured = {}

class FakeAnthropicClient:
    def __init__(self, outcome):
        self.outcome = outcome
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        captured.update(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.outcome)])

with mock.patch.object(ai_rules.anthropic, "Anthropic", lambda: FakeAnthropicClient(RESPONSE)):
    assert ai_rules._call_anthropic("P") == RESPONSE
assert captured["model"] == "claude-sonnet-5-5"
assert captured["output_config"]["format"]["type"] == "json_schema"

request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
rate = ai_rules.anthropic.RateLimitError("x", response=httpx.Response(429, request=request), body=None)
auth = ai_rules.anthropic.AuthenticationError("x", response=httpx.Response(401, request=request), body=None)
notfound = ai_rules.anthropic.NotFoundError("model: claude-x", response=httpx.Response(404, request=request), body=None)
with mock.patch.object(ai_rules.anthropic, "Anthropic", lambda: FakeAnthropicClient(RESPONSE)), \
        mock.patch.dict(os.environ, {"ANTHROPIC_MODEL": "claude-override"}):
    ai_rules._call_anthropic("P")
assert captured["model"] == "claude-override"                       # ANTHROPIC_MODEL override reaches the API call
for error, bit in [(rate, "rate limit"), (auth, "rejected"), (notfound, "ANTHROPIC_MODEL"),
                   (TypeError("Could not resolve authentication method"), "ANTHROPIC_API_KEY")]:
    with mock.patch.object(ai_rules.anthropic, "Anthropic", lambda e=error: FakeAnthropicClient(e)):
        try:
            ai_rules._call_anthropic("P")
            raise AssertionError("expected AISuggestionError")
        except ai_rules.AISuggestionError as caught:
            print(f"{type(error).__name__}: {caught}")
            assert bit.lower() in str(caught).lower()
print("PASSED")

print("=" * 70)
print("TEST: validate_rule param value checks")
print("=" * 70)
cols = ["Phone"]
base = {"id": "r", "column": "Phone", "description": "d"}
assert rule_engine.validate_rule({**base, "rule_type": "normalize_phone", "params": {"country": "AE"}}, cols) == ""
assert rule_engine.validate_rule({**base, "rule_type": "normalize_phone", "params": {"country": "XX"}}, cols) != ""
assert rule_engine.validate_rule({**base, "rule_type": "unify_casing", "params": {"style": "upper"}}, cols) == ""
assert rule_engine.validate_rule({**base, "rule_type": "unify_casing", "params": {"style": "x"}}, cols) != ""
assert rule_engine.validate_rule({**base, "rule_type": "parse_date", "params": {"output_format": "%Y-%m-%d", "day_first": True}}, cols) == ""
assert rule_engine.validate_rule({**base, "rule_type": "parse_date", "params": {"output_format": "%Y-%m-%d", "day_first": "yes"}}, cols) != ""
assert rule_engine.validate_rule({**base, "rule_type": "parse_date", "params": {"output_format": ""}}, cols) != ""
assert rule_engine.validate_rule({**base, "rule_type": "strip_currency_to_number", "params": {"decimal_places": 2}}, cols) == ""
assert rule_engine.validate_rule({**base, "rule_type": "strip_currency_to_number", "params": {}}, cols) == ""
assert rule_engine.validate_rule({**base, "rule_type": "strip_currency_to_number", "params": {"decimal_places": 99}}, cols) != ""
assert rule_engine.validate_rule({**base, "rule_type": "strip_currency_to_number", "params": {"decimal_places": True}}, cols) != ""
print("PASSED")

print()
print("ALL PROVIDER TESTS PASSED (no network calls made)")
