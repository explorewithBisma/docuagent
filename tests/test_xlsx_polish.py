from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"

import io
import sys

sys.path.insert(0, str(ROOT))

import numpy as np
import openpyxl
import pandas as pd
from streamlit.testing.v1 import AppTest

from io_utils import CSV_SHEET_NAME, convert_to_download_bytes

LONG = "x" * 200
df = pd.DataFrame({
    "Name": ["Ada Lovelace", "Bob", "Cy", None],
    "A Very Long Column Header Indeed": [1, 2, 3, 4],
    "Phone": ["0300-1234567", "+92 321 9876543", "03331112222", None],        # text phones with leading 0 / +
    "Mobile": [3001234567.0, 3219876543.0, np.nan, 3331112222.0],              # numeric phones (the '.0' must go)
    "Qty": pd.array([1, 2, pd.NA, 4], dtype="Int64"),
    "Price": [1.5, 2.0, np.nan, 4.25],
    "Notes": [LONG, "short", "", None],
    "Empty": [None, None, None, None],
})
original = df.copy()

print("TEST: header, freeze pane, widths")
data = convert_to_download_bytes({"Sheet One": df}, is_csv=False)
wb = openpyxl.load_workbook(io.BytesIO(data))
ws = wb["Sheet One"]
assert all(cell.font.bold for cell in ws[1]), "header row must be bold"
assert not any(cell.font.bold for cell in ws[2]), "data rows must not be bold"
assert ws.freeze_panes == "A2"
width = lambda name: ws.column_dimensions[openpyxl.utils.get_column_letter(list(df.columns).index(name) + 1)].width
print({c: width(c) for c in df.columns})
assert width("Name") == len("Ada Lovelace") + 2
assert width("A Very Long Column Header Indeed") == len("A Very Long Column Header Indeed") + 2     # header drives the width
assert width("Notes") == 60                                                                     # clamped
assert width("Empty") == 8                                                                      # minimum
assert width("Qty") == 8
print("PASSED")

print("TEST: phone columns are text (leading 0, +, no '.0') and other numbers stay numbers")
col = {name: i + 1 for i, name in enumerate(df.columns)}
phone = [ws.cell(row=r, column=col["Phone"]) for r in (2, 3, 4)]
assert [c.value for c in phone] == ["0300-1234567", "+92 321 9876543", "03331112222"]
assert all(c.data_type == "s" and c.number_format == "@" for c in phone)
assert ws.cell(row=5, column=col["Phone"]).value is None
mobile = [ws.cell(row=r, column=col["Mobile"]) for r in (2, 3, 5)]
assert [c.value for c in mobile] == ["3001234567", "3219876543", "3331112222"] and all(c.data_type == "s" for c in mobile)
assert ws.cell(row=4, column=col["Mobile"]).value is None
assert ws.cell(row=2, column=col["Qty"]).data_type == "n" and ws.cell(row=2, column=col["Qty"]).value == 1
assert ws.cell(row=4, column=col["Qty"]).value is None
assert ws.cell(row=2, column=col["Price"]).value == 1.5 and ws.cell(row=2, column=col["Price"]).data_type == "n"
assert ws.cell(row=2, column=col["A Very Long Column Header Indeed"]).number_format == "General"
print("PASSED")

print("TEST: the data itself is unchanged and the input frame is not modified")
back = pd.read_excel(io.BytesIO(data), sheet_name="Sheet One", dtype={"Phone": str, "Mobile": str})
assert back["Name"].tolist()[:3] == ["Ada Lovelace", "Bob", "Cy"]
assert back["Phone"].tolist()[:3] == ["0300-1234567", "+92 321 9876543", "03331112222"]
assert back["Price"].tolist()[:2] == [1.5, 2.0]
pd.testing.assert_frame_equal(df, original)
assert df["Mobile"].dtype == "float64"
print("PASSED")

print("TEST: every sheet is styled, names and order kept, no phone columns -> nothing forced to text")
multi = convert_to_download_bytes({"First": df, "Second": pd.DataFrame({"X": [1, 2], "Y": ["a", "b"]})}, is_csv=False)
wb2 = openpyxl.load_workbook(io.BytesIO(multi))
assert wb2.sheetnames == ["First", "Second"]
for sheet in wb2.worksheets:
    assert sheet.freeze_panes == "A2" and all(c.font.bold for c in sheet[1])
second = wb2["Second"]
assert second.cell(row=2, column=1).data_type == "n" and second.cell(row=2, column=1).number_format == "General"
empty = convert_to_download_bytes({"E": pd.DataFrame({"A": [], "B": []})}, is_csv=False)           # no rows must not crash
assert openpyxl.load_workbook(io.BytesIO(empty))["E"].freeze_panes == "A2"
print("PASSED")

print("TEST: identifier-like names are NOT forced to text; phone detection by values works")
mixed = pd.DataFrame({"Customer ID": [1001, 1002], "Contact Info": ["0300-1234567", "0321-9876543"],
                      "Reach": ["+92 300 1111111", "+92 321 2222222"]})
ws3 = openpyxl.load_workbook(io.BytesIO(convert_to_download_bytes({"S": mixed}, is_csv=False)))["S"]
assert ws3.cell(row=2, column=1).data_type == "n"                   # an ID column of numbers stays numeric
assert ws3.cell(row=2, column=2).number_format == "@"               # 'contact' in the name
assert ws3.cell(row=2, column=3).number_format == "@"               # recognised by its values alone
print("PASSED")

print("TEST: CSV downloads are untouched")
csv_out = convert_to_download_bytes({CSV_SHEET_NAME: mixed}, is_csv=True).decode().replace("\r\n", "\n")
assert csv_out == "Customer ID,Contact Info,Reach\n1001,0300-1234567,+92 300 1111111\n1002,0321-9876543,+92 321 2222222\n"
print("PASSED")

print("TEST: through the UI - Download Cleaned File produces a polished workbook")
CSV = b"Name,Phone,Qty\nAda,0300-1234567,2\nBob,0321-9876543,ERROR\n"
at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
at.run()
at.file_uploader[0].upload("people.xlsx", convert_to_download_bytes({"People": pd.DataFrame({
    "Name": ["Ada", "Bob"], "Phone": ["0300-1234567", "0321-9876543"], "Qty": [2, 3]})}, is_csv=False),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
at.run()
next(b for b in at.button if b.label == "Clean File").click()
at.run()
assert not at.exception, [e.value for e in at.exception]
from io_utils import convert_to_download_bytes as build
ws4 = openpyxl.load_workbook(io.BytesIO(build(at.session_state["cleaned_sheets"], False)))["People"]
assert ws4.freeze_panes == "A2" and ws4.cell(row=2, column=2).value == "0300-1234567" and ws4.cell(row=2, column=2).number_format == "@"
print("PASSED")

print("\nALL XLSX POLISH TESTS PASSED")
