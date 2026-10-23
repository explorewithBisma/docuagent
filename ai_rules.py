"""
ai_rules.py
------------
Talks to an AI provider (Anthropic or Groq) to get AI-suggested cleaning
rules. This is the "AI plans" half of the project: the AI only ever sees
column names and a handful of (optionally masked) sample values - never the
full dataset, never a full column, and never another column's data. That is
true for both providers: they receive the exact same prompt.

Whatever the provider, its answer goes through the same parsing and
rule_engine.validate_rule() checks, so only valid rules ever reach the UI.
Nothing is executed until the user approves it in the review screen.

API keys are read from environment variables (ANTHROPIC_API_KEY or
GROQ_API_KEY) - never typed into the page itself. Which provider is used:
  1. AI_PROVIDER=groq|anthropic, if set
  2. otherwise Groq if GROQ_API_KEY is set
  3. otherwise Anthropic
"""

import json
import os
import re
import pandas as pd
import anthropic

from rule_engine import RULE_HANDLERS, validate_rule
from locales import LOCALES
from quality import (
    DEFAULT_PLACEHOLDERS, columns_with_inconsistent_casing, normalize_placeholders, real_values,
)

# The only two places a model is named - change them here.
# Default models. Either can be overridden without touching code by setting
# the ANTHROPIC_MODEL / GROQ_MODEL environment variable (see model_for), so a
# future model retirement only needs a config change.
MODEL_NAME = "claude-sonnet-5-5"        # Anthropic
GROQ_MODEL_NAME = "openai/gpt-oss-120b"  # Groq (a reasoning model; replaced the retired llama-3.3-70b-versatile)

# gpt-oss "thinks" before answering and that thinking can count toward the
# completion budget, so leave plenty of room for thinking + the JSON answer.
GROQ_MAX_COMPLETION_TOKENS = 16000

SAMPLE_SIZE = 8  # how many sample values per column get sent to the AI

PROVIDERS = ("groq", "anthropic")
PROVIDER_LABELS = {"groq": "Groq", "anthropic": "Anthropic"}


class AISuggestionError(Exception):
    """Something went wrong getting suggestions, with a message written for
    the user (missing key, rate limit, unusable response...). The UI shows
    it as-is instead of crashing."""


def select_provider(env=None) -> str:
    """Decide which provider to use: AI_PROVIDER if set, otherwise Groq when
    GROQ_API_KEY is set, otherwise Anthropic. `env` defaults to os.environ
    and exists so this can be tested without touching real variables."""
    env = os.environ if env is None else env

    chosen = (env.get("AI_PROVIDER") or "").strip().lower()
    if chosen:
        if chosen not in PROVIDERS:
            raise AISuggestionError(
                f"AI_PROVIDER is set to '{chosen}', but it must be 'groq' or 'anthropic'."
            )
        return chosen

    if (env.get("GROQ_API_KEY") or "").strip():
        return "groq"
    return "anthropic"


def model_for(provider: str, env=None) -> str:
    """The model used for a provider: the GROQ_MODEL / ANTHROPIC_MODEL
    environment variable if set, otherwise the default constant above."""
    env = os.environ if env is None else env
    variable, default = ("GROQ_MODEL", GROQ_MODEL_NAME) if provider == "groq" else ("ANTHROPIC_MODEL", MODEL_NAME)
    return (env.get(variable) or "").strip() or default


def _model_not_found_message(provider: str, model: str) -> str:
    """Friendly explanation for a 'model does not exist' API error."""
    variable = "GROQ_MODEL" if provider == "groq" else "ANTHROPIC_MODEL"
    models_page = "https://console.groq.com/docs/models" if provider == "groq" else "https://docs.anthropic.com/en/docs/about-claude/models"
    return (
        f"{PROVIDER_LABELS[provider]} says the model '{model}' wasn't found or isn't available to your "
        f"account - it may have been retired. Pick a current model from {models_page} and set the "
        f"{variable} environment variable to its name (e.g. in PowerShell: $env:{variable} = \"<model-name>\"), "
        "then restart the app."
    )


def _is_model_not_found(error) -> bool:
    """True if an API error means 'that model doesn't exist'."""
    text = str(error).lower()
    return getattr(error, "status_code", None) == 404 or "model_not_found" in text or "does not exist" in text


def mask_value(value) -> str:
    """Format- and case-preserving mask: digits become '9', uppercase letters
    become 'X', lowercase letters become 'x', and everything else (spaces,
    punctuation, currency symbols) is left untouched. This keeps the *shape*
    of a value visible to the AI - it still looks like a phone number, a date,
    or a price, and casing inconsistencies ("Active" vs "ACTIVE") stay
    visible - without ever sending the real content.
    Examples: "0300-1234567" -> "9999-9999999", "Active" -> "Xxxxxx"."""
    text = value if isinstance(value, str) else str(value)
    masked_characters = []

    for character in text:
        if character.isdigit():
            masked_characters.append("9")
        elif character.isalpha():
            masked_characters.append("X" if character.isupper() else "x")
        else:
            masked_characters.append(character)  # spaces, punctuation, symbols stay as-is

    return "".join(masked_characters)


def build_column_profile(df: pd.DataFrame, mask: bool, placeholders=DEFAULT_PLACEHOLDERS) -> list:
    """Build the small, privacy-safe description of each column that gets
    sent to the AI: its name, data type, how many values are missing, and a
    handful of sample values (optionally masked). This is the *entire*
    privacy boundary - nothing else about the data ever leaves the machine:
    no full columns, no row counts beyond non-null/null, no other columns.

    Placeholder text such as "ERROR" or "UNKNOWN" is junk, not data: it is
    never used as a sample, and it counts as missing, so the AI judges a
    column by its real values only."""
    placeholder_set = normalize_placeholders(placeholders)
    profiles = []

    for column in df.columns:
        series = df[column]
        real = real_values(series, placeholder_set)  # not blank, not a placeholder
        sample_count = min(SAMPLE_SIZE, len(real))

        if sample_count == 0:
            samples = []
        else:
            # A fixed random_state keeps the sample (and therefore the AI's
            # suggestions) reproducible for the same file.
            samples = real.sample(n=sample_count, random_state=42).tolist()

        if mask:
            samples = [mask_value(value) for value in samples]
        else:
            samples = [str(value) for value in samples]

        profiles.append({
            "column": str(column),
            "dtype": str(series.dtype),
            "non_null_count": int(len(real)),
            "null_count": int(len(series) - len(real)),
            "sample_values": samples,
        })

    return profiles


# ---------------------------------------------------------------------------
# THE JSON SHAPE WE ASK THE API TO FOLLOW
# (Passed as output_config.format, so the API is constrained to return
# exactly this shape - no prose, no markdown fences to strip out.)
# ---------------------------------------------------------------------------

RULE_TYPES = list(RULE_HANDLERS.keys())  # the AI may only pick from these 4

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "column": {"type": "string"},
                    "rule_type": {"type": "string", "enum": RULE_TYPES},
                    "params": {"type": "object"},
                    "description": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["id", "column", "rule_type", "params", "description", "confidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["rules"],
    "additionalProperties": False,
}


def build_prompt(column_profiles: list, locale_code: str) -> str:
    """Build the text prompt describing the data and asking for rule
    suggestions. Only ever includes the small column profiles, never the
    underlying file."""
    locale = LOCALES[locale_code]
    profiles_json = json.dumps(column_profiles, indent=2)

    return f"""You are helping clean a spreadsheet. Based ONLY on the column
profiles below (names, data types, and a few sample values), suggest
cleaning rules for columns that would benefit from one.

The samples contain real values only: blanks and placeholders such as ERROR or
UNKNOWN have already been removed, so never build a rule around them.

Only suggest a rule when the sample values clearly show the kind of problem
it would fix - never suggest a rule "just in case". It is completely fine to
suggest rules for only some columns, or none at all.

Default locale: {locale['label']} ({locale_code}). Use this as your default
assumption for phone region, currency symbol, and whether dates are usually
day-first - but override it for a specific column if its sample values
clearly show a different country or format.

You may ONLY use these 4 rule types, with these exact params:
- "normalize_phone": params = {{"country": "<one of {list(LOCALES.keys())}>"}}
- "unify_casing": params = {{"style": "title" | "upper" | "lower"}}
  Only suggest this for a column whose sample values show the SAME word in
  different casings (e.g. "active", "Active", "ACTIVE"). If the sample
  values are already written consistently - even if not in Title Case, e.g.
  "In-store", "Cash", or codes like "USD" - do NOT suggest it.
- "parse_date": params = {{"output_format": "<strftime format, e.g. '%Y-%m-%d'>",
  "input_format": "<optional strptime hint>", "day_first": <true/false>}}
- "strip_currency_to_number": params = {{"currency_symbol": "<e.g. '$'>",
  "decimal_places": <integer, usually 2>}}

Give each rule a short, unique "id" (e.g. "rule_1"), the exact "column" name
from the profiles below, and a one-sentence human-readable "description" of
what it will do. Set "confidence" to "high", "medium", or "low".

Respond with ONLY a JSON object of exactly this shape, and nothing else:
{{"rules": [{{"id": "rule_1", "column": "<column name>", "rule_type": "<one of the 4>",
"params": {{...}}, "description": "<one sentence>", "confidence": "high"}}]}}
If no rule is warranted, respond with {{"rules": []}}.

Column profiles:
{profiles_json}
"""


def parse_rules_response(response_text: str) -> list:
    """Pull the list of candidate rules out of a provider's JSON response
    text. Raises ValueError if it isn't JSON of the expected shape. Kept
    separate from the API calls so it can be tested without any network call."""
    try:
        parsed = json.loads(response_text)
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError(f"not valid JSON: {error}")

    if not isinstance(parsed, dict) or not isinstance(parsed.get("rules"), list):
        raise ValueError("JSON is missing a 'rules' list")
    return parsed["rules"]


def filter_valid_rules(raw_rules: list, available_columns: list, inconsistent_casing_columns=None):
    """The single gate every provider's answer passes through. Keeps only
    rules that pass rule_engine.validate_rule() and returns (valid_rules,
    dropped), where `dropped` lists {"description", "reason"} for every
    rejected suggestion so the UI can say what was discarded and why.

    `inconsistent_casing_columns` (optional) is the set of columns whose real
    values really do contain the same word in different casings. When given,
    a unify_casing rule for any other column is discarded: re-casing values
    that are already consistent can only damage them."""
    valid_rules = []
    dropped = []
    used_ids = set()

    for raw in raw_rules:
        error = validate_rule(raw, available_columns)
        if (not error and inconsistent_casing_columns is not None and raw["rule_type"] == "unify_casing"
                and raw["column"] not in inconsistent_casing_columns):
            error = "The real values in this column already have consistent casing."
        if error:
            description = raw.get("description") if isinstance(raw, dict) else None
            dropped.append({"description": str(description or "(unreadable suggestion)"), "reason": error})
            continue

        # Rebuild the rule from known fields only, so stray extras never travel on.
        rule_id = str(raw["id"]).strip()
        if not rule_id or rule_id in used_ids:
            rule_id = f"rule_{len(valid_rules) + 1}"
            while rule_id in used_ids:
                rule_id += "_"
        used_ids.add(rule_id)

        confidence = raw.get("confidence")
        valid_rules.append({
            "id": rule_id,
            "column": raw["column"],
            "rule_type": raw["rule_type"],
            "params": raw["params"],
            "description": str(raw["description"]).strip() or f"{raw['rule_type']} on {raw['column']}",
            "confidence": confidence if confidence in ("high", "medium", "low") else "n/a",
            "approved": False,
        })

    return valid_rules, dropped


# ---------------------------------------------------------------------------
# THE TWO PROVIDER CALLS
# (Each takes the same prompt text and returns the raw response text. All
# provider-specific errors become AISuggestionError with a clear message.)
# ---------------------------------------------------------------------------

def _call_anthropic(prompt: str) -> str:
    """Ask Claude, constrained to RESPONSE_SCHEMA via structured outputs."""
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    model = model_for("anthropic")

    try:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            output_config={"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError:
        raise AISuggestionError("Anthropic rejected the API key. Check that ANTHROPIC_API_KEY is correct.")
    except anthropic.NotFoundError as error:
        if _is_model_not_found(error):
            raise AISuggestionError(_model_not_found_message("anthropic", model))
        raise AISuggestionError(f"Anthropic returned an error (404): {error}")
    except anthropic.RateLimitError:
        raise AISuggestionError("Anthropic's rate limit was reached. Wait a minute and try again.")
    except anthropic.APIConnectionError:
        raise AISuggestionError("Could not reach Anthropic. Check your internet connection and try again.")
    except TypeError as error:
        if "authentication" in str(error).lower():
            raise AISuggestionError(
                "No Anthropic API key found. Set the ANTHROPIC_API_KEY environment variable "
                "(or set GROQ_API_KEY to use Groq instead)."
            )
        raise

    return next((block.text for block in response.content if block.type == "text"), "")


def _call_groq(prompt: str) -> str:
    """Ask a Groq-hosted model in JSON mode. JSON mode guarantees valid JSON
    but not the right shape, which is why the answer is then run through the
    same filter_valid_rules() check as Anthropic's."""
    api_key = (os.environ.get("GROQ_API_KEY") or "").strip()
    if not api_key:
        raise AISuggestionError(
            "No Groq API key found. Set the GROQ_API_KEY environment variable "
            "(or set AI_PROVIDER=anthropic to use Anthropic instead)."
        )

    try:
        import groq  # imported here so the app still runs without it when only Anthropic is used
    except ImportError:
        raise AISuggestionError("The 'groq' package is not installed. Run: pip install -r requirements.txt")

    client = groq.Groq(api_key=api_key)
    model = model_for("groq")

    request = dict(
        model=model,
        temperature=0,
        max_completion_tokens=GROQ_MAX_COMPLETION_TOKENS,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": "You are a JSON API. Respond with a single valid JSON object and nothing else."},
            {"role": "user", "content": prompt},
        ],
    )
    # gpt-oss models reason before answering: keep that short and don't return
    # the reasoning text. Other models don't take these options, so only send
    # them to gpt-oss.
    reasoning_options = {"reasoning_effort": "low", "include_reasoning": False} if model.startswith("openai/gpt-oss") else {}

    try:
        try:
            response = client.chat.completions.create(**request, **reasoning_options)
        except groq.BadRequestError as error:
            # If the API rejects the reasoning options, retry once without them.
            if reasoning_options and "reasoning" in str(error).lower():
                response = client.chat.completions.create(**request)
            else:
                raise
    except groq.RateLimitError as error:
        retry_after = error.response.headers.get("retry-after") if getattr(error, "response", None) is not None else None
        wait = f" Try again in about {retry_after} seconds." if retry_after else " Wait a minute and try again."
        raise AISuggestionError(f"Groq's rate limit was reached.{wait}")
    except groq.AuthenticationError:
        raise AISuggestionError("Groq rejected the API key. Check that GROQ_API_KEY is correct.")
    except groq.APIConnectionError:
        raise AISuggestionError("Could not reach Groq. Check your internet connection and try again.")
    except groq.APIStatusError as error:
        if _is_model_not_found(error):
            raise AISuggestionError(_model_not_found_message("groq", model))
        raise AISuggestionError(f"Groq returned an error ({error.status_code}): {error.message}")

    choice = response.choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        raise AISuggestionError(
            "Groq's answer was cut off before it finished (a reasoning model can use up its token "
            "budget thinking). Try again, or set GROQ_MODEL to a non-reasoning model."
        )

    # Only the final answer (message.content) is used - never message.reasoning.
    return _final_answer_text(choice.message.content)


def _final_answer_text(content) -> str:
    """Clean a model's final answer before JSON parsing: drop any inline
    <think>...</think> reasoning some models include, and unwrap a ```json fence."""
    text = re.sub(r"<think>.*?</think>", "", content or "", flags=re.DOTALL).strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    return fenced.group(1) if fenced else text


def suggest_rules(column_profiles: list, locale_code: str, provider: str = None,
                  inconsistent_casing_columns=None) -> dict:
    """Get cleaning-rule suggestions from the chosen provider and return
    {"rules": [...valid rules...], "dropped": [...], "provider": ..., "model": ...}.
    Raises AISuggestionError (with a user-readable message) on any problem -
    the UI shows it instead of crashing."""
    provider = provider or select_provider()
    prompt = build_prompt(column_profiles, locale_code)  # identical for both providers

    response_text = _call_groq(prompt) if provider == "groq" else _call_anthropic(prompt)

    try:
        raw_rules = parse_rules_response(response_text)
    except ValueError as error:
        raise AISuggestionError(
            f"{PROVIDER_LABELS[provider]} returned a response that couldn't be used ({error}). "
            "Try again - if it keeps happening, the response may have been cut off."
        )

    rules, dropped = filter_valid_rules(
        raw_rules, [profile["column"] for profile in column_profiles], inconsistent_casing_columns
    )
    return {"rules": rules, "dropped": dropped, "provider": provider, "model": model_for(provider)}
