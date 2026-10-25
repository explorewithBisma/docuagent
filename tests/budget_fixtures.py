"""Builds messy personal-budget workbooks for the budget tests (synthetic - no real data)."""
import io
from datetime import datetime

from openpyxl import Workbook


def messy_budget_bytes() -> bytes:
    """A daily expense log the way people really keep them: a title above the header, the date only on the first
    row of each day, mostly empty categories, text amounts, hardcoded totals, a notes line and blank rows."""
    wb = Workbook()
    ws = wb.active
    ws.title = "June"
    ws.append(["June 2026 daily budget"])
    ws.append([])
    ws.append(["Date", "Description", "Amount", "Category", "Payment", "Daily Total"])
    rows = [
        # day 1: real datetime on the first row only
        [datetime(2026, 6, 1), "Taxi to office", 25, None, "Cash", None],
        [None, "Grocery - Carrefour", 120, None, "Card", None],
        [None, "Lunch", 30, "food", "Cash", None],
        [None, "Total", 175, None, None, 175],                        # labelled hardcoded total
        # day 2: date as text, day-first (13/06 below proves it)
        ["02/06/2026", "Salary June", 5000, None, "Bank transfer", None],
        [None, "Metro card top-up", "Rs. 1,200/-", None, "Cash", None],
        [None, "Mystery shop", 45, None, None, None],
        [None, None, 1245, None, None, None],                         # unlabelled total: 1200 + 45 (not the salary)
        [],
        # day 3: an Excel serial number
        [46176, "Car EMI", 800, None, "Bank transfer", None],
        [None, "Credit card bill - Visa", 600, None, "Bank transfer", None],
        [None, "Transfer to savings", 300, None, "Bank transfer", None],
        [None, "Dog food", 60, "Pets", "Cash", None],
        [None, "Back at the office tomorrow", None, None, None, None],  # a note, no amount
        ["13/06/2026", "Cafe latte", 18, None, "Card", None],
        [None, "Netflix", 15.5, None, "Credit card", None],
        [None, "Grand total", 99999, None, None, None],
    ]
    for row in rows:
        ws.append(row)
    other = wb.create_sheet("Notes")
    other.append(["just some notes"])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
