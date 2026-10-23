"""
rule_engine.py
---------------
The 4 AI-suggestable rule types and the plain pandas/phonenumbers code that
actually executes them. This is the "local code cleans" half of the project:
the AI (in ai_rules.py) only ever picks a rule_type + params from here - it
never runs anything itself. Every function below is pure pandas, fully
testable offline, with zero network dependency.

Each apply_* function takes (df, column, params) and returns (df, stats),
the same (data, stats) convention used throughout cleaning.py. `stats` is
always {"converted": N, "failed": M} - how many cells were successfully
changed, and how many looked like they should match but couldn't be parsed
(those are left untouched rather than guessed at).
"""

import re
import pandas as pd
import phonenumbers

from locales import LOCALES
from quality import normalize_placeholders


def _is_skipped(value, skip_values) -> bool:
    """True for placeholder text (e.g. 'ERROR') that rules must leave alone: it is
    neither converted nor counted as a failure. `skip_values` is a set of
    upper-case texts from quality.normalize_placeholders()."""
    return isinstance(value, str) and value.strip().upper() in skip_values

# ---------------------------------------------------------------------------
# RULE TYPE 1: normalize_phone
# ---------------------------------------------------------------------------

def apply_normalize_phone(df: pd.DataFrame, column: str, params: dict, skip_values=frozenset()):
    """Reformat phone numbers in `column` to the international E.164 format
    (e.g. "0300-1234567" -> "+923001234567"), using params['country'] as the
    default region. Numbers that can't be parsed are left exactly as-is."""
    country = params.get("country", "PK")
    converted = 0
    failed = 0

    def normalize_one(value):
        nonlocal converted, failed
        if not isinstance(value, str) or value.strip() == "" or _is_skipped(value, skip_values):
            return value
        try:
            parsed = phonenumbers.parse(value, country)
            if not phonenumbers.is_valid_number(parsed):
                failed += 1
                return value
        except phonenumbers.NumberParseException:
            failed += 1
            return value

        converted += 1
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)

    df[column] = df[column].apply(normalize_one)
    return df, {"converted": converted, "failed": failed}


# ---------------------------------------------------------------------------
# RULE TYPE 2: unify_casing
# ---------------------------------------------------------------------------

def apply_unify_casing(df: pd.DataFrame, column: str, params: dict, skip_values=frozenset()):
    """Make every real text value in `column` use the same capitalization style
    (e.g. "active"/"Active"/"ACTIVE" -> all "Active"). Missing cells and
    placeholder text (e.g. "ERROR") are left alone, and only cells that
    actually changed are counted as converted."""
    style = params.get("style", "title")
    style_functions = {"title": str.title, "upper": str.upper, "lower": str.lower}
    change_case = style_functions.get(style, str.title)
    converted = 0

    def unify_one(value):
        nonlocal converted
        if not isinstance(value, str) or value.strip() == "" or _is_skipped(value, skip_values):
            return value
        new_value = change_case(value)
        if new_value != value:
            converted += 1
        return new_value

    df[column] = df[column].apply(unify_one)
    return df, {"converted": converted, "failed": 0}


# ---------------------------------------------------------------------------
# RULE TYPE 3: parse_date
# ---------------------------------------------------------------------------

def apply_parse_date(df: pd.DataFrame, column: str, params: dict, skip_values=frozenset()):
    """Parse dates in `column` and rewrite them all in one consistent format.
    params['input_format'] is an optional strptime-style hint (e.g. "%d/%m/%Y");
    if left out, pandas guesses using params['day_first'] as a tie-breaker.
    params['output_format'] controls the final text (default: "%Y-%m-%d").
    Values that can't be parsed as dates are left exactly as-is."""
    input_format = params.get("input_format")
    output_format = params.get("output_format", "%Y-%m-%d")
    day_first = params.get("day_first", False)
    converted = 0
    failed = 0

    def parse_one(value):
        nonlocal converted, failed
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return value
        if isinstance(value, str) and (value.strip() == "" or _is_skipped(value, skip_values)):
            return value
        try:
            if input_format:
                parsed = pd.to_datetime(value, format=input_format)
            elif re.match(r"\s*\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", str(value)):
                # Year-first dates (2025-04-01) are unambiguous: never apply
                # the day-first tie-breaker, or April 1st becomes January 4th.
                parsed = pd.to_datetime(value, yearfirst=True, dayfirst=False)
            else:
                parsed = pd.to_datetime(value, dayfirst=day_first)
        except (ValueError, TypeError):
            failed += 1
            return value

        converted += 1
        return parsed.strftime(output_format)

    df[column] = df[column].apply(parse_one)
    return df, {"converted": converted, "failed": failed}


# ---------------------------------------------------------------------------
# RULE TYPE 4: strip_currency_to_number
# ---------------------------------------------------------------------------

def apply_strip_currency_to_number(df: pd.DataFrame, column: str, params: dict, skip_values=frozenset()):
    """Turn text like "$1,234.50" or "Rs 1234" into a plain number (1234.5),
    by taking the first number found in the text (commas removed).
    Values containing no number are treated as failures and left exactly
    as-is."""
    decimal_places = params.get("decimal_places", 2)
    converted = 0
    failed = 0

    def strip_one(value):
        nonlocal converted, failed
        if not isinstance(value, str) or value.strip() == "" or _is_skipped(value, skip_values):
            return value

        # Find the first number in the text, ignoring currency symbols and
        # trailing junk: "Rs. 1,500/-" -> "1,500", "$15.50" -> "15.50".
        match = re.search(r"-?\d[\d,]*(?:\.\d+)?", value)
        if match is None:
            failed += 1
            return value

        try:
            number = round(float(match.group().replace(",", "")), decimal_places)
        except ValueError:
            failed += 1
            return value

        converted += 1
        return number

    df[column] = df[column].apply(strip_one)
    return df, {"converted": converted, "failed": failed}


# ---------------------------------------------------------------------------
# THE RULE VOCABULARY, DISPATCHER, AND VALIDATION
# ---------------------------------------------------------------------------

# The AI must pick from exactly these 4 rule types - never invent its own.
RULE_HANDLERS = {
    "normalize_phone": apply_normalize_phone,
    "unify_casing": apply_unify_casing,
    "parse_date": apply_parse_date,
    "strip_currency_to_number": apply_strip_currency_to_number,
}

# What each rule_type's "params" is expected to look like: which keys are
# required vs. optional. ai_rules.py shows these same rule types to the AI,
# so there is exactly one source of truth for "what a valid rule looks like".
RULE_PARAM_SCHEMAS = {
    "normalize_phone": {"required": ["country"], "optional": []},
    "unify_casing": {"required": ["style"], "optional": []},
    "parse_date": {"required": ["output_format"], "optional": ["input_format", "day_first"]},
    "strip_currency_to_number": {"required": [], "optional": ["currency_symbol", "decimal_places"]},
}

REQUIRED_RULE_KEYS = {"id", "column", "rule_type", "params", "description"}


def validate_rule(rule: dict, available_columns: list) -> str:
    """Check that a rule (from the AI or a loaded recipe file) is safe to show
    and run. Returns an empty string if the rule is valid, otherwise a short
    explanation of what's wrong - callers should drop the rule and show this
    message instead of running it."""
    if not isinstance(rule, dict) or not REQUIRED_RULE_KEYS.issubset(rule.keys()):
        return "Rule is missing required fields."

    if rule["rule_type"] not in RULE_HANDLERS:
        return f"Unknown rule type '{rule['rule_type']}'."

    if rule["column"] not in available_columns:
        return f"Column '{rule['column']}' does not exist in this file."

    if not isinstance(rule["params"], dict):
        return "Rule 'params' must be an object."

    required_params = RULE_PARAM_SCHEMAS[rule["rule_type"]]["required"]
    missing = [name for name in required_params if name not in rule["params"]]
    if missing:
        return f"Rule is missing required params: {', '.join(missing)}."

    # Check the param *values* too - some providers (e.g. Groq's JSON mode)
    # only guarantee valid JSON, not that the values make sense.
    params = rule["params"]
    rule_type = rule["rule_type"]

    if rule_type == "normalize_phone" and params["country"] not in LOCALES:
        return f"Unsupported country '{params['country']}' (use one of {', '.join(LOCALES)})."

    if rule_type == "unify_casing" and params["style"] not in ("title", "upper", "lower"):
        return f"Unsupported casing style '{params['style']}'."

    if rule_type == "parse_date":
        if not isinstance(params["output_format"], str) or not params["output_format"]:
            return "parse_date needs a non-empty output_format."
        if "day_first" in params and not isinstance(params["day_first"], bool):
            return "parse_date day_first must be true or false."

    if rule_type == "strip_currency_to_number" and "decimal_places" in params:
        places = params["decimal_places"]
        if isinstance(places, bool) or not isinstance(places, int) or not 0 <= places <= 6:
            return "decimal_places must be a whole number from 0 to 6."

    return ""


def apply_rule(df: pd.DataFrame, rule: dict, skip_values=()):
    """Run one approved rule against `df` and return (df, stats).
    Assumes the rule has already been checked with validate_rule().
    `skip_values` lists placeholder texts (e.g. ["ERROR", "UNKNOWN"]) that the
    rule must leave untouched and not count."""
    handler = RULE_HANDLERS[rule["rule_type"]]
    return handler(df, rule["column"], rule["params"], normalize_placeholders(skip_values))
