"""Shared by the reconcile tests: a table the way the reader hands it over, and the whole preparation."""
import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import pandas as pd

from reconcile import columns as rc, prepare as rp


def to_raw(df, title_rows=0):
    """What the reader gives: no header, every cell an object, the column names in a row of their own (after some title rows)."""
    titles = [[f"Statement line {i}"] + [None] * (len(df.columns) - 1) for i in range(title_rows)]
    rows = titles + [list(df.columns)] + df.astype(object).where(df.notna(), None).values.tolist()
    return pd.DataFrame(rows, dtype=object)


def prepared(df, title_rows=0):
    table, header_row = rc.table_from_raw(to_raw(df, title_rows))
    mapping = rc.detect_mapping(table)
    return table, mapping, rp.prepare_side(table, mapping, header_row), header_row
