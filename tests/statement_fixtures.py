"""
SYNTHETIC bank statements (invented merchants and amounts) for the "bank statement to budget" tests. One month, May 2026, an opening
balance of 5,000.00 and 28 transactions that cover every case:

  credits       salary, freelance, a refund, an internal transfer in from the user's own savings account
  debits        groceries, restaurants, transport (one with a typo: "Starbuks"), bills, a shop nothing knows ("Mazaya Trading LLC")
  loan          "EMI HDFC loan"                                    -> Type Loan payment
  card bills    "Credit card payment VISA", "Mastercard bill"       -> Type Card bill payment (not spending)
  own accounts  "Transfer to own account - savings"                 -> Type Card bill payment (not spending)
  unclear       "IBFT transfer Ali Khan"                            -> stays an Expense, listed in Please check

statement_bytes(shape) renders the same transactions in different layouts; EXPECTED holds the answers.
"""

import io
from datetime import date

import pandas as pd

OPENING = 5000.00

# (day of May 2026, description, signed amount: + money in, - money out)
TRANSACTIONS = [
    (1, "Salary May 2026", 8000.00), (1, "Rent May", -2200.00), (2, "Carrefour Supermarket", -184.35), (2, "Uber trip", -23.50),
    (3, "Resturant Al Mallah", -18.00), (4, "DEWA electricity bill", -310.20), (5, "Netflix subscription", -45.00), (6, "Spinneys grocery", -97.80),
    (7, "Freelance payment - logo design", 1200.00), (8, "Pizza Hut restaurant", -64.00), (9, "Careem ride", -31.25), (10, "Credit card payment VISA", -2500.00),
    (11, "Refund Amazon order", 45.50), (12, "Mazaya Trading LLC", -120.00), (13, "Carrefour Supermarket", -143.10), (14, "EMI HDFC loan", -950.00),
    (15, "Transfer to own account - savings", -1500.00), (16, "Etisalat mobile bill", -199.00), (17, "Lulu grocery", -76.40), (18, "Mastercard bill", -800.00),
    (19, "Internal transfer from savings", 500.00), (20, "Shell petrol fuel", -150.00), (21, "IBFT transfer Ali Khan", -300.00), (22, "Cafe breakfast", -42.00),
    (23, "Pharmacy Boots", -58.90), (25, "Cinema Vox tickets", -90.00), (27, "Taxi to airport", -85.00), (28, "Spinneys grocery", -112.65),
]


def chronological():
    """[(date, description, signed, balance after)] in time order."""
    out, balance = [], OPENING
    for day, description, signed in TRANSACTIONS:
        balance = round(balance + signed, 2)
        out.append((date(2026, 5, day), description, signed, balance))
    return out


CLOSING = chronological()[-1][3]


def _money(value):
    return f"{value:,.2f}"


def statement_frame(shape: str = "debit_credit"):
    """The statement table (header row included as the first row of the returned raw table), plus how many title rows come before it."""
    rows = chronological()
    if shape in ("newest_first",):
        rows = rows[::-1]
    if shape in ("debit_credit", "newest_first"):
        header = ["Date", "Description", "Debit", "Credit", "Balance"]
        body = [[d.strftime("%d/%m/%Y"), text, _money(-s) if s < 0 else None, _money(s) if s > 0 else None, _money(b)] for d, text, s, b in rows]
        titles = [["Account Statement", None, None, None, None], ["Account: 1234-5678  Period: 01/05/2026 - 31/05/2026", None, None, None, None], [None] * 5]
        return titles + [header] + body
    if shape == "signed":
        header = ["Transaction Date", "Narration", "Amount (AED)", "Running Balance"]
        body = [[d.isoformat(), text, s, b] for d, text, s, b in rows]
        return [header] + body
    if shape == "signed_positive_debits":        # the file writes money OUT as positive and money IN as negative
        header = ["Date", "Details", "Amount", "Balance"]
        body = [[d.isoformat(), text, -s, b] for d, text, s, b in rows]
        return [header] + body
    if shape == "signed_no_balance":
        header = ["Date", "Description", "Amount"]
        return [header] + [[d.isoformat(), text, s] for d, text, s, b in rows]
    raise ValueError(shape)


def statement_bytes(shape: str = "debit_credit", xlsx: bool = False) -> bytes:
    table = pd.DataFrame(statement_frame(shape))
    if xlsx:
        buffer = io.BytesIO()
        table.to_excel(buffer, index=False, header=False, sheet_name="Statement")
        return buffer.getvalue()
    return table.to_csv(index=False, header=False).encode("utf-8")


# What the budget must come out as -----------------------------------------------------------------------------------------------
CARD_BILL_ROWS = {"Credit card payment VISA": -2500.00, "Mastercard bill": -800.00, "Transfer to own account - savings": -1500.00, "Internal transfer from savings": 500.00}
EXPECTED_TYPES = {
    "Salary May 2026": "Income", "Freelance payment - logo design": "Income", "Refund Amazon order": "Income",
    "EMI HDFC loan": "Loan payment", **{d: "Card bill payment" for d in CARD_BILL_ROWS},
}
EXPECTED_CATEGORIES = {"Carrefour Supermarket": "Food", "Uber trip": "Transport", "Resturant Al Mallah": "Food", "DEWA electricity bill": "Bills",
                       "Spinneys grocery": "Food", "Pizza Hut restaurant": "Food", "Careem ride": "Transport", "Shell petrol fuel": "Transport"}
INCOME_TOTAL = 8000.00 + 1200.00 + 45.50
NET_OF_CARD_BILL_ROWS = sum(CARD_BILL_ROWS.values())         # -4,300.00: money that left the account but is not spending
