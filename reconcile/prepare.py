"""
reconcile/prepare.py
---------------------
Turns one table plus its column mapping into a `Side`: plain arrays the matcher can compare quickly - the date (as a day number),
the amount (ONE signed number: money in is positive, money out negative, whether the file had one signed column or a debit and a
credit column), the normalised reference and description. The user's table is only read.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from budget.importer import parse_amount
from dedupe.normalize import norm_id, norm_text
from quality import is_blank
from reconcile.columns import parse_date

NO_DATE = -1


@dataclass
class Side:
    n: int
    row_numbers: list                                   # the row each record has in the user's file (Excel numbering, header row included)
    dates: np.ndarray                                   # day number, NO_DATE when missing
    amounts: np.ndarray                                 # signed money, NaN when missing
    refs: list                                          # normalised reference ('' when none)
    texts: list                                         # normalised description
    tokens: list                                        # frozenset of description words
    has_date: bool = False
    has_ref: bool = False
    has_text: bool = False
    notes: list = field(default_factory=list)
    money_mode: str = ""
    signs: str = ""                                     # a plain sentence about how money in / out was read

    def cents(self) -> np.ndarray:
        return np.where(np.isnan(self.amounts), 0, np.rint(np.nan_to_num(self.amounts) * 100)).astype(np.int64)


def _mapped(series: pd.Series, function) -> list:
    cache, out = {}, []
    for value in series.tolist():
        key = value if not isinstance(value, float) or value == value else "__nan__"
        if key not in cache:
            cache[key] = function(value)
        out.append(cache[key])
    return out


def normalize_reference(value) -> str:
    text = norm_id(value)
    return (text.lstrip("0") or "0") if text.isdigit() else text


def words(text: str) -> frozenset:
    return frozenset(w for w in text.split() if len(w) > 1 and not (w.isdigit() and len(w) >= 4))


def prepare_side(df: pd.DataFrame, mapping, header_row: int) -> Side:
    n = len(df)
    side = Side(n=n, row_numbers=[header_row + int(i) + 2 for i in range(n)], dates=np.full(n, NO_DATE, dtype=np.int64),
                amounts=np.full(n, np.nan), refs=[""] * n, texts=[""] * n, tokens=[frozenset()] * n)

    date_column = mapping.column("date")
    if date_column:
        parsed = _mapped(df[date_column], lambda v: parse_date(v, mapping.day_first, True))
        side.dates = np.array([d.toordinal() if d else NO_DATE for d in parsed], dtype=np.int64)
        side.has_date = True
        missing = int((side.dates == NO_DATE).sum())
        if missing:
            side.notes.append(f"{missing} row(s) have no readable date; they can only be matched by reference.")

    mode = mapping.money_mode
    side.money_mode = mode
    if mode == "signed":
        side.amounts = np.array([np.nan if (a := parse_amount(v)) is None else a for v in _mapped(df[mapping.column("amount")], lambda v: v)], dtype=float)
        side.signs = f"one amount column '{mapping.column('amount')}' (negative = money out)"
    elif mode == "debit_credit":
        debit = _mapped(df[mapping.column("debit")], parse_amount) if mapping.column("debit") else [None] * n
        credit = _mapped(df[mapping.column("credit")], parse_amount) if mapping.column("credit") else [None] * n
        shown = [d for d in debit if d is not None]
        already_negative = bool(shown) and sum(d < 0 for d in shown) / len(shown) > 0.5
        out = []
        for d, c in zip(debit, credit):
            if d is None and c is None:
                out.append(np.nan)
            else:
                d, c = d or 0.0, c or 0.0
                out.append(c + d if already_negative else c - d)          # money in is positive, money out negative
        side.amounts = np.array(out, dtype=float)
        side.signs = (f"debit column '{mapping.column('debit')}' (money out, shown as negative) and credit column '{mapping.column('credit')}' (money in)"
                      if mapping.column("debit") and mapping.column("credit") else "a one-sided money column")
    missing_amount = int(np.isnan(side.amounts).sum())
    if missing_amount:
        side.notes.append(f"{missing_amount} row(s) have no readable amount and cannot be matched; they are listed as unmatched.")

    reference_column = mapping.column("reference")
    if reference_column:
        side.refs = _mapped(df[reference_column], normalize_reference)
        side.has_ref = any(side.refs)

    text_column = mapping.column("description")
    if text_column:
        side.texts = _mapped(df[text_column], norm_text)
        token_cache = {}
        side.tokens = [token_cache.setdefault(t, words(t)) for t in side.texts]
        side.has_text = any(side.texts)
    return side
