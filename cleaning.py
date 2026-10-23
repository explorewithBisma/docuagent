"""
cleaning.py
------------
The 4 deterministic, always-available cleaning steps. Plain Python/pandas —
no Streamlit code in here, so this logic can be tested and reused on its own.
"""

import numpy as np   # used to represent "missing" values (NaN)
import pandas as pd


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


def clean_dataframe_with_steps(df: pd.DataFrame, steps: dict):
    """Run only the cleaning steps the user left turned on, in a fixed,
    sensible order. 'steps' is a dict like {"headers": True, "trim": True, ...}.

    For every step, we record what it did in `stats`. A value of None means
    "this step was skipped", so the report can tell 'skipped' apart from
    'ran but found nothing to change' (which would be 0)."""
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
        df, trimmed_count = trim_whitespace(df)
        stats["cells_trimmed"] = trimmed_count

    if steps["duplicates"]:
        df, duplicates_removed = remove_duplicate_rows(df)
        stats["duplicates_removed"] = duplicates_removed

    if steps["empty"]:
        df, rows_removed, columns_removed = remove_empty_rows_and_columns(df)
        stats["empty_rows_removed"] = rows_removed
        stats["empty_columns_removed"] = columns_removed

    return df, stats
