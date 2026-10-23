"""
cleaning.py
------------
The 4 deterministic, always-available cleaning steps. Plain Python/pandas —
no Streamlit code in here, so this logic can be tested and reused on its own.
"""

import numpy as np   # used to represent "missing" values (NaN)
import pandas as pd

from changes import NUMERIC, PLACEHOLDER, TRIMMED
from derive import apply_fills

from quality import (
    DEFAULT_PLACEHOLDERS, NUMERIC_SHARE, has_identifier_name, is_blank, is_phone_like, is_placeholder,
    normalize_placeholders, parse_number, real_values,
)


def clean_column_headers(df: pd.DataFrame):
    """Strip extra spaces from column headers and collapse double spaces.
    Returns the updated DataFrame and a list of (old_name, new_name) pairs
    for every header that actually changed."""
    new_headers = []
    renames = []  # (old_name, new_name) pairs, only for headers that changed

    for position, header in enumerate(df.columns, start=1):
        original_header = str(header)
        header_text = original_header.strip()          # remove leading/trailing spaces
        header_text = " ".join(header_text.split())    # "First   Name" -> "First Name"

        if header_text == "" or header_text.startswith("Unnamed:"):
            header_text = f"Column_{position}"          # give blank headers a readable name

        if header_text != original_header:
            renames.append((original_header, header_text))

        new_headers.append(header_text)

    df.columns = new_headers
    return df, renames


def trim_whitespace(df: pd.DataFrame):
    """Strip extra leading/trailing spaces from every text cell.
    A cell that only contains spaces becomes a proper empty value (NaN).
    Returns the updated DataFrame and how many cells actually had spaces removed."""
    trimmed_count = 0

    for column in df.columns:
        if df[column].dtype == "object":  # "object" columns hold text in pandas
            original_values = df[column]
            stripped_values = original_values.apply(lambda cell: cell.strip() if isinstance(cell, str) else cell)

            # A cell only "counts" as trimmed if it was text AND stripping changed it
            was_text = original_values.apply(lambda cell: isinstance(cell, str))
            changed = was_text & (original_values != stripped_values)
            trimmed_count += int(changed.sum())

            df[column] = stripped_values.replace("", np.nan)  # empty string -> missing value

    return df, trimmed_count


def convert_placeholders(df: pd.DataFrame, placeholders=DEFAULT_PLACEHOLDERS):
    """Turn placeholder text such as "ERROR" or "unknown" into proper missing
    values. A cell only counts if the WHOLE cell is a placeholder (ignoring
    case and surrounding spaces), so "Error code 5" is left alone.
    Returns the updated DataFrame and {"total": N, "by_column": {column:
    {PLACEHOLDER: count}}} describing exactly what was converted."""
    placeholder_set = normalize_placeholders(placeholders)
    by_column = {}

    for column in df.columns:
        if df[column].dtype != "object":  # only text columns can hold placeholder text
            continue
        series = df[column]
        is_placeholder_cell = series.map(lambda value: is_placeholder(value, placeholder_set)).astype(bool)
        if is_placeholder_cell.any():
            counts = series[is_placeholder_cell].map(lambda value: value.strip().upper()).value_counts()
            by_column[column] = {name: int(count) for name, count in counts.items()}
            df[column] = series.where(~is_placeholder_cell, np.nan)

    total = sum(sum(counts.values()) for counts in by_column.values())
    return df, {"total": total, "by_column": by_column}


def convert_numeric_columns(df: pd.DataFrame, placeholders=DEFAULT_PLACEHOLDERS):
    """Turn text columns that are really numbers ("5", "3.0", "1,500") into
    real numeric columns. A column is converted when at least 90% of its real
    values parse as plain numbers. Values that don't parse are kept as text
    and reported as invalid (the column then stays mixed); placeholder text
    that is still present is left alone and not counted as invalid.

    Identifiers are never converted: columns named like an ID / code / zip /
    phone, columns of phone numbers, and values with leading zeros or 10+ digits
    (see quality.parse_number). Integer-valued columns become whole numbers.

    Returns the DataFrame and {"converted": [{"column", "kind", "invalid",
    "examples"}], "skipped": {column: reason}}."""
    placeholder_set = normalize_placeholders(placeholders)
    converted = []
    skipped = {}

    for column in df.columns:
        series = df[column]
        if series.dtype != "object":
            continue

        values = real_values(series, placeholder_set)
        if len(values) == 0:
            continue
        parsed_count = sum(parse_number(value) is not None for value in values)
        if parsed_count / len(values) < NUMERIC_SHARE:
            continue  # genuinely text

        if has_identifier_name(column):
            skipped[column] = "name looks like an ID, code or phone column"
            continue
        if is_phone_like(column, series):
            skipped[column] = "values look like phone numbers"
            continue

        cells = []  # the new value of every cell, in order
        whole_numbers = True
        has_leftovers = False  # text that stays text: unparseable values and surviving placeholders
        invalid = 0
        invalid_examples = []
        for value in series:
            if is_blank(value):
                cells.append(np.nan)
                continue
            number = parse_number(value)
            if number is not None:
                cells.append(number)
                whole_numbers = whole_numbers and number == int(number)
            else:
                cells.append(value)
                has_leftovers = True
                if not is_placeholder(value, placeholder_set):
                    invalid += 1
                    if len(invalid_examples) < 3:
                        invalid_examples.append(str(value))

        if has_leftovers:
            # Mixed column: real numbers where the text parsed, the original text elsewhere.
            mixed = [int(cell) if isinstance(cell, float) and cell == cell and cell == int(cell) else cell for cell in cells]
            df[column] = pd.Series(mixed, index=series.index, dtype="object")
            kind = "mixed"
        else:
            numeric = pd.Series(cells, index=series.index, dtype="float64")
            df[column] = numeric.astype("Int64") if whole_numbers else numeric
            kind = "integer" if whole_numbers else "decimal"

        converted.append({"column": column, "kind": kind, "invalid": invalid, "examples": invalid_examples})

    return df, {"converted": converted, "skipped": skipped}


def remove_duplicate_rows(df: pd.DataFrame):
    """Drop rows that are exact duplicates of an earlier row.
    Returns the updated DataFrame and how many duplicate rows were removed."""
    duplicate_count = int(df.duplicated().sum())
    return df.drop_duplicates(), duplicate_count


def remove_empty_rows_and_columns(df: pd.DataFrame):
    """Drop any row or column where every single cell is empty.
    Returns the updated DataFrame, rows removed, and columns removed."""
    rows_before = len(df)
    columns_before = len(df.columns)

    df = df.dropna(axis=0, how="all")  # drop rows that are entirely empty
    df = df.dropna(axis=1, how="all")  # drop columns that are entirely empty

    return df, rows_before - len(df), columns_before - len(df.columns)


def clean_dataframe_with_steps(df: pd.DataFrame, steps: dict, options: dict = None):
    """Run only the cleaning steps the user left turned on, in a fixed,
    sensible order: headers -> trim -> placeholders -> numbers -> duplicates -> empty -> fills.
    'steps' is a dict like {"headers": True, "trim": True, ...}; steps that
    aren't in the dict are not run. 'options' carries settings such as the
    placeholder list and the approved relationships to fill from:
    {"placeholders": ["ERROR", "UNKNOWN"], "relationships": [...from derive.detect_relationships...],
    "changes": a changes.ChangeLog that records every cell these steps change}.

    For every step, we record what it did in `stats`. A value of None means
    "this step was skipped", so the report can tell 'skipped' apart from
    'ran but found nothing to change' (which would be 0)."""
    options = options or {}
    log = options.get("changes")  # optional changes.ChangeLog collecting every changed cell
    stats = {
        "header_renames": [],
        "cells_trimmed": None,
        "duplicates_removed": None,
        "empty_rows_removed": None,
        "empty_columns_removed": None,
    }

    if steps["headers"]:
        df, renames = clean_column_headers(df)
        stats["header_renames"] = renames

    if steps["trim"]:
        before = df.copy() if log is not None else None
        df, trimmed_count = trim_whitespace(df)
        stats["cells_trimmed"] = trimmed_count
        if log is not None:
            log.record_diff(before, df, TRIMMED, strict=True)

    # Placeholders come before duplicates/empty-row removal so that rows which
    # only differed by a placeholder ("ERROR" vs blank) are recognised as the
    # same, and rows made only of placeholders count as empty.
    if "placeholders" in steps:
        stats["placeholders"] = None
        if steps["placeholders"]:
            before = df.copy() if log is not None else None
            df, stats["placeholders"] = convert_placeholders(df, options.get("placeholders", DEFAULT_PLACEHOLDERS))
            if log is not None:
                log.record_diff(before, df, PLACEHOLDER)

    # Numbers stored as text become real numbers (after placeholders are gone, so
    # "ERROR" can no longer block the conversion).
    if "numeric" in steps:
        stats["numeric"] = None
        if steps["numeric"]:
            before = df.copy() if log is not None else None
            df, stats["numeric"] = convert_numeric_columns(df, options.get("placeholders", DEFAULT_PLACEHOLDERS))
            if log is not None:
                # Only conversions that change what the cell shows ("1,500" -> 1500) are cell changes;
                # "5" -> 5 just changes the type, which the report already lists per column.
                log.record_diff(before, df, NUMERIC, detail="thousands separator removed", strict=True,
                                where=lambda old, new: isinstance(old, str) and "," in old)

    if steps["duplicates"]:
        df, duplicates_removed = remove_duplicate_rows(df)
        stats["duplicates_removed"] = duplicates_removed

    if steps["empty"]:
        df, rows_removed, columns_removed = remove_empty_rows_and_columns(df)
        stats["empty_rows_removed"] = rows_removed
        stats["empty_columns_removed"] = columns_removed

    # Fill gaps last, only from relationships the user approved, and only on
    # the rows that survived deduplication / empty-row removal.
    if "fills" in steps:
        stats["fills"] = None
        if steps["fills"]:
            df, stats["fills"] = apply_fills(df, options.get("relationships", []), log)

    return df, stats
