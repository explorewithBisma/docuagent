"""
quality.py
-----------
Shared helpers for judging what a column actually contains, plus the
per-column data-quality profile shown "before" and "after" cleaning.

Pure pandas/Python - no Streamlit. The same helpers are used by the cleaning
steps (cleaning.py), the relationship detection (derive.py), the XLSX writer
(io_utils.py) and the AI profiling (ai_rules.py), so they all agree on what
counts as a placeholder, a number, or a phone number.
"""

import re

import numpy as np
import pandas as pd

# Text that means "no real value here". Matched against the whole cell,
# ignoring case and surrounding spaces. Editable in the UI.
DEFAULT_PLACEHOLDERS = ("ERROR", "UNKNOWN", "N/A", "NA", "#N/A", "NULL", "NONE", "NAN", "-", "--", "?")

# A text column becomes numeric when at least this share of its real values
# parses as a number.
NUMERIC_SHARE = 0.9

_PHONE_WORDS = {"phone", "mobile", "cell", "tel", "telephone", "contact", "whatsapp", "fax"}
_IDENTIFIER_WORDS = _PHONE_WORDS | {
    "id", "ids", "code", "zip", "postal", "postcode", "pin", "account", "acct", "sku",
    "ref", "reference", "number", "no", "cnic", "ssn", "iban", "barcode", "isbn",
}

_PHONE_VALUE = re.compile(r"^\+?[\d\s\-().]+$")
_PLAIN_NUMBER = re.compile(r"^-?(\d+(\.\d*)?|\.\d+)$")
_THOUSANDS_NUMBER = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")


# ---------------------------------------------------------------------------
# PLACEHOLDERS AND BLANKS
# ---------------------------------------------------------------------------

def normalize_placeholders(items) -> frozenset:
    """Turn a list of placeholder texts into a set for fast, case-insensitive
    matching ("error", " ERROR " and "Error" all become "ERROR")."""
    return frozenset(str(item).strip().upper() for item in items if str(item).strip())


def parse_placeholder_text(text: str) -> list:
    """Read the placeholder list typed into the UI: one per line and/or
    comma-separated. Blank entries are ignored."""
    parts = re.split(r"[\n,]", text or "")
    return [part.strip() for part in parts if part.strip()]


def is_blank(value) -> bool:
    """True for a missing cell: NaN/None/NA or text that is only spaces."""
    if isinstance(value, str):
        return value.strip() == ""
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def is_placeholder(value, placeholder_set) -> bool:
    """True if a cell is placeholder text such as 'ERROR' or 'unknown'."""
    return isinstance(value, str) and value.strip().upper() in placeholder_set


def real_values(series: pd.Series, placeholder_set=frozenset()) -> pd.Series:
    """The cells that hold an actual value: not blank and not a placeholder."""
    keep = [not is_blank(value) and not is_placeholder(value, placeholder_set) for value in series]
    return series[np.array(keep, dtype=bool)] if len(series) else series


# ---------------------------------------------------------------------------
# NUMBERS
# ---------------------------------------------------------------------------

def parse_number(value):
    """Return the number a value represents, or None if it isn't a plain
    number. Deliberately strict so identifiers are never turned into numbers:
    leading zeros ("007", "0300...") and 10+ digit strings are rejected, as is
    anything with a currency symbol, a plus sign or other text."""
    if isinstance(value, (bool, np.bool_)):
        return None
    if isinstance(value, (int, float, np.integer, np.floating)):
        return None if pd.isna(value) else float(value)
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    if _THOUSANDS_NUMBER.match(text):
        return float(text.replace(",", ""))

    if _PLAIN_NUMBER.match(text):
        digits = text.lstrip("-")
        whole = digits.split(".")[0]
        if len(whole) > 1 and whole.startswith("0"):
            return None  # leading zero: looks like a code or phone number
        if "." not in digits and len(whole) >= 10:
            return None  # very long digit string: looks like an identifier
        return float(text)

    return None


def _name_words(name) -> set:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(name))  # camelCase -> camel Case
    return set(re.findall(r"[a-z0-9]+", spaced.lower()))


def has_phone_name(name) -> bool:
    return bool(_name_words(name) & _PHONE_WORDS)


def has_identifier_name(name) -> bool:
    """Column names like 'Customer ID', 'ZIP', 'Phone Number', 'Account No'."""
    return bool(_name_words(name) & _IDENTIFIER_WORDS)


def is_phone_like(name, series: pd.Series) -> bool:
    """True if a column holds phone numbers: its name says so, or at least 80%
    of its text values look like one (9-15 digits written with a leading + or
    0, or with spaces/dashes/brackets)."""
    if has_phone_name(name):
        return True

    values = [v.strip() for v in series.dropna().head(500) if isinstance(v, str) and v.strip()]
    if not values:
        return False

    hits = 0
    for text in values:
        digits = sum(character.isdigit() for character in text)
        separated = text.startswith(("+", "0")) or any(character in text for character in " -()")
        if _PHONE_VALUE.match(text) and 9 <= digits <= 15 and separated:
            hits += 1
    return hits / len(values) >= 0.8


# ---------------------------------------------------------------------------
# CASING
# ---------------------------------------------------------------------------

def casing_variants(series: pd.Series, placeholders=DEFAULT_PLACEHOLDERS) -> dict:
    """Find words written in more than one casing among a column's REAL text
    values (blanks and placeholders are ignored). Returns
    {lowercase form: sorted list of spellings} for every word that has 2+
    spellings, e.g. {"active": ["ACTIVE", "Active", "active"]}. An empty dict
    means the column's casing is already consistent."""
    placeholder_set = normalize_placeholders(placeholders)
    spellings = {}
    for value in series:
        if isinstance(value, str) and not is_blank(value) and not is_placeholder(value, placeholder_set):
            spellings.setdefault(value.casefold(), set()).add(value)
    return {key: sorted(found) for key, found in spellings.items() if len(found) > 1}


def casefolded_values(series: pd.Series, placeholders=DEFAULT_PLACEHOLDERS) -> set:
    """The distinct REAL text values of a column, trimmed and lower-cased - what a
    value-mapping rule's keys are matched against (blanks and placeholders are not values)."""
    placeholder_set = normalize_placeholders(placeholders)
    return {
        value.strip().casefold() for value in series.dropna().unique()
        if isinstance(value, str) and value.strip() and not is_placeholder(value, placeholder_set)
    }


def columns_with_inconsistent_casing(df: pd.DataFrame, placeholders=DEFAULT_PLACEHOLDERS) -> set:
    """Names of the columns whose real values contain the same word in
    different casings. Only these are worth a 'unify casing' rule."""
    return {column for column in df.columns if casing_variants(df[column], placeholders)}


# ---------------------------------------------------------------------------
# COLUMN TYPE + QUALITY PROFILE
# ---------------------------------------------------------------------------

def detect_column_type(series: pd.Series, name, placeholder_set=frozenset()) -> str:
    """'numeric', 'text' or 'empty' (no real values at all)."""
    if pd.api.types.is_bool_dtype(series):
        return "text"
    if pd.api.types.is_numeric_dtype(series):
        return "numeric" if series.notna().any() else "empty"

    values = real_values(series, placeholder_set)
    if len(values) == 0:
        return "empty"
    if has_identifier_name(name) or is_phone_like(name, series):
        return "text"

    parsed = sum(parse_number(value) is not None for value in values)
    return "numeric" if parsed / len(values) >= NUMERIC_SHARE else "text"


def profile_dataframe(df: pd.DataFrame, placeholders=DEFAULT_PLACEHOLDERS) -> pd.DataFrame:
    """One row per column: detected type, blank cells, placeholder cells,
    invalid cells (values in a numeric column that aren't numbers) and the
    number of distinct real values. Used to show data quality before and
    after cleaning."""
    placeholder_set = normalize_placeholders(placeholders)
    rows = []

    for column in df.columns:
        series = df[column]
        blank = sum(is_blank(value) for value in series)
        placeholder = sum(is_placeholder(value, placeholder_set) for value in series)
        values = real_values(series, placeholder_set)
        column_type = detect_column_type(series, column, placeholder_set)

        invalid = 0
        if column_type == "numeric" and not pd.api.types.is_numeric_dtype(series):
            invalid = sum(parse_number(value) is None for value in values)

        rows.append({
            "Column": str(column),
            "Type": column_type,
            "Blank": int(blank),
            "Placeholders": int(placeholder),
            "Invalid": int(invalid),
            "Unique": int(values.nunique()) if len(values) else 0,
        })

    return pd.DataFrame(rows, columns=["Column", "Type", "Blank", "Placeholders", "Invalid", "Unique"])
