"""
xlsx_export.py
---------------
Builds the professional Excel download: the user's sheets (same names, same
order) with every changed cell colour-coded and commented, plus - optionally -
a "Cleaning Summary" sheet and a "Change Log" sheet added at the END.

Everything is built in memory and returned as bytes.
"""

import io
from datetime import datetime

import pandas as pd
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from changes import (
    COLOR_MEANINGS, REASON_COLORS, REASON_LABELS, REASON_ORDER, comment_text, reason_text, summarize,
)
from io_utils import MAX_WIDTH, MIN_WIDTH, WIDTH_SAMPLE_ROWS, _as_phone_text
from quality import _name_words, is_blank, is_phone_like, profile_dataframe

MAX_LOG_ROWS = 50_000       # Change Log sheet cap
MAX_COMMENTS = 20_000       # cell-comment cap (every comment adds weight to the file)
AUTHOR = "Excel Cleaner"

HEADER_FILL = PatternFill("solid", fgColor="E8EEF7")
SECTION_FILL = PatternFill("solid", fgColor="F2F2F2")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

_MONEY_WORDS = {"price", "amount", "cost", "total", "fee", "salary", "revenue", "sales", "balance", "paid",
                "payment", "spent", "charge", "tax", "income", "profit", "wage", "fare"}


def fill_for(reason):
    """The highlight fill for a change reason, or None (trimmed / converted cells are not coloured)."""
    color = REASON_COLORS.get(reason)
    return PatternFill("solid", fgColor=color) if color else None


def number_format_for(column, series: pd.Series):
    """Whole numbers without decimals; money-like columns with 2 decimals and a
    thousands separator; other decimal columns with 2 decimals when that is
    all they need. None means 'leave as General'."""
    if pd.api.types.is_bool_dtype(series):
        return None
    if pd.api.types.is_integer_dtype(series):
        return "0"
    if not pd.api.types.is_float_dtype(series):
        return None

    values = series.dropna().astype("float64")
    if values.empty:
        return None
    if _name_words(column) & _MONEY_WORDS:
        return "#,##0.00"
    if (values == values.round()).all():
        return "0"
    if ((values * 100).round(6) % 1 == 0).all():
        return "0.00"
    return None


def _style_header(worksheet, columns: int) -> None:
    for number in range(1, columns + 1):
        cell = worksheet.cell(row=1, column=number)
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL
        cell.border = BORDER
        cell.alignment = Alignment(vertical="center")


def _fit_widths(worksheet, df: pd.DataFrame) -> None:
    for position, column in enumerate(df.columns, start=1):
        lengths = [len(str(column))]
        lengths += [len(str(value)) for value in df[column].head(WIDTH_SAMPLE_ROWS) if not is_blank(value)]
        worksheet.column_dimensions[get_column_letter(position)].width = min(max(max(lengths) + 3, MIN_WIDTH), MAX_WIDTH)


def _style_data_sheet(worksheet, df: pd.DataFrame, text_columns: list, records: list, comment_budget: list) -> None:
    """Header, borders, number formats, phone text, filter, freeze, widths -
    then the colour fills and comments for every changed cell."""
    columns = len(df.columns)
    rows = len(df)
    if columns == 0:
        return

    _style_header(worksheet, columns)
    formats = {number: number_format_for(column, df[column]) for number, column in enumerate(df.columns, start=1)}

    for row in range(2, rows + 2):
        for number in range(1, columns + 1):
            cell = worksheet.cell(row=row, column=number)
            cell.border = BORDER
            if df.columns[number - 1] in text_columns:
                cell.number_format = "@"
            elif formats[number]:
                cell.number_format = formats[number]

    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = f"A1:{get_column_letter(columns)}{rows + 1}"
    _fit_widths(worksheet, df)

    column_number = {column: number for number, column in enumerate(df.columns, start=1)}
    for record in records:
        cell = worksheet.cell(row=record["row"], column=column_number[record["column"]])
        fill = fill_for(record["reason"])
        if fill:
            cell.fill = fill
        if comment_budget[0] > 0:
            text = comment_text(record)
            comment = Comment(text, AUTHOR)
            comment.width = 300
            comment.height = 40 + 18 * (text.count("\n") + 1 + len(text) // 45)
            cell.comment = comment
            comment_budget[0] -= 1


def _unique_title(wanted: str, taken: list) -> str:
    title, number = wanted, 2
    while title in taken:
        title = f"{wanted} ({number})"
        number += 1
    return title[:31]


def _blank_text(value):
    """Blank cells are shown as '(blank)' in the Change Log so they are not mistaken for missing data."""
    if not isinstance(value, str) and is_blank(value):
        return "(blank)"
    return "(blank)" if value == "" else value


# ---------------------------------------------------------------------------
# CLEANING SUMMARY SHEET
# ---------------------------------------------------------------------------

def _write_summary(worksheet, report: dict, records_by_sheet: dict, notes: list) -> None:
    worksheet.sheet_view.showGridLines = False
    row = 1

    def put(row_number, column_number, value, bold=False, fill=None, border=True, size=None, wrap=False):
        cell = worksheet.cell(row=row_number, column=column_number, value=value)
        cell.font = Font(bold=bold, size=size) if size else Font(bold=bold)
        if fill:
            cell.fill = fill
        if border:
            cell.border = BORDER
        cell.alignment = Alignment(vertical="top", wrap_text=wrap)
        return cell

    put(row, 1, "Cleaning Summary", bold=True, border=False, size=16)
    row += 2
    generated = report.get("generated") or datetime.now()
    for label, value in (("File", report["file_name"]), ("Date", generated.strftime("%Y-%m-%d %H:%M")),
                         ("Sheets cleaned", sum(1 for info in report["sheet_stats"].values() if info["cleaned"]))):
        put(row, 1, label, bold=True, fill=SECTION_FILL)
        put(row, 2, value)
        row += 1
    row += 1

    totals = {reason: 0 for reason in REASON_ORDER}
    for sheet_name, info in report["sheet_stats"].items():
        if not info["cleaned"]:
            continue
        records = records_by_sheet.get(sheet_name, [])
        counts = summarize(records)
        for reason, number in counts.items():
            totals[reason] += number
        stats = info["stats"]
        quality = report["quality_by_sheet"].get(sheet_name)

        put(row, 1, f"Sheet: {sheet_name}", bold=True, fill=HEADER_FILL)
        put(row, 2, "", fill=HEADER_FILL)
        row += 1
        lines = [
            ("Rows in your file", report["original_rows"].get(sheet_name)),
            ("Rows in the cleaned file", report["final_rows"].get(sheet_name)),
            ("Duplicate rows removed", stats.get("duplicates_removed")),
            ("Completely empty rows removed", stats.get("empty_rows_removed")),
            *(
                [(f"Full content of removed rows", f"see the '{report['removed_titles'][sheet_name]}' sheet "
                                                  f"({report['removed_counts'][sheet_name]:,} rows)")]
                if sheet_name in report.get("removed_titles", {}) else []
            ),
            ("Completely empty columns removed", stats.get("empty_columns_removed")),
            ("Placeholders cleared to blank", (stats.get("placeholders") or {}).get("total")),
            ("Cells filled by exact calculation", sum(((stats.get("fills") or {}).get("calculated") or {}).values())),
            ("Cells filled from a lookup (inferred)", sum(((stats.get("fills") or {}).get("lookup") or {}).values())),
            ("Cells changed by AI rules", counts["ai_rule"]),
            ("Cells with spaces trimmed", stats.get("cells_trimmed")),
        ]
        if quality:
            before_missing = int((quality["before"]["Blank"] + quality["before"]["Placeholders"]).sum())
            after_missing = int((quality["after"]["Blank"] + quality["after"]["Placeholders"]).sum())
            lines.append(("Missing cells (before -> after)", f"{before_missing:,} -> {after_missing:,}"))
        for label, value in lines:
            put(row, 1, label)
            put(row, 2, "skipped" if value is None else value)
            worksheet.cell(row=row, column=2).alignment = Alignment(horizontal="left")
            row += 1
        row += 1

        if quality:
            put(row, 1, f"Data quality by column - {sheet_name}", bold=True, border=False)
            row += 1
            headers = ["Column", "Type", "Missing before", "Missing after", "Placeholders before", "Invalid before", "Invalid after"]
            for number, header in enumerate(headers, start=1):
                put(row, number, header, bold=True, fill=HEADER_FILL)
            row += 1
            renames = dict(stats.get("header_renames") or [])
            after = quality["after"].set_index("Column")
            for _, line in quality["before"].iterrows():
                name = renames.get(line["Column"], line["Column"])
                present = name in after.index
                values = [
                    name, line["Type"], int(line["Blank"] + line["Placeholders"]),
                    int(after.at[name, "Blank"] + after.at[name, "Placeholders"]) if present else "column removed",
                    int(line["Placeholders"]), int(line["Invalid"]), int(after.at[name, "Invalid"]) if present else "column removed",
                ]
                for number, value in enumerate(values, start=1):
                    put(row, number, value)
                row += 1
            row += 1

    put(row, 1, "What the colors mean", bold=True, border=False)
    row += 1
    for number, header in enumerate(["Color", "Meaning", "Cells"], start=1):
        put(row, number, header, bold=True, fill=HEADER_FILL)
    row += 1
    for reason in REASON_ORDER:
        swatch = put(row, 1, "", fill=fill_for(reason))
        if not fill_for(reason):
            swatch.value = "(no color)"
        put(row, 2, COLOR_MEANINGS.get(reason, f"{REASON_LABELS[reason]} - listed in the Change Log, not colored"))
        put(row, 3, totals[reason])
        row += 1
    put(row, 1, "Total", bold=True, fill=SECTION_FILL)
    put(row, 2, "", fill=SECTION_FILL)
    put(row, 3, sum(totals.values()), bold=True, fill=SECTION_FILL)
    row += 2

    put(row, 1, "Hover over a colored cell to see what it was and why it changed.", border=False)
    row += 1
    for note in notes:
        put(row, 1, note, border=False)
        row += 1

    worksheet.column_dimensions["A"].width = 38
    worksheet.column_dimensions["B"].width = 58
    for letter in "CDEFG":
        worksheet.column_dimensions[letter].width = 20


# ---------------------------------------------------------------------------
# CHANGE LOG SHEET
# ---------------------------------------------------------------------------

def _write_change_log(worksheet, records_by_sheet: dict, multi_sheet: bool, notes: list) -> int:
    """Returns how many records exist in total (the sheet itself is capped)."""
    headers = (["Sheet"] if multi_sheet else []) + ["Row", "Column", "Old value", "New value", "Reason"]
    for number, header in enumerate(headers, start=1):
        worksheet.cell(row=1, column=number, value=header)
    _style_header(worksheet, len(headers))

    total = sum(len(records) for records in records_by_sheet.values())
    written = 0
    for sheet_name, records in records_by_sheet.items():
        for record in records:
            if written >= MAX_LOG_ROWS:
                break
            values = ([sheet_name] if multi_sheet else []) + [
                record["row"], record["column"], _blank_text(record["old"]), _blank_text(record["new"]), reason_text(record),
            ]
            for number, value in enumerate(values, start=1):
                if hasattr(value, "item"):
                    value = value.item()  # numpy scalar -> plain Python number
                cell = worksheet.cell(row=written + 2, column=number, value=value)
                cell.border = BORDER
                cell.alignment = Alignment(horizontal="left", vertical="top")
            written += 1

    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(written + 1, 2)}"
    widths = [14] * multi_sheet + [8, 22, 28, 28, 70]
    for number, width in enumerate(widths, start=1):
        worksheet.column_dimensions[get_column_letter(number)].width = width
    if total > written:
        notes.append(f"The Change Log shows the first {written:,} of {total:,} changes.")
    return total


# ---------------------------------------------------------------------------
# REMOVED ROWS SHEET
# ---------------------------------------------------------------------------

def _plain(value):
    """A cell value safe to write: missing -> empty, numpy scalars -> Python numbers."""
    if not isinstance(value, str) and is_blank(value):
        return None
    return value.item() if hasattr(value, "item") else value


def _write_removed(worksheet, title: str, columns: list, rows: list, notes: list) -> None:
    """Every row the cleaning removed, with its full ORIGINAL content, the row
    number it had in the uploaded file, and why it was removed."""
    headers = ["Original row", "Reason"] + [str(column) for column in columns]
    for number, header in enumerate(headers, start=1):
        worksheet.cell(row=1, column=number, value=header)
    _style_header(worksheet, len(headers))

    written = 0
    for item in rows:
        if written >= MAX_LOG_ROWS:
            break
        values = [item["original_row"], item["reason"]] + [_plain(value) for value in item["values"]]
        for number, value in enumerate(values, start=1):
            cell = worksheet.cell(row=written + 2, column=number, value=value)
            cell.border = BORDER
            cell.alignment = Alignment(horizontal="left", vertical="top")
        written += 1

    worksheet.freeze_panes = "C2"
    worksheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(written + 1, 2)}"
    worksheet.column_dimensions["A"].width = 14
    worksheet.column_dimensions["B"].width = 22
    for number, header in enumerate(headers[2:], start=3):
        sample = [len(str(_plain(item["values"][number - 3]) or "")) for item in rows[:WIDTH_SAMPLE_ROWS]]
        worksheet.column_dimensions[get_column_letter(number)].width = min(max(max(sample + [len(header)]) + 3, MIN_WIDTH), MAX_WIDTH)
    if len(rows) > written:
        notes.append(f"The '{title}' sheet shows the first {written:,} of {len(rows):,} removed rows.")


# ---------------------------------------------------------------------------
# THE WORKBOOK
# ---------------------------------------------------------------------------

def build_highlighted_workbook(sheets: dict, records_by_sheet: dict, report: dict = None,
                               removed_by_sheet: dict = None) -> bytes:
    """The user's sheets (same names, same order) with changed cells coloured
    and commented. If `report` is given, a "Cleaning Summary" and a "Change
    Log" sheet - plus a "Removed Rows" sheet when rows were removed - are
    added AFTER them. removed_by_sheet: {sheet: {"columns": [...], "rows":
    ChangeLog.finalize_removed()}}.

    records_by_sheet: {sheet: ChangeLog.finalize(...) records} (sheets that were not cleaned: omit).
    report: {"file_name", "sheet_stats", "quality_by_sheet", "original_rows", "final_rows", "generated"?}."""
    buffer = io.BytesIO()
    comment_budget = [MAX_COMMENTS]
    notes = []
    total_records = sum(len(records) for records in records_by_sheet.values())

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for sheet_name, df in sheets.items():
            to_write = df.copy()
            phone_columns = [column for column in to_write.columns if is_phone_like(column, to_write[column])]
            for column in phone_columns:
                to_write[column] = to_write[column].map(_as_phone_text)
            to_write.to_excel(writer, sheet_name=sheet_name, index=False)
            _style_data_sheet(writer.sheets[sheet_name], to_write, phone_columns, records_by_sheet.get(sheet_name, []), comment_budget)

        if comment_budget[0] <= 0 and total_records > MAX_COMMENTS:
            notes.append(f"Cell comments were added to the first {MAX_COMMENTS:,} of {total_records:,} changed cells "
                         "(colors are on all of them; the Change Log lists every change).")

        if report is not None:
            workbook = writer.book
            taken = list(workbook.sheetnames)
            summary_title = _unique_title("Cleaning Summary", taken)
            taken.append(summary_title)
            log_title = _unique_title("Change Log", taken)
            taken.append(log_title)

            removed_sheets = {name: item for name, item in (removed_by_sheet or {}).items() if item["rows"]}
            removed_titles = {}
            for name in removed_sheets:
                wanted = "Removed Rows" if len(removed_sheets) == 1 else f"Removed - {name}"[:31]
                removed_titles[name] = _unique_title(wanted, taken)
                taken.append(removed_titles[name])
            report = {**report, "removed_titles": removed_titles,
                      "removed_counts": {name: len(item["rows"]) for name, item in removed_sheets.items()}}

            summary_sheet = workbook.create_sheet(summary_title)
            log_sheet = workbook.create_sheet(log_title)
            removed_worksheets = {name: workbook.create_sheet(title) for name, title in removed_titles.items()}

            multi = len(records_by_sheet) > 1  # name the sheet in each log row only when several sheets were cleaned
            _write_change_log(log_sheet, records_by_sheet, multi, notes)
            for name, worksheet in removed_worksheets.items():
                _write_removed(worksheet, removed_titles[name], removed_sheets[name]["columns"], removed_sheets[name]["rows"], notes)
            _write_summary(summary_sheet, report, records_by_sheet, notes)

    return buffer.getvalue()


def export_workbook(cleaned_sheets: dict, change_logs: dict, sheet_stats: dict, quality_before: dict,
                    placeholders, file_name: str, original_rows: dict, include_report: bool = True) -> bytes:
    """Everything the app needs for the Excel download in one call: turns the
    per-sheet ChangeLogs into final records (rows numbered as in the cleaned
    sheets) and builds the workbook. Takes only plain data, so it can run on
    a background thread when the user clicks the download button."""
    records = {name: log.finalize(cleaned_sheets[name]) for name, log in (change_logs or {}).items() if name in cleaned_sheets}

    report = None
    if include_report:
        report = {
            "file_name": file_name,
            "generated": datetime.now(),
            "sheet_stats": sheet_stats,
            "quality_by_sheet": {
                name: {"before": quality_before[name], "after": profile_dataframe(cleaned_sheets[name], list(placeholders))}
                for name, info in sheet_stats.items() if info["cleaned"] and name in quality_before
            },
            "original_rows": original_rows,
            "final_rows": {name: len(df) for name, df in cleaned_sheets.items()},
        }
    removed = {name: {"columns": log.original_columns, "rows": log.finalize_removed()}
               for name, log in (change_logs or {}).items() if name in cleaned_sheets and log.removed}
    return build_highlighted_workbook(cleaned_sheets, records, report, removed)
