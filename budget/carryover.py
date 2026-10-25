"""
budget/carryover.py
--------------------
"Next month": read a workbook this app generated earlier, and set up the following month.

A generated workbook is recognised by its sheet names, its defined names and the marker in Settings!A1.
The closing balance is recomputed from the Transactions sheet with the same rules as the Dashboard
(rather than read from the Dashboard, which holds no stored values until Excel has opened the file).
Categories, budgets, payment methods, currency and the keyword map carry over; Transactions start empty.
"""

import io
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd
from openpyxl import load_workbook

from budget import categorize
from budget.model import (CARD_BILL, EXPENSE, INCOME, LOAN_PAYMENT, LOAN_RECEIVED, MAX_CATEGORIES, MAX_PAYMENT_METHODS,
                          SAVING, TRANSACTION_ROWS, TX_COLUMNS, BudgetConfig)
from budget.workbook import SETTINGS_FIRST, WORKBOOK_MARKER
from quality import is_blank

REQUIRED_SHEETS = ("How to use", "Dashboard", "Transactions", "Settings")
REQUIRED_NAMES = ("month_start", "month_end", "opening", "tx_date", "tx_type", "tx_amt")


@dataclass
class GeneratedWorkbook:
    config: BudgetConfig              # the month the file was for
    keyword_map: dict
    transactions: pd.DataFrame        # TX_COLUMNS
    closing_balance: float
    notes: list


def is_generated_workbook(file_bytes: bytes) -> bool:
    """True when the file looks like one this app made (sheet names + defined names + marker)."""
    try:
        book = load_workbook(io.BytesIO(file_bytes), read_only=False)
    except Exception:
        return False
    if not all(name in book.sheetnames for name in REQUIRED_SHEETS):
        return False
    if not all(name in book.defined_names for name in REQUIRED_NAMES):
        return False
    return book["Settings"]["A1"].value == WORKBOOK_MARKER


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def read_generated_workbook(file_bytes: bytes) -> GeneratedWorkbook:
    """Read settings, keywords and transactions back. Raises ValueError if this is not one of our workbooks."""
    if not is_generated_workbook(file_bytes):
        raise ValueError("This isn't a workbook made by 'Build my budget sheet' (its sheets or settings are missing).")
    book = load_workbook(io.BytesIO(file_bytes), data_only=False)
    settings = book["Settings"]
    notes = []

    month_start = _as_date(settings["B3"].value)
    if month_start is None:
        raise ValueError("The month in Settings!B3 isn't a date.")
    month_start = month_start.replace(day=1)
    opening = settings["B5"].value
    opening = float(opening) if isinstance(opening, (int, float)) else 0.0

    categories = {}
    for row in range(SETTINGS_FIRST, SETTINGS_FIRST + MAX_CATEGORIES):
        name = settings.cell(row=row, column=7).value
        if not is_blank(name) and str(name).strip():
            budget = settings.cell(row=row, column=8).value
            categories[str(name).strip()] = float(budget) if isinstance(budget, (int, float)) else 0.0
    methods = [str(settings.cell(row=row, column=10).value).strip()
               for row in range(SETTINGS_FIRST, SETTINGS_FIRST + MAX_PAYMENT_METHODS)
               if not is_blank(settings.cell(row=row, column=10).value) and str(settings.cell(row=row, column=10).value).strip()]
    currency = settings["B6"].value
    config = BudgetConfig(currency="" if is_blank(currency) else str(currency), month_start=month_start,
                          opening_balance=opening, categories=categories, payment_methods=methods)

    keyword_map = _read_keywords(book, notes)

    sheet = book["Transactions"]
    records = []
    for row in range(2, TRANSACTION_ROWS + 2):
        values = [sheet.cell(row=row, column=column).value for column in range(1, 7)]
        if all(is_blank(v) for v in values):
            continue
        when, description, kind, category, amount, payment = values
        records.append({"Date": _as_date(when), "Description": "" if is_blank(description) else str(description),
                        "Type": None if is_blank(kind) else str(kind), "Category": None if is_blank(category) else str(category),
                        "Amount": float(amount) if isinstance(amount, (int, float)) else None,
                        "Payment method": None if is_blank(payment) else str(payment)})
    transactions = pd.DataFrame(records, columns=list(TX_COLUMNS))
    return GeneratedWorkbook(config=config, keyword_map=keyword_map, transactions=transactions,
                             closing_balance=closing_balance(transactions, month_start, opening), notes=notes)


def _read_keywords(book, notes: list) -> dict:
    if "Keywords" not in book.sheetnames:
        notes.append("The Keywords sheet is missing, so the default keyword map is used.")
        return categorize.default_keyword_map()
    sheet = book["Keywords"]
    data = {"version": 1, "categories": {}, "type_keywords": {}}
    for row in range(2, sheet.max_row + 1):
        group, name, keywords = (sheet.cell(row=row, column=c).value for c in (1, 2, 3))
        if is_blank(group) or is_blank(name):
            continue
        words = [w.strip() for w in str(keywords or "").split(",") if w.strip()]
        target = data["categories"] if group == "Category" else data["type_keywords"] if group == "Type" else None
        if target is not None:
            target[str(name).strip()] = words
    problem = categorize.validate_keyword_map(data)
    if problem:
        notes.append(f"The Keywords sheet has a problem ({problem}), so the default keyword map is used.")
        return categorize.default_keyword_map()
    return data


def closing_balance(transactions: pd.DataFrame, month_start: date, opening: float) -> float:
    """Opening balance + net balance of the month, exactly as the Dashboard computes it:
    Income + Loan received - Expense - Loan payment - Saving (Card bill payments are not part of it)."""
    next_month = date(month_start.year + (month_start.month == 12), month_start.month % 12 + 1, 1)
    inside = transactions[transactions["Date"].map(lambda d: isinstance(d, date) and month_start <= d < next_month)
                          & transactions["Amount"].notna()]
    total = lambda kind: float(inside.loc[inside["Type"] == kind, "Amount"].sum())
    return opening + total(INCOME) + total(LOAN_RECEIVED) - total(EXPENSE) - total(LOAN_PAYMENT) - total(SAVING)


def next_month_config(generated: GeneratedWorkbook) -> BudgetConfig:
    """Settings for the month after the workbook's month: the closing balance becomes the opening balance."""
    current = generated.config.month_start
    following = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
    return BudgetConfig(currency=generated.config.currency, month_start=following,
                        opening_balance=round(generated.closing_balance, 2),
                        categories=dict(generated.config.categories),
                        payment_methods=list(generated.config.payment_methods))
