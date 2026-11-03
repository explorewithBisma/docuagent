"""
findings.py
------------
"Here's what I found": a plain-language report of what is wrong with each sheet of an uploaded file, BEFORE anything is changed.

There is no second detector. For each sheet the real cleaning steps (cleaning.py, derive.py, flags.py, the near-duplicate finder) are run once
on a COPY of the data with a change log, the cleaned copy is thrown away, and the numbers they report become the findings - so a count shown
here is exactly what "Clean my file" will do. The uploaded data is never touched.

  is_data_sheet(df)                -> (True | False, why)         a "Read Me" sheet is not a data table
  describe_sheet(df, placeholders) -> SheetFindings               the findings of one sheet, each with a count and an example

Each Finding says which cleaning step it belongs to (`step`), whether that fix is ticked by default, and which cells / rows it will touch
(for the highlighted preview). Risky fixes (possible duplicate people) are never ticked by default and are never applied automatically.
"""

from dataclasses import dataclass, field

import pandas as pd

import derive
from changes import CALCULATED, NUMERIC, PLACEHOLDER, TRIMMED, ChangeLog
from cleaning import clean_column_headers, clean_dataframe_with_steps
from flags import find_flags
from locales import DEFAULT_LOCALE
from quality import is_blank

NEAR_DUPLICATE_ROW_LIMIT = 5000            # above this the possible-duplicates check runs only when the user opens the review
SAFE_STEPS = ("headers", "trim", "placeholders", "numeric", "empty", "duplicates")
SHOWN_EXAMPLES = 3


@dataclass
class Finding:
    key: str                        # unique within a sheet ("headers", "trim", ..., "fill:Total = Qty x Unit Price")
    step: str                       # the cleaning step it belongs to: headers | trim | placeholders | numeric | empty | duplicates | fills | flags | near
    text: str                       # one plain sentence with the count and an example
    count: int = None               # None: not counted yet (a big sheet's possible duplicates)
    default_on: bool = True
    review: bool = False            # risky: shown as "Review", never applied automatically
    help: str = ""
    cells: tuple = ()               # ((row label, column name in the UPLOADED sheet), ...) the fix changes
    rows: tuple = ()                # row labels the fix removes
    relationship: dict = None       # for a calculated fill: the relationship it uses
    label: str = ""                 # the relationship's label without its "(holds on ...)" part, to match it again later


@dataclass
class SheetFindings:
    rows: int
    columns: int
    is_data: bool
    why: str = ""
    findings: list = field(default_factory=list)

    def get(self, step: str):
        return next((f for f in self.findings if f.step == step), None)


def plural(count: int, singular: str, plural_form: str = None) -> str:
    return f"{count:,} {singular if count == 1 else (plural_form or singular + 's')}"


def is_data_sheet(df: pd.DataFrame):
    """(True, '') for a table; (False, why) for a sheet that is just notes: only one column with anything in it, or fewer than two rows.
    (Only used for Excel files with several sheets - a CSV you upload is always treated as data.)"""
    filled = [column for column in df.columns if any(not is_blank(v) for v in df[column])]
    if len(filled) <= 1:
        return False, "it has only one column of text, like a note or a title"
    if len(df) < 2:
        return False, "it has fewer than two rows"
    return True, ""


def _short(value, limit: int = 28) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _first_text(values) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _examples(items, limit: int = SHOWN_EXAMPLES) -> str:
    unique = list(dict.fromkeys(str(i) for i in items))
    return ", ".join(unique[:limit]) + (" ..." if len(unique) > limit else "")


def describe_sheet(df: pd.DataFrame, placeholders, locale: str = DEFAULT_LOCALE, near_limit: int = NEAR_DUPLICATE_ROW_LIMIT) -> SheetFindings:
    """The findings of one sheet (see the module docstring). Works on copies: `df` is not changed."""
    is_data, why = is_data_sheet(df)
    result = SheetFindings(rows=len(df), columns=len(df.columns), is_data=is_data, why=why)
    placeholders = list(placeholders)

    steps = {**{step: True for step in SAFE_STEPS}, "fills": False}
    log = ChangeLog()
    out, stats = clean_dataframe_with_steps(df.copy(), steps, {"placeholders": placeholders, "changes": log})
    renames = dict((new, old) for old, new in stats["header_renames"])               # cleaned name -> uploaded name
    original_name = lambda column: renames.get(column, column)
    events = lambda reason: [e for e in log.events if e["reason"] == reason]
    findings = result.findings

    # --- column headers ----------------------------------------------------------------------------------------------------------
    if stats["header_renames"]:
        n, (old, new) = len(stats["header_renames"]), stats["header_renames"][0]
        text = (f"Column header '{old}' has extra spaces" if n == 1 and old.strip() == new else
                f"{plural(n, 'column header')} {'has' if n == 1 else 'have'} extra spaces or {'is' if n == 1 else 'are'} blank (for example '{old}')")
        findings.append(Finding("headers", "headers", text, n, help="The spaces are removed from the names; blank names get a name like Column_3."))

    # --- spaces in cells -----------------------------------------------------------------------------------------------------------
    if stats["cells_trimmed"]:
        trimmed = events(TRIMMED)
        n = stats["cells_trimmed"]
        example = f" (for example '{_short(trimmed[0]['old'])}')" if trimmed else ""
        findings.append(Finding("trim", "trim", f"Extra spaces in {plural(n, 'cell')}{example}", n, help="Spaces at the start and end of a cell are removed.",
                                cells=tuple((e["index"], original_name(e["column"])) for e in trimmed)))

    # --- placeholders ------------------------------------------------------------------------------------------------------------------
    placeholder_stats = stats.get("placeholders") or {"total": 0, "by_column": {}}
    if placeholder_stats["total"]:
        words = [word for counts in placeholder_stats["by_column"].values() for word in counts]
        n = placeholder_stats["total"]
        findings.append(Finding("placeholders", "placeholders", f"{plural(n, 'placeholder value')} like {_examples(words)}", n,
                                help="Cells that only say N/A, ERROR, UNKNOWN ... become truly empty, so they can be counted and filled in later. "
                                     "You can change the list of words under Customize.",
                                cells=tuple((e["index"], original_name(e["column"])) for e in events(PLACEHOLDER))))

    # --- numbers stored as text ----------------------------------------------------------------------------------------------------
    converted = (stats.get("numeric") or {}).get("converted", [])
    if converted:
        names = [c["column"] for c in converted]
        findings.append(Finding("numeric", "numeric", f"Numbers saved as text in {plural(len(names), 'column')} ({_examples(names)})", len(names),
                                help="They become real numbers, so they can be added up and sorted. Codes, IDs and anything with leading zeros are never converted.",
                                cells=tuple((e["index"], original_name(e["column"])) for e in events(NUMERIC))))

    # --- empty rows and columns ------------------------------------------------------------------------------------------------------
    empty_rows, empty_columns = stats["empty_rows_removed"] or 0, stats["empty_columns_removed"] or 0
    if empty_rows or empty_columns:
        parts = []
        if empty_rows:
            parts.append(plural(empty_rows, "empty row"))
        if empty_columns:
            gone = [c for c in clean_column_headers(df.copy())[0].columns if c not in out.columns]
            parts.append(f"{plural(empty_columns, 'empty column')} ({_examples(gone)})")
        findings.append(Finding("empty", "empty", " and ".join(parts), empty_rows + empty_columns, help="Rows and columns with nothing in them are removed.",
                                rows=tuple(r["index"] for r in log.removed if r["reason"] == "empty")))

    # --- duplicates --------------------------------------------------------------------------------------------------------------------
    if stats["duplicates_removed"]:
        removed = [r for r in log.removed if r["reason"] == "duplicate"]
        name = _first_text(removed[0]["values"]) if removed else ""
        n = stats["duplicates_removed"]
        findings.append(Finding("duplicates", "duplicates", f"{plural(n, 'duplicate row')}" + (f" ({_short(name)})" if name else ""), n,
                                help="Rows that are exact copies of an earlier row are removed (and listed on the 'Removed Rows' sheet). Untick this if repeats are "
                                     "legitimate in your data.", rows=tuple(r["index"] for r in removed)))

    # --- values that can be calculated exactly ------------------------------------------------------------------------------------------
    relationships = derive.detect_relationships(out)
    for relationship in relationships["products"]:
        fill_log = ChangeLog()
        _, fill_stats = derive.apply_fills(out.copy(), [relationship], fill_log)
        filled = fill_stats["calculated"]
        n = sum(filled.values())
        if not n:
            continue
        formula = relationship["label"].split("  (")[0]
        per_column = ", ".join(f"{count} in {column}" for column, count in filled.items())
        findings.append(Finding(f"fill:{formula}", "fills", f"{plural(n, 'missing value')} can be calculated exactly from {formula} ({per_column})", n,
                                help="This holds on every row where all the values are present, so a missing one can be worked out exactly. Existing values are never changed.",
                                cells=tuple((e["index"], original_name(e["column"])) for e in fill_log.events if e["reason"] == CALCULATED),
                                relationship=relationship, label=formula))

    # --- unusual values (marked, never changed) -------------------------------------------------------------------------------------------
    flagged = find_flags(df, placeholders)["flags"]
    if flagged:
        by_column = {}
        for flag in flagged:
            by_column.setdefault(flag["column"], []).append(flag["value"])
        column, values = max(by_column.items(), key=lambda item: len(item[1]))
        shown = ", ".join(f"{v:g}" if isinstance(v, (int, float)) else str(v) for v in values[:SHOWN_EXAMPLES])
        n = len(flagged)
        where = f"in {column}" if len(by_column) == 1 else f"in {plural(len(by_column), 'column')} (for example {column}"
        text = f"{plural(n, 'unusual value')} {where} ({shown})" if len(by_column) == 1 else f"{plural(n, 'unusual value')} {where}: {shown})"
        findings.append(Finding("flags", "flags", text, n,
                                help="Numbers that look wrong (a negative age, 999 ...) get a red border and a note. They are NOT changed or deleted.",
                                cells=tuple((f["index"], f["column"]) for f in flagged)))

    # --- possible duplicate people (never automatic) ----------------------------------------------------------------------------------------
    if is_data:
        if len(out) <= near_limit:
            from dedupe.analyze import DedupeOptions, find_near_duplicates
            found = find_near_duplicates(out, DedupeOptions(locale=locale))
            if found.groups:
                n = len(found.groups)
                names = [str(out.iloc[g.rows[0]][found.profile.used("name")[0].column]) for g in found.groups if found.profile.used("name")]
                findings.append(Finding("near", "near", f"{plural(n, 'possible duplicate person', 'possible duplicate people')}"
                                                         + (f" ({_examples(names, 2)})" if names else "") + " - Review", n, default_on=False, review=True,
                                        help="Rows that look like the same person written differently. Nothing is removed unless you decide, in the review after cleaning."))
        else:
            findings.append(Finding("near", "near", "Possible duplicate people are checked when you open the review (this sheet is large) - Review", None,
                                    default_on=False, review=True, help="Open 'Look for near-duplicates' after cleaning."))
    return result
