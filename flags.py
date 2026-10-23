"""
flags.py
---------
Deterministic warnings about implausible numbers. Nothing here ever changes or
removes a value - it only says "this one looks wrong, take a look". Four checks
on numeric columns:

1. NEGATIVE   - a negative value in a column that should not be negative
                (age, quantity, days, price, cost, amount ...).
2. SENTINEL   - a rare whole number such as 999 / 9999 / -999, which is usually a
                "missing" code typed into the data.
3. AGE        - an age below 10 or above 100.
4. OUTLIER    - a value far outside the usual range: more than 3x the IQR
                beyond the quartiles. Skipped for a column when more than 5% of
                its values would be flagged, because that means the column is
                naturally wide-ranging (prices across product types, for
                example) rather than containing a few odd values.

Pure pandas - no Streamlit.
"""

import numpy as np
import pandas as pd

from changes import FLAGGED
from quality import (
    DEFAULT_PLACEHOLDERS, _name_words, detect_column_type, has_identifier_name, is_blank, is_placeholder,
    normalize_placeholders, parse_number,
)

NON_NEGATIVE_WORDS = {
    "age", "ages", "quantity", "qty", "units", "count", "stock", "days", "day", "hours", "duration", "distance",
    "weight", "height", "price", "cost", "amount", "total", "sales", "fee", "salary", "discount", "shipping",
}
SENTINELS = (999, 9999, 99999, 999999, -999, -9999, -99999)
AGE_MIN, AGE_MAX = 10, 100
IQR_FACTOR = 3.0
MIN_VALUES_FOR_IQR = 30         # fewer values than this and "usual range" means little
MAX_OUTLIER_SHARE = 0.05        # more than this share outside the range = a naturally wide column
MAX_SENTINEL_SHARE = 0.02       # a "code" that makes up more than 2% of a column is a real value, not a code


def _fmt(value) -> str:
    """A number as a person would write it: 12.0 -> 12, 3.14159 -> 3.14."""
    value = float(value)
    return str(int(value)) if value == int(value) else f"{value:.2f}".rstrip("0").rstrip(".")


def numeric_view(df: pd.DataFrame, placeholders=DEFAULT_PLACEHOLDERS) -> pd.DataFrame:
    """Only the numeric columns of `df`, as floats. Text columns that hold numbers
    (a raw CSV load) are parsed; placeholders and blanks become NaN. ID / code /
    phone columns are left out - their digits are labels, not quantities."""
    placeholder_set = normalize_placeholders(placeholders)
    columns = {}
    for column in df.columns:
        series = df[column]
        if has_identifier_name(column) or pd.api.types.is_bool_dtype(series):
            continue
        if pd.api.types.is_numeric_dtype(series):
            columns[column] = series.astype("float64").reset_index(drop=True)
        elif series.dtype == object and detect_column_type(series, column, placeholder_set) == "numeric":
            columns[column] = pd.Series(
                [np.nan if (is_blank(v) or is_placeholder(v, placeholder_set)) else (parse_number(v) if parse_number(v) is not None else np.nan)
                 for v in series], dtype="float64")
    return pd.DataFrame(columns)


def _column_flags(name, series: pd.Series):
    """({position: [reasons]}, share_outside_range or None-if-not-checked, wide_range_skipped: bool)."""
    reasons = {}
    values = series.dropna()
    if values.empty:
        return reasons, None, False
    words = _name_words(name)

    def add(position, text):
        reasons.setdefault(int(position), []).append(text)

    if words & NON_NEGATIVE_WORDS:
        for position in np.flatnonzero((series < 0).to_numpy()):
            value = series.iloc[position]
            note = " (-1 is often used as a placeholder code)" if value == -1 else ""
            add(position, f"{name} {_fmt(value)} is negative{note}")

    sentinel = series.isin(SENTINELS)
    if sentinel.any() and sentinel.sum() / len(values) <= MAX_SENTINEL_SHARE:
        for position in np.flatnonzero(sentinel.to_numpy()):
            add(position, f"{_fmt(series.iloc[position])} looks like a placeholder code")

    if words & {"age", "ages"}:
        for position in np.flatnonzero(((series < AGE_MIN) | (series > AGE_MAX)).to_numpy()):
            add(position, f"age {_fmt(series.iloc[position])} looks invalid (expected {AGE_MIN}-{AGE_MAX})")

    share = None
    skipped = False
    if len(values) >= MIN_VALUES_FOR_IQR:
        q1, q3 = values.quantile(0.25), values.quantile(0.75)
        iqr = q3 - q1
        if iqr > 0:
            low, high = q1 - IQR_FACTOR * iqr, q3 + IQR_FACTOR * iqr
            outside = ((series < low) | (series > high)).fillna(False)
            share = float(outside.sum() / len(values))
            if share > MAX_OUTLIER_SHARE:
                skipped = True
            else:
                for position in np.flatnonzero(outside.to_numpy()):
                    add(position, f"{_fmt(series.iloc[position])} is far outside the usual range ({_fmt(low)} to {_fmt(high)})")
    return reasons, share, skipped


def find_flags(df: pd.DataFrame, placeholders=DEFAULT_PLACEHOLDERS) -> dict:
    """Look for implausible values. Returns {"flags": [...], "skipped_statistical": {column: share}} where each
    flag is {"position", "index", "row", "column", "value", "reasons"} (row = Excel row of the cleaned sheet,
    header = row 1). The data itself is never touched."""
    view = numeric_view(df, placeholders)
    flags, skipped_statistical = [], {}
    for column in view.columns:
        reasons, share, skipped = _column_flags(column, view[column])
        if skipped:
            skipped_statistical[column] = share
        for position, texts in reasons.items():
            flags.append({
                "position": position, "index": df.index[position], "row": position + 2, "column": column,
                "value": df[column].iloc[position], "reasons": texts,
            })
    order = {column: number for number, column in enumerate(df.columns)}
    flags.sort(key=lambda flag: (flag["position"], order[flag["column"]]))
    return {"flags": flags, "skipped_statistical": skipped_statistical}


def flag_counts(result: dict) -> dict:
    """{column: number of flagged cells}."""
    counts = {}
    for flag in result["flags"]:
        counts[flag["column"]] = counts.get(flag["column"], 0) + 1
    return counts


def flag_records(result: dict) -> list:
    """The flags as change-log-style records (reason 'flagged', old value == new value), so they can be
    listed in the Change Log and drawn on the sheet alongside real changes."""
    return [{
        "index": flag["index"], "position": flag["position"], "row": flag["row"], "column": flag["column"],
        "old": flag["value"], "new": flag["value"], "reason": FLAGGED, "detail": "; ".join(flag["reasons"]),
    } for flag in result["flags"]]


def merge_flag_records(records: list, result: dict, columns: list) -> list:
    """Change records plus flag records in sheet order (a cell's change comes before its flag)."""
    order = {column: number for number, column in enumerate(columns)}
    return sorted(list(records) + flag_records(result), key=lambda record: (record["position"], order[record["column"]]))
