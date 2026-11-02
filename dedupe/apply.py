"""
dedupe/apply.py
----------------
Applies the user's decisions about near-duplicate groups to a cleaned sheet and records everything in the change log.

    decisions = {group_id: Decision("merge" | "keep_one" | "keep_all", keep=position)}
    new_df = apply_decisions(df, change_log, groups, decisions)

* keep_all  - nothing happens.
* keep_one  - the other rows of the group are removed (to the Removed Rows sheet).
* merge     - like keep_one, but first every BLANK cell of the kept row is filled from the first removed row that has a value there.
              A cell that already has a value is never overwritten.
"""

from dataclasses import dataclass

import pandas as pd

from changes import MERGED
from quality import is_blank

KEEP_ONE, KEEP_ALL, MERGE = "keep_one", "keep_all", "merge"


@dataclass
class Decision:
    action: str
    keep: int = None                # position of the row to keep; None = the group's default


def fills_for(df: pd.DataFrame, keep: int, others: list) -> dict:
    """{column: (value, from_position)} - the blanks of row `keep` that a removed row can fill."""
    out = {}
    for column in df.columns:
        if not is_blank(df.iat[keep, df.columns.get_loc(column)]):
            continue
        for other in others:
            value = df.iat[other, df.columns.get_loc(column)]
            if not is_blank(value):
                out[column] = (value, other)
                break
    return out


def preview(df: pd.DataFrame, groups: list, decisions: dict) -> dict:
    """What applying would do: {'rows_removed', 'cells_filled', 'groups'} - shown before anything is changed."""
    removed = filled = applied = 0
    for group in groups:
        decision = decisions.get(group.id)
        if decision is None or decision.action == KEEP_ALL:
            continue
        keep = group.keep if decision.keep is None else decision.keep
        others = [r for r in group.rows if r != keep]
        applied += 1
        removed += len(others)
        if decision.action == MERGE:
            filled += len(fills_for(df, keep, others))
    return {"rows_removed": removed, "cells_filled": filled, "groups": applied}


def accept_all_high(groups: list) -> dict:
    """Decisions for 'Accept all high-confidence groups': keep the most complete row, fill its blanks, remove the rest."""
    return {g.id: Decision(MERGE, g.keep) for g in groups if g.level == "high"}


def apply_decisions(df: pd.DataFrame, log, groups: list, decisions: dict) -> pd.DataFrame:
    """Returns the sheet with the decided rows removed and blanks filled; `log` gets the filled cells and the removed rows."""
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
        fills = fills_for(df, keep, others) if decision.action == MERGE else {}
        for column, (value, source) in fills.items():
            old = out.at[keep_label, column]
            if not _fits(out[column], value):
                out[column] = out[column].astype(object)       # a text value into a number column: keep it, as text
            out.at[keep_label, column] = value
            log.add(keep_label, column, old, value, MERGED, f"filled from row {int(df.index[source]) + 2}", strict=True)
        labels = [df.index[r] for r in others]
        why = {df.index[r]: _short(group.reasons.get(r, "")) for r in others}
        log.record_removed(log.original_df if log.original_df is not None else df, labels, "near_duplicate",
                           duplicate_of={label: keep_label for label in labels}, details=why,
                           filled={label: len(fills) if label == labels[0] else 0 for label in labels})
        drop_labels += labels
    return out.drop(index=drop_labels)


def _fits(series: pd.Series, value) -> bool:
    try:
        return pd.api.types.is_numeric_dtype(series) == isinstance(value, (int, float)) and not isinstance(value, bool)
    except TypeError:
        return False


def _short(phrase: str) -> str:
    return phrase.split(" (through")[0]
