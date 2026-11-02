"""
datarules/suggest.py
---------------------
Looks at a CLEAN sample file and suggests, for every column, the rules a normal month's file should also obey - each with the evidence it
rests on ("0 blanks in 240 rows"). The user ticks and edits them afterwards; nothing here is final.

  required        no blank cells in the sample
  allowed_values  a text column with only a few different values, repeated (status, region ...)
  number_range    a number column: the sample's lowest and highest value, widened by a 10% margin
  date_range      a date column: the sample's span in days (+25%), so next month's dates still fit
  valid_email     most values look like email addresses
  valid_phone     a phone column (header or values)
  unique          an id column where every value is different
  column_present  the sample's columns must exist (renamed ones are matched by name similarity)
"""

import math
import re
from datetime import date, datetime

import pandas as pd

from budget.importer import parse_amount
from dedupe.normalize import norm_phone
from locales import DEFAULT_LOCALE, LOCALES
from quality import DEFAULT_PLACEHOLDERS, has_identifier_name, has_phone_name, is_blank, is_phone_like, normalize_placeholders
from reconcile import columns as rc
from datarules.model import Rule, RuleSet

MARGIN = 0.10                       # number ranges are widened by this share of the sample's range
SPAN_MARGIN = 0.25                  # date spans likewise
ALLOWED_MAX_DISTINCT = 12
ALLOWED_MAX_SHARE = 0.2             # the distinct values must be at most this share of the rows (they repeat)
MIN_ROWS_FOR_VALUE_RULES = 8
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PLACEHOLDERS = normalize_placeholders(DEFAULT_PLACEHOLDERS)


def blank(value) -> bool:
    """Empty, or text that means 'no value' (N/A, NULL, -, ...)."""
    return is_blank(value) or (isinstance(value, str) and value.strip().upper() in PLACEHOLDERS)


def as_number(value):
    """A number from a cell (1200, '1,200', 'Rs. 1,200', '(45)'), or None. Dates and booleans are not numbers."""
    if isinstance(value, (datetime, date, pd.Timestamp)) or isinstance(value, bool):
        return None
    return parse_amount(value)


def as_text(value) -> str:
    """A cell as the text compared against allowed values: whole numbers lose their '.0', spaces are trimmed."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _nice(value: float, integer: bool, up: bool) -> float:
    if integer:
        return float(math.ceil(value) if up else math.floor(value))
    return round(value, 2)


def number_bounds(low: float, high: float, integer: bool) -> tuple:
    """The sample's lowest and highest value widened by the margin (never below zero when the sample had no negatives)."""
    spread = high - low
    margin = MARGIN * spread if spread > 0 else MARGIN * max(abs(high), 1.0)
    lo, hi = low - margin, high + margin
    if low >= 0:
        lo = max(lo, 0.0)
    lo, hi = _nice(lo, integer, up=False), _nice(hi, integer, up=True)
    return (int(lo) if float(lo).is_integer() else lo), (int(hi) if float(hi).is_integer() else hi)


def column_kind(series: pd.Series, name, region: str) -> str:
    """'email' | 'phone' | 'date' | 'number' | 'text' | 'empty' - judged from the header AND the values."""
    values = [v for v in series if not blank(v)]
    if not values:
        return "empty"
    if sum(isinstance(v, str) and EMAIL.match(v.strip()) is not None for v in values) / len(values) >= 0.8:
        return "email"
    if has_phone_name(name) or (series.dtype == object and is_phone_like("", series)):
        return "phone"
    day_first = LOCALES.get(region, {}).get("day_first", True)
    if rc._date_share(values[:500], day_first, False) >= 0.8:
        return "date"
    if sum(as_number(v) is not None for v in values) / len(values) >= 0.9 and not (has_identifier_name(name) and not _is_plain_number_column(values)):
        return "number"
    return "text"


def _is_plain_number_column(values) -> bool:
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values[:50])


def suggest(table: pd.DataFrame, source: str = "", region: str = DEFAULT_LOCALE, name: str = "") -> RuleSet:
    """The suggested RuleSet for a clean sample table (as made by reconcile.columns.table_from_raw)."""
    region = region if region in LOCALES else DEFAULT_LOCALE
    rules = [Rule("columns:column_present", "", "column_present", {"columns": [str(c) for c in table.columns]}, True,
                  f"the sample has {len(table.columns)} columns; the same columns should come every month")]
    n = len(table)
    for column in table.columns:
        series = table[column]
        values = [v for v in series if not blank(v)]
        kind = column_kind(series, column, region)
        if kind == "empty" or not values:
            continue
        filled = len(values)

        def add(rule_type, params, why, enabled=True):
            rules.append(Rule(f"{column}:{rule_type}", str(column), rule_type, params, enabled, why))

        if filled == n:
            add("required", {}, f"no blank cells in the {n:,} rows of the sample")
        if kind == "text" and filled >= 0.9 * n:                       # an optional free-text column (often blank) is not a list of allowed values
            texts = [as_text(v) for v in values]
            distinct = sorted(set(texts), key=lambda t: (-texts.count(t), t))
            if 2 <= len(distinct) <= ALLOWED_MAX_DISTINCT and len(distinct) <= ALLOWED_MAX_SHARE * filled and filled >= MIN_ROWS_FOR_VALUE_RULES \
                    and not has_identifier_name(column):
                add("allowed_values", {"values": sorted(distinct, key=str.casefold)}, f"only {len(distinct)} different values in {filled:,} rows: {', '.join(sorted(distinct, key=str.casefold)[:6])}"
                    + (" ..." if len(distinct) > 6 else ""))
        if kind == "number":
            numbers = [as_number(v) for v in values]
            if not has_identifier_name(column):                         # an id's range means nothing
                low, high = min(numbers), max(numbers)
                integer = all(float(x).is_integer() for x in numbers)
                lo, hi = number_bounds(low, high, integer)
                add("number_range", {"min": lo, "max": hi}, f"the sample went from {low:g} to {high:g}; {lo:g} .. {hi:g} leaves a {MARGIN:.0%} margin")
        if kind == "date":
            day_first = rc.date_order(series, LOCALES[region]["day_first"])[0]
            dates = [rc.parse_date(v, day_first, True) for v in values]
            dates = [d for d in dates if d]
            if dates:
                span = (max(dates) - min(dates)).days
                allowed = int(math.ceil(span * (1 + SPAN_MARGIN))) + 3
                add("date_range", {"mode": "span", "max_span_days": allowed},
                    f"the sample's dates run {span} days ({min(dates):%d %b %Y} to {max(dates):%d %b %Y}); up to {allowed} days is allowed, so next month's dates fit "
                    "(change it to a fixed range if you prefer)")
        if kind == "email":
            add("valid_email", {}, f"{filled:,} values, all written like name@domain.com")
        if kind == "phone":
            good = sum(1 for v in values if norm_phone(v, region)[0])
            if good / filled >= 0.8:
                add("valid_phone", {}, f"{good:,} of {filled:,} values are phone numbers for {LOCALES[region]['label']}")
        if filled and (has_identifier_name(column) and not has_phone_name(column)) and kind in ("text", "number") and len({as_text(v).casefold() for v in values}) == filled \
                and filled >= 5:
            add("unique", {}, f"all {filled:,} values are different: an id")
    stamp = f"{date.today():%Y-%m-%d}"
    return RuleSet(name or (f"Checks for {source}" if source else "My monthly checks"), source, stamp, region, [str(c) for c in table.columns], rules)
