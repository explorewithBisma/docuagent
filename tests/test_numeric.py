from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"

import io
import sys

sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest
sys.path.insert(0, str(_Path(__file__).resolve().parent))
from clean_page_helpers import box, boxes, include_box

import cleaning
import report
from io_utils import CSV_SHEET_NAME, convert_to_download_bytes

convert = cleaning.convert_numeric_columns

print("TEST: numeric text becomes real numbers (whole -> Int64, decimals -> float64)")
df = pd.DataFrame({
    "Quantity": ["1", "2", np.nan, "5.0", "4"],
    "Price": ["3.0", "1.5", "2", np.nan, "4.0"],
    "Total": ["1,500", "-20", "0", "0.75", np.nan],
    "Item": ["Cake", "Tea", "Cake", "Tea", "Cake"],
})
out, info = convert(df.copy())
print(out.dtypes.to_dict(), info)
assert str(out["Quantity"].dtype) == "Int64" and out["Quantity"].tolist()[:2] == [1, 2] and pd.isna(out["Quantity"].iloc[2])
assert out["Price"].dtype == "float64" and out["Price"].tolist()[:3] == [3.0, 1.5, 2.0]
assert out["Total"].dtype == "float64" and out["Total"].tolist()[:4] == [1500.0, -20.0, 0.0, 0.75]
assert out["Item"].dtype == object
assert [(c["column"], c["kind"]) for c in info["converted"]] == [("Quantity", "integer"), ("Price", "decimal"), ("Total", "decimal")]
assert info["skipped"] == {}
print("PASSED")

print("TEST: identifiers are never converted")
ids = pd.DataFrame({
    "Phone": ["0300-1234567", "0321-9876543", "0333-1112222"],                      # phone values
    "Mobile": ["3001234567", "3219876543", "3331112222"],                           # phone NAME, 10-digit values
    "Code": ["007", "008", "009"],                                                  # leading zeros
    "Customer ID": ["1001", "1002", "1003"],                                        # ID name, plain digits
    "Zip": ["12345", "23456", "34567"],
    "Account No": ["5", "6", "7"],
    "Long": ["12345678901", "12345678902", "12345678903"],                          # 11-digit strings
    "Plus": ["+923001234567", "+923211234567", "+923331234567"],
    "Amount": ["10", "20", "30"],                                                   # the one that SHOULD convert
})
out, info = convert(ids.copy())
for column in ["Phone", "Mobile", "Code", "Customer ID", "Zip", "Account No", "Long", "Plus"]:
    assert out[column].tolist() == ids[column].tolist(), column
assert str(out["Amount"].dtype) == "Int64"
assert set(info["skipped"]) == {"Customer ID", "Zip", "Account No"}, info["skipped"]   # numeric-looking, deliberately skipped (Mobile/Long are already rejected by the 10-digit guard)
assert [c["column"] for c in info["converted"]] == ["Amount"]
print("PASSED")

print("TEST: 90% threshold; unparseable values stay as text and are reported invalid")
nine = pd.DataFrame({"N": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "oops"]})
out, info = convert(nine.copy())
assert info["converted"][0]["kind"] == "mixed" and info["converted"][0]["invalid"] == 1 and info["converted"][0]["examples"] == ["oops"]
assert out["N"].tolist()[:2] == [1, 2] and out["N"].iloc[9] == "oops" and out["N"].dtype == object
eight = pd.DataFrame({"N": ["1", "2", "3", "4", "5", "6", "7", "8", "x", "y"]})
out, info = convert(eight.copy())
assert info["converted"] == [] and out["N"].tolist() == eight["N"].tolist()
currency = pd.DataFrame({"Amount": ["Rs. 1,500/-", "$20", "1500", "Rs. 2,250/-"]})            # currency text is left for the AI rule
out, info = convert(currency.copy())
assert info["converted"] == [] and out["Amount"].tolist() == currency["Amount"].tolist()
print("PASSED")

print("TEST: placeholders left in place are not 'invalid' and don't block conversion")
left = pd.DataFrame({"Q": ["1", "2", "ERROR", "3", "UNKNOWN", "4"]})
out, info = convert(left.copy(), ["ERROR", "UNKNOWN"])
item = info["converted"][0]
assert item["kind"] == "mixed" and item["invalid"] == 0
assert out["Q"].tolist() == [1, 2, "ERROR", 3, "UNKNOWN", 4]
# without telling it about the placeholders, 4 of 6 parse -> stays text
out, info = convert(left.copy(), [])
assert info["converted"] == []
print("PASSED")

print("TEST: nothing to do cases")
out, info = convert(pd.DataFrame({"A": [1, 2], "B": [1.5, np.nan], "C": ["x", "y"], "D": [np.nan, np.nan]}))
assert info == {"converted": [], "skipped": {}} and out["A"].dtype == "int64"
print("PASSED")

print("TEST: pipeline order - 'ERROR' no longer blocks conversion; '5' and '5.0' are duplicates")
df = pd.DataFrame({
    "Item": ["Cake", "Cake", "Tea", "Tea"],
    "Qty": ["5", "5.0", "ERROR", "2"],
    "Total": ["10", "10.0", "UNKNOWN", "4"],
})
steps = {"headers": True, "trim": True, "duplicates": True, "empty": True, "placeholders": True, "numeric": True}
cleaned, stats = cleaning.clean_dataframe_with_steps(df.copy(), steps, {"placeholders": ["ERROR", "UNKNOWN"]})
print(cleaned.to_string(), cleaned.dtypes.to_dict())
assert stats["duplicates_removed"] == 1 and str(cleaned["Qty"].dtype) == "Int64" and str(cleaned["Total"].dtype) == "Int64"
assert [c["kind"] for c in stats["numeric"]["converted"]] == ["integer", "integer"]
_, off = cleaning.clean_dataframe_with_steps(df.copy(), {**steps, "numeric": False})
assert off["numeric"] is None
_, old = cleaning.clean_dataframe_with_steps(df.copy(), {"headers": True, "trim": True, "duplicates": True, "empty": True})
assert "numeric" not in old
print("PASSED")

print("TEST: output files - Int64 columns write cleanly to CSV and XLSX")
csv_bytes = convert_to_download_bytes({CSV_SHEET_NAME: cleaned}, is_csv=True).decode().replace("\r\n", "\n")
print(repr(csv_bytes))
assert csv_bytes == "Item,Qty,Total\nCake,5,10\nTea,,\nTea,2,4\n"
xlsx = convert_to_download_bytes({"S": cleaned}, is_csv=False)
back = pd.read_excel(io.BytesIO(xlsx), sheet_name="S")
assert back["Qty"].tolist()[0] == 5 and pd.isna(back["Qty"].iloc[1])
print("PASSED")

print("TEST: report lines")
text = report.build_report_text("f.csv", {"Data": {"cleaned": True, "stats": stats}})
print(text)
assert "Columns converted to numbers: 2 - Qty (integer), Total (integer)" in text
mixed_stats = cleaning.clean_dataframe_with_steps(
    pd.DataFrame({"N": [str(i) for i in range(1, 10)] + ["oops"], "Customer ID": ["1"] * 10}), steps)[1]
mixed_text = report.build_report_text("f.csv", {"Data": {"cleaned": True, "stats": mixed_stats}})
assert "N: 1 value(s) are not numbers and were kept as text (e.g. 'oops')" in mixed_text
assert "Customer ID: left as text (name looks like an ID, code or phone column)" in mixed_text
assert "skipped (step turned off)" in report.build_report_text("f.csv", {"Data": {"cleaned": True, "stats": off}})
print("PASSED")

print("TEST: UI checkbox")
CSV = b"Item,Quantity,Price,Phone\nCake,2,3.0,0300-1234567\nTea,ERROR,1.5,0321-9876543\nCake,4,UNKNOWN,0333-1112222\n"
def run(uncheck=False):
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    at.file_uploader[0].upload("c.csv", CSV, "text/csv")
    at.run()
    if uncheck:
        box(at, "numeric").uncheck()
        at.run()
    next(b for b in at.button if b.label == "Clean my file").click()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at
at = run()
data = at.session_state["cleaned_sheets"]["Data"]
print(data.dtypes.to_dict())
assert str(data["Quantity"].dtype) == "Int64" and data["Price"].dtype == "float64" and data["Phone"].iloc[0] == "0300-1234567"
assert "Columns converted to numbers: 2" in at.session_state["report_text"]
at = run(uncheck=True)
data = at.session_state["cleaned_sheets"]["Data"]
assert data["Quantity"].dtype == object and "skipped (step turned off)" in at.session_state["report_text"]
print("PASSED")

print("\nALL NUMERIC TESTS PASSED")
