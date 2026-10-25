"""
budget/importer.py
-------------------
Turns a messy personal-budget sheet (a daily expense log) into clean transactions:

  1. find the header row and which column is the date / description / amount / ...
  2. drop hardcoded total rows (kept in a list, so the user can restore them)
  3. fill missing dates down from the row above - and flag every filled cell
  4. parse dates and amounts, decide each row's Type and Category
  5. list the descriptions that no keyword matched, for the user to assign

Nothing is silently lost: every source row ends up either in `rows` or in `dropped`
(blank rows are counted in `stats["blank_rows"]`).
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd
from dateutil import parser as dateutil_parser

from budget import categorize
from budget.model import (CARD_BILL, EXPENSE, INCOME, LOAN_PAYMENT, LOAN_RECEIVED, SAVING, TX_COLUMNS, TYPES)
from locales import LOCALES
from quality import is_blank, parse_number
from rule_checks import detect_date_order

HEADER_SCAN_ROWS = 40

# Words (casefolded) that identify a column from its header text.
HEADER_WORDS = {
    "date": ("date", "day", "dt", "transaction date", "txn date", "when"),
    "description": ("description", "item", "items", "details", "detail", "particulars", "narration", "note", "notes",
                    "memo", "purpose", "expense", "expenses", "spent on", "name", "what", "transaction"),
    "amount": ("amount", "cost", "price", "spent", "value", "amt", "expense amount", "sum"),
    "debit": ("debit", "withdrawal", "money out", "paid out", "out"),
    "credit": ("credit", "deposit", "money in", "paid in", "income", "in"),
    "category": ("category", "categories", "expense type", "type of expense", "group"),
    "type": ("type", "transaction type", "kind", "txn type"),
    "payment": ("payment", "payment method", "payment mode", "mode", "method", "paid by", "paid with", "account", "via"),
}
TOTAL_WORDS = re.compile(r"^\s*(grand\s+|sub\s*|daily\s+|day\s+|weekly\s+|monthly\s+)?(total|totals|sum|balance)\b|"
                         r"\b(carried|brought)\s+forward\b|^\s*(closing|opening)\s+balance\b", re.IGNORECASE)

TYPE_WORDS = {
    INCOME: ("income", "salary", "earning", "earnings", "credit", "cr", "received", "in"),
    EXPENSE: ("expense", "expenses", "spend", "spent", "debit", "dr", "out", "payment"),
    LOAN_RECEIVED: ("loan received", "loan in", "borrowed", "borrow"),
    LOAN_PAYMENT: ("loan payment", "loan", "emi", "loan repayment", "repayment"),
    SAVING: ("saving", "savings", "invest", "investment", "deposit to savings"),
    CARD_BILL: ("card bill", "card bill payment", "credit card bill", "credit card payment", "card payment"),
}

_CURRENCY_TEXT = re.compile(r"(?i)(rs\.?|pkr|inr|aed|usd|eur|gbp|dhs?|dirhams?|[$£€₹]|/-|only)")
_EXCEL_SERIAL_RANGE = (20000, 80000)
_NUMERIC_TEXT_DATE = re.compile(r"^\s*\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}\s*$")     # 03/04/2026 - the only text that needs a day/month decision


@dataclass
class ImportResult:
    rows: pd.DataFrame            # TX_COLUMNS + "Flags" + "Source row"
    dropped: pd.DataFrame         # "Source row", "Description", "Amount", "Reason"
    unknown: pd.DataFrame         # "Description", "Count", "Total" - expenses no keyword matched
    new_categories: list          # categories found in the file's own Category column that the map does not know
    layout: dict
    stats: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# READING
# ---------------------------------------------------------------------------

def read_raw_sheets(file_bytes: bytes, is_csv: bool = False) -> dict:
    """{sheet name: DataFrame of raw cell values, no header}, formulas read as their last calculated value."""
    import io
    if is_csv:
        return {"Sheet1": pd.read_csv(io.BytesIO(file_bytes), header=None, dtype=object, keep_default_na=False,
                                      na_values=[""], skip_blank_lines=False)}
    return pd.read_excel(io.BytesIO(file_bytes), sheet_name=None, header=None, dtype=object,
                         keep_default_na=False, na_values=[""])


# ---------------------------------------------------------------------------
# LAYOUT
# ---------------------------------------------------------------------------

# Words that name a role even inside a longer header ("Amount (AED)", "Description / What did you spend on?").
# Ordered: the first role with a hit wins. Short or common words are left out on purpose.
HEADER_TOKENS = (
    ("payment", ("payment", "paid")), ("category", ("category", "categories")),
    ("amount", ("amount", "cost", "price", "amt")), ("debit", ("debit", "withdrawal")), ("credit", ("credit", "deposit")),
    ("date", ("date",)), ("type", ("type",)),
    ("description", ("description", "details", "particulars", "narration", "item", "items")),
)


def _header_role(text) -> str:
    """The column role a header cell names ('' if none): the whole cell matches a known header, or it contains a
    clear key word ("Amount (AED)" is an amount, "Payment Method" a payment method)."""
    key = re.sub(r"[^a-z ]", " ", str(text).casefold())
    key = re.sub(r"\s+", " ", key).strip()
    for role, words in HEADER_WORDS.items():
        if key in words:
            return role
    tokens = set(key.split())
    for role, words in HEADER_TOKENS:
        if tokens & set(words):
            return role
    return ""


def _numeric_share(values: pd.Series) -> float:
    real = [v for v in values if not is_blank(v)]
    return sum(parse_amount(v) is not None for v in real) / len(real) if real else 0.0


def detect_layout(raw: pd.DataFrame):
    """Find the header row and the role of each column, or None when no header row looks right.
    Returns {"header_row": int (0-based), "columns": {role: column index}, "ignored": [header texts]}."""
    best = None
    for row_index in range(min(HEADER_SCAN_ROWS, len(raw))):
        found = {}
        for column_index, value in enumerate(raw.iloc[row_index]):
            role = _header_role(value) if not is_blank(value) else ""
            if role:
                found.setdefault(role, []).append(column_index)
        has_money = bool(set(found) & {"amount", "debit", "credit"}) or len(found.get("description", [])) > 1
        if ("date" in found or "description" in found) and has_money:
            score = sum(len(indices) for indices in found.values())
            if best is None or score > best[0]:
                best = (score, row_index, found)
    if best is None:
        return None
    _, header_row, found = best
    body = raw.iloc[header_row + 1:]
    columns = {role: indices[0] for role, indices in found.items()}

    # "Expense" can head a description column or an amount column: decide from the cells below it.
    descriptions = found.get("description", [])
    numeric = [i for i in descriptions if _numeric_share(body.iloc[:, i]) > 0.8]
    texty = [i for i in descriptions if i not in numeric]
    if numeric and "amount" not in columns:
        columns["amount"] = numeric[0]
    if texty:
        columns["description"] = texty[0]
    elif "description" in columns and columns["description"] in numeric:
        del columns["description"]
    used = set(columns.values())
    ignored = [str(raw.iat[header_row, i]).strip() for i in range(raw.shape[1])
               if i not in used and not is_blank(raw.iat[header_row, i])]
    return {"header_row": header_row, "columns": columns, "ignored": ignored}


def best_sheet(raw_sheets: dict):
    """(sheet name, layout) of the sheet that looks most like a transaction log, or (None, None)."""
    best = (None, None, 0)
    for name, raw in raw_sheets.items():
        layout = detect_layout(raw)
        if layout and len(raw) - layout["header_row"] > best[2]:
            best = (name, layout, len(raw) - layout["header_row"])
    return best[0], best[1]


# ---------------------------------------------------------------------------
# VALUE PARSING
# ---------------------------------------------------------------------------

def parse_amount(value):
    """A number from an amount cell: 1200, '1,200', 'Rs. 1,200/-', '$45.50', '(45)' (negative). None if not an amount."""
    if is_blank(value) or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if pd.isna(value) else float(value)
    text = str(value).strip()
    negative = text.startswith("(") and text.endswith(")") or text.startswith("-")
    cleaned = _CURRENCY_TEXT.sub("", text).strip().strip("()-").strip()
    cleaned = cleaned.replace(" ", "")
    number = parse_number(cleaned) if cleaned else None
    if number is None and re.fullmatch(r"\d{1,3}(,\d{3})*(\.\d+)?|\d+(\.\d+)?", cleaned or ""):
        number = float(cleaned.replace(",", ""))          # e.g. '050' or very long digit strings are fine for money
    if number is None:
        return None
    return -abs(number) if negative else number


def _to_date(value, day_first: bool, default_year: int):
    """A date from a cell (datetime, Excel serial number or text), or None."""
    if is_blank(value):
        return None
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        if _EXCEL_SERIAL_RANGE[0] <= value <= _EXCEL_SERIAL_RANGE[1]:
            return (pd.Timestamp("1899-12-30") + pd.Timedelta(days=int(value))).date()
        return None
    text = str(value).strip()
    try:
        # dateutil fills anything missing (a year, for "3 Jun") from `default`
        year_first = bool(re.match(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", text))
        parsed = dateutil_parser.parse(text, dayfirst=day_first and not year_first, yearfirst=year_first,
                                       default=datetime(default_year, 1, 1))
    except (ValueError, OverflowError, TypeError):
        return None
    return parsed.date()


def _day_of_month_column(values) -> bool:
    """True when a date column holds only day numbers (1..31) - then the month comes from the settings."""
    real = [v for v in values if not is_blank(v)]
    return bool(real) and all(isinstance(v, (int, float)) and not isinstance(v, bool) and float(v).is_integer()
                              and 1 <= v <= 31 for v in real)


def _clean_text(value) -> str:
    return "" if is_blank(value) else re.sub(r"\s+", " ", str(value)).strip()


def _type_from_text(text: str):
    key = re.sub(r"\s+", " ", text.casefold()).strip()
    for type_name in TYPES:
        if key == type_name.casefold():
            return type_name
    for type_name, words in TYPE_WORDS.items():
        if key in words:
            return type_name
    return None


# ---------------------------------------------------------------------------
# THE IMPORT
# ---------------------------------------------------------------------------

def import_transactions(raw: pd.DataFrame, layout: dict, keyword_map: dict, locale_code: str = "PK",
                        month_start: date = None) -> ImportResult:
    """Clean one raw sheet into transactions. `month_start` (the 1st of the target month) supplies the month/year
    for day-number-only dates and for dates written without a year."""
    columns = layout["columns"]
    body = raw.iloc[layout["header_row"] + 1:]
    default_year = (month_start or date.today()).year
    notes = []

    def cell(row, role):
        index = columns.get(role)
        return row.iloc[index] if index is not None else None

    # --- how to read the dates -----------------------------------------------------------
    date_values = list(body.iloc[:, columns["date"]]) if "date" in columns else []
    day_numbers_only = _day_of_month_column(date_values)
    day_first = LOCALES.get(locale_code, {}).get("day_first", True)
    text_dates = pd.Series([v for v in date_values if isinstance(v, str) and _NUMERIC_TEXT_DATE.match(v)], dtype=object)
    if len(text_dates):
        found = detect_date_order(text_dates)
        if found["order"] in ("day_first", "month_first"):
            day_first = found["order"] == "day_first"
            notes.append(f"Dates are {'day-first' if day_first else 'month-first'} "
                         f"({found['day_first_count'] or found['month_first_count']} values prove it).")
        else:
            notes.append(f"Text dates are ambiguous, so the locale default is used "
                         f"({'day-first' if day_first else 'month-first'}).")
    if day_numbers_only:
        if month_start:
            notes.append(f"The date column holds day numbers only; they are placed in {month_start:%B %Y}.")
        else:
            notes.append("The date column holds day numbers only; choose the month so they become real dates.")

    # --- the sign convention of a single amount column -------------------------------------
    amounts_seen = [parse_amount(cell(row, "amount")) for _, row in body.iterrows()] if "amount" in columns else []
    amounts_seen = [a for a in amounts_seen if a is not None]
    signed_log = any(a < 0 for a in amounts_seen) and any(a > 0 for a in amounts_seen)
    if signed_log:
        notes.append("The amount column mixes positive and negative numbers: negative = Expense, positive = Income.")

    kept, dropped = [], []
    blank_rows = 0
    previous_date = None
    running = []                     # amounts of kept rows, to recognise hardcoded totals
    stats = {"fill_down": 0, "no_date": 0, "defaulted_to_expense": 0, "dropped": 0, "blank_rows": 0}

    for index, row in body.iterrows():
        source_row = int(index) + 1
        description = _clean_text(cell(row, "description"))
        debit, credit = parse_amount(cell(row, "debit")), parse_amount(cell(row, "credit"))
        amount_raw = cell(row, "amount")
        amount = parse_amount(amount_raw)
        from_credit = False
        if amount is None and (debit is not None or credit is not None):
            amount, from_credit = (credit, True) if credit not in (None, 0) else (debit, False)
        date_cell = cell(row, "date")
        category_cell = _clean_text(cell(row, "category"))

        if not description and amount is None and is_blank(date_cell) and not category_cell:
            blank_rows += 1                   # nothing but, say, a pre-filled payment method: an empty template row
            continue

        def drop(reason):
            dropped.append({"Source row": source_row, "Description": description,
                            "Amount": amount, "Reason": reason})

        if TOTAL_WORDS.search(description):
            drop("Looks like a total or balance line")
            continue
        if amount is None:
            drop("No amount" + (" (a note or label line)" if description else ""))
            continue
        if not description and is_blank(date_cell):
            total = abs(amount)
            if len(running) >= 2 and any(abs(sum(running[-size:]) - total) < 0.005 for size in range(2, len(running) + 1)):
                drop("Hardcoded total: the amount equals the sum of the rows above")
                continue

        # --- date -----------------------------------------------------------------------
        flags = []
        if not is_blank(date_cell):
            if day_numbers_only:
                parsed = date(month_start.year, month_start.month, int(date_cell)) if month_start else None
                if parsed is None:
                    flags.append("needs month")
            else:
                parsed = _to_date(date_cell, day_first, default_year)
            if parsed is None and not (day_numbers_only and not month_start):
                flags.append("date not understood")
            if parsed is not None:
                previous_date = parsed
            row_date = parsed
        elif previous_date is not None:
            row_date = previous_date
            flags.append("date filled down")
            stats["fill_down"] += 1
        else:
            row_date = None
            flags.append("no date")
            stats["no_date"] += 1

        # --- type ---------------------------------------------------------------------------
        type_text = _clean_text(cell(row, "type"))
        row_type = _type_from_text(type_text) if type_text else None
        if row_type is None:
            row_type = categorize.match_type(description, keyword_map)
        if row_type is None and from_credit:
            row_type = INCOME
        if row_type is None and signed_log:
            row_type = INCOME if amount > 0 else EXPENSE
        if row_type is None:
            row_type = EXPENSE        # the normal case in an expense log: spending is the default, so it is not flagged
            stats["defaulted_to_expense"] += 1
            if type_text:
                flags.append(f"type '{type_text}' not understood")

        # --- category -----------------------------------------------------------------------
        category = None
        if row_type == EXPENSE:
            known = {name.casefold(): name for name in keyword_map["categories"]}
            if category_cell:
                category = known.get(category_cell.casefold(), category_cell)
            else:
                category = categorize.match_category(description, keyword_map)
            if category is None:
                flags.append("no category")

        kept.append({
            "Date": row_date, "Description": description or "(no description)", "Type": row_type, "Category": category,
            "Amount": abs(amount), "Payment method": _clean_text(cell(row, "payment")) or None,
            "Flags": "; ".join(flags), "Source row": source_row,
        })
        if not description:
            kept[-1]["Flags"] = "; ".join(filter(None, [kept[-1]["Flags"], "no description"]))
        running.append(abs(amount))

    rows = pd.DataFrame(kept, columns=[*TX_COLUMNS, "Flags", "Source row"])
    dropped_frame = pd.DataFrame(dropped, columns=["Source row", "Description", "Amount", "Reason"])
    dropped_frame["Amount"] = pd.to_numeric(dropped_frame["Amount"], errors="coerce")
    stats["dropped"] = len(dropped_frame)
    stats["blank_rows"] = blank_rows
    stats["rows"] = len(rows)
    stats["source_rows"] = len(rows) + len(dropped_frame) + blank_rows

    known_names = {name.casefold() for name in keyword_map["categories"]}
    new_categories = sorted({c for c in rows["Category"].dropna() if c.casefold() not in known_names}, key=str.casefold)
    stats["uncategorised"] = int(((rows["Type"] == EXPENSE) & rows["Category"].isna()).sum())
    return ImportResult(rows=rows, dropped=dropped_frame, unknown=unknown_descriptions(rows), new_categories=new_categories,
                        layout=layout, stats=stats, notes=notes)


def unknown_descriptions(rows: pd.DataFrame) -> pd.DataFrame:
    """Expense descriptions with no category: Description | Count | Total (largest total first)."""
    pending = rows[(rows["Type"] == EXPENSE) & rows["Category"].isna()]
    if pending.empty:
        return pd.DataFrame(columns=["Description", "Count", "Total"])
    grouped = (pending.assign(_key=pending["Description"].str.casefold())
               .groupby("_key", sort=False).agg(Description=("Description", "first"), Count=("Amount", "size"),
                                                 Total=("Amount", "sum")).reset_index(drop=True))
    return grouped.sort_values(["Total", "Description"], ascending=[False, True]).reset_index(drop=True)


def categorize_rows(rows: pd.DataFrame, keyword_map: dict) -> pd.DataFrame:
    """Re-run the keyword map on expense rows still without a category (after the user edits the map).
    Flags are updated; rows the user already categorised are never touched."""
    rows = rows.copy()
    for index, row in rows.iterrows():
        if row["Type"] == EXPENSE and is_blank(row["Category"]):
            found = categorize.match_category(row["Description"], keyword_map)
            if found:
                rows.at[index, "Category"] = found
                rows.at[index, "Flags"] = "; ".join(f for f in str(row["Flags"]).split("; ") if f and f != "no category")
    return rows


def assign_category(rows: pd.DataFrame, description: str, category: str) -> pd.DataFrame:
    """Give every uncategorised expense row with this description (ignoring capitals) the category."""
    rows = rows.copy()
    mask = (rows["Type"] == EXPENSE) & rows["Category"].isna() & (rows["Description"].str.casefold() == description.casefold())
    rows.loc[mask, "Category"] = category
    rows.loc[mask, "Flags"] = rows.loc[mask, "Flags"].map(
        lambda text: "; ".join(f for f in str(text).split("; ") if f and f != "no category"))
    return rows


def most_common_month(rows: pd.DataFrame):
    """The first of the month that most rows fall in, or None when no row has a date."""
    dates = pd.to_datetime(rows["Date"], errors="coerce").dropna()
    if dates.empty:
        return None
    month = dates.dt.to_period("M").value_counts().idxmax()
    return date(month.year, month.month, 1)


def flag_outside_month(rows: pd.DataFrame, month_start: date) -> pd.DataFrame:
    """Add/remove the 'outside month' flag for the chosen month (rows with no date are not outside)."""
    rows = rows.copy()
    next_month = date(month_start.year + (month_start.month == 12), month_start.month % 12 + 1, 1)

    def update(row):
        parts = [f for f in str(row["Flags"]).split("; ") if f and f != "outside month"]
        if isinstance(row["Date"], date) and not (month_start <= row["Date"] < next_month):
            parts.append("outside month")
        return "; ".join(parts)

    rows["Flags"] = rows.apply(update, axis=1) if len(rows) else rows["Flags"]
    return rows


def guess_columns(raw: pd.DataFrame, header_row: int) -> dict:
    """{role: column index} guessed from the header texts of one chosen row (for the manual column picker)."""
    columns = {}
    if 0 <= header_row < len(raw):
        for column_index, value in enumerate(raw.iloc[header_row]):
            role = _header_role(value) if not is_blank(value) else ""
            if role and role not in columns:
                columns[role] = column_index
    return columns
