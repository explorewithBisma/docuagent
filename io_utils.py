"""
io_utils.py
------------
Helpers for loading an uploaded file and converting cleaned data back into
downloadable file bytes. Everything works with a *dict of sheets*
({sheet_name: DataFrame}) so that single-sheet CSVs and multi-sheet Excel
files can be handled with the same code everywhere else in the app.
"""

import io
import pandas as pd

# Internal name used for a CSV file's single table. CSV files don't have the
# concept of "sheets" like Excel does, but using the same dict-of-sheets shape
# everywhere lets the rest of the code treat CSV and Excel the same way.
CSV_SHEET_NAME = "Data"


def load_sheets(uploaded_file, is_csv: bool) -> dict:
    """Read the uploaded file into a dict of {sheet_name: DataFrame}.
    A CSV file always produces exactly one entry. An Excel file produces one
    entry per sheet it actually contains — every sheet is loaded, so none of
    them can be silently dropped later."""
    if is_csv:
        return {CSV_SHEET_NAME: pd.read_csv(uploaded_file)}
    else:
        # sheet_name=None tells pandas "read every sheet", returned as a dict
        # that keeps the sheets in the same order as the original file.
        return pd.read_excel(uploaded_file, sheet_name=None)


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
                df.to_excel(writer, sheet_name=sheet_name, index=False)

    buffer.seek(0)  # rewind to the start so Streamlit can read it from the beginning
    return buffer.getvalue()
