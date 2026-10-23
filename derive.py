"""
derive.py
----------
Fill a missing value ONLY when it can be worked out exactly from other
columns in the same row. Two kinds of relationship are detected:

1. "product" - one numeric column always equals two others multiplied
   (e.g. Total Spent = Quantity x Price Per Unit). This is arithmetic, so
   any one missing column can be calculated from the other two.
2. "lookup" - one column always determines another (e.g. every Item always
   has the same Price Per Unit). This is *inferred from the file itself*, not
   a law of arithmetic, so it is reported separately and is opt-in in the UI.

Detection is deliberately strict (100% agreement on enough rows), and filling
never overwrites an existing value. Pure pandas/numpy - no Streamlit.
"""

from itertools import combinations

import numpy as np
import pandas as pd

from changes import CALCULATED, LOOKUP
from quality import has_identifier_name

MIN_ROWS = 50           # rows that must support a relationship before it is believed
MIN_KEY_ROWS = 5        # a lookup key must appear at least this often to be trusted
MAX_COLUMNS = 12        # cap on columns examined, to keep detection fast
MAX_KEYS = 50           # a lookup key column may have at most this many distinct values
MAX_PASSES = 3          # fills can unlock each other; stop after this many rounds
DECIMALS = 6            # derived divisions must be exact to this many decimals


def _numeric_columns(df: pd.DataFrame) -> list:
    """Columns that are really numeric (not text, not bool, not ID-like)."""
    return [
        column for column in df.columns
        if pd.api.types.is_numeric_dtype(df[column]) and not pd.api.types.is_bool_dtype(df[column])
        and not has_identifier_name(column)
    ][:MAX_COLUMNS]


def _as_floats(series: pd.Series) -> np.ndarray:
    return pd.to_numeric(series, errors="coerce").astype("float64").to_numpy()


# ---------------------------------------------------------------------------
# DETECTION
# ---------------------------------------------------------------------------

def detect_products(df: pd.DataFrame, min_rows: int = MIN_ROWS) -> list:
    """Find 'A = B x C' relationships that hold on EVERY row where all three
    values are present (and on at least `min_rows` such rows). Columns that
    never change are ignored, so 'Total = Price x 1' style coincidences are
    not mistaken for a real relationship."""
    columns = _numeric_columns(df)
    arrays = {column: _as_floats(df[column]) for column in columns}
    found = []

    for target in columns:
        others = [column for column in columns if column != target]
        for first, second in combinations(others, 2):
            a, b, c = arrays[target], arrays[first], arrays[second]
            complete = ~(np.isnan(a) | np.isnan(b) | np.isnan(c))
            rows = int(complete.sum())
            if rows < min_rows:
                continue
            av, bv, cv = a[complete], b[complete], c[complete]
            if min(len(np.unique(av)), len(np.unique(bv)), len(np.unique(cv))) < 2:
                continue
            if np.allclose(av, bv * cv, rtol=1e-9, atol=1e-9):
                found.append({
                    "kind": "product",
                    "target": target,
                    "factors": [first, second],
                    "rows": rows,
                    "label": f"{target} = {first} x {second}  (holds on {rows} of {rows} rows)",
                })
                break  # one relationship per target is enough

    return found


def detect_lookups(df: pd.DataFrame, min_rows: int = MIN_ROWS) -> list:
    """Find 'column X always determines column Y' relationships, e.g. each Item
    always has the same Price. A key column must repeat (avg 5+ rows per
    value, at most 50 distinct values) so an ID column - unique on every row -
    can never 'determine' everything. Keys seen fewer than 5 times are left
    out of the lookup. Includes a reverse map (Y -> X) restricted to values of
    Y that belong to exactly one X."""
    columns = list(df.columns)[:MAX_COLUMNS]
    found = []

    for key_column in columns:
        for value_column in columns:
            if key_column == value_column:
                continue
            both = df[key_column].notna() & df[value_column].notna()
            rows = int(both.sum())
            if rows < min_rows:
                continue

            keys = df.loc[both, key_column]
            values = df.loc[both, value_column]
            if keys.nunique() > MAX_KEYS or rows / keys.nunique() < MIN_KEY_ROWS or values.nunique() < 2:
                continue

            counts = keys.value_counts()
            trusted = counts[counts >= MIN_KEY_ROWS].index
            covered = int(counts[trusted].sum())
            if len(trusted) < 2 or covered / rows < 0.9:
                continue

            pairs = pd.DataFrame({"key": keys, "value": values})
            pairs = pairs[pairs["key"].isin(trusted)]
            if (pairs.groupby("key")["value"].nunique() != 1).any():
                continue  # at least one key maps to more than one value: not a lookup

            forward = pairs.groupby("key")["value"].first().to_dict()
            keys_per_value = pairs.groupby("value")["key"].nunique()
            unique_values = keys_per_value[keys_per_value == 1].index
            single = set(unique_values)
            reverse = {value: key for key, value in forward.items() if value in single}

            found.append({
                "kind": "lookup",
                "key": key_column,
                "value": value_column,
                "rows": covered,
                "forward": forward,
                "reverse": reverse,
                "label": (f"{key_column} -> {value_column}  ({len(forward)} {key_column} values, "
                          f"each always the same {value_column}, across {covered} rows)"),
            })

    return found


def detect_relationships(df: pd.DataFrame) -> dict:
    """{'products': [...], 'lookups': [...]} found in a cleaned DataFrame."""
    return {"products": detect_products(df), "lookups": detect_lookups(df)}


# ---------------------------------------------------------------------------
# FILLING
# ---------------------------------------------------------------------------

def _assign(df: pd.DataFrame, column: str, index, values) -> None:
    """Write values into the given rows while keeping the column's dtype."""
    if str(df[column].dtype) == "Int64":
        df.loc[index, column] = pd.array(np.round(np.asarray(values, dtype="float64")), dtype="Int64")
    elif df[column].dtype == "float64":
        df.loc[index, column] = np.asarray(values, dtype="float64")
    else:
        df.loc[index, column] = list(values)


def _show(df: pd.DataFrame, column: str, value) -> str:
    """A number as it will look in the cell: whole numbers without '.0' in integer columns."""
    if str(df[column].dtype) == "Int64" or pd.api.types.is_integer_dtype(df[column]):
        return str(int(round(float(value))))
    return str(float(value)) if isinstance(value, (float, np.floating)) else str(value)


def _fill_lookup(df: pd.DataFrame, relationship: dict, stats: dict, log=None) -> int:
    key_column, value_column = relationship["key"], relationship["value"]
    filled = 0

    for target, source, mapping in (
        (value_column, key_column, relationship["forward"]),
        (key_column, value_column, relationship["reverse"]),
    ):
        wanted = df[target].isna() & df[source].notna()
        if not wanted.any():
            continue
        found = df.loc[wanted, source].map(mapping)
        found = found[found.notna()]
        if len(found):
            if log is not None:
                for index, value in found.items():
                    source_value = df.at[index, source]
                    if target == value_column:
                        detail = (f"{key_column} {source_value} -> {value_column} {_show(df, target, value)} "
                                  f"(every other {source_value} row had the same {value_column})")
                    else:
                        detail = (f"{value_column} {_show(df, source, source_value)} -> {key_column} {value} "
                                  f"(only {value} has this {value_column})")
                    log.add(index, target, df.at[index, target], value, LOOKUP, detail)
            _assign(df, target, found.index, found.to_numpy())
            stats["lookup"][target] = stats["lookup"].get(target, 0) + len(found)
            filled += len(found)
    return filled


def _fill_product(df: pd.DataFrame, relationship: dict, stats: dict, log=None) -> int:
    target = relationship["target"]
    first, second = relationship["factors"]
    filled = 0

    a, b, c = (_as_floats(df[column]) for column in (target, first, second))

    # A = B x C
    wanted = np.isnan(a) & ~np.isnan(b) & ~np.isnan(c)
    products = b * c
    if str(df[target].dtype) == "Int64":
        wanted &= np.isclose(products, np.round(products), rtol=0, atol=1e-9)
    if wanted.any():
        if log is not None:
            for position in np.flatnonzero(wanted):
                detail = f"{first} {_show(df, first, b[position])} x {second} {_show(df, second, c[position])}"
                log.add(df.index[position], target, df.iat[position, df.columns.get_loc(target)],
                        products[position], CALCULATED, detail)
        _assign(df, target, df.index[wanted], products[wanted])
        stats["calculated"][target] = stats["calculated"].get(target, 0) + int(wanted.sum())
        filled += int(wanted.sum())
        a = _as_floats(df[target])

    # B = A / C   and   C = A / B   (only when the division is exact)
    for column, known, divisor in ((first, a, c), (second, a, b)):
        values = _as_floats(df[column])
        with np.errstate(divide="ignore", invalid="ignore"):
            quotient = np.round(known / divisor, DECIMALS)
        wanted = np.isnan(values) & ~np.isnan(known) & ~np.isnan(divisor) & (divisor != 0)
        wanted &= np.isfinite(quotient)
        wanted &= np.isclose(quotient * divisor, known, rtol=0, atol=1e-9)
        if str(df[column].dtype) == "Int64":
            wanted &= np.isclose(quotient, np.round(quotient), rtol=0, atol=1e-9)
        if wanted.any():
            if log is not None:
                other = second if column == first else first
                for position in np.flatnonzero(wanted):
                    detail = (f"{target} {_show(df, target, known[position])} / "
                              f"{other} {_show(df, other, divisor[position])}")
                    log.add(df.index[position], column, df.iat[position, df.columns.get_loc(column)],
                            quotient[position], CALCULATED, detail)
            _assign(df, column, df.index[wanted], quotient[wanted])
            stats["calculated"][column] = stats["calculated"].get(column, 0) + int(wanted.sum())
            filled += int(wanted.sum())
            if column == first:
                b = _as_floats(df[first])
            else:
                c = _as_floats(df[second])

    return filled


def apply_fills(df: pd.DataFrame, relationships: list, log=None):
    """Fill missing values using the APPROVED relationships (a list of dicts
    from detect_relationships). Existing values are never changed. Runs up to
    3 rounds because one fill can make another possible. Returns the
    DataFrame and {"calculated": {column: n}, "lookup": {column: n},
    "passes": rounds that filled something}; 'calculated' is exact
    arithmetic, 'lookup' is inferred from the file. If a changes.ChangeLog is
    passed as `log`, every filled cell is recorded with how it was worked out."""
    stats = {"calculated": {}, "lookup": {}, "passes": 0}

    for _ in range(MAX_PASSES):
        filled = 0
        for relationship in relationships:
            if relationship["kind"] == "lookup":
                filled += _fill_lookup(df, relationship, stats, log)
        for relationship in relationships:
            if relationship["kind"] == "product":
                filled += _fill_product(df, relationship, stats, log)
        if filled == 0:
            break
        stats["passes"] += 1

    return df, stats
