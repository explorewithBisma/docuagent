"""
budget/detect.py
-----------------
Everything the budget builder can work out about an uploaded workbook without being told. Each finding carries a
confidence level (high / medium / low) and the source it came from, so the summary card can show where it got it:

  detect_currency   - from the amount header ("Amount (AED)"), symbols in cells, sheet titles, else the locale default
  detect_month      - the most common month in the transaction dates
  detect_opening_balance - a labelled number such as "May 2026 Closing Balance" (alternatives are kept)
  detect_own_closing - the file's own "estimated closing balance", for a sanity check against ours
  detect_budgets    - a Category | Budget table (the one that adds up to its own total is preferred)
  detect_income     - an income list sheet -> Income rows
  detect_payments   - a loan / card payment log -> Loan payment rows

Nothing here changes the user's data: the functions only read the raw sheets and return findings and rows.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd

from budget import categorize, names
from budget.model import INCOME, LOAN_PAYMENT, LOAN_RECEIVED, TX_COLUMNS
from locales import LOCALES
from quality import is_blank

CURRENCY_CODES = ("AED", "USD", "EUR", "GBP", "PKR", "INR", "SAR", "QAR", "KWD", "BHD", "OMR", "CAD", "AUD")
CURRENCY_SYMBOLS = ("$", "€", "£", "₹")
CODE_TO_LOCALE = {"AED": "AE", "PKR": "PK", "INR": "IN", "GBP": "GB", "USD": "US"}
_CODE = re.compile(r"(?<![A-Za-z])(" + "|".join(CURRENCY_CODES) + r")(?![A-Za-z])")
_SYMBOL = re.compile("[" + re.escape("".join(CURRENCY_SYMBOLS)) + "]")
MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
               "december"]


@dataclass
class Finding:
    value: object
    confidence: str                     # "high" | "medium" | "low"
    source: str                         # a plain sentence: where it came from
    alternatives: list = field(default_factory=list)      # [(value, source)] other candidates


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and not pd.isna(value)


def _text(value) -> str:
    return "" if is_blank(value) else str(value).strip()


def _as_date(value):
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.date()
    return value if isinstance(value, date) else None


def _clean_sheet_name(name) -> str:
    return names.display_name(name) or str(name)


# ---------------------------------------------------------------------------
# CURRENCY
# ---------------------------------------------------------------------------

def _currency_in(text: str):
    """The currency code or symbol written in a text, or None."""
    found = _CODE.search(text)
    if found:
        return found.group(1)
    symbol = _SYMBOL.search(text)
    return symbol.group(0) if symbol else None


def detect_currency(raw_sheets: dict, main_sheet: str, layout: dict, locale_code: str) -> Finding:
    """Header of the amount column (high) > symbols/codes in the amount cells (medium) > titles of the other
    sheets (medium) > the locale's usual currency (low)."""
    candidates = []
    main = raw_sheets[main_sheet]
    columns = layout["columns"]
    for role in ("amount", "debit", "credit"):
        if role in columns:
            header = _text(main.iat[layout["header_row"], columns[role]])
            code = _currency_in(header)
            if code:
                candidates.append((code, "high", f"header '{header}' on sheet '{_clean_sheet_name(main_sheet)}'"))
                break

    if "amount" in columns:
        body = main.iloc[layout["header_row"] + 1:, columns["amount"]]
        seen = Counter(code for value in body if isinstance(value, str) for code in [_currency_in(value)] if code)
        if seen and sum(seen.values()) >= 3:
            code, count = seen.most_common(1)[0]
            candidates.append((code, "medium", f"{count} amounts written with '{code}'"))

    titles = Counter()
    for name, raw in raw_sheets.items():
        if name == main_sheet:
            continue
        for value in raw.iloc[:12].to_numpy().ravel():
            if isinstance(value, str):
                code = _currency_in(value)
                if code:
                    titles[code] += 1
    if titles:
        code, count = titles.most_common(1)[0]
        candidates.append((code, "medium", f"'{code}' appears {count} times in the headings of the other sheets"))

    fallback = LOCALES.get(locale_code, LOCALES["PK"])["currency_symbol"]
    if candidates:
        value, confidence, source = candidates[0]
        others = [(v, s) for v, _, s in candidates[1:] if v != value]
        return Finding(value, confidence, source, others)
    return Finding(fallback, "low", f"nothing found in the file; the usual currency for {LOCALES.get(locale_code, LOCALES['PK'])['label']}")


def locale_for_currency(currency: str, default: str) -> str:
    """The locale (date order, phone region ...) that goes with a currency code, else `default`."""
    return CODE_TO_LOCALE.get(currency, default)


# ---------------------------------------------------------------------------
# MONTH
# ---------------------------------------------------------------------------

def detect_month(rows: pd.DataFrame, today: date = None) -> Finding:
    """The month most transaction dates fall in."""
    today = today or date.today()
    dates = [d for d in rows["Date"] if isinstance(d, date)] if len(rows) else []
    if not dates:
        return Finding(today.replace(day=1), "low", "no dates found; using the current month")
    counts = Counter(d.replace(day=1) for d in dates)
    (month, top), *rest = counts.most_common()
    share = top / len(dates)
    confidence = "high" if share >= 0.8 and len(dates) >= 5 else "medium"
    source = f"{top} of {len(dates)} dated rows are in {month:%B %Y}"
    return Finding(month, confidence, source, [(m, f"{n} rows") for m, n in rest[:3]])


# ---------------------------------------------------------------------------
# BALANCES
# ---------------------------------------------------------------------------

def _labelled_numbers(raw: pd.DataFrame, must_contain: str):
    """(label, value) for every text cell containing `must_contain` with a number in the next three cells of its row."""
    for row in raw.to_numpy():
        for column, cell in enumerate(row):
            if isinstance(cell, str) and must_contain in names.canonical(cell):
                number = next((v for v in row[column + 1:column + 4] if _is_number(v)), None)
                if number is not None:
                    yield cell.strip(), float(number)


def _opening_score(label: str, month_start: date) -> int:
    text = names.canonical(label)
    words = set(text.split())
    previous = MONTH_NAMES[(month_start.month - 2) % 12]
    current = MONTH_NAMES[month_start.month - 1]
    score = 0
    if "opening balance" in text:
        score += 5
    if "adjusted opening" in text:
        score += 3
    if any(phrase in text for phrase in ("previous month", "last month", "prior month", "brought forward", "carried forward")):
        score += 4
    if "closing balance" in text:
        score += 4
    if previous in words and "closing" in text:
        score += 4
    if current in words and "closing" in text and "opening" not in text:
        score -= 6
    if any(word in words for word in ("estimated", "expected", "projected")):
        score -= 6
    if "total" in words:
        score -= 4
    if "current" in words:
        score -= 3
    return score


def detect_opening_balance(raw_sheets: dict, main_sheet: str, month_start: date) -> Finding:
    """The labelled balance that best matches 'the balance at the start of this month'. Needs a label with 'balance'
    and a score of at least 3; the other candidates are kept as alternatives."""
    scored = []
    for name, raw in raw_sheets.items():
        if name == main_sheet:
            continue
        for label, value in _labelled_numbers(raw, "balance"):
            score = _opening_score(label, month_start)
            if score >= 3:
                scored.append((score, value, f"'{label}' on sheet '{_clean_sheet_name(name)}'"))
    if not scored:
        return Finding(0.0, "low", "no opening or previous-month balance found; assuming 0")
    scored.sort(key=lambda item: -item[0])
    best_score, best_value, source = scored[0]
    others = [(value, s) for _, value, s in scored[1:] if abs(value - best_value) > 0.005]
    if others:
        return Finding(best_value, "medium", source + " (other candidates disagree)", others)
    confidence = "high" if best_score >= 8 else "medium"
    agreeing = len(scored) - 1
    return Finding(best_value, confidence, source + (f"; {agreeing} other label(s) give the same number" if agreeing else ""))


def detect_own_closing(raw_sheets: dict, main_sheet: str, month_start: date):
    """The file's own estimated closing balance for this month, or None."""
    current = MONTH_NAMES[month_start.month - 1]
    for name, raw in raw_sheets.items():
        if name == main_sheet:
            continue
        for label, value in _labelled_numbers(raw, "closing balance"):
            words = set(names.canonical(label).split())
            if words & {"estimated", "expected", "projected"} or current in words:
                return Finding(value, "medium", f"'{label}' on sheet '{_clean_sheet_name(name)}'")
    return None


# ---------------------------------------------------------------------------
# TABLES (budgets, income list, payment log)
# ---------------------------------------------------------------------------

def _find_header(raw: pd.DataFrame, wanted: dict):
    """Rows whose cells satisfy `wanted` = {role: predicate on canonical text}: yields (row index, {role: column})."""
    for r in range(len(raw)):
        found = {}
        for c, cell in enumerate(raw.iloc[r]):
            if not isinstance(cell, str):
                continue
            text = names.canonical(cell)
            for role, predicate in wanted.items():
                if role not in found and predicate(text):
                    found[role] = c
        if all(role in found for role in wanted if not role.endswith("?")):
            yield r, found


def detect_budgets(raw_sheets: dict, main_sheet: str) -> Finding:
    """Category -> monthly budget from a 'Category | Budget' table on any other sheet. When several sheets have one, the
    table whose budgets add up to its own TOTAL row wins; disagreements with the others are listed."""
    tables = []
    for name, raw in raw_sheets.items():
        if name == main_sheet:
            continue
        wanted = {"category": lambda t: t == "category" or t.startswith("category "), "budget": lambda t: "budget" in t}
        for r, found in _find_header(raw, wanted):
            budgets, total = {}, None
            for row in range(r + 1, len(raw)):
                label, value = raw.iat[row, found["category"]], raw.iat[row, found["budget"]]
                if is_blank(label):
                    break
                if "total" in names.canonical(label):
                    total = float(value) if _is_number(value) else None
                    break
                if _is_number(value):
                    budgets[names.display_name(label)] = float(value)
            if budgets and any(v > 0 for v in budgets.values()):
                consistent = total is not None and abs(sum(budgets.values()) - total) < 0.01
                tables.append({"sheet": name, "budgets": budgets, "total": total, "consistent": consistent})
            break
    if not tables:
        return Finding({}, "low", "no Category | Budget table found")
    tables.sort(key=lambda t: (not t["consistent"], -len(t["budgets"])))
    chosen, others = tables[0], tables[1:]
    conflicts = []
    for other in others:
        for category, value in other["budgets"].items():
            mine = chosen["budgets"].get(category)
            if mine is not None and abs(mine - value) > 0.005:
                conflicts.append((f"{category}: {value:g}", f"sheet '{_clean_sheet_name(other['sheet'])}' says {value:g}, "
                                                             f"sheet '{_clean_sheet_name(chosen['sheet'])}' says {mine:g}"))
    source = f"table on sheet '{_clean_sheet_name(chosen['sheet'])}'" + \
        (" (its budgets add up to its own total)" if chosen["consistent"] else "")
    confidence = "high" if chosen["consistent"] and not conflicts else "medium"
    return Finding(chosen["budgets"], confidence, source, conflicts)


def _empty_rows() -> pd.DataFrame:
    return pd.DataFrame(columns=[*TX_COLUMNS, "Flags", "Source row"])


def detect_income(raw_sheets: dict, main_sheet: str, month_start: date, keyword_map: dict):
    """(rows, Finding): Income rows from an income list ('Income Source | Amount | Date Received'), or no rows."""
    wanted = {"source": lambda t: "source" in t or t.startswith("income"), "amount": lambda t: "amount" in t,
              "date?": lambda t: "date" in t}
    for name, raw in raw_sheets.items():
        if name == main_sheet or "income" not in names.canonical(name) + " " + " ".join(
                names.canonical(v) for v in raw.iloc[:6].to_numpy().ravel() if isinstance(v, str)):
            continue
        for r, found in _find_header(raw, wanted):
            date_column = next((c for c, v in enumerate(raw.iloc[r]) if isinstance(v, str) and "date" in names.canonical(v)), None)
            records, listed_total = [], None
            for row in range(r + 1, len(raw)):
                label, amount = raw.iat[row, found["source"]], raw.iat[row, found["amount"]]
                if is_blank(label):
                    break
                if "total" in names.canonical(label):
                    listed_total = float(amount) if _is_number(amount) else None
                    break
                if not _is_number(amount) or float(amount) == 0:
                    continue
                when = _as_date(raw.iat[row, date_column]) if date_column is not None else None
                flags = [f"from sheet '{_clean_sheet_name(name)}'"] + ([] if when else ["date not given"])
                description = names.display_name(label) or str(label)
                kind = categorize.match_type(description, keyword_map)
                records.append({"Date": when or month_start, "Description": description,
                                "Type": LOAN_RECEIVED if kind == LOAN_RECEIVED else INCOME, "Category": None, "Amount": abs(float(amount)), "Payment method": None,
                                "Flags": "; ".join(flags), "Source row": None})
            if not records:
                continue
            frame = pd.DataFrame(records, columns=list(_empty_rows().columns))
            total = float(frame["Amount"].sum())
            matches = listed_total is not None and abs(total - listed_total) < 0.01
            source = f"list on sheet '{_clean_sheet_name(name)}' ({len(frame)} source(s)" + \
                (", adds up to its own total)" if matches else ")")
            return frame, Finding(total, "high" if matches else "medium", source)
    return _empty_rows(), Finding(0.0, "low", "no income list found")


def detect_payments(raw_sheets: dict, main_sheet: str, month_start: date):
    """(rows, Finding): Loan payment rows from a dated payment log on a loan / card sheet."""
    wanted = {"date": lambda t: "payment date" in t or t == "date", "amount": lambda t: "amount" in t,
              "who?": lambda t: "loan" in t or "card" in t}
    for name, raw in raw_sheets.items():
        if name == main_sheet:
            continue
        for r, found in _find_header(raw, wanted):
            context = names.canonical(name) + " " + " ".join(
                names.canonical(v) for v in raw.iloc[max(0, r - 2):r + 1].to_numpy().ravel() if isinstance(v, str))
            if not any(word in context for word in ("loan", "card", "instal", "emi", "payment log")):
                continue
            who_column = next((c for c, v in enumerate(raw.iloc[r]) if isinstance(v, str) and
                               ("loan" in names.canonical(v) or "card" in names.canonical(v))), None)
            method_column = next((c for c, v in enumerate(raw.iloc[r]) if isinstance(v, str) and "method" in names.canonical(v)), None)
            records, listed_total = [], None
            for row in range(r + 1, len(raw)):
                first = raw.iat[row, 0]
                if isinstance(first, str) and "total" in names.canonical(first):
                    value = raw.iat[row, found["amount"]]
                    listed_total = float(value) if _is_number(value) else None
                    break
                amount, when = raw.iat[row, found["amount"]], _as_date(raw.iat[row, found["date"]])
                if not _is_number(amount) or float(amount) <= 0:
                    continue
                who = _text(raw.iat[row, who_column]) if who_column is not None else ""
                description = f"Loan / card payment ({who})" if who else "Loan / card payment"
                flags = [f"from the payment log on sheet '{_clean_sheet_name(name)}'"] + ([] if when else ["date not given"])
                records.append({"Date": when or month_start, "Description": description, "Type": LOAN_PAYMENT, "Category": None,
                                "Amount": float(amount), "Payment method": _text(raw.iat[row, method_column]) or None
                                if method_column is not None else None,
                                "Flags": "; ".join(flags), "Source row": None})
            if not records:
                continue
            frame = pd.DataFrame(records, columns=list(_empty_rows().columns))
            total = float(frame["Amount"].sum())
            matches = listed_total is not None and abs(total - listed_total) < 0.01
            source = f"payment log on sheet '{_clean_sheet_name(name)}' ({len(frame)} payment(s)" + \
                (", adds up to its own total)" if matches else ")")
            return frame, Finding(total, "high" if matches else "medium", source)
    return _empty_rows(), Finding(0.0, "low", "no loan or card payment log found")
