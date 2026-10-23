from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"
"""End-to-end test of the recipe load -> approve -> apply -> save flow,
driven through real Streamlit widgets, with no network call involved."""
import sys
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest
import recipes

APP_PATH = str(ROOT / "app.py")
CSV_PATH = str(FIXTURES / "messy_data.csv")
RECIPE_PATH = str(FIXTURES / "test_recipe.json")


def print_exceptions(at, label):
    if at.exception:
        for exc in at.exception:
            print(exc.value)
        raise AssertionError(f"Unexpected exception after {label}")
    print(f"OK - no exception after {label}")


# Build a recipe that upper-cases the "Product" column - bypasses the AI entirely.
recipe = recipes.build_recipe(
    "Test Recipe",
    "PK",
    [{
        "id": "rule_1",
        "column": "Product",
        "rule_type": "unify_casing",
        "params": {"style": "upper"},
        "description": "Make all Product values UPPERCASE.",
        "confidence": "high",
        "approved": True,
    }],
)
with open(RECIPE_PATH, "wb") as f:
    f.write(recipes.save_recipe_bytes(recipe))

at = AppTest.from_file(APP_PATH, default_timeout=30)
at.run()

with open(CSV_PATH, "rb") as f:
    csv_bytes = f.read()
at.file_uploader[0].upload("messy_data.csv", csv_bytes, "text/csv")
at.run()

clean_button = next(b for b in at.button if b.label == "Clean File")
clean_button.click()
at.run()
print_exceptions(at, "Clean File click")

# --- Load the recipe via the "recipe_uploader" file_uploader ---
recipe_uploader = next(fu for fu in at.file_uploader if fu.key == "recipe_uploader")
with open(RECIPE_PATH, "rb") as f:
    recipe_bytes = f.read()
recipe_uploader.upload("test_recipe.json", recipe_bytes, "application/json")
at.run()
print_exceptions(at, "uploading recipe file")

load_recipe_button = next(b for b in at.button if b.label == "Load Recipe")
load_recipe_button.click()
at.run()
print_exceptions(at, "Load Recipe click")

success_msgs = [s.value for s in at.success]
print("Success messages:", success_msgs)
assert any("Loaded recipe" in msg for msg in success_msgs), "Expected a 'Loaded recipe' success message"

# --- Confirm the rule shows up pre-checked ---
rule_checkboxes = [cb for cb in at.checkbox if "UPPERCASE" in cb.label]
assert len(rule_checkboxes) == 1, f"Expected exactly 1 matching checkbox, found {len(rule_checkboxes)}"
assert rule_checkboxes[0].value is True, "Loaded recipe rule should be pre-checked (approved=True)"
print("Confirmed: loaded recipe rule is shown pre-checked for review")

# --- Apply the approved rule ---
apply_button = next(b for b in at.button if b.label == "Apply Approved Rules")
apply_button.click()
at.run()
print_exceptions(at, "Apply Approved Rules click")

applied_success = [s.value for s in at.success]
assert any("Approved rules applied" in msg for msg in applied_success)
print("Confirmed: 'Approved rules applied!' success message shown")

# --- Confirm the actual data was transformed (Product column now uppercase) ---
cleaned_df = at.session_state["cleaned_sheets"]["Data"]
print(cleaned_df)
assert list(cleaned_df["Product"]) == ["WIDGET", "GADGET", "GIZMO"], cleaned_df["Product"].tolist()
print("Confirmed: Product column was actually uppercased by the applied rule")

# --- Confirm the report text includes the rule outcome ---
report_text = at.session_state["report_text"]
print(report_text)
assert "AI-suggested rules applied" in report_text
assert "UPPERCASE" in report_text
print("Confirmed: report text includes the applied rule's outcome")

# --- Confirm the "Download Recipe" button renders ---
recipe_download_buttons = [b for b in at.download_button if b.label == "Download Recipe (.json)"]
assert len(recipe_download_buttons) == 1
print("Confirmed: 'Download Recipe (.json)' button is rendered")

print()
print("ALL RECIPE-FLOW END-TO-END TESTS PASSED")
