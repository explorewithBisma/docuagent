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


def personal_style_bytes() -> bytes:
    """A workbook shaped like a template budget tracker (synthetic numbers): a long-header daily log, an income list,
    a loan payment log, balances with several candidate labels, and two budget tables that disagree."""
    wb = Workbook()
    dash = wb.active
    dash.title = "📊 Dashboard"
    dash.append(["📊 PERSONAL BUDGET DASHBOARD"])
    dash.append([])
    dash.append(["Category", "Spent (AED)", "Budget (AED)", "Left / Over", "Status"])
    for row in (["🏠 Housing", 2000, 2000], ["🍔 Food", 0, 1000], ["🚗 Transport", 40, 500], ["💊 Health", 0, 500]):
        dash.append(row)
    dash.append(["🏁 GRAND TOTAL", 2040, 3500])                      # 4000 of budgets, but the total says 3500: inconsistent

    balance = wb.create_sheet("🏦 Account Balance")
    balance.append(["🏦 ACCOUNT BALANCE TRACKER — June 2026"])
    for label, value in (("May 2026 Closing Balance (AED)", 11236), ("Any Pending Transactions (AED)", 0),
                         ("Adjusted Opening Balance (AED)", 11236), ("Opening Balance (from May closing)", 11236),
                         ("(+) Total Income This Month", 7225), ("(=) Estimated Closing Balance", 1463.29),
                         ("🏁 TOTAL BALANCE (All Accounts)", 0)):
        balance.append([label, value])

    loan = wb.create_sheet("💳 Credit Card Loan")
    loan.append(["💳 CREDIT CARD LOAN TRACKER — June 2026"])
    loan.append(["Total Loan Amount (AED)", 5000])
    loan.append([])
    loan.append(["🗓️ MONTHLY PAYMENT LOG"])
    loan.append(["Payment Date", "Loan / Card", "Amount Paid (AED)", "Payment Method"])
    loan.append([datetime(2026, 6, 15), "ENBD", 500, "Debit card"])
    loan.append([datetime(2026, 6, 17), None, 500.5, None])
    loan.append([None, None, 0, None])
    loan.append(["✅ TOTAL PAYMENTS THIS MONTH", None, 1000.5])

    log = wb.create_sheet("📝 Daily Log")
    log.append(["📝 DAILY EXPENSE LOG — June 2026"])
    log.append(["Fill in the BLUE cells"])
    log.append(["#", "Date", "Description / What did you spend on?", "Category", "Amount (AED)", "Payment Method", "Notes"])
    entries = [
        (1, datetime(2026, 6, 1), "Resturant", "Food/Grocery", 5), (None, None, "Grocery", "Food/Grocery", 6),
        (2, datetime(2026, 6, 2), "malabar", None, 23.5), (None, None, "Taxi", None, 15),
        (3, datetime(2026, 6, 3), "Aster", None, 40),
        (4, datetime(2026, 6, 4), "Resturant", None, 12), (None, None, "Metro", None, 4),
        (5, datetime(2026, 6, 5), "Room rent", None, 2000),
    ]
    for number, when, description, category, amount in entries:
        log.append([number, when, description, category, amount, "Debit Card", None])
    log.append([None, None, None, None, None, "Debit Card", None])           # an empty template row
    log.append([None, None, "TOTAL", None, 2105.5, None, None])

    income = wb.create_sheet("💵 Income")
    income.append(["💵 INCOME — June 2026"])
    income.append([])
    income.append(["Income Source", "Amount (AED)", "Date Received", "Notes"])
    for row in (["Primary Salary", 7225, None, None], ["Freelance / Side Work", 0, None, None], ["Credit Card Loan", None, None, None]):
        income.append(row)
    income.append(["✅ TOTAL INCOME", 7225])

    summary = wb.create_sheet("📅 Monthly Summary")
    summary.append(["📅 MONTHLY SUMMARY — June 2026"])
    summary.append([])
    summary.append(["Category", "Total Spent (AED)", "Budget (AED)", "Difference", "Status"])
    for row in (["🏠 Housing", 2000, 2000], ["🍔 Food", 0, 1000], ["🚗 Transport", 40, 0], ["💊 Health", 0, 500]):
        summary.append(row)
    summary.append(["🏁 GRAND TOTAL", 2040, 3500])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
