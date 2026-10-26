"""
doctor/output.py
-----------------
Turns the Doctor's Report into the things the user takes away: the highlighted copy of the workbook (cells coloured, a note with
the reason on each, a report sheet at the end) and a plain-text report. The highlighted copy is made by toolkit/highlight.py, which
checks it before it is handed out; if that fails the caller keeps only the text report.
"""

from toolkit.highlight import Annotation, PatchError, PatchResult, ReportRow, patch_workbook

FOOTER = ("Excel Doctor only added cell colours, the notes and this sheet. No number, text or formula in your workbook was changed. "
          "Red = serious, yellow = worth checking, blue = small thing.")


def note_for(issue) -> str:
    parts = [f"[{issue.severity.upper()}] {issue.title}", issue.detail]
    if issue.evidence:
        parts.append(f"Evidence: {issue.evidence}")
    if issue.fix:
        parts.append(f"Suggestion: {issue.fix}")
    return "\n".join(parts)


def annotations(report) -> list:
    return [Annotation(sheet, coord, issue.severity, note_for(issue)) for issue in report.issues for sheet, coord in issue.locations]


def report_rows(report) -> list:
    return [ReportRow(number, issue.severity, issue.where, issue.title, issue.detail, issue.evidence, issue.fix,
                      issue.locations[0] if issue.locations else None) for number, issue in enumerate(report.issues, start=1)]


def make_highlighted(original: bytes, report) -> PatchResult:
    """The validated highlighted copy. Raises PatchError (with a message for the user) when it cannot be made safely."""
    if not report.issues:
        raise PatchError("There is nothing to highlight.")
    return patch_workbook(original, annotations(report), report_rows(report), FOOTER)


def report_text(report, file_name: str = "") -> str:
    """The whole report as plain text (for the download that is always available)."""
    lines = [f"Excel Doctor report" + (f" for {file_name}" if file_name else ""), "=" * 60, ""]
    lines.append("WHAT I FOUND")
    for fact in report.facts:
        lines.append(f"- {fact.label}: {fact.value}   [{fact.confidence}; {fact.source}]")
    counts = report.counts()
    lines += ["", f"PLEASE CHECK  ({counts['high']} serious, {counts['medium']} worth checking, {counts['low']} small)"]
    if not report.issues:
        lines.append("- Nothing to check: no problems were found.")
    for number, issue in enumerate(report.issues, start=1):
        lines.append(f"{number}. [{issue.severity.upper()}] {issue.title}")
        lines.append(f"   Where: {issue.where or '-'}")
        lines.append(f"   {issue.detail}")
        if issue.evidence:
            lines.append(f"   Evidence: {issue.evidence}")
        if issue.fix:
            lines.append(f"   Suggestion: {issue.fix}")
    if report.notes:
        lines += ["", "NOT CHECKED / NOTES"] + [f"- {note}" for note in report.notes]
    lines += ["", "Excel Doctor never changes your numbers: it only reads your workbook."]
    return "\n".join(lines)
