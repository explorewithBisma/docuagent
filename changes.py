"""
changes.py
-----------
Cell-level change tracking. Every cleaning step and AI rule reports the
cells it changed into a ChangeLog (row, column, old value, new value, reason).
Everything is kept in memory - nothing is written to disk.

A cell can be changed more than once (e.g. "ERROR" -> blank -> calculated).
`ChangeLog.finalize()` merges those into ONE record per cell that shows the
original value, the final value and the final reason, and drops cells whose
row was later removed (duplicate / empty rows).

Pure pandas - no Streamlit.
"""

import pandas as pd

from quality import is_blank, parse_number

PLACEHOLDER = "placeholder"
CALCULATED = "calculated"
LOOKUP = "lookup"
TRIMMED = "trimmed"
AI_RULE = "ai_rule"
NUMERIC = "numeric"
FLAGGED = "flagged"   # not a change: a value that looks implausible, flagged and left as it is
MERGED = "merged"     # a blank cell filled in from a near-duplicate row that the user merged

REASON_ORDER = (PLACEHOLDER, CALCULATED, LOOKUP, AI_RULE, MERGED, TRIMMED, NUMERIC, FLAGGED)

REASON_LABELS = {
    PLACEHOLDER: "Placeholder cleared",
    CALCULATED: "Calculated exactly",
    LOOKUP: "Inferred from lookup",
    AI_RULE: "AI rule",
    MERGED: "Merged from a near-duplicate",
    TRIMMED: "Whitespace trimmed",
    NUMERIC: "Number converted",
    FLAGGED: "Flagged (not changed)",
}

# Soft, readable fill colours (hex, no '#'). Trimmed / converted cells get no fill.
REASON_COLORS = {
    CALCULATED: "C6E0B4",   # green
    LOOKUP: "FFE699",       # yellow
    PLACEHOLDER: "D9D9D9",  # light grey
    AI_RULE: "BDD7EE",      # light blue
    MERGED: "E4DFEC",       # light purple
}

FLAG_BORDER_COLOR = "E26B0A"   # orange-red border around a flagged cell
FLAG_FILL_COLOR = "F8D7DA"     # light red fill, used only when the cell has no change color of its own
FLAG_MEANING = "Orange-red border - flagged: looks implausible (the value was NOT changed)"

COLOR_MEANINGS = {
    CALCULATED: "Green - calculated exactly from other values in the row",
    LOOKUP: "Yellow - inferred from a pattern in your file (lookup)",
    PLACEHOLDER: "Grey - placeholder (e.g. ERROR) cleared to blank",
    AI_RULE: "Blue - changed by an approved AI rule",
    MERGED: "Purple - a blank filled in from a near-duplicate row you merged",
}


def _exact_text(value) -> str:
    """The cell as exact text; every kind of 'missing' (NaN, None, <NA>) is ''."""
    if not isinstance(value, str) and is_blank(value):
        return ""
    return str(value)


def values_differ(old, new, strict: bool = False) -> bool:
    """True if a cell really changed. Two blanks are the same. By default a
    number and the text of that same number ("3.0" and 3.0) count as the same;
    with strict=True the texts must match exactly (needed for trimming)."""
    if strict:
        # Exact text: a cell of spaces is NOT the same as an empty cell here,
        # because trimming "   " down to nothing is a change worth recording.
        return _exact_text(old) != _exact_text(new)

    old_blank, new_blank = is_blank(old), is_blank(new)
    if old_blank and new_blank:
        return False
    if old_blank != new_blank:
        return True
    if isinstance(old, str) or isinstance(new, str):
        old_number, new_number = parse_number(old), parse_number(new)
        if old_number is not None and new_number is not None:
            return old_number != new_number
        return str(old) != str(new)
    return bool(old != new)


class ChangeLog:
    """Collects cell changes while a sheet is cleaned (in memory only)."""

    def __init__(self):
        self.events = []
        self.removed = []            # rows removed as duplicates / empty, with their original content
        self.original_columns = []   # the column headers exactly as uploaded
        self.original_df = None      # the uploaded rows before any cleaning (set by the cleaner; near-duplicate removal reads removed rows from it)

    def record_removed(self, original: pd.DataFrame, labels, reason: str, duplicate_of: dict = None, details: dict = None,
                       filled: dict = None) -> None:
        """Remember rows that were removed: their row label (original position in the
        uploaded file), why, and their full ORIGINAL content (before any cleaning).
        `duplicate_of` maps a removed label to the label of the row that was kept; `details` says why a near-duplicate matched
        ("name almost same, phone same") and `filled` how many blank cells of the kept row it filled."""
        labels = list(labels)
        if not labels:
            return
        contents = original.loc[labels].to_numpy(dtype=object).tolist()
        for label, values in zip(labels, contents):
            self.removed.append({"index": label, "reason": reason, "duplicate_of": (duplicate_of or {}).get(label), "values": values,
                                 "detail": (details or {}).get(label, ""), "filled": (filled or {}).get(label, 0)})

    def finalize_removed(self) -> list:
        """The removed rows in file order: original_row (Excel row in the uploaded file, header =
        row 1), a plain-English reason, and the original cell values."""
        rows = []
        for item in sorted(self.removed, key=lambda item: item["index"]):
            if item["reason"] == "duplicate":
                text = f"Duplicate of row {item['duplicate_of'] + 2}" if item["duplicate_of"] is not None else "Duplicate of an earlier row"
            elif item["reason"] == "near_duplicate":
                text = f"Near-duplicate of row {item['duplicate_of'] + 2}" + (f" ({item['detail']})" if item["detail"] else "")
            else:
                text = "Fully empty row"
            row = {"original_row": item["index"] + 2, "kind": item["reason"], "reason": text, "values": item["values"]}
            if item["reason"] == "near_duplicate":
                row["filled"] = item.get("filled", 0)
            rows.append(row)
        return rows

    def add(self, index, column, old, new, reason, detail="", strict=False):
        """Record one cell change (ignored if the value did not really change)."""
        if values_differ(old, new, strict):
            self.events.append({"index": index, "column": column, "old": old, "new": new,
                                "reason": reason, "detail": detail})

    def record_diff(self, before: pd.DataFrame, after: pd.DataFrame, reason, detail="", strict=False, where=None):
        """Compare two versions of a DataFrame (same row labels) and record
        every cell that differs. `where(old, new)` can narrow what counts."""
        for column in after.columns:
            if column in before.columns:
                self.record_series(column, before[column], after[column], reason, detail, strict, where)

    def record_series(self, column, before: pd.Series, after: pd.Series, reason, detail="", strict=False, where=None):
        common = after.index.intersection(before.index)
        if before.loc[common].equals(after.loc[common]):
            return
        for index, old, new in zip(common, before.loc[common].tolist(), after.loc[common].tolist()):
            if where is None or where(old, new):
                self.add(index, column, old, new, reason, detail, strict)

    def finalize(self, final_df: pd.DataFrame) -> list:
        """One record per changed cell that still exists in `final_df`, in
        sheet order. Each record: row (Excel row in the cleaned sheet, header
        = row 1), position (0-based), column, old (the ORIGINAL value), new
        (the final value), reason, detail."""
        row_of = {label: position for position, label in enumerate(final_df.index)}
        column_order = {column: number for number, column in enumerate(final_df.columns)}
        merged = {}

        for event in self.events:
            key = (event["index"], event["column"])
            if key not in merged:
                merged[key] = dict(event)
            else:
                merged[key].update(new=event["new"], reason=event["reason"], detail=event["detail"])

        records = []
        for (index, column), record in merged.items():
            if index not in row_of or column not in column_order:
                continue  # the row (or column) was removed later on
            record["new"] = final_df.at[index, column]
            if not values_differ(record["old"], record["new"], strict=True):
                continue  # changed and changed back: nothing to show
            record["position"] = row_of[index]
            record["row"] = row_of[index] + 2
            records.append(record)

        records.sort(key=lambda record: (record["position"], column_order[record["column"]]))
        return records


def summarize(records: list) -> dict:
    """{reason: number of changed cells} for every reason, in display order."""
    counts = {reason: 0 for reason in REASON_ORDER}
    for record in records:
        counts[record["reason"]] += 1
    return counts


def reason_text(record: dict) -> str:
    """'Calculated exactly: Quantity 2 x Price Per Unit 3.0' style text."""
    label = REASON_LABELS[record["reason"]]
    return f"{label}: {record['detail']}" if record["detail"] else label


def comment_text(record: dict) -> str:
    """The text of the cell comment: what the cell was, and why it changed."""
    if record["reason"] == FLAGGED:
        return f"Flagged: {record['detail']}"

    old = record["old"]
    if (not isinstance(old, str) and is_blank(old)) or old == "":  # NA check first: pd.NA == "" raises
        was = "Was: (blank)"
    elif isinstance(old, str) and old != old.strip():
        was = f'Was: "{old}"'
    else:
        was = f"Was: {old}"

    if record["reason"] in (CALCULATED, LOOKUP, AI_RULE):
        return f"{was}\n{reason_text(record)}"
    return was
