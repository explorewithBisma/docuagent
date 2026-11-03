from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""'Clean my file' as three steps: findings first (detection only), decisions second. Exact numbers for test_data/clean_test_pack.xlsx, the leading-zeros fix,
the 'not a data sheet' rule, the tick boxes as the only control, caching, and recipes."""
import glob
import io
import sys
from unittest import mock

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import openpyxl
import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

import findings
import io_utils
import report
from clean_page_helpers import box, boxes, include_box
from quality import DEFAULT_PLACEHOLDERS
from xlsx_export import export_workbook

PACK = ROOT / "test_data" / "clean_test_pack.xlsx"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PACK_BYTES = PACK.read_bytes()
sheets = io_utils.load_sheets(io.BytesIO(PACK_BYTES), False)


def new_app(data=PACK_BYTES, name="clean_test_pack.xlsx", mime=XLSX_MIME):
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
    at.run()
    at.file_uploader[0].upload(name, data, mime)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def clean(at):
    next(b for b in at.button if b.label == "Clean my file").click()
    at.run()
    assert not at.exception, [e.value for e in at.exception]


def output_workbook(at):
    s = at.session_state
    data = export_workbook(s["cleaned_sheets"], s["change_logs"], s["sheet_stats"], s["quality_before"], s["placeholder_list"], s["original_filename"], s["original_rows"], True,
                           bool(s["flagging_enabled"]), s["flags_before"])
    return openpyxl.load_workbook(io.BytesIO(data))


print("TEST: the loader keeps text as text - '000101' and '0012345' keep their zeros; every other column loads exactly as before (frozen comparison on all fixtures)")
allowed = {("clean_test_pack.xlsx", "Customers", "Account No"), ("clean_test_pack.xlsx", "Orders", "Order No")}      # text that looks like a number: the point of the fix
different = set()
for path in glob.glob(str(ROOT / "tests" / "fixtures" / "*.xlsx")) + glob.glob(str(ROOT / "test_data" / "*.xlsx")):
    raw = open(path, "rb").read()
    old = pd.read_excel(io.BytesIO(raw), sheet_name=None, keep_default_na=False, na_values=[""])                     # how it was loaded before
    new = io_utils.load_sheets(io.BytesIO(raw), False)
    assert list(old) == list(new)
    for sheet in old:
        assert list(old[sheet].columns) == list(new[sheet].columns) and len(old[sheet]) == len(new[sheet])
        for column in old[sheet].columns:
            if not (str(old[sheet][column].dtype) == str(new[sheet][column].dtype) and old[sheet][column].equals(new[sheet][column])):
                different.add((_Path(path).name, sheet, column))
print("   columns that load differently:", sorted(different))
assert different == allowed, different ^ allowed
assert sheets["Orders"]["Order No"].tolist()[:2] == ["000101", "000102"] and sheets["Orders"]["Order No"].iloc[-1] == "000170"
assert sheets["Customers"]["Account No"].iloc[0] == "0012345" and sheets["Customers"]["Account No"].isna().sum() == 3     # the blank cells are blank, not "None" or 0
assert sheets["Customers"]["Age"].dtype == "float64" and sheets["Orders"]["Unit Price"].dtype == "float64"           # real numbers stay numbers
csv_frame = io_utils.load_sheets(io.BytesIO(b"Account No,Qty\n0012345,3\n0012346,4\n"), True)["Data"]
assert csv_frame["Account No"].tolist() == ["0012345", "0012346"]
print("PASSED")

print("TEST: findings for Customers - exact counts, each with a short example, safe fixes on, risky ones off")
customers = findings.describe_sheet(sheets["Customers"], DEFAULT_PLACEHOLDERS)
by = {f.step: f for f in customers.findings}
for finding in customers.findings:
    print("  ", finding.step.ljust(12), finding.text, "| on" if finding.default_on else "| OFF (Review)")
assert customers.is_data and (customers.rows, customers.columns) == (12, 10)
assert by["duplicates"].text == "1 duplicate row (Sara Ahmed)" and by["duplicates"].count == 1
assert by["empty"].text == "1 empty row and 1 empty column (Notes)"
assert by["placeholders"].count == 5 and by["placeholders"].text.startswith("5 placeholder values like N/A")
assert by["trim"].text.startswith("Extra spaces in 2 cells") and by["headers"].text == "Column header ' Customer Name ' has extra spaces"
assert by["flags"].text == "2 unusual values in Age (-5, 999)" and by["flags"].default_on
assert by["near"].review and by["near"].default_on is False and by["near"].count == 2 and "Review" in by["near"].text
assert all(by[s].default_on for s in ("duplicates", "empty", "placeholders", "trim", "headers", "flags"))
assert all("dtype" not in f.text and "normalize" not in f.text.lower() and "trim" not in f.text.lower() and f.count != 0 for f in customers.findings)
print("PASSED")

print("TEST: findings for Orders and Read Me")
orders = findings.describe_sheet(sheets["Orders"], DEFAULT_PLACEHOLDERS)
by_orders = {f.step: f for f in orders.findings}
assert by_orders["placeholders"].count == 7 and by_orders["placeholders"].text == "7 placeholder values like UNKNOWN, ERROR"
assert by_orders["numeric"].text == "Numbers saved as text in 1 column (Qty)"
assert by_orders["fills"].count == 12 and "calculated exactly from Total = Qty x Unit Price" in by_orders["fills"].text and "5 in Total" in by_orders["fills"].text
assert set(by_orders) == {"placeholders", "numeric", "fills"}                   # nothing else is wrong with Orders
readme = findings.describe_sheet(sheets["Read Me"], DEFAULT_PLACEHOLDERS)
assert readme.is_data is False and "one column" in readme.why
assert findings.is_data_sheet(sheets["Customers"])[0] and findings.is_data_sheet(sheets["Orders"])[0]
assert findings.is_data_sheet(pd.DataFrame({"a": ["x"], "b": ["y"]}))[0] is False                      # one row
print("PASSED")

print("TEST: detection changes nothing - the sheets are the same after, and the dry run is the real pipeline (its counts equal what cleaning does)")
before = {n: df.copy() for n, df in sheets.items()}
for n, df in sheets.items():
    findings.describe_sheet(df, DEFAULT_PLACEHOLDERS)
    assert df.equals(before[n]) and list(df.columns) == list(before[n].columns)
print("PASSED")

print("TEST: the page - findings first (nothing cleaned yet), one tab per sheet, 'Not a data sheet' marked, empty cells shown blank, leading zeros kept in the preview")
at = new_app()
assert [s.value for s in at.subheader][:2] == ["1. Upload your file", "2. Here's what I found"]
assert at.session_state["cleaned_sheets"] is None and not at.metric                                  # no change was made
assert [t.label for t in at.tabs] == ["Customers", "Orders", "Read Me"]
assert any("Not a data sheet - kept exactly as is" in i.value for i in at.info)
assert include_box(at, "Read Me").value is False and include_box(at, "Customers").value is True
assert box(at, "duplicates", "Customers").value and box(at, "near", "Customers").value is False
assert not any(c.label in ("Remove duplicate rows", "Clean column headers", "Trim extra spaces from text cells") for c in at.checkbox)    # the old switches are gone
assert not at.radio and any(e.label == "Customize" for e in at.expander)
frames = [d.value for d in at.dataframe]
orders_preview = next(f for f in frames if "Order No" in f.columns)
assert orders_preview["Order No"].tolist()[:2] == ["000101", "000102"] and "None" not in orders_preview.to_string() and "nan" not in orders_preview.to_string()
customers_preview = next(f for f in frames if "Account No" in f.columns)
assert customers_preview["Account No"].iloc[0] == "0012345" and "None" not in customers_preview.to_string() and "nan" not in customers_preview.to_string()
print("PASSED")

print("TEST: the preview marks the problems - colours for the cells, crossed-out rows, red border for unusual values")
shown = findings.describe_sheet(sheets["Customers"], DEFAULT_PLACEHOLDERS).findings
styler, count, total = report.styled_findings_preview(sheets["Customers"], shown)
html = styler.to_html()
assert total == 12 and "text-decoration: line-through" in html and "border: 2px solid #E26B0A" in html and "background-color: #D9D9D9" in html and "background-color: #FFF2CC" in html
import re
crossed = re.search(r"([^{}]*)\{[^}]*line-through", html).group(1)                                      # the selectors of the crossed-out cells (the Styler groups equal styles)
assert all(f"_row{r}_col{c}" in crossed for r in (3, 4) for c in range(10))                                # the duplicate (sheet row 5) and the empty row (row 6), every cell
assert "0012345" in html and "None" not in html and "nan" not in html
print("PASSED")

print("TEST: default 'Clean my file' - Customers 12 -> 10 rows (exactly the duplicate and the empty row), Notes gone, 5 placeholders; Orders 70 -> 70, 7 placeholders")
clean(at)
cleaned, stats, logs = at.session_state["cleaned_sheets"], at.session_state["sheet_stats"], at.session_state["change_logs"]
customers_out, orders_out = cleaned["Customers"], cleaned["Orders"]
assert (len(sheets["Customers"]), len(customers_out)) == (12, 10) and (len(sheets["Orders"]), len(orders_out)) == (70, 70)
removed = logs["Customers"].finalize_removed()
assert [(r["original_row"], r["kind"]) for r in removed] == [(5, "duplicate"), (6, "empty")] and len(removed) == 2         # rows 5 (Sara Ahmed again) and 6 (empty), as numbered in the file
assert "Notes" not in customers_out.columns and list(customers_out.columns) == [c.strip() for c in sheets["Customers"].columns if c != "Notes"]
assert stats["Customers"]["stats"]["placeholders"] == {"total": 5, "by_column": {"Phone": {"N/A": 1}, "Email": {"UNKNOWN": 1}, "Status": {"-": 1}, "Join Date": {"ERROR": 1},
                                                                                 "Amount Paid": {"NULL": 1}}}
assert stats["Orders"]["stats"]["placeholders"] == {"total": 7, "by_column": {"Item": {"UNKNOWN": 3}, "Qty": {"ERROR": 4}}}
assert "Read Me" in cleaned and stats["Read Me"]["cleaned"] is False
print("PASSED")

print("TEST: leading zeros survive everything - Order No '000101'..'000170' and Account No '0012345' are text in the cleaned data and in the output workbook; blank Account No cells stay blank")
assert orders_out["Order No"].tolist() == [f"{n:06d}" for n in range(101, 171)]
assert customers_out["Account No"].tolist()[:5] == ["0012345", "0012346", "0012347", "0012348", "0012349"] and customers_out["Account No"].isna().sum() == 2
book = output_workbook(at)
assert book.sheetnames == ["Customers", "Orders", "Read Me", "Cleaning Summary", "Change Log", "Removed Rows"]
order_cells = [book["Orders"].cell(row=r, column=1) for r in range(2, 72)]
assert [c.value for c in order_cells] == [f"{n:06d}" for n in range(101, 171)] and {c.data_type for c in order_cells} == {"s"}
account = [c for c in book["Customers"][1] if c.value == "Account No"][0].column
account_cells = [book["Customers"].cell(row=r, column=account) for r in range(2, 12)]
assert account_cells[0].value == "0012345" and account_cells[0].data_type == "s"
assert [c.value for c in account_cells if c.value is None] == [None, None]                       # the two blank cells are still blank
assert book["Customers"].max_row == 11 and "Notes" not in [c.value for c in book["Customers"][1]]
print("PASSED")

print("TEST: Read Me is kept exactly as is - in the output workbook both cells are identical to the upload, spaces included")
original = openpyxl.load_workbook(io.BytesIO(PACK_BYTES))["Read Me"]
kept = book["Read Me"]
assert [(c.value, c.data_type) for row in kept.iter_rows() for c in row] == [(c.value, c.data_type) for row in original.iter_rows() for c in row]
assert kept["A1"].value == "This sheet should never change" and kept["A2"].value == "  keep   these   spaces  "
assert (kept.max_row, kept.max_column) == (original.max_row, original.max_column) == (2, 1)
assert "Read Me" not in [c.value for row in book["Cleaning Summary"].iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith("Sheet: Read Me")]
print("PASSED")

print("TEST: the tick boxes are the only control - unticking 'duplicate row' keeps it; unticking 'placeholders' keeps them; 'Include anyway' cleans Read Me")
at = new_app()
box(at, "duplicates", "Customers").uncheck()
box(at, "placeholders", "Orders").uncheck()
include_box(at, "Read Me").check()
at.run()
assert box(at, "trim", "Read Me").value and "Extra spaces in 1 cell" in box(at, "trim", "Read Me").label       # now its findings are shown
clean(at)
cleaned, stats = at.session_state["cleaned_sheets"], at.session_state["sheet_stats"]
assert len(cleaned["Customers"]) == 11 and "Sara Ahmed" in cleaned["Customers"]["Customer Name"].tolist() and cleaned["Customers"]["Customer Name"].tolist().count("Sara Ahmed") == 2
assert stats["Customers"]["stats"]["duplicates_removed"] is None and stats["Orders"]["stats"]["placeholders"] is None
assert "ERROR" in cleaned["Orders"]["Qty"].tolist() and cleaned["Read Me"].iloc[0, 0] == "keep   these   spaces" and stats["Read Me"]["cleaned"] is True
print("PASSED")

print("TEST: the calculated fills are a finding (on); the lookups are opt-in under Customize (off)")
at = new_app()
assert box(at, "fills", "Orders").value is True
lookup = [c for c in at.checkbox if "always the same" in c.label]
assert lookup and all(c.value is False for c in lookup)
clean(at)
assert at.session_state["sheet_stats"]["Orders"]["stats"]["fills"]["calculated"] == {"Total": 5, "Qty": 4, "Unit Price": 3}
assert at.session_state["sheet_stats"]["Orders"]["stats"]["fills"]["lookup"] == {}
print("PASSED")

print("TEST: a one-column CSV is data (the 'not a data sheet' rule is for Excel files with several sheets)")
at = new_app(b"Email\nA@x.com \nb@x.com\n  c@x.com\n", "emails.csv", "text/csv")
assert not any("Not a data sheet" in i.value for i in at.info) and any(b.label == "Clean my file" for b in at.button)
clean(at)
assert at.session_state["cleaned_sheets"]["Data"]["Email"].tolist() == ["A@x.com", "b@x.com", "c@x.com"]
print("PASSED")

print("TEST: possible duplicate people are never automatic and, on a big sheet, are not looked for during the findings (only in the review)")
calls = []
real = __import__("dedupe.analyze", fromlist=["find_near_duplicates"]).find_near_duplicates
with mock.patch("dedupe.analyze.find_near_duplicates", lambda *a, **k: calls.append(1) or real(*a, **k)):
    small = findings.describe_sheet(sheets["Customers"], DEFAULT_PLACEHOLDERS)
    big = findings.describe_sheet(sheets["Customers"], DEFAULT_PLACEHOLDERS, near_limit=5)
assert len(calls) == 1                                                                                 # the small sheet was searched, the 'big' one was not
near = next(f for f in big.findings if f.step == "near")
assert near.count is None and near.default_on is False and "when you open the review" in near.text
assert findings.NEAR_DUPLICATE_ROW_LIMIT == 5000
at = new_app()
clean(at)
assert len(at.session_state["cleaned_sheets"]["Customers"]) == 10 and not any(r["kind"] == "near_duplicate" for r in at.session_state["change_logs"]["Customers"].finalize_removed())
assert any(h.value == "Near-duplicates" for h in at.header)                                              # the review is there in step 3
print("PASSED")

print("TEST: the detection is cached - a click on a tick box does not run it again")
counter = []
real_describe = findings.describe_sheet
unique = ("Name,Phone\n" + "\n".join(f"Person{i % 7} Cache,03001234{i:03d}" for i in range(30)) + "\nPerson0 Cache,03001234000\n").encode()   # (the last row repeats the first)
with mock.patch.object(findings, "describe_sheet", lambda *a, **k: counter.append(1) or real_describe(*a, **k)):
    at = new_app(unique, "cache_check.csv", "text/csv")
    first = len(counter)
    box(at, "duplicates").uncheck()                                                                       # (there is a duplicate row to untick and tick again)
    at.run()
    box(at, "duplicates").check()
    at.run()
print("   detection runs:", first, "after upload,", len(counter), "after more clicks")
assert first == 1 and len(counter) == 1
print("PASSED")

print("TEST: a recipe carries the ticks - saved from the page, loaded into the boxes next time")
captured = {}
original_download = st.download_button


def spy(label, data=None, file_name=None, mime=None, key=None, **kwargs):
    captured[key or label] = data
    return original_download(label, data=data, file_name=file_name, mime=mime, key=key, **kwargs)


with mock.patch.object(st, "download_button", spy):
    at = new_app()
    box(at, "duplicates", "Customers").uncheck()
    box(at, "trim", "Customers").uncheck()
    at.run()
recipe = __import__("json").loads(captured["download_settings_recipe"])
print("  ", recipe["steps"])
assert recipe["steps"]["duplicates"] is False and recipe["steps"]["trim"] is False and recipe["steps"]["empty"] is True and recipe["rules"] == []
at = new_app()
assert box(at, "duplicates", "Customers").value is True
next(u for u in at.file_uploader if u.key == "settings_recipe_uploader").upload("recipe.json", captured["download_settings_recipe"], "application/json")
at.run()
next(b for b in at.button if b.key == "use_recipe_settings").click()
at.run()
assert not at.exception, [e.value for e in at.exception]
assert box(at, "duplicates", "Customers").value is False and box(at, "trim", "Customers").value is False and box(at, "empty", "Customers").value is True
assert any("follow the recipe" in s.value for s in at.success)
at = new_app()
next(u for u in at.file_uploader if u.key == "settings_recipe_uploader").upload("rules_only.json", b'{"recipe_name": "x", "rules": []}', "application/json")
at.run()
next(b for b in at.button if b.key == "use_recipe_settings").click()
at.run()
assert any("no cleaning settings" in e.value for e in at.error) and box(at, "duplicates", "Customers").value is True        # an old recipe (AI rules only) changes nothing, and says so
print("PASSED")
