"""
budget/prepare.py
------------------
Logic between the review screen and the workbook builder, kept out of the page so it can be tested:
the starting categories, restoring dropped rows, and the final checks before the file is built.
"""

import pandas as pd

from budget.categorize import match_type
from budget.model import (EXPENSE, MAX_CATEGORIES, MAX_PAYMENT_METHODS, TX_COLUMNS, TYPES, BudgetConfig)
from quality import is_blank


def starting_categories(keyword_map: dict, rows: pd.DataFrame = None, existing: dict = None) -> dict:
    """{category: budget} to start the Setup table from: the existing settings if given (next month), otherwise the
    keyword map's categories plus any category the imported rows already use. New ones get a budget of 0."""
    categories = dict(existing) if existing else {name: 0.0 for name in keyword_map["categories"]}
    if rows is not None and len(rows):
        known = {name.casefold() for name in categories}
        for name in rows["Category"].dropna():
            if str(name).casefold() not in known:
                categories[str(name)] = 0.0
                known.add(str(name).casefold())
    return categories


def restore_dropped(rows: pd.DataFrame, dropped: pd.DataFrame, source_rows: list, keyword_map: dict) -> pd.DataFrame:
    """Put dropped source rows back among the kept ones (wrongly dropped totals, notes ...). A restored row gets the
    date of the nearest kept row above it, and the flag 'restored'. Rows are returned in source order."""
    rows = rows.copy()
    additions = []
    for source in source_rows:
        record = dropped[dropped["Source row"] == source]
        if record.empty:
            continue
        record = record.iloc[0]
        amount = record["Amount"]
        above = rows[rows["Source row"] < source]
        description = str(record["Description"]) or "(no description)"
        additions.append({
            "Date": above["Date"].iloc[-1] if len(above) else None,
            "Description": description, "Type": match_type(description, keyword_map) or EXPENSE, "Category": None,
            "Amount": abs(float(amount)) if isinstance(amount, (int, float)) else None,
            "Payment method": None, "Flags": "restored; date copied from the row above", "Source row": source})
    if not additions:
        return rows
    combined = pd.concat([rows, pd.DataFrame(additions, columns=rows.columns)], ignore_index=True)
    return combined.sort_values("Source row", kind="stable").reset_index(drop=True)


def finalize(rows: pd.DataFrame, config: BudgetConfig):
    """The last checks before building. Returns (rows, config, problems, notes):
    rows reduced to the six Transactions columns (empty lines removed); categories and payment methods the rows use but
    the settings lack are added (budget 0) and mentioned in `notes`; `problems` (non-empty = do not build) lists things the
    user has to fix, such as an unknown Type or too many categories."""
    problems, notes = list(config.validate()), []
    frame = rows.reindex(columns=list(TX_COLUMNS)).copy()
    empty = frame.apply(lambda row: all(is_blank(v) for v in row), axis=1) if len(frame) else pd.Series([], dtype=bool)
    frame = frame[~empty].reset_index(drop=True)

    bad_types = sorted({str(t) for t in frame["Type"].dropna() if t not in TYPES})
    if bad_types:
        problems.append(f"Unknown Type: {', '.join(bad_types)}. Use one of: {', '.join(TYPES)}.")
    if frame["Amount"].notna().any():
        numeric = pd.to_numeric(frame["Amount"], errors="coerce")
        if (numeric.isna() & frame["Amount"].notna()).any():
            problems.append("Some amounts are not numbers.")
        if (numeric < 0).any():
            problems.append("Amounts must be positive numbers; the Type says if the money comes in or goes out.")
        frame["Amount"] = numeric

    categories = dict(config.categories)
    known = {name.casefold() for name in categories}
    added = []
    for name in frame["Category"].dropna():
        if str(name).casefold() not in known:
            categories[str(name)] = 0.0
            known.add(str(name).casefold())
            added.append(str(name))
    methods = list(config.payment_methods)
    known_methods = {m.casefold() for m in methods}
    added_methods = []
    for name in frame["Payment method"].dropna():
        if str(name).casefold() not in known_methods:
            methods.append(str(name))
            known_methods.add(str(name).casefold())
            added_methods.append(str(name))
    if added:
        notes.append(f"Added to your categories (budget 0): {', '.join(added)}.")
    if added_methods:
        notes.append(f"Added to your payment methods: {', '.join(added_methods)}.")
    if len(categories) > MAX_CATEGORIES:
        problems.append(f"That makes {len(categories)} categories; at most {MAX_CATEGORIES} are supported. Merge some.")
    if len(methods) > MAX_PAYMENT_METHODS:
        problems.append(f"That makes {len(methods)} payment methods; at most {MAX_PAYMENT_METHODS} are supported.")

    final = BudgetConfig(currency=config.currency, month_start=config.month_start, opening_balance=config.opening_balance,
                         categories=categories, payment_methods=methods)
    problems = list(dict.fromkeys(problems + [p for p in final.validate() if p not in problems]))
    return frame, final, problems, notes
