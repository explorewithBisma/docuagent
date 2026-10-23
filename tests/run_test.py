from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"
"""Exercises the real cleaning/io/report modules (not Streamlit widgets) against the test files."""
import sys
import io
import pandas as pd

sys.path.insert(0, str(ROOT))

import cleaning as cleaning_module
import io_utils as io_module
import report as report_module


class app:
    """Thin shim so the rest of this test script (written against the old
    single-file app.py) can keep calling app.<function> unchanged."""
    load_sheets = staticmethod(io_module.load_sheets)
    convert_to_download_bytes = staticmethod(io_module.convert_to_download_bytes)
    clean_dataframe_with_steps = staticmethod(cleaning_module.clean_dataframe_with_steps)
    build_report_text = staticmethod(report_module.build_report_text)
    CSV_SHEET_NAME = io_module.CSV_SHEET_NAME

steps_all_on = {"headers": True, "trim": True, "duplicates": True, "empty": True}

print("=" * 70)
print("TEST 1: multi-sheet xlsx, cleaning ALL sheets")
print("=" * 70)

with open(FIXTURES / "messy_multi_sheet.xlsx", "rb") as f:
    sheets = app.load_sheets(f, is_csv=False)

print("Sheets found:", list(sheets.keys()))

cleaned_sheets = {}
sheet_stats = {}
for name, df in sheets.items():
    cleaned_df, stats = app.clean_dataframe_with_steps(df.copy(), steps_all_on)
    cleaned_sheets[name] = cleaned_df
    sheet_stats[name] = {"cleaned": True, "stats": stats}

print()
print(app.build_report_text("messy_multi_sheet.xlsx", sheet_stats))

print()
for name, df in cleaned_sheets.items():
    print(f"--- Cleaned sheet: {name} ---")
    print(df)
    print()

# Write out and read back to confirm every sheet survives the round trip
output_bytes = app.convert_to_download_bytes(cleaned_sheets, is_csv=False)
roundtrip = pd.read_excel(io.BytesIO(output_bytes), sheet_name=None)
print("Sheets present in downloaded file:", list(roundtrip.keys()))
assert list(roundtrip.keys()) == list(sheets.keys()), "SHEET ORDER/NAMES CHANGED!"

print()
print("=" * 70)
print("TEST 2: multi-sheet xlsx, cleaning ONLY the 'Customers' sheet")
print("=" * 70)

with open(FIXTURES / "messy_multi_sheet.xlsx", "rb") as f:
    sheets2 = app.load_sheets(f, is_csv=False)

sheets_to_clean = ["Customers"]
cleaned_sheets2 = {}
sheet_stats2 = {}
for name, df in sheets2.items():
    if name in sheets_to_clean:
        cleaned_df, stats = app.clean_dataframe_with_steps(df.copy(), steps_all_on)
        cleaned_sheets2[name] = cleaned_df
        sheet_stats2[name] = {"cleaned": True, "stats": stats}
    else:
        cleaned_sheets2[name] = df.copy()
        sheet_stats2[name] = {"cleaned": False, "stats": None}

print(app.build_report_text("messy_multi_sheet.xlsx", sheet_stats2))
print()

output_bytes2 = app.convert_to_download_bytes(cleaned_sheets2, is_csv=False)
roundtrip2 = pd.read_excel(io.BytesIO(output_bytes2), sheet_name=None)
print("Sheets present in downloaded file:", list(roundtrip2.keys()))
print()
print("Orders sheet (should be UNCHANGED, including messy spacing/duplicates):")
print(roundtrip2["Orders"])
print()
print("Notes sheet (should be UNCHANGED, including leading/trailing spaces):")
for val in roundtrip2["Notes"].iloc[:, 0]:
    print(repr(val))

print()
print("=" * 70)
print("TEST 3: messy CSV, all steps on")
print("=" * 70)

with open(FIXTURES / "messy_data.csv", "rb") as f:
    csv_sheets = app.load_sheets(f, is_csv=True)

print("Sheets dict keys (internal only, not shown in UI):", list(csv_sheets.keys()))

cleaned_csv = {}
csv_stats = {}
for name, df in csv_sheets.items():
    cleaned_df, stats = app.clean_dataframe_with_steps(df.copy(), steps_all_on)
    cleaned_csv[name] = cleaned_df
    csv_stats[name] = {"cleaned": True, "stats": stats}

print()
print(app.build_report_text("messy_data.csv", csv_stats))
print()
print(next(iter(cleaned_csv.values())))

csv_bytes = app.convert_to_download_bytes(cleaned_csv, is_csv=True)
roundtrip_csv = pd.read_csv(io.BytesIO(csv_bytes))
print()
print("Downloaded CSV content:")
print(roundtrip_csv)

print()
print("=" * 70)
print("TEST 4: CSV with 'Remove duplicate rows' turned OFF")
print("=" * 70)
steps_no_dupes = {"headers": True, "trim": True, "duplicates": False, "empty": True}
with open(FIXTURES / "messy_data.csv", "rb") as f:
    csv_sheets_b = app.load_sheets(f, is_csv=True)
df_b = next(iter(csv_sheets_b.values()))
cleaned_b, stats_b = app.clean_dataframe_with_steps(df_b.copy(), steps_no_dupes)
print(app.build_report_text("messy_data.csv", {app.CSV_SHEET_NAME: {"cleaned": True, "stats": stats_b}}))
print()
print(cleaned_b)
print("(Row for 'Widget' should appear twice since duplicate removal was skipped)")

print()
print("ALL TESTS COMPLETED WITHOUT ERRORS")
