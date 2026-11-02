"""
reconcile/views.py
-------------------
Small tables for the page: the key columns of each bucket (row number, date, description, amount, reference), side by side for pairs.
The Excel result has every column; the page shows just enough to recognise the row.
"""

import numpy as np
import pandas as pd


def _cell(table, mapping, i, role):
    column = mapping.column(role)
    if not column:
        return ""
    value = table.iloc[i][column]
    return "" if pd.isna(value) else (value.date().isoformat() if hasattr(value, "date") and hasattr(value, "hour") else str(value))


def _amount(side, i):
    return None if np.isnan(side.amounts[i]) else round(float(side.amounts[i]), 2)


def pairs_frame(pairs, A, B, table_a, table_b, map_a, map_b, accepted=None) -> pd.DataFrame:
    rows = []
    for p in pairs:
        row = {"Row in A": A.row_numbers[p.a], "A date": _cell(table_a, map_a, p.a, "date"), "A description": _cell(table_a, map_a, p.a, "description"),
               "A amount": _amount(A, p.a), "Row in B": B.row_numbers[p.b], "B date": _cell(table_b, map_b, p.b, "date"),
               "B description": _cell(table_b, map_b, p.b, "description"), "B amount": _amount(B, p.b), "Why": p.reason}
        if accepted is not None:
            row = {"Accept": (p.a, p.b) in accepted, **row}
        rows.append(row)
    return pd.DataFrame(rows)


def single_frame(rows, side, table, mapping, label) -> pd.DataFrame:
    return pd.DataFrame([{f"Row in {label}": side.row_numbers[i], "Date": _cell(table, mapping, i, "date"), "Description": _cell(table, mapping, i, "description"),
                          "Reference": _cell(table, mapping, i, "reference"), "Amount": _amount(side, i)} for i in rows])
