from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"

import io
import sys

sys.path.insert(0, str(ROOT))

import numpy as np
import openpyxl
import pandas as pd

from cleaning import clean_dataframe_with_steps
from io_utils import CSV_SHEET_NAME, convert_to_download_bytes, load_sheets

STEPS = {"headers": True, "trim": True, "duplicates": True, "empty": True}

print("TEST: CSV keeps placeholder-like text, turns only empty cells into missing, keeps codes as text")
csv = b"Name,Code,Phone,Qty,Note\nAda,007,0300-1234567,5,N/A\nBob,008,+92 321 9876543,,null\nCy,009,03331112222,3,None\nDi,010,0345-7654321,NA,ERROR\n"
sheets = load_sheets(io.BytesIO(csv), is_csv=True)
df = sheets[CSV_SHEET_NAME]
print(df.to_string())
assert list(df["Note"]) == ["N/A", "null", "None", "ERROR"]          # nothing silently converted
assert df["Qty"].tolist()[0] == "5" and pd.isna(df["Qty"].iloc[1]) and df["Qty"].iloc[3] == "NA"
assert list(df["Code"]) == ["007", "008", "009", "010"]               # leading zeros survive
assert df["Phone"].iloc[0] == "0300-1234567"
assert all(df[c].dtype == object for c in df.columns)                  # everything is text
print("PASSED")

print("TEST: Excel keeps real cell types and placeholder text, blank cells are missing")
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "S1"
ws.append(["Name", "Qty", "Note", "Score"])
ws.append(["Ada", 5, "N/A", 1.5])
ws.append(["Bob", None, "null", 2.5])
ws.append(["Cy", 3, "None", None])
buffer = io.BytesIO()
wb.save(buffer)
buffer.seek(0)
xl = load_sheets(buffer, is_csv=False)["S1"]
print(xl.to_string())
assert list(xl["Note"]) == ["N/A", "null", "None"]
assert pd.isna(xl["Qty"].iloc[1]) and xl["Qty"].iloc[0] == 5 and pd.api.types.is_numeric_dtype(xl["Qty"])
assert pd.isna(xl["Score"].iloc[2]) and pd.api.types.is_numeric_dtype(xl["Score"])
print("PASSED")

print("REGRESSION GATE: 4-step results on the old fixtures are unchanged")
with open(FIXTURES / "messy_data.csv", "rb") as f:
    csv_df = load_sheets(f, is_csv=True)[CSV_SHEET_NAME]
cleaned, stats = clean_dataframe_with_steps(csv_df, STEPS)
assert stats["duplicates_removed"] == 1 and stats["empty_rows_removed"] == 1 and stats["empty_columns_removed"] == 1
assert stats["cells_trimmed"] == 2
assert stats["header_renames"] == [("  Product", "Product"), ("Price ", "Price")]
out = convert_to_download_bytes({CSV_SHEET_NAME: cleaned}, is_csv=True).decode().replace("\r\n", "\n")
assert out == "Product,Price\nWidget,9.99\nGadget,19.99\nGizmo,29.99\n", repr(out)   # same text as before the loader change

with open(FIXTURES / "messy_multi_sheet.xlsx", "rb") as f:
    book = load_sheets(f, is_csv=False)
assert list(book) == ["Customers", "Orders", "Notes"]
customers, cs = clean_dataframe_with_steps(book["Customers"], STEPS)
assert customers["Name"].tolist() == ["John Smith", "Jane Doe", "Bob Johnson"]
assert (cs["duplicates_removed"], cs["empty_rows_removed"], cs["empty_columns_removed"], cs["cells_trimmed"]) == (2, 1, 1, 5)
orders, os_ = clean_dataframe_with_steps(book["Orders"], STEPS)
assert os_["duplicates_removed"] == 1 and orders["Customer"].tolist() == ["John Smith", "Jane Doe", "Bob Johnson"]
notes, ns = clean_dataframe_with_steps(book["Notes"], STEPS)
assert notes["Note"].tolist() == ["keep this exactly as-is", "another note"] and ns["cells_trimmed"] == 1
print("PASSED")

print("\nALL LOADER TESTS PASSED")
