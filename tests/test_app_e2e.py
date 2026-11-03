from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"
"""End-to-end test of app.py driven through Streamlit's AppTest simulator -
exercises real widget interactions (upload, clean, AI suggest) without a
browser or a real Anthropic API key."""
from streamlit.testing.v1 import AppTest
import sys
sys.path.insert(0, str(_Path(__file__).resolve().parent))
from clean_page_helpers import box, boxes, include_box

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

clean_button = next(b for b in at.button if b.label == "Clean my file")
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
assert suggest_button.proto.disabled is True, "without an API key the AI button must be switched off"
captions = [c.value for c in at.caption]
assert any("AI suggestions are switched off: No API key is set" in c and "works without them" in c for c in captions), captions
print_exceptions(at, "page with no API key (expected: the AI button is off and says why)")
assert not at.error
print("Confirmed: with no API key the AI button is disabled and the page explains why (no crash, no error)")

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

print("Sheet tick boxes:", [c.label for c in at2.checkbox if c.key and c.key.startswith("include|")])

# Clean only the "Customers" sheet: untick "Clean this sheet" on the others
for other in ("Orders", "Notes"):
    include_box(at2, other).uncheck()
at2.run()
print_exceptions(at2, "selecting single sheet to clean")

clean_button2 = next(b for b in at2.button if b.label == "Clean my file")
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
