"""
budget/autopilot.py
--------------------
"Upload and go": one call that reads a workbook and works out everything the budget builder needs, with the evidence.

analyze(file_bytes, is_csv)  ->  Analysis
    1. the transaction log (best sheet / header row / columns)       budget.importer
    2. currency, month, opening balance, budgets, income, loan payments   budget.detect
    3. categories, in this order: the file's own category (merged into ours when obvious: "Food/Grocery" -> "Food"),
       the keyword map, a typo-tolerant keyword match; what is left can optionally go to the AI (apply_ai)
    4. a "Please check" list of only the uncertain things, and a sanity check against the file's own closing balance

Nothing here is hidden from the user: every value is prefilled into the normal (editable) sections of the page.
"""

import copy
import statistics
from dataclasses import dataclass, field, replace
from datetime import date

import pandas as pd

from budget import carryover, categorize, detect, importer, names, prepare
from budget.model import (CARD_BILL, DEFAULT_PAYMENT_METHODS, EXPENSE, INCOME, LOAN_PAYMENT, TX_COLUMNS, BudgetConfig)
from quality import is_blank

UNUSUAL_FACTOR = 10          # an expense this many times the median is listed as unusual
UNUSUAL_MIN_ROWS = 8         # ... but only when there are enough rows for a median to mean something
CHECK_LIMIT_TEXT = 60
PAYMENT_FLAG = "payment log"
EXPLAIN_PAYMENT_TYPES = ("Loan payment = paying back a loan or an instalment plan. Card bill payment = paying your "
                         "credit card bill for purchases that are already counted as expenses.")


@dataclass
class CheckItem:
    kind: str                 # payments | opening | currency | month | budget | category | type | unusual | date
    text: str
    amount: float = None
    action: str = None        # a one-click fix the page can offer, e.g. "switch_payments_to_card"


@dataclass
class Analysis:
    main_sheet: str
    layout: dict
    raw_sheets: dict
    rows: pd.DataFrame                  # TX_COLUMNS + Flags + Source row
    dropped: pd.DataFrame
    keyword_map: dict
    currency: detect.Finding
    month: detect.Finding
    opening: detect.Finding
    budgets: detect.Finding
    income: detect.Finding
    payments: detect.Finding
    own_closing: object                 # Finding or None
    categories: dict                    # {name: monthly budget} for the settings table
    merges: dict                        # {category written in the file: our category}
    locale: str
    import_result: importer.ImportResult
    notes: list = field(default_factory=list)
    checks: list = field(default_factory=list)
    unmatched: pd.DataFrame = None      # Description | Count | Total: expenses still without a category
    ai_assigned: int = 0
    ai_info: dict = None                # {'provider', 'model'} once the AI step has run

    @property
    def config(self) -> BudgetConfig:
        return BudgetConfig(currency=self.currency.value, month_start=self.month.value, opening_balance=float(self.opening.value),
                            categories=dict(self.categories),
                            payment_methods=list(dict.fromkeys(list(DEFAULT_PAYMENT_METHODS)
                                                               + [m for m in self.rows["Payment method"].dropna()])))

    @property
    def closing_balance(self) -> float:
        return carryover.closing_balance(self.rows, self.month.value, float(self.opening.value))

    @property
    def closing_difference(self):
        """(file's own estimated closing, ours, our - theirs) or None when the file has no such figure."""
        if self.own_closing is None:
            return None
        return self.own_closing.value, self.closing_balance, self.closing_balance - self.own_closing.value


# ---------------------------------------------------------------------------
# THE ANALYSIS
# ---------------------------------------------------------------------------

def analyze(file_bytes: bytes, is_csv: bool = False, keyword_map: dict = None, locale_code: str = "PK",
            today: date = None) -> Analysis:
    """Read the workbook and detect everything. Raises ValueError when no transaction log can be found."""
    keyword_map = keyword_map or categorize.default_keyword_map()
    raw_sheets = importer.read_raw_sheets(file_bytes, is_csv)
    main, layout = importer.best_sheet(raw_sheets)
    if main is None:
        raise ValueError("No transaction log was found (a sheet with a date or description column and an amount column).")

    currency = detect.detect_currency(raw_sheets, main, layout, locale_code)
    locale = detect.locale_for_currency(currency.value, locale_code)
    first = importer.import_transactions(raw_sheets[main], layout, keyword_map, locale, None)
    month = detect.detect_month(first.rows, today)
    result = importer.import_transactions(raw_sheets[main], layout, keyword_map, locale, month.value)
    notes = list(result.notes)

    rows, merges = _merge_own_categories(result.rows, keyword_map)
    rows = _typo_categories(rows, keyword_map)

    income_rows, income = detect.detect_income(raw_sheets, main, month.value, keyword_map)
    payment_rows, payments = detect.detect_payments(raw_sheets, main, month.value)
    income_rows, skipped_income = _without_duplicates(income_rows, rows, INCOME)
    payment_rows, skipped_payments = _without_duplicates(payment_rows, rows, LOAN_PAYMENT)
    for skipped, label in ((skipped_income, "income"), (skipped_payments, "loan payment")):
        if skipped:
            notes.append(f"{skipped} {label} row(s) from the other sheets were already in the log and were not added twice.")
    rows = _chronological(pd.concat([f for f in (income_rows, rows, payment_rows) if not f.empty], ignore_index=True))
    rows = importer.flag_outside_month(rows, month.value)
    rows["Payment method"] = _canonical_methods(rows["Payment method"], DEFAULT_PAYMENT_METHODS)

    opening = detect.detect_opening_balance(raw_sheets, main, month.value)
    own_closing = detect.detect_own_closing(raw_sheets, main, month.value)
    budgets = detect.detect_budgets(raw_sheets, main)
    categories, budget_notes = _categories_with_budgets(keyword_map, rows, budgets)
    notes += budget_notes
    if merges:
        notes.append("Merged your categories into ours: " + ", ".join(f"{a} → {b}" for a, b in merges.items()) + ".")

    analysis = Analysis(main_sheet=main, layout=layout, raw_sheets=raw_sheets, rows=rows, dropped=result.dropped,
                        keyword_map=keyword_map, currency=currency, month=month, opening=opening, budgets=budgets,
                        income=income, payments=payments, own_closing=own_closing, categories=categories, merges=merges,
                        locale=locale, import_result=result, notes=notes)
    return refresh(analysis)


def _merge_own_categories(rows: pd.DataFrame, keyword_map: dict):
    """Rename the file's own categories to ours when the match is obvious. Returns (rows, {file name: our name})."""
    own = [c for c in rows["Category"].dropna().unique()]
    merges = names.merge_map(own, keyword_map["categories"])
    rows = rows.copy()
    if merges:
        rows["Category"] = rows["Category"].map(lambda c: merges.get(c, c) if not is_blank(c) else c)
    return rows, merges


def _typo_categories(rows: pd.DataFrame, keyword_map: dict) -> pd.DataFrame:
    rows = rows.copy()
    for index, row in rows.iterrows():
        if row["Type"] == EXPENSE and is_blank(row["Category"]):
            category, keyword = names.fuzzy_category(row["Description"], keyword_map)
            if category:
                rows.at[index, "Category"] = category
                flags = [f for f in str(row["Flags"]).split("; ") if f and f != "no category"]
                rows.at[index, "Flags"] = "; ".join(flags + [f"category guessed: looks like '{keyword}'"])
    return rows


def _without_duplicates(extra: pd.DataFrame, log_rows: pd.DataFrame, kind: str):
    """Drop rows of another sheet that the log already has (same Type, same amount). Returns (rows, number dropped)."""
    if extra.empty:
        return extra, 0
    have = [round(a, 2) for a in log_rows.loc[log_rows["Type"] == kind, "Amount"].dropna()]
    keep = []
    for index, row in extra.iterrows():
        if round(row["Amount"], 2) in have:
            have.remove(round(row["Amount"], 2))
        else:
            keep.append(index)
    return extra.loc[keep].reset_index(drop=True), len(extra) - len(keep)


def _chronological(rows: pd.DataFrame) -> pd.DataFrame:
    """Stable sort by date, rows without a date last."""
    order = sorted(range(len(rows)), key=lambda i: (not isinstance(rows["Date"].iloc[i], date), rows["Date"].iloc[i]
                                                    if isinstance(rows["Date"].iloc[i], date) else date.min))
    return rows.iloc[order].reset_index(drop=True)


def _canonical_methods(series: pd.Series, preferred=()) -> pd.Series:
    """'cash' and 'Cash' are one payment method: use the preferred spelling if there is one, else the first seen."""
    first = {method.casefold(): method for method in preferred}
    return series.map(lambda m: None if is_blank(m) else first.setdefault(str(m).strip().casefold(), str(m).strip()))


def _categories_with_budgets(keyword_map: dict, rows: pd.DataFrame, budgets: detect.Finding):
    """The category table: the keyword map's categories + the ones the rows use + those with a budget, budgets filled in."""
    categories = prepare.starting_categories(keyword_map, rows)
    notes = []
    for name, amount in budgets.value.items():
        target, _ = names.match_name(name, categories)
        if target is None:
            target = names.display_name(name)
            if amount <= 0 or not target:
                continue
            categories[target] = 0.0
            notes.append(f"Added the category '{target}' because your file gives it a budget.")
        categories[target] = float(amount)
    return categories, notes


# ---------------------------------------------------------------------------
# DERIVED INFORMATION: unmatched descriptions, the "Please check" list
# ---------------------------------------------------------------------------

def refresh(analysis: Analysis) -> Analysis:
    """Recompute what depends on the rows (unmatched descriptions, the Please-check list)."""
    unmatched = importer.unknown_descriptions(analysis.rows)
    return replace(analysis, unmatched=unmatched, checks=_please_check(replace(analysis, unmatched=unmatched)))


def _money(amount: float, currency: str) -> str:
    return f"{currency} {amount:,.2f}"


def _please_check(analysis: Analysis) -> list:
    rows, cur = analysis.rows, str(analysis.currency.value)
    checks = []

    payment_rows = rows[rows["Flags"].astype(str).str.contains(PAYMENT_FLAG)]
    if len(payment_rows):
        total = float(payment_rows["Amount"].sum())
        current = payment_rows["Type"].iloc[0]
        checks.append(CheckItem(
            "payments", f"{len(payment_rows)} payment(s), {_money(total, cur)} in total, from your loan / card payment log "
                        f"are recorded as {current}. {EXPLAIN_PAYMENT_TYPES}", total,
            "switch_payments_to_card" if current == LOAN_PAYMENT else "switch_payments_to_loan"))

    if analysis.opening.confidence != "high" or analysis.opening.alternatives:
        alt = "; ".join(f"{value:,.2f} ({source})" for value, source in analysis.opening.alternatives[:3])
        checks.append(CheckItem("opening", f"Opening balance {analysis.opening.value:,.2f} is {analysis.opening.confidence}-confidence: "
                                           f"{analysis.opening.source}." + (f" Other candidates: {alt}." if alt else "")))
    if analysis.currency.confidence == "low":
        checks.append(CheckItem("currency", f"Currency '{analysis.currency.value}' is a guess ({analysis.currency.source})."))
    if analysis.month.confidence != "high":
        checks.append(CheckItem("month", f"Month {analysis.month.value:%B %Y} is {analysis.month.confidence}-confidence "
                                         f"({analysis.month.source})."))
    for label, source in analysis.budgets.alternatives:
        checks.append(CheckItem("budget", f"Budgets disagree between sheets - {source}. Using the {analysis.budgets.source.split(' (')[0]}."))

    # categories: biggest amounts first; guessed (typo / AI) ones and the ones still without a category
    expenses = rows[rows["Type"] == EXPENSE]
    guessed = expenses[expenses["Flags"].astype(str).str.contains("category guessed|AI suggested")]
    for description, group in sorted(guessed.groupby(guessed["Description"].str.casefold(), sort=False),
                                     key=lambda item: -item[1]["Amount"].sum()):
        how = str(group["Flags"].iloc[0]).split("; ")
        how = next((f for f in how if f.startswith(("category guessed", "AI suggested"))), "guessed")
        total = float(group["Amount"].sum())
        if how == "AI suggested (low)":      # the AI said it is not sure: this goes to the very top of the list
            checks.append(CheckItem("ai_low", f"The AI is not sure about '{group['Description'].iloc[0]}' - it guessed "
                                              f"{group['Category'].iloc[0]} ({how}); {len(group)} row(s), {_money(total, cur)}. "
                                              "Please pick the right category.", total))
            continue
        checks.append(CheckItem("category", f"'{group['Description'].iloc[0]}' → {group['Category'].iloc[0]} ({how}); "
                                            f"{len(group)} row(s), {_money(total, cur)}.", total))
    for _, item in analysis.unmatched.iterrows():
        checks.append(CheckItem("category", f"'{item['Description']}' has no category; {int(item['Count'])} row(s), "
                                            f"{_money(float(item['Total']), cur)}.", float(item["Total"])))

    wrong_type = rows[rows["Flags"].astype(str).str.contains("not understood")]
    for _, row in wrong_type.iterrows():
        checks.append(CheckItem("type", f"'{row['Description']}': {row['Flags']} (recorded as {row['Type']}).", float(row["Amount"] or 0)))
    if len(expenses) >= UNUSUAL_MIN_ROWS:
        median = statistics.median(float(a) for a in expenses["Amount"].dropna())
        for _, row in expenses.iterrows():
            if median > 0 and row["Amount"] >= UNUSUAL_FACTOR * median and row["Amount"] >= 100:
                checks.append(CheckItem("unusual", f"'{row['Description']}' ({_money(float(row['Amount']), cur)}) is "
                                                   f"{row['Amount'] / median:.0f}× the typical expense ({median:,.2f}).", float(row["Amount"])))
    undated = rows[rows["Flags"].astype(str).str.contains("no date|date not understood|needs month")]
    if len(undated):
        checks.append(CheckItem("date", f"{len(undated)} row(s) have no usable date and are ignored by the Dashboard."))

    order = {"ai_low": -1, "payments": 0, "opening": 1, "currency": 2, "month": 3, "budget": 4, "type": 5, "unusual": 6, "date": 7, "category": 8}
    return sorted(checks, key=lambda c: (order.get(c.kind, 9), -(c.amount or 0)))


# ---------------------------------------------------------------------------
# ONE-CLICK ACTIONS
# ---------------------------------------------------------------------------

def apply_ai(analysis: Analysis, assignments: dict, ai_info: dict = None) -> Analysis:
    """Give expenses without a category the AI's suggestion ({description: (category, confidence)}); each such row is
    flagged 'AI suggested (confidence)'. Rows that already have a category are never changed."""
    wanted = {description.casefold(): value for description, value in assignments.items()}
    rows, count = analysis.rows.copy(), 0
    categories = dict(analysis.categories)
    for index, row in rows.iterrows():
        if row["Type"] == EXPENSE and is_blank(row["Category"]) and str(row["Description"]).casefold() in wanted:
            category, confidence = wanted[str(row["Description"]).casefold()]
            flags = [f for f in str(row["Flags"]).split("; ") if f and f != "no category"]
            rows.at[index, "Category"] = category
            rows.at[index, "Flags"] = "; ".join(flags + [f"AI suggested ({confidence})"])
            if category.casefold() not in {name.casefold() for name in categories}:
                categories[category] = 0.0            # e.g. "Other", which the AI may always answer
            count += 1
    return refresh(replace(analysis, rows=rows, categories=categories, ai_assigned=analysis.ai_assigned + count,
                           ai_info=ai_info or analysis.ai_info))


def with_edits(analysis: Analysis, config: BudgetConfig, rows: pd.DataFrame) -> Analysis:
    """The analysis as it stands after the user's edits: the currency, month, opening balance and category budgets from
    the settings they are looking at, and the (possibly edited) rows. Anything they changed is shown as 'edited by you'
    with full confidence; the unmatched list and the Please-check list are recomputed from the new rows."""
    def edited(finding: detect.Finding, value, changed: bool) -> detect.Finding:
        return detect.Finding(value, "high", "edited by you") if changed else finding

    currency = edited(analysis.currency, config.currency, str(config.currency) != str(analysis.currency.value))
    month = edited(analysis.month, config.month_start, config.month_start != analysis.month.value)
    opening = edited(analysis.opening, float(config.opening_balance),
                     abs(float(config.opening_balance) - float(analysis.opening.value)) > 0.005)
    budgets = edited(analysis.budgets, analysis.budgets.value, dict(config.categories) != dict(analysis.categories))
    rows = rows.copy()
    for column in ("Flags", "Source row"):
        if column not in rows.columns:
            rows[column] = None
    rows["Flags"] = rows["Flags"].map(lambda f: "" if is_blank(f) else str(f))
    return refresh(replace(analysis, currency=currency, month=month, opening=opening, budgets=budgets,
                           categories=dict(config.categories), rows=rows))


def switch_payments(analysis: Analysis, new_type: str) -> Analysis:
    """Record the loan / card log payments as `new_type` (Loan payment or Card bill payment) instead."""
    rows = analysis.rows.copy()
    mask = rows["Flags"].astype(str).str.contains(PAYMENT_FLAG)
    rows.loc[mask, "Type"] = new_type
    rows.loc[mask, "Description"] = rows.loc[mask, "Description"].str.replace("Loan / card payment", f"{new_type} (from the log)", regex=False)
    rows.loc[mask, "Flags"] = rows.loc[mask, "Flags"].map(lambda f: f"{f}; switched to {new_type}" if f else f"switched to {new_type}")
    # the Please-check item is kept (switched rows keep the flag) so the choice stays visible and reversible
    return refresh(replace(analysis, rows=rows))


# ---------------------------------------------------------------------------
# WHAT TO SHOW
# ---------------------------------------------------------------------------

def _source_or_edit(finding: detect.Finding, current_total: float, empty_text: str) -> str:
    """The finding's source while the rows still add up to what was detected, 'edited by you' once they do not."""
    if abs(current_total - float(finding.value)) >= 0.005:
        return "edited by you"
    return finding.source if current_total else empty_text


def summary_lines(analysis: Analysis) -> list:
    """[(label, value text, source, confidence)] for the summary card."""
    cur = str(analysis.currency.value)
    rows = analysis.rows
    expenses = rows[rows["Type"] == EXPENSE]
    income = rows[rows["Type"] == INCOME]
    lines = [
        ("Currency", cur, analysis.currency.source, analysis.currency.confidence),
        ("Month", f"{analysis.month.value:%B %Y}", analysis.month.source, analysis.month.confidence),
        ("Opening balance", _money(float(analysis.opening.value), cur), analysis.opening.source, analysis.opening.confidence),
        ("Income", _money(float(income["Amount"].sum()), cur) + f" ({len(income)} source(s))",
         _source_or_edit(analysis.income, float(income["Amount"].sum()), "no income list found - add income rows in the review table"),
         analysis.income.confidence if abs(float(income["Amount"].sum()) - float(analysis.income.value)) < 0.005 else "high"),
        ("Expenses", f"{len(expenses)} rows, {_money(float(expenses['Amount'].sum()), cur)}",
         f"sheet '{names.display_name(analysis.main_sheet)}'", "high"),
    ]
    payments = rows[rows["Type"].isin([LOAN_PAYMENT, CARD_BILL]) & rows["Flags"].astype(str).str.contains(PAYMENT_FLAG)]
    if len(payments):
        lines.append(("Loan / card payments", f"{len(payments)} payment(s), {_money(float(payments['Amount'].sum()), cur)} "
                                              f"(recorded as {payments['Type'].iloc[0]})",
                      _source_or_edit(analysis.payments, float(payments["Amount"].sum()), ""),
                      analysis.payments.confidence if abs(float(payments["Amount"].sum()) - float(analysis.payments.value)) < 0.005 else "high"))
    used = sorted({c for c in expenses["Category"].dropna()}, key=str.casefold)
    with_budget = sum(1 for v in analysis.categories.values() if v > 0)
    lines.append(("Categories", f"{len(analysis.categories)} ({len(used)} used, {with_budget} with a budget)",
                  analysis.budgets.source if analysis.budgets.value else "keyword map; no budget table found", analysis.budgets.confidence))
    return lines


def import_info(analysis: Analysis, rows: pd.DataFrame) -> importer.ImportResult:
    """The ImportResult the workbook's 'Import Notes' sheet is written from: the final (possibly edited) rows, counts
    recomputed from them, and every note - merges, duplicates, the AI step and the sanity check."""
    flags = rows["Flags"].astype(str)
    stats = dict(analysis.import_result.stats)
    stats.update({"rows": len(rows), "fill_down": int(flags.str.contains("date filled down").sum()),
                  "no_date": int(flags.str.contains("no date").sum()),
                  "uncategorised": int(((rows["Type"] == EXPENSE) & rows["Category"].isna()).sum())})
    notes = list(analysis.notes)
    notes.append(f"Currency {analysis.currency.value} ({analysis.currency.confidence}): {analysis.currency.source}.")
    notes.append(f"Month {analysis.month.value:%B %Y} ({analysis.month.confidence}): {analysis.month.source}.")
    notes.append(f"Opening balance {analysis.opening.value:,.2f} ({analysis.opening.confidence}): {analysis.opening.source}.")
    if analysis.income.value:
        notes.append(f"Income {analysis.income.value:,.2f}: {analysis.income.source}.")
    if analysis.payments.value:
        notes.append(f"Loan / card payments {analysis.payments.value:,.2f}: {analysis.payments.source}.")
    if analysis.ai_info:
        notes.append(f"{analysis.ai_assigned} categories were suggested by AI ({analysis.ai_info.get('provider')}, "
                     f"{analysis.ai_info.get('model')}); only the description texts were sent. They are marked 'AI suggested'.")
    difference = analysis.closing_difference
    if difference:
        notes.append(f"Sanity check: the file's own estimated closing balance is {difference[0]:,.2f}; ours is "
                     f"{difference[1]:,.2f} (difference {difference[2]:,.2f}).")
    return replace(analysis.import_result, rows=rows, stats=stats, notes=notes)
