"""
budget/workbook.py
-------------------
Builds the budget workbook (.xlsx). Sheets, in order:

  How to use    - 8 short steps in plain English
  Dashboard     - only formulas (SUMIFS etc.): totals, data checks, spending vs budget, top 3, daily totals, charts
  Transactions  - the entry sheet: Date, Description, Type, Category, Amount, Payment method (+ dropdowns)
  Settings      - month, opening balance, currency label, and the lists behind the dropdowns (categories + budgets ...)
  Keywords      - the keyword map, kept so next month's workbook can reuse it
  Import Notes  - only when the data was imported: what was filled, dropped or flagged

Money direction (the Dashboard formulas):
  net balance      = Income + Loan received - Expense - Loan payment - Saving
  closing balance  = opening balance + net balance
  Card bill payment is shown on its own line and is NOT part of expenses or of the net balance:
  the purchases were already counted as Expenses on the day they were made.

The Dashboard contains no typed numbers: change anything on Transactions or Settings and it follows.
"""

import io
import json
from datetime import date, datetime

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, DoughnutChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.comments import Comment
from openpyxl.formatting.rule import DataBarRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation

from budget.model import (CARD_BILL, EXPENSE, INCOME, LOAN_PAYMENT, LOAN_RECEIVED, MAX_CATEGORIES,
                          MAX_PAYMENT_METHODS, SAVING, TRANSACTION_ROWS, TX_COLUMNS, TYPES, BudgetConfig)
from quality import is_blank

WORKBOOK_MARKER = "Budget Builder workbook"
FIRST_ROW, LAST_ROW = 2, TRANSACTION_ROWS + 1            # Transactions rows that are formatted and counted
SETTINGS_FIRST = 3                                         # first row of the lists on Settings
CATEGORY_ROW_1 = 18                                        # first row of the Dashboard category table
DAILY_ROW_1 = 12                                           # first row of the Dashboard daily table
CHART_MIN_SHARE = 0.03                                     # doughnut slices under this share of the spending get no label
CHART_HELPER_COLUMN = 27                                   # AA: the chart's own small table lives out of the way
SHEET_NAMES = ("How to use", "Dashboard", "Transactions", "Settings", "Keywords")

# --- look -------------------------------------------------------------------------------
INK, MUTED, ACCENT = "1F2937", "6B7280", "2563EB"
HEADER_FILL = PatternFill("solid", fgColor="1F2937")
CARD_FILL = PatternFill("solid", fgColor="EFF6FF")
SAVED_FILL = PatternFill("solid", fgColor="ECFDF5")
INPUT_FILL = PatternFill("solid", fgColor="FFFBEB")
YELLOW_FILL = PatternFill("solid", bgColor="FEF08A", fgColor="FEF08A")
GREY_FILL = PatternFill("solid", bgColor="E5E7EB", fgColor="E5E7EB")
ORANGE_FILL = PatternFill("solid", bgColor="FED7AA", fgColor="FED7AA")
RED_FILL = PatternFill("solid", bgColor="FECACA", fgColor="FECACA")
THIN = Side(style="thin", color="D1D5DB")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WHITE_BOLD = Font(bold=True, color="FFFFFF")


def money_format(currency: str) -> str:
    symbol = str(currency).replace('"', "").strip()
    return f'"{symbol}" #,##0.00' if symbol else "#,##0.00"


def _text(cell, value) -> None:
    """Write user text so it is never read as a formula."""
    cell.value = value
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        cell.data_type = "s"


def _header(ws, row: int, labels, first_column: int = 1) -> None:
    for offset, label in enumerate(labels):
        cell = ws.cell(row=row, column=first_column + offset, value=label)
        cell.font, cell.fill, cell.border = WHITE_BOLD, HEADER_FILL, BOX
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _name(wb, name: str, reference: str) -> None:
    wb.defined_names[name] = DefinedName(name, attr_text=reference)


def _window(column_letter: str, sheet: str = "Transactions") -> str:
    return f"{sheet}!${column_letter}${FIRST_ROW}:${column_letter}${LAST_ROW}"


# ---------------------------------------------------------------------------
# SETTINGS + KEYWORDS
# ---------------------------------------------------------------------------

def _build_settings(wb, config: BudgetConfig) -> None:
    ws = wb.create_sheet("Settings")
    ws["A1"], ws["A2"] = WORKBOOK_MARKER, "Version 1 - change the yellow cells; everything else follows."
    ws["A1"].font, ws["A2"].font = Font(bold=True, size=14, color=INK), Font(italic=True, color=MUTED)

    ws["A3"], ws["B3"] = "Month (first day)", config.month_start
    ws["A4"], ws["B4"] = "Month ends", "=EDATE(B3,1)-1"
    ws["A5"], ws["B5"] = "Opening balance", float(config.opening_balance)
    ws["A6"], ws["B6"] = "Currency label", config.currency
    ws["A7"], ws["B7"] = "Doughnut chart: hide slices under", CHART_MIN_SHARE
    ws["B7"].number_format = "0%"
    ws["B3"].number_format = ws["B4"].number_format = "dd mmm yyyy"
    ws["B5"].number_format = money_format(config.currency)
    for address in ("B3", "B5", "B6", "B7"):
        ws[address].fill, ws[address].border = INPUT_FILL, BOX
    ws["A9"] = "The currency symbol in the number formats was set when this file was made;"
    ws["A10"] = "the label above is for reference only."
    ws["A9"].font = ws["A10"].font = Font(italic=True, color=MUTED, size=9)

    _header(ws, 2, ["Types"], 5)
    ws["E2"].value = "Types"
    _header(ws, 2, ["Category", "Monthly budget"], 7)
    _header(ws, 2, ["Payment methods"], 10)
    for offset, type_name in enumerate(TYPES):
        ws.cell(row=SETTINGS_FIRST + offset, column=5, value=type_name)
    for offset in range(MAX_CATEGORIES):
        row = SETTINGS_FIRST + offset
        name_cell, budget_cell = ws.cell(row=row, column=7), ws.cell(row=row, column=8)
        for cell in (name_cell, budget_cell):
            cell.fill, cell.border = INPUT_FILL, BOX
        budget_cell.number_format = money_format(config.currency)
    for offset, (name, budget) in enumerate(config.categories.items()):
        _text(ws.cell(row=SETTINGS_FIRST + offset, column=7), str(name).strip())
        ws.cell(row=SETTINGS_FIRST + offset, column=8, value=float(budget or 0))
    for offset in range(MAX_PAYMENT_METHODS):
        cell = ws.cell(row=SETTINGS_FIRST + offset, column=10)
        cell.fill, cell.border = INPUT_FILL, BOX
    for offset, method in enumerate(config.payment_methods):
        _text(ws.cell(row=SETTINGS_FIRST + offset, column=10), str(method).strip())

    for column, width in zip("ABCDEFGHIJ", (22, 16, 3, 3, 20, 3, 24, 16, 3, 22)):
        ws.column_dimensions[column].width = width

    last = SETTINGS_FIRST + MAX_CATEGORIES - 1
    _name(wb, "month_start", "Settings!$B$3")
    _name(wb, "month_end", "Settings!$B$4")
    _name(wb, "opening", "Settings!$B$5")
    _name(wb, "chart_min_share", "Settings!$B$7")
    _name(wb, "lst_types", f"Settings!$E${SETTINGS_FIRST}:$E${SETTINGS_FIRST + len(TYPES) - 1}")
    _name(wb, "lst_categories", f"OFFSET(Settings!$G${SETTINGS_FIRST},0,0,MAX(1,COUNTA(Settings!$G${SETTINGS_FIRST}:$G${last})),1)")
    _name(wb, "lst_payment", f"OFFSET(Settings!$J${SETTINGS_FIRST},0,0,"
                             f"MAX(1,COUNTA(Settings!$J${SETTINGS_FIRST}:$J${SETTINGS_FIRST + MAX_PAYMENT_METHODS - 1})),1)")


def _build_keywords(wb, keyword_map: dict) -> None:
    ws = wb.create_sheet("Keywords")
    _header(ws, 1, ["Group", "Name", "Keywords (comma-separated)"])
    row = 2
    for group, mapping in (("Category", keyword_map["categories"]), ("Type", keyword_map.get("type_keywords", {}))):
        for name, keywords in mapping.items():
            ws.cell(row=row, column=1, value=group)
            _text(ws.cell(row=row, column=2), name)
            _text(ws.cell(row=row, column=3), ", ".join(keywords))
            row += 1
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width, ws.column_dimensions["C"].width = 12, 24, 90
    ws.freeze_panes = "A2"
    ws["E1"] = "Used by the app when you start next month: edit freely, keep the three columns."
    ws["E1"].font = Font(italic=True, color=MUTED, size=9)


# ---------------------------------------------------------------------------
# TRANSACTIONS
# ---------------------------------------------------------------------------

def _build_transactions(wb, config: BudgetConfig, transactions) -> None:
    ws = wb.create_sheet("Transactions")
    _header(ws, 1, TX_COLUMNS)
    ws.freeze_panes = "A2"
    for column, width in zip("ABCDEF", (14, 38, 18, 18, 14, 18)):
        ws.column_dimensions[column].width = width
    ws.row_dimensions[1].height = 22

    for name, letter in (("tx_date", "A"), ("tx_desc", "B"), ("tx_type", "C"), ("tx_cat", "D"), ("tx_amt", "E"), ("tx_pay", "F")):
        _name(wb, name, _window(letter))

    money = money_format(config.currency)
    for row in range(FIRST_ROW, LAST_ROW + 1):
        ws.cell(row=row, column=1).number_format = "dd mmm yyyy"
        ws.cell(row=row, column=5).number_format = money
        for column in range(1, 7):
            ws.cell(row=row, column=column).border = BOX

    def validation(kind, formula, column, **options):
        dv = DataValidation(type=kind, formula1=formula, allow_blank=True, showErrorMessage=True, **options)
        dv.add(f"{column}{FIRST_ROW}:{column}{LAST_ROW}")
        ws.add_data_validation(dv)

    validation("list", "lst_types", "C", errorTitle="Type", error="Pick one of the types in the list.")
    validation("list", "lst_categories", "D", errorTitle="Category",
               error="Pick a category from the list (add new ones on the Settings sheet).")
    validation("list", "lst_payment", "F", errorTitle="Payment method",
               error="Pick a payment method from the list (add new ones on the Settings sheet).")
    validation("date", "36526", "A", operator="greaterThanOrEqual", errorTitle="Date", error="Enter a real date.")
    validation("decimal", "0", "E", operator="greaterThanOrEqual", errorTitle="Amount",
               error="Type the amount as a positive number. The Type says if it is money in or out.")

    area = f"A{FIRST_ROW}:F{LAST_ROW}"
    ws.conditional_formatting.add(area, FormulaRule(
        formula=[f'AND(ISNUMBER($A{FIRST_ROW}),OR($A{FIRST_ROW}<month_start,$A{FIRST_ROW}>month_end))'], fill=GREY_FILL,
        font=Font(color="6B7280")))
    ws.conditional_formatting.add(f"D{FIRST_ROW}:D{LAST_ROW}", FormulaRule(
        formula=[f'AND($C{FIRST_ROW}="{EXPENSE}",ISNUMBER($E{FIRST_ROW}),$D{FIRST_ROW}="")'], fill=ORANGE_FILL))

    if transactions is None or len(transactions) == 0:
        return
    if len(transactions) > TRANSACTION_ROWS:
        raise ValueError(f"The Transactions sheet holds {TRANSACTION_ROWS} rows; this data has {len(transactions)}.")
    for offset, (_, record) in enumerate(transactions.iterrows()):
        row = FIRST_ROW + offset
        when = record.get("Date")
        if isinstance(when, (date, datetime)) and not is_blank(when):
            ws.cell(row=row, column=1, value=when if isinstance(when, datetime) else datetime(when.year, when.month, when.day))
            if "date filled down" in str(record.get("Flags", "")):
                ws.cell(row=row, column=1).fill = YELLOW_FILL
                ws.cell(row=row, column=1).comment = Comment("Date filled down from the row above when the file was imported.", "Budget Builder")
        description = record.get("Description")
        if not is_blank(description):
            _text(ws.cell(row=row, column=2), str(description))
        for column, key in ((3, "Type"), (4, "Category"), (6, "Payment method")):
            value = record.get(key)
            if not is_blank(value):
                _text(ws.cell(row=row, column=column), str(value))
        amount = record.get("Amount")
        if not is_blank(amount):
            ws.cell(row=row, column=5, value=float(amount))


# ---------------------------------------------------------------------------
# DASHBOARD
# ---------------------------------------------------------------------------

def _in_month(extra: str = "") -> str:
    return f'tx_date,">="&month_start,tx_date,"<="&month_end{extra}'


def _sum_type(type_name: str) -> str:
    return f'=SUMIFS(tx_amt,tx_type,"{type_name}",{_in_month()})'


def _build_dashboard(wb, config: BudgetConfig) -> None:
    ws = wb.create_sheet("Dashboard")
    money = money_format(config.currency)
    ws.sheet_view.showGridLines = False
    for column, width in zip("ABCDEFGHIJKLM", (44, 16, 16, 16, 12, 12, 11, 3, 8, 22, 16, 14, 3)):
        ws.column_dimensions[column].width = width

    ws["A1"] = "Budget Dashboard"
    ws["A1"].font = Font(bold=True, size=20, color=INK)
    ws["A2"] = '="Month: "&TEXT(month_start,"mmmm yyyy")'
    ws["A2"].font = Font(size=12, color=MUTED)

    # --- summary cards ---
    _header(ws, 4, ["Summary", "Amount"])
    summary = [
        (5, "Total income", _sum_type(INCOME)),
        (6, "Total expenses", _sum_type(EXPENSE)),
        (7, "Loan payments", _sum_type(LOAN_PAYMENT)),
        (8, "Loans received", _sum_type(LOAN_RECEIVED)),
        (9, "Total saved this month", _sum_type(SAVING)),
        (10, "Net balance (after savings)", "=B5+B8-B6-B7-B9"),
        (11, "Opening balance", "=opening"),
        (12, "Closing balance", "=B11+B10"),
        (13, "Card bill payments (not counted as spending)", _sum_type(CARD_BILL)),
    ]
    for row, label, formula in summary:
        ws.cell(row=row, column=1, value=label)
        ws.cell(row=row, column=2, value=formula).number_format = money
        for column in (1, 2):
            ws.cell(row=row, column=column).border = BOX
            ws.cell(row=row, column=column).fill = CARD_FILL
    for column in (1, 2):
        ws.cell(row=9, column=column).fill = SAVED_FILL          # savings: a separate card, not spending
        ws.cell(row=12, column=column).font = Font(bold=True, size=12)
        ws.cell(row=10, column=column).font = Font(bold=True)
        ws.cell(row=13, column=column).font = Font(italic=True, color=MUTED)
    ws.conditional_formatting.add("B10:B12", FormulaRule(formula=["B10<0"], font=Font(bold=True, color="B91C1C")))

    # --- data checks ---
    _header(ws, 4, ["Data checks", "", "Rows"], 4)
    ws.merge_cells("D4:E4")
    checks = [
        (5, "Rows outside this month (ignored)",
         '=COUNTIFS(tx_amt,">=0",tx_date,"<"&month_start,tx_date,">0")+COUNTIFS(tx_amt,">=0",tx_date,">"&month_end)'),
        (6, "Rows with an amount but no date (ignored)", '=COUNT(tx_amt)-COUNTIFS(tx_amt,">=0",tx_date,">0")'),
        (7, "Rows with an amount but no Type (ignored)", "=SUMPRODUCT(ISNUMBER(tx_amt)*(tx_type=\"\"))"),
        (8, "Expense rows with no category", f'=SUMPRODUCT((tx_type="{EXPENSE}")*ISNUMBER(tx_amt)*(tx_cat=""))'),
        (9, "Expense amount with no category", f"=B6-C{CATEGORY_ROW_1 + MAX_CATEGORIES}"),
    ]
    for row, label, formula in checks:
        ws.cell(row=row, column=4, value=label)
        cell = ws.cell(row=row, column=6, value=formula)
        cell.number_format = money if row == 9 else "0"
        ws.merge_cells(start_row=row, start_column=4, end_row=row, end_column=5)
        for column in (4, 5, 6):
            ws.cell(row=row, column=column).border = BOX
    ws.conditional_formatting.add("F5:F9", FormulaRule(formula=["F5>0"], fill=ORANGE_FILL))
    notes = [
        "How credit cards are counted: a purchase with a credit card is an Expense on the day you buy it.",
        "Paying the card bill later is a 'Card bill payment': it only moves money, so it is NOT counted as",
        "spending and NOT in the net balance (it has its own line on the left). Use 'Loan payment' only for real loans / EMIs.",
    ]
    ws.merge_cells("D11:G15")
    note = ws["D11"]
    note.value = " ".join(notes)
    note.font = Font(italic=True, size=9, color=MUTED)
    note.alignment = Alignment(wrap_text=True, vertical="top")

    # --- spending vs budget ---
    ws.cell(row=CATEGORY_ROW_1 - 2, column=1, value="Spending vs budget").font = Font(bold=True, size=13, color=INK)
    _header(ws, CATEGORY_ROW_1 - 1, ["Category", "Budget", "Spent", "Remaining", "% used", "Status", "rank key"])
    ws.cell(row=CATEGORY_ROW_1 - 1, column=7).font = Font(color="9CA3AF", size=8)
    last_cat_row = CATEGORY_ROW_1 + MAX_CATEGORIES - 1
    for offset in range(MAX_CATEGORIES):
        row, source = CATEGORY_ROW_1 + offset, SETTINGS_FIRST + offset
        ws.cell(row=row, column=1, value=f'=IF(Settings!G{source}="","",Settings!G{source})')
        ws.cell(row=row, column=2, value=f'=IF(A{row}="","",Settings!H{source})').number_format = money
        ws.cell(row=row, column=3, value=(
            f'=IF(A{row}="","",SUMIFS(tx_amt,tx_type,"{EXPENSE}",tx_cat,A{row},{_in_month()}))')).number_format = money
        ws.cell(row=row, column=4, value=f'=IF(A{row}="","",B{row}-C{row})').number_format = money
        ws.cell(row=row, column=5, value=f'=IF(A{row}="","",IF(B{row}=0,"",C{row}/B{row}))').number_format = "0%"
        ws.cell(row=row, column=6, value=(
            f'=IF(A{row}="","",IF(B{row}=0,IF(C{row}>0,"No budget",""),IF(C{row}>B{row},"OVER","OK")))'))
        key = ws.cell(row=row, column=7, value=f'=IF(A{row}="","",C{row}-ROW()/1000000000)')
        key.font, key.number_format = Font(color="9CA3AF", size=8), "0.00"
        for column in range(1, 7):
            ws.cell(row=row, column=column).border = BOX
    total_row = last_cat_row + 1
    ws.cell(row=total_row, column=1, value="Total of the categories").font = Font(bold=True)
    for column, letter in ((2, "B"), (3, "C"), (4, "D")):
        cell = ws.cell(row=total_row, column=column, value=f"=SUM({letter}{CATEGORY_ROW_1}:{letter}{last_cat_row})")
        cell.number_format, cell.font = money, Font(bold=True)
    ws.cell(row=total_row + 1, column=1, value="Expenses with no category")
    ws.cell(row=total_row + 1, column=3, value=f"=B6-C{total_row}").number_format = money
    area = f"A{CATEGORY_ROW_1}:F{last_cat_row}"
    ws.conditional_formatting.add(area, FormulaRule(
        formula=[f'AND($A{CATEGORY_ROW_1}<>"",$B{CATEGORY_ROW_1}>0,$C{CATEGORY_ROW_1}>$B{CATEGORY_ROW_1})'],
        fill=RED_FILL, font=Font(bold=True, color="B91C1C")))
    ws.conditional_formatting.add(f"E{CATEGORY_ROW_1}:E{last_cat_row}", DataBarRule(
        start_type="num", start_value=0, end_type="num", end_value=1, color="93C5FD"))

    # --- top 3 ---
    ws["I3"] = "Top 3 spending categories"
    ws["I3"].font = Font(bold=True, size=13, color=INK)
    _header(ws, 4, ["#", "Category", "Spent"], 9)
    key_range = f"$G${CATEGORY_ROW_1}:$G${last_cat_row}"
    name_range = f"$A${CATEGORY_ROW_1}:$A${last_cat_row}"
    for offset in range(3):
        row = 5 + offset
        rank = f"(ROW($H{row})-ROW($H$4))"          # column H is an empty spacer: ROW() of a cell that is not this one
        ws.cell(row=row, column=9, value=f'="#"&{rank}')
        spent_at = f"INDEX($C${CATEGORY_ROW_1}:$C${last_cat_row},MATCH(LARGE({key_range},{rank}),{key_range},0))"
        ws.cell(row=row, column=11, value=f'=IFERROR(IF({spent_at}=0,"",{spent_at}),"")').number_format = money
        ws.cell(row=row, column=10, value=(
            f'=IF(K{row}="","",INDEX({name_range},MATCH(LARGE({key_range},{rank}),{key_range},0)))'))
        for column in (9, 10, 11):
            ws.cell(row=row, column=column).border = BOX

    # --- daily totals ---
    ws.cell(row=DAILY_ROW_1 - 2, column=9, value="Daily totals").font = Font(bold=True, size=13, color=INK)
    _header(ws, DAILY_ROW_1 - 1, ["Date", "", "Income", "Expenses", "Net"], 9)
    ws.merge_cells(start_row=DAILY_ROW_1 - 1, start_column=9, end_row=DAILY_ROW_1 - 1, end_column=10)
    last_day_row = DAILY_ROW_1 + 30
    for offset in range(31):
        row = DAILY_ROW_1 + offset
        day = f"(month_start+ROW($H{row})-ROW($H${DAILY_ROW_1 - 1})-1)"
        ws.cell(row=row, column=9, value=f'=IF({day}>month_end,"",{day})').number_format = "dd mmm"
        ws.merge_cells(start_row=row, start_column=9, end_row=row, end_column=10)
        ws.cell(row=row, column=11, value=f'=IF(I{row}="","",SUMIFS(tx_amt,tx_type,"{INCOME}",tx_date,I{row}))').number_format = money
        ws.cell(row=row, column=12, value=f'=IF(I{row}="","",SUMIFS(tx_amt,tx_type,"{EXPENSE}",tx_date,I{row}))').number_format = money
        ws.cell(row=row, column=13, value=f'=IF(I{row}="","",K{row}-L{row})').number_format = money
        for column in (9, 10, 11, 12, 13):
            ws.cell(row=row, column=column).border = BOX
    ws.column_dimensions["M"].width = 14
    ws.freeze_panes = "A4"
    _add_chart_helper(ws, len(config.categories), total_row)
    _add_charts(ws, len(config.categories), last_day_row)


def _add_chart_helper(ws, category_count: int, total_row: int) -> None:
    """The doughnut's own small table (columns AA:AC, formulas only): the categories ranked by spending, with the name left
    blank - and so no label - for any slice under chart_min_share of the total, plus one 'Other' slice that collects them.
    Not hidden: Excel does not draw data from hidden columns."""
    count = max(1, min(category_count, MAX_CATEGORIES))
    name_col, value_col, spent_col = (get_column_letter(CHART_HELPER_COLUMN + i) for i in range(3))
    header_row, first = CATEGORY_ROW_1 - 1, CATEGORY_ROW_1
    last = first + count - 1
    other_row = last + 1
    grey = Font(color="9CA3AF", size=8)
    ws.cell(row=header_row - 1, column=CHART_HELPER_COLUMN, value="Chart data (calculated, do not edit)").font = grey
    for offset, label in enumerate(("Label", "Spent", "Rank spent")):
        cell = ws.cell(row=header_row, column=CHART_HELPER_COLUMN + offset, value=label)
        cell.font = grey
    keys = f"$G${CATEGORY_ROW_1}:$G${CATEGORY_ROW_1 + MAX_CATEGORIES - 1}"
    names_range = f"$A${CATEGORY_ROW_1}:$A${CATEGORY_ROW_1 + MAX_CATEGORIES - 1}"
    spent_range = f"$C${CATEGORY_ROW_1}:$C${CATEGORY_ROW_1 + MAX_CATEGORIES - 1}"
    total = f"$C${total_row}"
    for row in range(first, last + 1):
        rank = f"(ROW($H{row})-ROW($H${header_row}))"
        position = f"MATCH(LARGE({keys},{rank}),{keys},0)"
        ws.cell(row=row, column=CHART_HELPER_COLUMN + 2, value=f"=IFERROR(INDEX({spent_range},{position}),0)")
        ws.cell(row=row, column=CHART_HELPER_COLUMN, value=(
            f'=IF({total}<=0,"",IF({spent_col}{row}/{total}>=chart_min_share,IFERROR(INDEX({names_range},{position}),""),""))'))
        ws.cell(row=row, column=CHART_HELPER_COLUMN + 1, value=f'=IF({name_col}{row}="",0,{spent_col}{row})')
    rest = f"{total}-SUM({value_col}{first}:{value_col}{last})"
    ws.cell(row=other_row, column=CHART_HELPER_COLUMN, value=(
        f'=IF({total}<=0,"",IF(({rest})/{total}>=chart_min_share,"Small categories (under "&TEXT(chart_min_share,"0%")&")",""))'))
    ws.cell(row=other_row, column=CHART_HELPER_COLUMN + 1, value=f"=MAX(0,{rest})")
    for row in range(first, other_row + 1):
        for column in range(CHART_HELPER_COLUMN, CHART_HELPER_COLUMN + 3):
            cell = ws.cell(row=row, column=column)
            cell.font = grey
            if column > CHART_HELPER_COLUMN:
                cell.number_format = "0.00"
    for offset, width in enumerate((22, 10, 10)):
        ws.column_dimensions[get_column_letter(CHART_HELPER_COLUMN + offset)].width = width


def _add_charts(ws, category_count: int, last_day_row: int) -> None:
    count = max(1, min(category_count, MAX_CATEGORIES))
    last = CATEGORY_ROW_1 + count - 1
    categories = Reference(ws, min_col=1, min_row=CATEGORY_ROW_1, max_row=last)

    bars = BarChart()
    bars.type, bars.title, bars.style = "col", "Budget vs spent, by category", 10
    bars.add_data(Reference(ws, min_col=2, max_col=3, min_row=CATEGORY_ROW_1 - 1, max_row=last), titles_from_data=True)
    bars.set_categories(categories)
    bars.y_axis.title, bars.height, bars.width = "Amount", 8.5, 17
    bars.x_axis.delete = bars.y_axis.delete = False
    ws.add_chart(bars, "O4")

    daily = BarChart()
    daily.type, daily.title, daily.style = "col", "Expenses per day", 12
    daily.add_data(Reference(ws, min_col=12, min_row=DAILY_ROW_1 - 1, max_row=last_day_row), titles_from_data=True)
    daily.set_categories(Reference(ws, min_col=9, min_row=DAILY_ROW_1, max_row=last_day_row))
    daily.legend, daily.height, daily.width = None, 8.5, 17
    daily.x_axis.number_format, daily.x_axis.delete, daily.y_axis.delete = "dd", False, False
    ws.add_chart(daily, "O22")

    share = DoughnutChart()
    share.title, share.height, share.width = "Spending by category", 9.5, 17
    helper_last = last + 1                                          # one extra row: the 'Other' slice
    share.add_data(Reference(ws, min_col=CHART_HELPER_COLUMN + 1, min_row=CATEGORY_ROW_1 - 1, max_row=helper_last),
                   titles_from_data=True)
    share.set_categories(Reference(ws, min_col=CHART_HELPER_COLUMN, min_row=CATEGORY_ROW_1, max_row=helper_last))
    share.holeSize = 50
    labels = DataLabelList()
    labels.showCatName, labels.showPercent = True, True              # only the name and the percentage ...
    labels.showVal = labels.showSerName = labels.showLegendKey = False       # (no leader-line element: openpyxl would write it after "separator", the wrong order)
    labels.separator = "\n"
    labels.numFmt = '[<0.005]"";0%'                                  # ... and nothing at all for an empty slice
    share.dataLabels = labels
    share.legend.position = "r"                                      # the legend sits on the right, off the ring
    ws.add_chart(share, "O40")


# ---------------------------------------------------------------------------
# HOW TO USE + IMPORT NOTES
# ---------------------------------------------------------------------------

HOW_TO_USE = [
    ("1. Set up", "Open the Settings sheet. Choose the month, your opening balance, your categories with a monthly "
                  "budget for each, and your payment methods. Change only the yellow cells."),
    ("2. Add every transaction", "Go to the Transactions sheet. One row per transaction: Date, Description, Type, "
                                 "Category, Amount, Payment method. Use the dropdown arrows. Always type the amount "
                                 "as a positive number; the Type says if money comes in or goes out."),
    ("3. Pick the right Type", "Income = money you earn (Salary 5000).   Expense = spending (Lunch 30).   "
                               "Loan received = money you borrowed (Borrowed from a friend 500).   "
                               "Loan payment = paying back a real loan (Car EMI 800).   "
                               "Saving = money you put aside (Transfer to savings 300).   "
                               "Card bill payment = paying your credit card bill (Visa bill 600)."),
    ("4. Credit cards", "When you buy something with a credit card, add it as an Expense on that day. "
                        "When you later pay the card bill, add it as a Card bill payment. It is not counted as "
                        "spending and not in the net balance, so nothing is counted twice. "
                        "The Dashboard shows it on its own line."),
    ("5. Read the Dashboard", "Do not type on the Dashboard. It is all formulas and updates by itself: "
                              "totals, closing balance, spending against each budget, your top 3 categories, "
                              "daily totals and charts. Savings have their own box so they do not look like spending."),
    ("6. Colors", "Yellow date = the date was copied down from the row above when the file was imported.   "
                  "Grey row = a date outside the month (ignored by the Dashboard).   "
                  "Orange category = an expense with no category.   "
                  "Red on the Dashboard = you are over budget."),
    ("7. Check the data checks", "On the Dashboard, the Data checks box counts rows that are ignored or incomplete "
                                 "(no date, no Type, no category, outside the month). Fix them on Transactions "
                                 "until the numbers are 0."),
    ("8. Next month", "Open the app, choose Build my budget sheet, and upload this file. It starts a new empty "
                      "Transactions sheet, uses this month's closing balance as the new opening balance, and keeps "
                      "your categories, budgets, payment methods and keywords."),
]


def _build_how_to_use(wb) -> None:
    ws = wb.active
    ws.title = "How to use"
    ws.sheet_view.showGridLines = False
    ws["A1"] = "How to use this budget sheet"
    ws["A1"].font = Font(bold=True, size=18, color=INK)
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 110
    for offset, (title, text) in enumerate(HOW_TO_USE):
        row = 3 + offset
        ws.cell(row=row, column=1, value=title).font = Font(bold=True, color=ACCENT, size=12)
        body = ws.cell(row=row, column=2, value=text)
        body.alignment = Alignment(wrap_text=True, vertical="top")
        ws.cell(row=row, column=1).alignment = Alignment(vertical="top")
        ws.row_dimensions[row].height = 18 * max(1, -(-len(text) // 105)) + 6


def _build_import_notes(wb, import_info) -> None:
    ws = wb.create_sheet("Import Notes")
    ws.column_dimensions["A"].width = 14
    for column, width in zip("BCDE", (44, 14, 30, 60)):
        ws.column_dimensions[column].width = width
    ws["A1"] = "What happened when your file was imported"
    ws["A1"].font = Font(bold=True, size=14, color=INK)
    stats = import_info.stats
    lines = [
        f"Rows in the source sheet: {stats.get('source_rows', 0)}   |   kept: {stats.get('rows', 0)}   |   "
        f"dropped: {stats.get('dropped', 0)}   |   blank: {stats.get('blank_rows', 0)}",
        f"Dates filled down from the row above (yellow in Transactions): {stats.get('fill_down', 0)}",
        f"Rows with no date at all: {stats.get('no_date', 0)}",
        f"Expense rows still without a category: {stats.get('uncategorised', 0)}",
        *import_info.notes,
    ]
    for offset, line in enumerate(lines):
        _text(ws.cell(row=3 + offset, column=1), line)
    row = 4 + len(lines)
    ws.cell(row=row, column=1, value="Rows with flags").font = Font(bold=True)
    _header(ws, row + 1, ["Source row", "Description", "Date", "Type / Category", "Flags"])
    row += 2
    flagged = import_info.rows[import_info.rows["Flags"].astype(str).str.len() > 0]
    for _, record in flagged.iterrows():
        ws.cell(row=row, column=1, value=int(record["Source row"]) if not is_blank(record["Source row"]) else "other sheet")
        _text(ws.cell(row=row, column=2), str(record["Description"]))
        if isinstance(record["Date"], date):
            ws.cell(row=row, column=3, value=record["Date"]).number_format = "dd mmm yyyy"
        _text(ws.cell(row=row, column=4), f"{record['Type']} / {record['Category'] or '-'}")
        _text(ws.cell(row=row, column=5), str(record["Flags"]))
        row += 1
    row += 1
    ws.cell(row=row, column=1, value="Rows that were dropped (not in Transactions)").font = Font(bold=True)
    _header(ws, row + 1, ["Source row", "Description", "Amount", "", "Reason"])
    row += 2
    for _, record in import_info.dropped.iterrows():
        ws.cell(row=row, column=1, value=int(record["Source row"]))
        _text(ws.cell(row=row, column=2), str(record["Description"]))
        amount = record["Amount"]
        if not is_blank(amount):
            ws.cell(row=row, column=3, value=float(amount))
        _text(ws.cell(row=row, column=5), str(record["Reason"]))
        row += 1


# ---------------------------------------------------------------------------
# THE WORKBOOK
# ---------------------------------------------------------------------------

def build_workbook(config: BudgetConfig, keyword_map: dict, transactions: pd.DataFrame = None, import_info=None) -> bytes:
    """The finished .xlsx as bytes. `transactions` has the TX_COLUMNS (+ optional Flags); None = a blank sheet."""
    problems = config.validate()
    if problems:
        raise ValueError(" ".join(problems))
    wb = Workbook()
    _build_how_to_use(wb)
    _build_dashboard(wb, config)
    _build_transactions(wb, config, transactions)
    _build_settings(wb, config)
    _build_keywords(wb, keyword_map)
    if import_info is not None:
        _build_import_notes(wb, import_info)
    wb.active = 1                                              # open on the Dashboard
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
