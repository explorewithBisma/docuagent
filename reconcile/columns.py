"""
reconcile/columns.py
---------------------
Reads one uploaded table and works out, from the headers AND the values, which column is the date, the amount (one signed column,
or a debit and a credit column), the reference / id and the description - each with a confidence and a plain sentence saying where
the answer came from. Nothing is changed in the file; the user can override every choice.

  find_header(raw)             which row holds the column names (bank exports often start with a few title rows)
  table_from_raw(raw)          -> (DataFrame with those names, header row number)
  detect_mapping(df)           -> Mapping
  Mapping.with_overrides(...)  the user's own choices, marked "set by you"
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
import pandas as pd

from budget.importer import _to_date, parse_amount
from quality import _name_words, is_blank
from rule_checks import detect_date_order
from toolkit.report import Finding

HEADER_SCAN_ROWS = 40
SAMPLE = 500

DATE_WORDS = {"date", "dt", "posted", "posting", "booking", "day", "time", "txn", "transaction"}
DATE_BOOST = {"transaction", "txn", "posting", "posted", "booking"}
DATE_PENALTY = {"due", "expiry", "expires", "birth", "dob", "created", "updated", "maturity"}
AMOUNT_WORDS = {"amount", "amt", "sum", "value", "total", "price", "cost", "quantity", "qty", "units", "net", "gross", "paid", "stock"}
DEBIT_WORDS = {"debit", "debits", "dr", "withdrawal", "withdrawals"}
CREDIT_WORDS = {"credit", "credits", "cr", "deposit", "deposits"}
BALANCE_WORDS = {"balance", "running", "closing", "opening", "available"}
REFERENCE_WORDS = {"ref", "reference", "refno", "id", "no", "number", "cheque", "check", "chq", "voucher", "invoice", "receipt", "txn", "trx", "rrn", "utr",
                   "trace", "doc", "document", "order", "po", "sku", "code", "serial", "transaction"}
NOT_REFERENCE = {"account", "phone", "mobile", "card", "customer", "branch", "postcode", "zip", "tel", "iban"}
DESCRIPTION_WORDS = {"description", "details", "detail", "narration", "memo", "particulars", "payee", "merchant", "name", "item", "items", "note", "notes",
                     "remarks", "narrative", "text", "reference", "product", "supplier", "vendor", "employee"}

_DATE_TEXT = re.compile(r"^\s*(\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}|\d{1,2}[ \-][A-Za-z]{3,9}[ \-,]+\d{2,4}|[A-Za-z]{3,9}\.? \d{1,2},? \d{2,4})([ T]\d{1,2}:\d{2}.*)?\s*$")
ROLES = ("date", "amount", "debit", "credit", "reference", "description")


# ---------------------------------------------------------------------------------------------------------------
# HEADER ROW AND TABLE
# ---------------------------------------------------------------------------------------------------------------
def _known_header_words(value) -> int:
    if is_blank(value):
        return 0
    tokens = _name_words(value)
    return int(bool(tokens & (DATE_WORDS | AMOUNT_WORDS | DEBIT_WORDS | CREDIT_WORDS | REFERENCE_WORDS | DESCRIPTION_WORDS | BALANCE_WORDS)))


def find_header(raw: pd.DataFrame) -> int:
    """The 0-based row holding the column names: the early row with the most header-like words (and text cells), else the first row."""
    best, best_score = 0, -1
    for row in range(min(HEADER_SCAN_ROWS, len(raw))):
        cells = [v for v in raw.iloc[row] if not is_blank(v)]
        if len(cells) < 2:
            continue
        texty = sum(isinstance(v, str) and parse_amount(v) is None for v in cells)
        if texty / len(cells) < 0.6:
            continue
        score = 3 * sum(_known_header_words(v) for v in cells) + len(cells)
        if score > best_score:
            best, best_score = row, score
    return best


def table_from_raw(raw: pd.DataFrame):
    """-> (DataFrame with the header row as column names, header row number). Blank rows/columns dropped; names made unique.
    `table.attrs` remembers where everything came from: "source_rows" (the Excel row number of every table row) and "source_columns"
    (the 0-based column of the uploaded sheet of every table column)."""
    header_row = find_header(raw)
    names, seen = [], {}
    for index, value in enumerate(raw.iloc[header_row] if len(raw) else []):
        name = str(value).strip() if not is_blank(value) else f"Column {index + 1}"
        seen[name] = seen.get(name, 0) + 1
        names.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    body = raw.iloc[header_row + 1:].copy()
    body.columns = names
    keep = [c for c in body.columns if body[c].map(lambda v: not is_blank(v)).any()]
    source_columns = [names.index(c) for c in keep]
    body = body[keep]
    body = body[body.map(lambda v: not is_blank(v)).any(axis=1)]
    source_rows = [int(i) + 1 for i in body.index]                 # raw row i (0-based) is Excel row i + 1
    body = body.reset_index(drop=True)
    # Kept as immutable bytes: pandas deep-copies `attrs` on every .iloc / slice, and a copied list of 10,000 numbers makes every row read slow.
    body.attrs["source_rows"] = np.asarray(source_rows, dtype=np.int64).tobytes()
    body.attrs["source_columns"] = np.asarray(source_columns, dtype=np.int64).tobytes()
    return body, header_row


def source_rows(table: pd.DataFrame):
    """The Excel row number of every row of a table made by table_from_raw (None for a table made some other way)."""
    packed = table.attrs.get("source_rows")
    return np.frombuffer(packed, dtype=np.int64).tolist() if packed is not None else None


def source_columns(table: pd.DataFrame):
    """The 0-based column of the uploaded sheet of every column of the table (None for a table made some other way)."""
    packed = table.attrs.get("source_columns")
    return np.frombuffer(packed, dtype=np.int64).tolist() if packed is not None else None


# ---------------------------------------------------------------------------------------------------------------
# VALUES
# ---------------------------------------------------------------------------------------------------------------
def _sample(series: pd.Series) -> list:
    return [v for v in series.head(SAMPLE * 2) if not is_blank(v)][:SAMPLE]


def parse_date(value, day_first: bool, header_says_date: bool = False):
    """A date from a cell, or None. Plain numbers count as Excel serial dates only when the header says date (an amount of 25000 is not a date)."""
    if is_blank(value) or isinstance(value, bool):
        return None
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        return _to_date(value, day_first, 2000) if header_says_date else None
    text = str(value).strip()
    if not _DATE_TEXT.match(text):
        return None
    return _to_date(text, day_first, 2000)


def date_order(series: pd.Series, default_day_first: bool = True) -> tuple:
    """-> (day_first, certain). From the values: a first part above 12 can only be a day."""
    info = detect_date_order(series)
    if info["order"] == "day_first":
        return True, True
    if info["order"] == "month_first":
        return False, True
    return default_day_first, False


def _date_share(values: list, day_first: bool, header_says_date: bool) -> float:
    return sum(parse_date(v, day_first, header_says_date) is not None for v in values) / len(values) if values else 0.0


def _number_share(values: list) -> float:
    return sum(parse_amount(v) is not None and parse_date(v, True) is None for v in values) / len(values) if values else 0.0


def _distinct_share(values: list) -> float:
    return len({str(v).strip().casefold() for v in values}) / len(values) if values else 0.0


def _avg_len(values: list) -> float:
    return sum(len(str(v)) for v in values) / len(values) if values else 0.0


# ---------------------------------------------------------------------------------------------------------------
# THE MAPPING
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class Mapping:
    findings: dict = field(default_factory=dict)         # role -> Finding (value = column name)
    day_first: bool = True
    day_first_certain: bool = False
    notes: list = field(default_factory=list)

    def column(self, role):
        finding = self.findings.get(role)
        return finding.value if finding else None

    @property
    def money_mode(self) -> str:
        """'signed' (one amount column), 'debit_credit', or '' when no money column was found."""
        if self.column("amount"):
            return "signed"
        if self.column("debit") or self.column("credit"):
            return "debit_credit"
        return ""

    def with_overrides(self, overrides: dict, columns: list) -> "Mapping":
        """The mapping with the user's choices ({role: column or None}); a choice replaces the detected column and is marked as set by the user."""
        findings = dict(self.findings)
        for role, column in (overrides or {}).items():
            if column is None or column == "(none)":
                findings.pop(role, None)
            elif column in columns and (role not in findings or findings[role].value != column):
                findings[role] = Finding(column, "high", "set by you")
        if "amount" in (overrides or {}) and overrides["amount"] not in (None, "(none)"):
            findings.pop("debit", None)
            findings.pop("credit", None)
        return Mapping(findings, self.day_first, self.day_first_certain, list(self.notes))


def detect_balance(df: pd.DataFrame, mapping: Mapping):
    """The running-balance column of a bank statement (header says balance, the values are numbers, it is not the amount), or None."""
    used = {mapping.column(r) for r in ROLES}
    best = None
    for column in df.columns:
        tokens = _name_words(column)
        values = _sample(df[column])
        if column in used or not values or not tokens & BALANCE_WORDS or not _number_share(values) >= 0.8:
            continue
        score = (2 if "balance" in tokens else 0) + (1 if tokens & {"running", "closing", "available"} else 0) - (1 if "opening" in tokens else 0)
        if best is None or score > best[0]:
            best = (score, column)
    return best[1] if best else None


def detect_mapping(df: pd.DataFrame, default_day_first: bool = True) -> Mapping:
    mapping = Mapping()
    stats = {}
    for column in df.columns:
        values = _sample(df[column])
        tokens = _name_words(column)
        stats[column] = {"values": values, "tokens": tokens}

    # --- date -----------------------------------------------------------------------------------------------------------
    best = None
    for column, s in stats.items():
        header = bool(s["tokens"] & DATE_WORDS)
        ordered = date_order(df[column], default_day_first)
        share = _date_share(s["values"], ordered[0], header)
        if share < 0.8 or not s["values"]:
            continue
        score = (3 if header else 0) + (1 if s["tokens"] & DATE_BOOST else 0) - (2 if s["tokens"] & DATE_PENALTY else 0) + share
        if best is None or score > best[0]:
            best = (score, column, header, ordered)
    if best:
        _, column, header, ordered = best
        mapping.day_first, mapping.day_first_certain = ordered
        mapping.findings["date"] = Finding(column, "high" if header else "medium",
                                           f"header '{column}' and the values are dates" if header else "most values are dates (the header does not say so)")
    taken = {mapping.column("date")}

    # --- money: debit / credit columns, or one signed amount ------------------------------------------------------------
    def numeric(column):
        return stats[column]["values"] and _number_share(stats[column]["values"]) >= 0.8

    def with_words(words):
        return [c for c, s in stats.items() if c not in taken and s["tokens"] & words and not s["tokens"] & BALANCE_WORDS and
                (numeric(c) or not stats[c]["values"])]

    def is_debit(tokens):
        return bool(tokens & DEBIT_WORDS) or ({"money", "paid"} & tokens and "out" in tokens)

    def is_credit(tokens):
        return bool(tokens & CREDIT_WORDS) or ({"money", "paid"} & tokens and "in" in tokens)

    available = with_words(DEBIT_WORDS | CREDIT_WORDS | AMOUNT_WORDS | {"money", "paid", "out", "in"})
    debit = [c for c in available if is_debit(stats[c]["tokens"])]
    credit = [c for c in available if is_credit(stats[c]["tokens"]) and c not in debit]
    amount = [c for c in with_words(AMOUNT_WORDS) if c not in debit and c not in credit]
    if debit and credit:
        mapping.findings["debit"] = Finding(debit[0], "high", f"header '{debit[0]}' and the values are numbers")
        mapping.findings["credit"] = Finding(credit[0], "high", f"header '{credit[0]}' and the values are numbers")
        taken |= {debit[0], credit[0]}
    elif amount:
        column = max(amount, key=lambda c: (bool(stats[c]["tokens"] & {"amount", "amt"}), len(stats[c]["values"])))
        mapping.findings["amount"] = Finding(column, "high", f"header '{column}' and the values are numbers")
        taken.add(column)
    elif debit or credit:                       # only one of the two: it is the (one-sided) amount
        role, column = ("debit", debit[0]) if debit else ("credit", credit[0])
        mapping.findings[role] = Finding(column, "medium", f"header '{column}' and the values are numbers (the only money column; "
                                         + ("its amounts are money out)" if role == "debit" else "its amounts are money in)"))
        taken.add(column)
    else:
        numbers = [c for c, s in stats.items() if c not in taken and not s["tokens"] & BALANCE_WORDS and numeric(c) and _distinct_share(s["values"]) > 0.3]
        if numbers:
            column = max(numbers, key=lambda c: (_distinct_share(stats[c]["values"]), len(stats[c]["values"])))
            mapping.findings["amount"] = Finding(column, "low", f"'{column}' holds numbers (the header does not say they are amounts)")
            taken.add(column)

    # --- reference ------------------------------------------------------------------------------------------------------
    candidates = []
    for column, s in stats.items():
        if column in taken or not s["values"] or s["tokens"] & NOT_REFERENCE:
            continue
        header = bool(s["tokens"] & REFERENCE_WORDS)
        distinct = _distinct_share(s["values"])
        code_like = _avg_len(s["values"]) <= 24 and sum(any(ch.isdigit() for ch in str(v)) for v in s["values"]) / len(s["values"]) >= 0.8
        if _date_share(s["values"], True, False) >= 0.8:
            continue
        if header and distinct >= 0.5 and code_like:
            candidates.append((2 + distinct, column, "high" if distinct >= 0.9 else "medium",
                               f"header '{column}' and {distinct:.0%} of the values are different (an id)"))
        elif not header and distinct >= 0.97 and code_like and len(s["values"]) >= 20 and not numeric(column):
            candidates.append((distinct, column, "low", f"'{column}' looks like a unique code on every row"))
    if candidates:
        _, column, confidence, source = max(candidates)
        mapping.findings["reference"] = Finding(column, confidence, source)
        taken.add(column)

    # --- description ----------------------------------------------------------------------------------------------------
    texts = []
    for column, s in stats.items():
        if column in taken or not s["values"]:
            continue
        texty = sum(isinstance(v, str) and re.search(r"[^\W\d_]", v) is not None for v in s["values"]) / len(s["values"])
        if texty < 0.7 or _date_share(s["values"], True, False) >= 0.8:
            continue
        header = bool(s["tokens"] & DESCRIPTION_WORDS)
        texts.append(((2 if header else 0) + min(_avg_len(s["values"]), 40) / 40 + (0.5 if _distinct_share(s["values"]) > 0.3 else 0), column, header))
    if texts:
        _, column, header = max(texts)
        mapping.findings["description"] = Finding(column, "high" if header else "low",
                                                  f"header '{column}' and the values are text" if header else f"'{column}' is the most text-like column (the header does not say so)")

    if "date" not in mapping.findings:
        mapping.notes.append("No date column was found, so rows can only be matched by reference (or by amount alone as a 'maybe').")
    if not mapping.money_mode:
        mapping.notes.append("No amount column was found. Choose it under Edit details.")
    return mapping
