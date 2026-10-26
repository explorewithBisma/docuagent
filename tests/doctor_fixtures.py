"""
A SYNTHETIC workbook shaped like a template budget tracker (invented numbers - no personal data), for the Excel Doctor tests.

It is built with openpyxl, then Excel's *stored results* are written into the formula cells (openpyxl cannot do that
itself), so it looks like a file that was saved by Excel. It contains one real chart, and a list of PLANTED DEFECTS
(the ground truth the tests compare the Doctor's report with) next to clean areas that must NOT be reported.
"""

import io
import re
import zipfile
from datetime import datetime

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference

LOG, DASH, ACCOUNT, LOAN, INCOME, MONTHLY, DAILY = (
    "📝 Daily Log", "📊 Dashboard", "🏦 Account Balance", "💳 Credit Card Loan", "💵 Income", "📅 Monthly Summary", "📆 Daily Summary")

LOG_FIRST, LOG_LAST, LOG_TOTAL_ROW = 4, 43, 45            # 40 invented expenses; the TOTAL row is 45
TEXT_NUMBER_ROW = 20                                       # E20 holds "17.5" as text (planted)
CATEGORY_CYCLE = ["Food/Grocery", "Transport", "Health", None]


def log_rows():
    """[(row, date or None, description, category, amount)]: a date only on the first row of each day, as in real trackers."""
    rows = []
    for i in range(40):
        amount = round(3 + (i * 7) % 50 + (i % 4) * 0.25, 2)
        when = datetime(2026, 6, i // 3 + 1) if i % 3 == 0 else None
        rows.append((LOG_FIRST + i, when, f"item {i + 1}", CATEGORY_CYCLE[i % 4], amount))
    return rows


def truth() -> dict:
    """The numbers the tests expect, worked out from the data above."""
    rows = log_rows()
    numeric = [r for r in rows if r[0] != TEXT_NUMBER_ROW]                      # the text "17.5" is ignored by SUM, as in Excel
    by_category = {}
    for _, _, _, category, amount in numeric:
        by_category[category] = by_category.get(category, 0) + amount
    by_date = {}
    for _, when, _, _, amount in numeric:
        if when is not None:
            by_date[when] = by_date.get(when, 0) + amount
    return {"log_total": round(sum(r[4] for r in numeric), 2), "by_category": {k: round(v, 2) for k, v in by_category.items()},
            "by_date": by_date, "text_number": next(r[4] for r in rows if r[0] == TEXT_NUMBER_ROW), "typed_expenses": 11755,
            "rows": rows}


# What the Doctor must report on this workbook: (kind, sheet, cell). Everything else must stay quiet.
PLANTED = [
    ("criteria_no_match", MONTHLY, "B5"),        # SUMIF criterion "🍔 Food" while the log says "Food/Grocery"
    ("cross_total", ACCOUNT, "B4"),              # total expenses TYPED as 11,755; the log adds up to less
    ("cross_total", MONTHLY, "B8"),              # the categories add up to less than the log (because of the Food SUMIF)
    ("cross_total", DAILY, "B34"),               # the daily totals add up to less than the log (a date on the first row only)
    ("formula_error", DAILY, "D15"),             # a #REF! left behind
    ("range_short", DAILY, "B34"),               # =SUM(B4:B30) while the table runs to row 33
    ("typed_among_formulas", DAILY, "B12"),      # a number typed over a formula in a column of formulas
    ("number_as_text", LOG, "E20"),              # an amount stored as text
    ("budget_conflict", DASH, "C10"),            # Transport budget 500 on the Dashboard, 0 in Monthly Summary
]


class _Book:
    """openpyxl workbook that remembers the stored result of every formula cell."""

    def __init__(self):
        self.wb = Workbook()
        self.wb.remove(self.wb.active)
        self.cached = {}

    def sheet(self, name):
        return self.wb.create_sheet(name)

    def formula(self, ws, coord, text, stored):
        ws[coord] = "=" + text
        self.cached[(ws.title, coord)] = stored


def build_workbook(with_cached_values: bool = True) -> bytes:
    t = truth()
    b = _Book()

    # --- Daily Log -------------------------------------------------------------------------------------------
    log = b.sheet(LOG)
    log["A1"], log["A2"] = "📝 DAILY EXPENSE LOG — June 2026", "Fill in the BLUE cells"
    for column, header in enumerate(["#", "Date", "Description / What did you spend on?", "Category", "Amount (AED)",
                                     "Payment Method", "Notes"], start=1):
        log.cell(row=3, column=column, value=header)
    for row, when, description, category, amount in t["rows"]:
        if when is not None:
            log.cell(row=row, column=1, value=(row - LOG_FIRST) // 3 + 1)
            log.cell(row=row, column=2, value=when).number_format = "yyyy-mm-dd"
        log.cell(row=row, column=3, value=description)
        log.cell(row=row, column=4, value=category)
        log.cell(row=row, column=5, value="17.5" if row == TEXT_NUMBER_ROW else amount)
        log.cell(row=row, column=6, value="Debit Card")
    log.cell(row=LOG_TOTAL_ROW, column=3, value="TOTAL")
    b.formula(log, f"E{LOG_TOTAL_ROW}", f"SUM(E{LOG_FIRST}:E{LOG_LAST})", t["log_total"])

    # --- Income (clean) ----------------------------------------------------------------------------------------
    income = b.sheet(INCOME)
    income["A1"] = "💵 INCOME — June 2026"
    for column, header in enumerate(["Income Source", "Amount (AED)", "Date Received", "Notes"], start=1):
        income.cell(row=3, column=column, value=header)
    for row, (source, amount) in enumerate([("Primary Salary", 7225), ("Freelance / Side Work", 0), ("Rental Income", 0)], start=4):
        income.cell(row=row, column=1, value=source)
        income.cell(row=row, column=2, value=amount)
    income["A7"] = "✅ TOTAL INCOME"
    b.formula(income, "B7", "SUM(B4:B6)", 7225)

    # --- Credit Card Loan (clean) --------------------------------------------------------------------------------
    loan = b.sheet(LOAN)
    loan["A1"] = "💳 CREDIT CARD LOAN TRACKER — June 2026"
    loan["A3"] = "🗓️ MONTHLY PAYMENT LOG"
    for column, header in enumerate(["Payment Date", "Loan / Card", "Amount Paid (AED)", "Payment Method"], start=1):
        loan.cell(row=4, column=column, value=header)
    payments = [(datetime(2026, 6, 15), "ENBD", 500), (datetime(2026, 6, 17), None, 500.5), (datetime(2026, 6, 17), None, 3580.22),
                (datetime(2026, 6, 19), None, 662.49)]
    for row, (when, who, amount) in enumerate(payments, start=5):
        loan.cell(row=row, column=1, value=when).number_format = "yyyy-mm-dd"
        loan.cell(row=row, column=2, value=who)
        loan.cell(row=row, column=3, value=amount)
    loan["A9"] = "✅ TOTAL PAYMENTS THIS MONTH"
    loan_total = round(sum(p[2] for p in payments), 2)
    b.formula(loan, "C9", "SUM(C5:C8)", loan_total)

    # --- Monthly Summary ----------------------------------------------------------------------------------------
    monthly = b.sheet(MONTHLY)
    monthly["A1"] = "📅 MONTHLY SUMMARY — June 2026"
    for column, header in enumerate(["Category", "Total Spent (AED)", "Budget (AED)", "Difference", "Status"], start=1):
        monthly.cell(row=3, column=column, value=header)
    log_cat = f"'{LOG}'!$D${LOG_FIRST}:$D${LOG_LAST}"
    log_amt = f"'{LOG}'!$E${LOG_FIRST}:$E${LOG_LAST}"
    monthly_rows = [("🏠 Housing", "Housing", 0, 2000), ("🍔 Food", "🍔 Food", 0, 1000),          # planted: "🍔 Food" matches nothing
                    ("🚗 Transport", "Transport", t["by_category"]["Transport"], 0),
                    ("💊 Health", "Health", t["by_category"]["Health"], 500)]
    spent_total = 0
    for row, (label, criterion, stored, budget) in enumerate(monthly_rows, start=4):
        monthly.cell(row=row, column=1, value=label)
        b.formula(monthly, f"B{row}", f'SUMIF({log_cat},"{criterion}",{log_amt})', stored)
        monthly.cell(row=row, column=3, value=budget)
        b.formula(monthly, f"D{row}", f"C{row}-B{row}", budget - stored)
        spent_total += stored
    monthly["A8"] = "🏁 GRAND TOTAL"
    b.formula(monthly, "B8", "SUM(B4:B7)", round(spent_total, 2))
    b.formula(monthly, "C8", "SUM(C4:C7)", 3500)

    # --- Dashboard ------------------------------------------------------------------------------------------------
    dash = b.sheet(DASH)
    dash["A1"] = "📊 PERSONAL BUDGET DASHBOARD"
    dash["A4"] = "TOTAL EXPENSES"
    b.formula(dash, "B4", f"'{LOG}'!E{LOG_TOTAL_ROW}", t["log_total"])
    for column, header in enumerate(["Category", "Spent (AED)", "Budget (AED)", "Left / Over"], start=1):
        dash.cell(row=7, column=column, value=header)
    for row, (label, _, stored, _) in zip(range(8, 12), monthly_rows):
        dash.cell(row=row, column=1, value=label)
    budgets = {8: 2000, 9: 1000, 10: 500, 11: 500}                       # planted: Transport 500 here, 0 in Monthly Summary
    for row, budget in budgets.items():
        b.formula(dash, f"B{row}", f"'{MONTHLY}'!B{row - 4}", monthly_rows[row - 8][2])
        dash.cell(row=row, column=3, value=budget)
        b.formula(dash, f"D{row}", f"C{row}-B{row}", budget - monthly_rows[row - 8][2])
    chart = BarChart()
    chart.title = "Spent by category"
    chart.add_data(Reference(dash, min_col=2, min_row=7, max_row=11), titles_from_data=True)
    chart.set_categories(Reference(dash, min_col=1, min_row=8, max_row=11))
    dash.add_chart(chart, "F4")

    # --- Account Balance ---------------------------------------------------------------------------------------------
    account = b.sheet(ACCOUNT)
    account["A1"] = "🏦 ACCOUNT BALANCE TRACKER — June 2026"
    account["A2"], account["B2"] = "May 2026 Closing Balance (AED)", 11236
    account["A3"] = "(+) Total Income This Month"
    b.formula(account, "B3", f"'{INCOME}'!B7", 7225)
    account["A4"], account["B4"] = "(-) Total Expenses This Month", t["typed_expenses"]              # planted: typed by hand
    account["A5"] = "(-) Total Loan Payments This Month"
    b.formula(account, "B5", f"'{LOAN}'!C9", loan_total)
    account["A6"] = "(=) Estimated Closing Balance"
    b.formula(account, "B6", "B2+B3-B4-B5", round(11236 + 7225 - t["typed_expenses"] - loan_total, 2))

    # --- Daily Summary ------------------------------------------------------------------------------------------------
    daily = b.sheet(DAILY)
    daily["A1"] = "📆 DAILY SPENDING SUMMARY — June 2026"
    for column, header in enumerate(["Date", "Total Spent (AED)", "Daily Budget (AED)", "Over / Under"], start=1):
        daily.cell(row=3, column=column, value=header)
    log_dates = f"'{LOG}'!$B${LOG_FIRST}:$B${LOG_LAST}"
    daily_total = 0
    for day in range(1, 31):
        row = 3 + day
        when = datetime(2026, 6, day)
        daily.cell(row=row, column=1, value=when).number_format = "yyyy-mm-dd"
        stored = round(t["by_date"].get(when, 0), 2)
        if row == 12:
            daily.cell(row=row, column=2, value=99)                      # planted: a number typed over a formula
            stored = 99
        else:
            b.formula(daily, f"B{row}", f"SUMIF({log_dates},A{row},{log_amt})", stored)
        daily.cell(row=row, column=3, value=0)
        if row == 15:
            b.formula(daily, f"D{row}", f"C{row}-#REF!", "#REF!")        # planted: a broken reference
        else:
            b.formula(daily, f"D{row}", f"C{row}-B{row}", -stored)
        daily_total += stored
    daily["A34"] = "TOTAL"
    b.formula(daily, "B34", "SUM(B4:B30)", round(sum(daily.cell(row=r, column=2).value if r == 12 else
                                                   b.cached[(DAILY, f"B{r}")] for r in range(4, 31)), 2))   # planted: stops 3 rows short

    buffer = io.BytesIO()
    b.wb.save(buffer)
    data = buffer.getvalue()
    return inject_stored_results(data, b.cached) if with_cached_values else data


def inject_stored_results(xlsx_bytes: bytes, cached: dict) -> bytes:
    """Write Excel's stored result next to every formula, like a file saved by Excel: <c><f>..</f><v>result</v></c>."""
    source = zipfile.ZipFile(io.BytesIO(xlsx_bytes))
    workbook_xml = source.read("xl/workbook.xml").decode("utf-8")
    rels = source.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    targets = dict(re.findall(r'<Relationship [^>]*?Id="([^"]+)"[^>]*?Target="([^"]+)"', rels))
    targets.update({a: b for b, a in re.findall(r'<Relationship [^>]*?Target="([^"]+)"[^>]*?Id="([^"]+)"', rels)})
    part_of = {}
    for name, rid in re.findall(r'<sheet [^>]*?name="([^"]+)"[^>]*?r:id="([^"]+)"', workbook_xml):
        target = targets[rid]
        part_of[name] = ("xl/" + target.lstrip("/")) if not target.startswith("/xl/") else target.lstrip("/")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target_zip:
        for item in source.infolist():
            content = source.read(item.filename)
            for (sheet, coord), stored in cached.items():
                if part_of.get(sheet) == item.filename:
                    text = content.decode("utf-8")
                    pattern = re.compile(rf'<c r="{coord}"([^>]*)>(<f>.*?</f>)<v\s*/></c>')
                    if isinstance(stored, str):
                        kind = "e" if stored.startswith("#") else "str"
                        replacement = rf'<c r="{coord}"\1 t="{kind}">\2<v>{stored}</v></c>'
                    else:
                        replacement = rf'<c r="{coord}"\1>\2<v>{stored!r}</v></c>'
                    text, count = pattern.subn(replacement, text)
                    assert count == 1, (sheet, coord, count)
                    content = text.encode("utf-8")
            target_zip.writestr(item, content)
    return out.getvalue()
