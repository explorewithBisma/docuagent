"""
rule_checks.py
---------------
Local, deterministic safety checks on suggested rules - no AI involved.

The AI only sees a few samples, so it can get details wrong or miss things the
whole column makes obvious. These checks look at the real column on this
machine and:
  1. correct a parse_date rule whose day-first / month-first setting contradicts
     the dates in the data (or say so when the data cannot decide),
  2. add a unify_casing suggestion for a category column whose values differ only
     by capitalisation (delivered / Delivered), when the AI missed it,
  3. build a before -> after preview of any rule, so the user can judge it
     before approving.

Rules stay plain dicts in the usual format; the notes that explain what was
corrected or added are returned separately, so they never end up in a recipe.
"""

import copy
import re

import pandas as pd

from locales import LOCALES
from quality import casing_variants, normalize_placeholders, real_values
from rule_engine import apply_rule, validate_rule

# 12/05/2024, 12-05-24, 12.05.2024 - day and month first, year last (year-first dates are never ambiguous)
_NUMERIC_DATE = re.compile(r"^\s*(\d{1,2})([/\-.])(\d{1,2})\2(\d{2}|\d{4})\s*$")

PREVIEW_EXAMPLES = 3


# ---------------------------------------------------------------------------
# 1. DATE ORDER
# ---------------------------------------------------------------------------

def detect_date_order(series: pd.Series, placeholders=()) -> dict:
    """Decide from the data alone whether the numeric dates in a column are
    day-first or month-first. A value with a first part above 12 can only be
    day-first; a second part above 12 can only be month-first.
    Returns {"order": "day_first" | "month_first" | "ambiguous" | "mixed",
    "day_first_count", "month_first_count", "day_example", "month_example"}."""
    placeholder_set = normalize_placeholders(placeholders)
    day_count = month_count = 0
    day_example = month_example = None
    for value in real_values(series, placeholder_set):
        match = _NUMERIC_DATE.match(str(value))
        if not match:
            continue
        first, second = int(match.group(1)), int(match.group(3))
        if first > 12 and second <= 12:
            day_count += 1
            day_example = day_example or str(value).strip()
        elif second > 12 and first <= 12:
            month_count += 1
            month_example = month_example or str(value).strip()
    if day_count and not month_count:
        order = "day_first"
    elif month_count and not day_count:
        order = "month_first"
    elif day_count and month_count:
        order = "mixed"
    else:
        order = "ambiguous"
    return {"order": order, "day_first_count": day_count, "month_first_count": month_count,
            "day_example": day_example, "month_example": month_example}


def _swap_day_month(input_format: str) -> str:
    return input_format.replace("%d", "\0").replace("%m", "%d").replace("\0", "%m")


def _format_is_day_first(input_format: str):
    """True/False for a strptime format with both %d and %m, else None."""
    if "%d" in input_format and "%m" in input_format:
        return input_format.index("%d") < input_format.index("%m")
    return None


def check_date_rule(rule: dict, series: pd.Series, locale_code: str, placeholders=()):
    """Return (rule, note) for a parse_date rule: the rule corrected if the data
    proves its day_first / input_format wrong, and a short note saying what was
    checked. Other rule types come back unchanged with note None."""
    if rule.get("rule_type") != "parse_date":
        return rule, None
    rule = copy.deepcopy(rule)
    params = rule["params"]
    locale_day_first = LOCALES.get(locale_code, {}).get("day_first", False)
    found = detect_date_order(series, placeholders)
    order = found["order"]

    input_format = params.get("input_format")
    chosen_day_first = _format_is_day_first(input_format) if input_format else None
    if chosen_day_first is None:
        chosen_day_first = params.get("day_first", False)

    if order in ("day_first", "month_first"):
        data_day_first = order == "day_first"
        count = found["day_first_count"] if data_day_first else found["month_first_count"]
        example = found["day_example"] if data_day_first else found["month_example"]
        name = "day-first" if data_day_first else "month-first"
        if chosen_day_first == data_day_first:
            note = f"Checked against the data: {name} confirmed ({count} values like {example} can only be {name})."
        else:
            if input_format and _format_is_day_first(input_format) is not None:
                params["input_format"] = _swap_day_month(input_format)
            note = f"Corrected to {name}: {count} values like {example} can only be {name}."
        params["day_first"] = data_day_first
        return rule, {"note": note, "kind": "date_order"}

    label = "day-first" if locale_day_first else "month-first"
    country = LOCALES.get(locale_code, {}).get("label", locale_code)
    if order == "mixed":
        note = (f"The dates are inconsistent: {found['day_first_count']} can only be day-first and "
                f"{found['month_first_count']} can only be month-first. Using the {country} default ({label}); "
                "check the preview before approving.")
    else:
        note = f"The data cannot tell day-first from month-first, so the {country} default ({label}) is used."
    if not input_format:
        params["day_first"] = locale_day_first
    return rule, {"note": note, "kind": "date_order"}


# ---------------------------------------------------------------------------
# 2. LOCAL CASING DETECTION
# ---------------------------------------------------------------------------

def _best_casing_style(values) -> str:
    """The style most of the values already use (title on a tie)."""
    scores = {"title": 0, "upper": 0, "lower": 0}
    for value in values:
        scores["title"] += value == value.title()
        scores["upper"] += value == value.upper()
        scores["lower"] += value == value.lower()
    return max(("title", "upper", "lower"), key=lambda style: scores[style])


def local_casing_rules(df: pd.DataFrame, existing_rules: list, placeholders=()) -> list:
    """unify_casing rules for category columns whose values differ only by
    capitalisation, skipping any column an existing rule already standardizes
    (a map_values or unify_casing rule). Only short, repeated-category text
    columns qualify - never IDs, names, emails or phone numbers - because
    re-casing those would damage them."""
    from ai_rules import qualifies_for_value_list  # imported here: ai_rules imports rule_engine too

    covered = {rule["column"] for rule in existing_rules if rule.get("rule_type") in ("map_values", "unify_casing")}
    placeholder_set = normalize_placeholders(placeholders)
    used_ids = {rule["id"] for rule in existing_rules}
    new_rules = []
    for column in df.columns:
        if column in covered or df[column].dtype != object:
            continue
        real = real_values(df[column], placeholder_set)
        stripped = real.astype(str).str.strip()
        if not qualifies_for_value_list(column, df[column], real) or not casing_variants(stripped, ()):
            continue
        style = _best_casing_style(stripped.unique())
        change = {"title": str.title, "upper": str.upper, "lower": str.lower}[style]
        spellings = next(iter(casing_variants(stripped, ()).values()))
        before = next((s for s in spellings if change(s) != s), spellings[0])
        after = change(before)
        rule_id = f"local_casing_{len(new_rules) + 1}"
        while rule_id in used_ids:
            rule_id += "_"
        used_ids.add(rule_id)
        new_rules.append({
            "id": rule_id,
            "column": str(column),
            "rule_type": "unify_casing",
            "params": {"style": style},
            "description": f"Make capitalisation consistent in '{column}' ({style} case, e.g. \"{before}\" -> \"{after}\")",
            "confidence": "high",
            "approved": False,
        })
    return new_rules


# ---------------------------------------------------------------------------
# 3. BEFORE -> AFTER PREVIEW
# ---------------------------------------------------------------------------

def preview_rule(series: pd.Series, rule: dict, placeholders=()) -> dict:
    """Run a rule on a COPY of its column and report what would happen:
    up to 3 example conversions (before, after) plus the converted / already
    correct / failed counts for the whole column. Nothing is changed."""
    column = rule["column"]
    frame = pd.DataFrame({column: series.copy()})
    changed, stats = apply_rule(frame.copy(), rule, placeholders)
    examples = []
    seen = set()
    for before, after in zip(frame[column], changed[column]):
        if pd.isna(before) or before == after or str(before) in seen:
            continue
        seen.add(str(before))
        examples.append((before, after))
        if len(examples) == PREVIEW_EXAMPLES:
            break
    return {"examples": examples, **stats}


def preview_text(preview: dict) -> str:
    """One readable line, e.g. '25/12/2024 -> 2024-12-25 · 15/01/2024 -> 2024-01-15 | would convert 150, ...'"""
    examples = " · ".join(f"{before} → {after}" for before, after in preview["examples"]) or "no value would change"
    counts = f"would convert {preview['converted']}, already correct {preview.get('already_correct', 0)}"
    if "unmapped" in preview:
        counts += f", not in the map {preview['unmapped']}"
    return f"{examples}  |  {counts}, would fail {preview['failed']}"


# ---------------------------------------------------------------------------
# THE ENTRY POINT USED BY THE APP
# ---------------------------------------------------------------------------

def review_rules(df: pd.DataFrame, rules: list, locale_code: str, placeholders=()):
    """Run all local checks on one sheet's freshly suggested rules. Returns
    (rules, notes): the rules with date order corrected and any locally detected
    casing rules appended, and {rule_id: {"note": ..., "source": "local" | None}}."""
    notes = {}
    reviewed = []
    for rule in rules:
        corrected, info = check_date_rule(rule, df[rule["column"]], locale_code, placeholders)
        if info:
            notes[corrected["id"]] = {"note": info["note"], "source": None}
        reviewed.append(corrected)

    available = list(df.columns)
    known = {column: set() for column in available}
    for rule in local_casing_rules(df, reviewed, placeholders):
        if not validate_rule(rule, available, known):
            reviewed.append(rule)
            notes[rule["id"]] = {"note": "This column has the same word in different capitalisations (the AI did not suggest it).", "source": "local"}
    return reviewed, notes
