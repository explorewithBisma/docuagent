"""
datarules/output.py
--------------------
What the user takes away from a monthly check: a highlighted COPY of the file (the cells that broke a rule coloured, a note on each, a
"Rule report" sheet at the end - made by toolkit/highlight.py, which checks it before it is handed out) and a plain-text report.
The data itself is never changed.
"""

import io

import pandas as pd
from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from toolkit.highlight import Annotation, PatchError, PatchResult, ReportRow, patch_workbook

SHEET_TITLE = "Rule report"
AUTHOR = "Monthly check"
MAX_NOTES = 1500                    # notes in one copy (colours and the report sheet still cover everything we mark)
FOOTER = ("The monthly check only added cell colours, the notes and this sheet. No value in your file was changed. "
          "Red = serious, yellow = worth checking, blue = small thing. A blank cell cannot be coloured, so a column with blanks is marked on its header.")


def workbook_from_raw(raw: pd.DataFrame, sheet: str = "Data") -> bytes:
    """A plain .xlsx holding the uploaded rows exactly as read (title lines, header and data in their original rows and columns) - for a CSV upload,
    so it can be highlighted like a workbook."""
    book = Workbook()
    ws = book.active
    ws.title = sheet
    for r, row in enumerate(raw.itertuples(index=False), start=1):
        for c, value in enumerate(row, start=1):
            if value is not None and not (isinstance(value, float) and pd.isna(value)):
                ws.cell(row=r, column=c, value=value.item() if hasattr(value, "item") else value)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def annotations(result, sheet: str) -> list:
    return [Annotation(sheet, f"{get_column_letter(column)}{row}", severity, text) for severity, row, column, text in result.cells[:MAX_NOTES]]


def report_rows(result) -> list:
    rows = []
    for number, issue in enumerate(result.report.issues, start=1):
        rows.append(ReportRow(number, issue.severity, issue.where, issue.title, issue.detail, issue.evidence, issue.fix, issue.locations[0] if issue.locations else None))
    if not rows:
        counts = result.counts()
        rows.append(ReportRow(1, "low", "-", f"All {counts['pass']} rules passed", f"{result.rows:,} rows were checked and none broke a rule.", "", ""))
    return rows


def make_highlighted(original: bytes, result, sheet: str) -> PatchResult:
    """The validated highlighted copy of `original` (an .xlsx). Raises PatchError (with a message for the user) when it cannot be made safely."""
    return patch_workbook(original, annotations(result, sheet), report_rows(result), FOOTER, SHEET_TITLE, AUTHOR)


def report_text(result, file_name: str = "", rules_name: str = "") -> str:
    lines = ["Monthly check" + (f" of {file_name}" if file_name else ""), "=" * 60, ""]
    lines.append(f"RESULT: {result.status}")
    for fact in result.report.facts:
        lines.append(f"- {fact.label}: {fact.value}   [{fact.source}]")
    counts = result.report.counts()
    lines += ["", f"PLEASE CHECK  ({counts['high']} serious, {counts['medium']} worth checking, {counts['low']} small)"]
    if not result.report.issues:
        lines.append("- Nothing to check: every rule passed.")
    for number, issue in enumerate(result.report.issues, start=1):
        lines.append(f"{number}. [{issue.severity.upper()}] {issue.title}")
        lines.append(f"   Where: {issue.where or '-'}")
        lines.append(f"   {issue.detail}")
        if issue.evidence:
            lines.append(f"   Evidence: {issue.evidence}")
        if issue.fix:
            lines.append(f"   Suggestion: {issue.fix}")
    lines += ["", "EVERY RULE"]
    for o in result.outcomes:
        where = f" on '{o.rule.column}'" if o.rule.column else ""
        lines.append(f"- {o.status.upper():8} {o.rule.label}{where}" + (f" ({o.failing_total} problem(s))" if o.status == "fail" else "") + (f" - {o.note}" if o.note and o.status == "skipped" else ""))
    if result.notes:
        lines += ["", "NOTES"] + [f"- {n}" for n in result.notes]
    lines += ["", "The monthly check never changes your data: it only reads your file."]
    return "\n".join(lines)
