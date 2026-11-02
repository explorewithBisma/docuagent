"""
dedupe/apply.py
----------------
Applies the user's decisions about near-duplicate groups to a cleaned sheet and records everything in the change log.

    decisions = {group_id: Decision("merge" | "keep_one" | "keep_all", keep=position)}
    new_df = apply_decisions(df, change_log, groups, decisions, rules)

* keep_all  - nothing happens.
* keep_one  - the other rows of the group are removed (to the Removed Rows sheet).
* merge     - like keep_one, but first the kept row is completed from the removed rows:
    - a column with a merge rule (contact lists only) is combined: totals are added up (Sum), a "last ... date" takes the
      Latest, a "first ... date" the Earliest - so the kept row says 5700, not just the 4500 it had;
    - every other column keeps the kept row's value and only its BLANKS are filled. A value that is there is never overwritten.
"""

import numbers
import warnings
from dataclasses import dataclass

import pandas as pd

from changes import COMBINED, MERGED
from quality import _name_words, is_blank

KEEP_ONE, KEEP_ALL, MERGE = "keep_one", "keep_all", "merge"
KEEP, SUM, LATEST, EARLIEST = "keep", "sum", "latest", "earliest"
RULES = (KEEP, SUM, LATEST, EARLIEST)

SUM_WORDS = {"total", "spent", "spend", "amount", "count", "orders", "visits", "points", "balance", "revenue", "purchases", "sales", "quantity", "qty"}
NOT_SUM_WORDS = {"average", "avg", "rate", "price", "percent", "percentage", "ratio", "score", "rating", "age", "id", "code", "no", "zip", "postcode"}
LATEST_WORDS = {"last", "latest", "recent", "most"}
EARLIEST_WORDS = {"first", "joined", "created", "signup", "registered", "since", "start", "member"}
IDENTITY_ROLES = ("name", "email", "phone", "id", "company", "address", "dob", "place")


@dataclass
class Decision:
    action: str
    keep: int = None                # position of the row to keep; None = the group's default


# ---------------------------------------------------------------------------------------------------------------
# MERGE RULES
# ---------------------------------------------------------------------------------------------------------------
def _number(value):
    if is_blank(value) or isinstance(value, bool):
        return None
    if isinstance(value, numbers.Real):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _date(value):
    if is_blank(value):
        return None
    if hasattr(value, "year") and hasattr(value, "month"):
        return pd.Timestamp(value)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        stamp = pd.to_datetime(str(value).strip(), errors="coerce")
    return None if pd.isna(stamp) else stamp


def column_kinds(df: pd.DataFrame, profile) -> dict:
    """{column: 'number' | 'date'} for the columns a merge rule makes sense for: not identity columns, and numbers or dates."""
    from dedupe.columns import _is_datetime_like, _values
    identity = {r.column for r in profile.roles if r.used and r.role in IDENTITY_ROLES}
    kinds = {}
    for column in df.columns:
        if column in identity:
            continue
        series = df[column]
        if _is_datetime_like(series):
            kinds[column] = "date"
        else:
            values = _values(series)
            if values and sum(_number(v) is not None for v in values) / len(values) >= 0.9:
                kinds[column] = "number"
    return kinds


def suggest_rules(df: pd.DataFrame, profile) -> dict:
    """{column: rule} for every column that is suggested a rule other than 'keep'. Contact lists only - never for transactions."""
    if profile.kind != "contacts":
        return {}
    rules = {}
    for column, kind in column_kinds(df, profile).items():
        tokens = _name_words(column)
        if kind == "date":
            if tokens & LATEST_WORDS:
                rules[column] = LATEST
            elif tokens & EARLIEST_WORDS:
                rules[column] = EARLIEST
        elif tokens & SUM_WORDS and not tokens & NOT_SUM_WORDS:
            rules[column] = SUM
    return rules


def _show(value) -> str:
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    number = _number(value)
    if number is not None and not isinstance(value, str):
        return f"{number:.6f}".rstrip("0").rstrip(".")
    return str(value)


def plan_merge(df: pd.DataFrame, keep: int, others: list, rules: dict):
    """-> ({column: (new value, one-line text, log detail)}, [(column, why it could not be combined)]).
    Only columns whose value really changes appear."""
    changes, skipped = {}, []
    for column, rule in sorted((rules or {}).items(), key=lambda item: item[1] != SUM):      # totals first, then dates
        if rule == KEEP or column not in df.columns:
            continue
        position = df.columns.get_loc(column)
        present = [(r, df.iat[r, position]) for r in [keep] + others if not is_blank(df.iat[r, position])]
        if not present:
            continue
        current = df.iat[keep, position]
        rows = ", ".join(str(int(df.index[r]) + 2) for r, _ in present)
        if rule == SUM:
            parsed = [_number(v) for _, v in present]
            if any(n is None for n in parsed):
                skipped.append((column, "not every value is a number"))
                continue
            total = sum(parsed)
            new = int(total) if total.is_integer() and all(float(n).is_integer() for n in parsed) else round(total, 6)
            if _number(current) is not None and float(_number(current)) == float(new):
                continue
            shown = " + ".join(_show(v) for _, v in present)
            text = f"{column} {shown} = {_show(new)}" if len(present) > 1 else f"{column} -> {_show(new)}"
            changes[column] = (new, text, f"sum of {shown} (rows {rows} of your file)")
        else:
            stamps = [_date(v) for _, v in present]
            if any(s is None for s in stamps):
                skipped.append((column, "not every value is a date"))
                continue
            pick = max if rule == LATEST else min
            chosen = pick(range(len(present)), key=lambda k: stamps[k])
            new = present[chosen][1]
            if _date(current) is not None and _date(current) == stamps[chosen]:
                continue
            word = "latest" if rule == LATEST else "earliest"
            changes[column] = (new, f"{column} -> {_show(new)}", f"{word} of {', '.join(_show(v) for _, v in present)} (rows {rows} of your file)")
    return changes, skipped


def describe_merge(df: pd.DataFrame, group, keep: int, rules: dict) -> str:
    """The group's merge in one line: 'Total Spent 4500 + 1200 = 5700; Last Order Date -> 2026-10-15'."""
    changes, _ = plan_merge(df, keep, [r for r in group.rows if r != keep], rules)
    return "; ".join(text for _, text, _ in changes.values())


# ---------------------------------------------------------------------------------------------------------------
# BLANKS, PREVIEW, APPLY
# ---------------------------------------------------------------------------------------------------------------
def fills_for(df: pd.DataFrame, keep: int, others: list, skip=()) -> dict:
    """{column: (value, from_position)} - the blanks of row `keep` that a removed row can fill (columns in `skip` are combined instead)."""
    out = {}
    for column in df.columns:
        if column in skip or not is_blank(df.iat[keep, df.columns.get_loc(column)]):
            continue
        for other in others:
            value = df.iat[other, df.columns.get_loc(column)]
            if not is_blank(value):
                out[column] = (value, other)
                break
    return out


def _effective_rules(rules, skipped_columns=()):
    return {c for c, r in (rules or {}).items() if r != KEEP and c not in skipped_columns}


def preview(df: pd.DataFrame, groups: list, decisions: dict, rules: dict = None) -> dict:
    """What applying would do: rows removed, blanks filled, values combined - shown before anything is changed."""
    removed = filled = combined = applied = 0
    for group in groups:
        decision = decisions.get(group.id)
        if decision is None or decision.action == KEEP_ALL:
            continue
        keep = group.keep if decision.keep is None else decision.keep
        others = [r for r in group.rows if r != keep]
        applied += 1
        removed += len(others)
        if decision.action == MERGE:
            changes, skipped = plan_merge(df, keep, others, rules)
            combined += len(changes)
            filled += len(fills_for(df, keep, others, _effective_rules(rules, [c for c, _ in skipped])))
    return {"rows_removed": removed, "cells_filled": filled, "cells_combined": combined, "groups": applied}


def accept_all_high(groups: list) -> dict:
    """Decisions for 'Accept all high-confidence groups': keep the most complete row, fill its blanks, remove the rest."""
    return {g.id: Decision(MERGE, g.keep) for g in groups if g.level == "high"}


def apply_decisions(df: pd.DataFrame, log, groups: list, decisions: dict, rules: dict = None) -> pd.DataFrame:
    """Returns the sheet with the decided rows removed and blanks filled / values combined; `log` gets every changed cell and the removed rows."""
    out = df.copy()
    drop_labels = []
    for group in groups:
        decision = decisions.get(group.id)
        if decision is None or decision.action == KEEP_ALL:
            continue
        keep = group.keep if decision.keep is None else decision.keep
        if keep not in group.rows:
            raise ValueError(f"Row {keep} is not part of group {group.id}.")
        others = [r for r in group.rows if r != keep]
        keep_label = df.index[keep]
        combos, fills = {}, {}
        if decision.action == MERGE:
            combos, skipped = plan_merge(df, keep, others, rules)
            fills = fills_for(df, keep, others, _effective_rules(rules, [c for c, _ in skipped]))
        for column, (value, _text, detail) in combos.items():
            old = out.at[keep_label, column]
            _assign(out, keep_label, column, value)
            log.add(keep_label, column, old, value, COMBINED, detail, strict=True)
        for column, (value, source) in fills.items():
            old = out.at[keep_label, column]
            _assign(out, keep_label, column, value)
            log.add(keep_label, column, old, value, MERGED, f"filled from row {int(df.index[source]) + 2}", strict=True)
        labels = [df.index[r] for r in others]
        why = {df.index[r]: _short(group.reasons.get(r, "")) for r in others}
        log.record_removed(log.original_df if log.original_df is not None else df, labels, "near_duplicate",
                           duplicate_of={label: keep_label for label in labels}, details=why,
                           filled={labels[0]: len(fills)}, combined={labels[0]: len(combos)})
        drop_labels += labels
    return out.drop(index=drop_labels)


def _assign(frame: pd.DataFrame, label, column, value) -> None:
    if not _fits(frame[column], value):
        frame[column] = frame[column].astype(object)       # a text value into a number column: keep it, as text
    frame.at[label, column] = value


def _fits(series: pd.Series, value) -> bool:
    try:
        if series.dtype == object:
            return True
        return pd.api.types.is_numeric_dtype(series) == isinstance(value, numbers.Real) and not isinstance(value, bool)
    except TypeError:
        return False


def _short(phrase: str) -> str:
    return phrase.split(" (through")[0]
