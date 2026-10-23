from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"
"""End-to-end test of app.py driven through Streamlit's AppTest simulator -
exercises real widget interactions (upload, clean, AI suggest) without a
browser or a real Anthropic API key."""
from streamlit.testing.v1 import AppTest

APP_PATH = str(ROOT / "app.py")
CSV_PATH = str(FIXTURES / "messy_data.csv")
XLSX_PATH = str(FIXTURES / "messy_multi_sheet.xlsx")


def print_exceptions(at, label):
    if at.exception:
        print(f"!!! EXCEPTION after {label}:")
        for exc in at.exception:
            print(exc.value)
        raise AssertionError(f"Unexpected exception after {label}")
    else:
        print(f"OK - no exception after {label}")


print("=" * 70)
print("TEST A: CSV upload -> clean -> try AI suggest (no API key present)")
print("=" * 70)

at = AppTest.from_file(APP_PATH, default_timeout=30)
at.run()
print_exceptions(at, "initial load")

with open(CSV_PATH, "rb") as f:
    csv_bytes = f.read()
at.file_uploader[0].upload("messy_data.csv", csv_bytes, "text/csv")
at.run()
print_exceptions(at, "CSV upload")

clean_button = next(b for b in at.button if b.label == "Clean File")
clean_button.click()
at.run()
print_exceptions(at, "Clean File click")

assert any("Cleaning Summary" in el.value for el in at.subheader), "Cleaning Summary header missing"
print("Cleaning Summary rendered correctly")

download_labels = [b.label for b in at.download_button]
print("Download buttons present:", download_labels)
assert "Download Cleaned File" in download_labels
assert "Download Cleaning Report (.txt)" in download_labels

suggest_button = next(b for b in at.button if b.label == "Suggest Cleaning Rules")
suggest_button.click()
at.run()
print_exceptions(at, "Suggest Cleaning Rules click (expected: graceful API error, not a crash)")

error_texts = [e.value for e in at.error]
print("st.error messages shown:", error_texts)
assert any("Could not get AI suggestions" in msg for msg in error_texts), \
    "Expected a graceful 'Could not get AI suggestions' error without an API key"
print("Confirmed: missing API key is handled gracefully (no crash, clear error shown)")

print()
print("=" * 70)
print("TEST B: multi-sheet xlsx upload -> clean only one sheet -> verify UI")
print("=" * 70)

at2 = AppTest.from_file(APP_PATH, default_timeout=30)
at2.run()

with open(XLSX_PATH, "rb") as f:
    xlsx_bytes = f.read()
at2.file_uploader[0].upload(
    "messy_multi_sheet.xlsx", xlsx_bytes,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
at2.run()
print_exceptions(at2, "xlsx upload")

radio_labels = [r.value for r in at2.radio]
print("Sheet-selection radio default value:", radio_labels)

# Select only the "Customers" sheet to clean
sheet_radio = at2.radio[0]
sheet_radio.set_value("Customers")
at2.run()
print_exceptions(at2, "selecting single sheet to clean")

clean_button2 = next(b for b in at2.button if b.label == "Clean File")
clean_button2.click()
at2.run()
print_exceptions(at2, "Clean File click (single sheet mode)")

summary_texts = "\n".join(el.value for el in at2.markdown) + "\n".join(el.value for el in at2.text)
print("Looking for 'left exactly as uploaded' confirmation in summary...")
all_text = " ".join(getattr(el, "value", "") for el in (list(at2.markdown) + list(at2.text)))
assert "left exactly as uploaded" in all_text, "Expected unselected sheets to be marked as not cleaned"
print("Confirmed: non-selected sheets correctly marked 'left exactly as uploaded'")

print()
print("ALL APP.PY END-TO-END UI TESTS PASSED")
