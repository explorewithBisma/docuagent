"""Test helper: recalculate a generated budget workbook without Excel (the `formulas` package) and compare with pandas."""
import io
import itertools
import os
import tempfile
import warnings
from datetime import date

import numpy as np
import pandas as pd

_counter = itertools.count(1)


def recalc(xlsx_bytes: bytes) -> dict:
    """{'Dashboard!B5': value, ...} for every cell of the Dashboard after a full recalculation."""
    import formulas
    warnings.filterwarnings("ignore")
    folder = tempfile.mkdtemp(prefix="budget_calc_")
    name = f"book{next(_counter)}.xlsx"
    path = os.path.join(folder, name)
    with open(path, "wb") as handle:
        handle.write(xlsx_bytes)
    solution = formulas.ExcelModel().loads(path).finish().calculate()
    prefix = f"'[{name}]DASHBOARD'!"
    values = {}
    for key, ranges in solution.items():
        if key.startswith(prefix) and ":" not in key:
            values["Dashboard!" + key[len(prefix):]] = _scalar(ranges.value)
    return values


def _scalar(value):
    while isinstance(value, (list, tuple, np.ndarray)):
        value = np.asarray(value, dtype=object).ravel()[0] if np.size(value) else None
    if isinstance(value, (np.floating, np.integer)):
        return float(value)
    return value


def expected_totals(transactions: pd.DataFrame, month_start: date, opening: float) -> dict:
    """The Dashboard numbers computed the plain pandas way."""
    next_month = date(month_start.year + (month_start.month == 12), month_start.month % 12 + 1, 1)
    frame = transactions.copy()
    frame = frame[frame["Date"].map(lambda d: isinstance(d, date) and month_start <= d < next_month)]
    total = lambda kind: float(frame.loc[frame["Type"] == kind, "Amount"].sum())
    income, expenses, loan_pay, loan_in, saving, card = (
        total("Income"), total("Expense"), total("Loan payment"), total("Loan received"), total("Saving"), total("Card bill payment"))
    net = income + loan_in - expenses - loan_pay - saving
    return {"income": income, "expenses": expenses, "loan_payments": loan_pay, "loans_received": loan_in,
            "saved": saving, "net": net, "opening": opening, "closing": opening + net, "card_bills": card,
            "by_category": frame[frame["Type"] == "Expense"].groupby("Category")["Amount"].sum().to_dict(),
            "daily_expenses": frame[frame["Type"] == "Expense"].groupby("Date")["Amount"].sum().to_dict(),
            "daily_income": frame[frame["Type"] == "Income"].groupby("Date")["Amount"].sum().to_dict()}


SUMMARY_CELLS = {"income": "B5", "expenses": "B6", "loan_payments": "B7", "loans_received": "B8", "saved": "B9",
                 "net": "B10", "opening": "B11", "closing": "B12", "card_bills": "B13"}


def excel_serial(day: date) -> float:
    return float((pd.Timestamp(day) - pd.Timestamp("1899-12-30")).days)


def stored_results(xlsx_bytes: bytes) -> dict:
    """{(sheet, 'B5'): value} for EVERY formula cell of every sheet, calculated without Excel (the `formulas` package)."""
    import formulas
    from openpyxl import load_workbook
    warnings.filterwarnings("ignore")
    book = load_workbook(io.BytesIO(xlsx_bytes))
    real = {ws.title.upper(): ws.title for ws in book.worksheets}
    folder = tempfile.mkdtemp(prefix="budget_calc_")
    name = f"book{next(_counter)}.xlsx"
    path = os.path.join(folder, name)
    with open(path, "wb") as handle:
        handle.write(xlsx_bytes)
    solution = formulas.ExcelModel().loads(path).finish().calculate()
    formula_cells = {(ws.title, c.coordinate) for ws in book.worksheets for row in ws.iter_rows() for c in row
                     if isinstance(c.value, str) and c.value.startswith("=")}
    out = {}
    prefix = f"'[{name}]"
    for key, ranges in solution.items():
        if not key.startswith(prefix) or ":" in key:
            continue
        sheet_upper, _, coord = key[len(prefix):].partition("'!")
        sheet = real.get(sheet_upper)
        if sheet is None or (sheet, coord) not in formula_cells:
            continue
        value = _scalar(ranges.value)
        if isinstance(value, bool) or value is None:
            continue
        out[(sheet, coord)] = value if not isinstance(value, (np.floating, np.integer)) else float(value)
    return out


def as_saved_by_excel(xlsx_bytes: bytes) -> bytes:
    """The same workbook with Excel's stored result written next to every formula, like a file that was opened and saved in Excel."""
    import doctor_fixtures
    return doctor_fixtures.inject_stored_results(xlsx_bytes, stored_results(xlsx_bytes))
