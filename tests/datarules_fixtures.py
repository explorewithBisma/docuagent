"""
A SYNTHETIC monthly sales file (invented people and orders) for the "Check every month" tests: a clean sample month, a clean next month, and a
month with planted problems whose exact rows are known.

  sales_month(year, month, n, seed)                 -> DataFrame
  problem_month()                                   -> (DataFrame, planted)   planted[name] = [Excel rows]   (header on row 1, data from row 2)

Columns: Order ID (unique), Order Date, Customer Email, Phone, Region (4 values), Status (3 values), Quantity (1-9), Amount, Notes (free text, often blank).
"""

import calendar
import random
from datetime import date

import pandas as pd

REGIONS = ["North", "South", "East", "West"]
STATUSES = ["Paid", "Pending", "Refunded"]
NOTES = ["gift wrap", "call before delivery", "fragile", "second attempt", "leave at door"]


def sales_month(year: int = 2026, month: int = 4, n: int = 240, seed: int = 1, first_id: int = 1000) -> pd.DataFrame:
    rng = random.Random(seed)
    last_day = calendar.monthrange(year, month)[1]
    rows = []
    for i in range(n):
        rows.append({
            "Order ID": f"ORD-{first_id + i}", "Order Date": date(year, month, rng.randint(1, last_day)),
            "Customer Email": f"customer{rng.randint(1, 900)}@mail.test", "Phone": f"03{rng.randint(0, 4)}{rng.randint(0, 9)}{rng.randint(1000000, 9999999)}",
            "Region": rng.choice(REGIONS), "Status": rng.choices(STATUSES, [8, 3, 1])[0], "Quantity": rng.randint(1, 9),
            "Amount": round(rng.uniform(5, 480), 2), "Notes": rng.choice(NOTES) if rng.random() < 0.3 else None})
    return pd.DataFrame(rows)


def problem_month(n: int = 200, seed: int = 9):
    """May 2026 with planted problems. Returns (frame, {problem: [Excel rows]}); Excel row = DataFrame position + 2."""
    frame = sales_month(2026, 5, n, seed, first_id=2000).astype(object)
    planted = {}

    def put(name, positions, column, values):
        for position, value in zip(positions, values):
            frame.at[position, column] = value
        planted[name] = [p + 2 for p in positions]

    put("blank_email", [3, 40, 77], "Customer Email", [None, None, "  "])
    put("bad_email", [10, 90], "Customer Email", ["not-an-email", "john@@mail"])
    put("bad_phone", [15], "Phone", ["12"])
    put("bad_status", [20, 21], "Status", ["Shipped", "paid in full"])
    put("bad_region", [30], "Region", ["Nroth"])
    put("huge_amount", [50], "Amount", [99999.0])
    put("negative_amount", [60], "Amount", [-45.0])
    put("amount_text", [65], "Amount", ["twelve"])
    put("quantity_zero_ok", [70], "Quantity", [0])                          # inside the margin (the sample had 1..9): must NOT be reported
    put("quantity_huge", [71], "Quantity", [500])
    put("date_typo_year", [100], "Order Date", [date(2062, 5, 3)])
    put("date_text", [110], "Order Date", ["soon"])
    frame.at[130, "Order ID"] = frame.at[120, "Order ID"]                    # a duplicate id: row 132 repeats the id of row 122
    planted["duplicate_id"] = [132]
    planted["duplicate_id_first"] = [122]
    planted.pop("quantity_zero_ok")
    return frame, planted
