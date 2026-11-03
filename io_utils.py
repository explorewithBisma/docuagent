"""
io_utils.py
------------
Helpers for loading an uploaded file and converting cleaned data back into
downloadable file bytes. Everything works with a *dict of sheets*
({sheet_name: DataFrame}) so that single-sheet CSVs and multi-sheet Excel
files can be handled with the same code everywhere else in the app.
"""

import io

import datetime

import pandas as pd
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from quality import is_blank, is_phone_like

WIDTH_SAMPLE_ROWS = 1000  # rows looked at when sizing a column (keeps big files fast)
MIN_WIDTH, MAX_WIDTH = 8, 60

# Internal name used for a CSV file's single table. CSV files don't have the
# concept of "sheets" like Excel does, but using the same dict-of-sheets shape
# everywhere lets the rest of the code treat CSV and Excel the same way.
CSV_SHEET_NAME = "Data"


def load_sheets(uploaded_file, is_csv: bool) -> dict:
    """Read the uploaded file into a dict of {sheet_name: DataFrame}.
    A CSV file always produces exactly one entry. An Excel file produces one
    entry per sheet it actually contains — every sheet is loaded, so none of
    them can be silently dropped later."""
    # By default pandas silently turns text like "N/A", "NA", "null" or "None"
    # into missing values, which would hide them from the placeholder report.
    # So only truly empty cells become missing here; every other piece of text
    # is kept exactly as written and the cleaning steps decide what it means.
    if is_csv:
        # A CSV has no cell types, so read everything as text (this also keeps
        # leading zeros in codes and phone numbers). The numeric-conversion
        # step turns genuine number columns back into numbers.
        return {CSV_SHEET_NAME: pd.read_csv(uploaded_file, dtype=str, keep_default_na=False, na_values=[""])}
    else:
        # sheet_name=None tells pandas "read every sheet", returned as a dict
        # that keeps the sheets in the same order as the original file.
        # Excel cells keep their real types: numbers stay numbers, and TEXT stays text. (Plain read_excel would
        # turn a column of text like "000101" or "0012345" into the numbers 101 and 12345 - the zeros are lost.)
        raw = pd.read_excel(uploaded_file, sheet_name=None, keep_default_na=False, na_values=[""], dtype=object)
        return {name: restore_column_types(frame) for name, frame in raw.items()}


def restore_column_types(frame: pd.DataFrame) -> pd.DataFrame:
    """A sheet read cell by cell (every cell keeps the Python type it has in the file) gets back the column types pandas would give a
    normal read, but ONLY where every cell really is that type: all numbers -> a number column, all dates -> a date column, an empty
    column -> float. A column of text stays text, even when the text looks like a number ("000101" keeps its zeros)."""
    frame = frame.copy()
    for column in frame.columns:
        series = frame[column]
        real = [value for value in series if not (value is None or (isinstance(value, float) and value != value) or value is pd.NaT)]
        if not real:
            frame[column] = series.astype("float64")
        elif all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in real):
            frame[column] = pd.to_numeric(series)
        elif all(isinstance(value, bool) for value in real) and len(real) == len(series):
            frame[column] = series.astype(bool)
        elif all(isinstance(value, (datetime.datetime, pd.Timestamp)) for value in real):
            frame[column] = pd.to_datetime(series)
    return frame


def _as_phone_text(value):
    """A phone cell as text. Whole-number floats (3001234567.0, from Excel or
    pandas) lose the '.0'; missing cells stay missing."""
    if is_blank(value):
        return None
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def _polish_sheet(worksheet, df: pd.DataFrame, text_columns: list) -> None:
    """Make a written sheet pleasant to open: bold header row, header frozen
    so it stays visible while scrolling, columns sized to their content, and
    text format on the phone columns."""
    for cell in worksheet[1]:
        cell.font = Font(bold=True)
    worksheet.freeze_panes = "A2"

    for position, column in enumerate(df.columns, start=1):
        lengths = [len(str(column))]
        lengths += [len(str(value)) for value in df[column].head(WIDTH_SAMPLE_ROWS) if not is_blank(value)]
        worksheet.column_dimensions[get_column_letter(position)].width = min(max(max(lengths) + 2, MIN_WIDTH), MAX_WIDTH)

        if column in text_columns:
            for row in range(2, len(df) + 2):
                worksheet.cell(row=row, column=position).number_format = "@"


def convert_to_download_bytes(sheets: dict, is_csv: bool) -> bytes:
    """Turn the (possibly multi-sheet) cleaned data into raw file bytes,
    ready to download. We build the file in memory (io.BytesIO) instead of
    saving it to disk."""
    buffer = io.BytesIO()

    if is_csv:
        # A CSV file only ever has the one table.
        only_df = next(iter(sheets.values()))
        only_df.to_csv(buffer, index=False)
    else:
        # Write every sheet back out, keeping its original name, so nothing
        # that was in the uploaded file silently disappears from the output.
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            for sheet_name, df in sheets.items():
                to_write = df.copy()
                # Phone-like columns are written as TEXT so Excel never turns
                # "0300-1234567" into a number or drops a leading 0 / "+".
                phone_columns = [column for column in to_write.columns if is_phone_like(column, to_write[column])]
                for column in phone_columns:
                    to_write[column] = to_write[column].map(_as_phone_text)
                to_write.to_excel(writer, sheet_name=sheet_name, index=False)
                _polish_sheet(writer.sheets[sheet_name], to_write, phone_columns)

    buffer.seek(0)  # rewind to the start so Streamlit can read it from the beginning
    return buffer.getvalue()
