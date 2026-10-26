from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""budget/prepare.py: starting categories, restoring dropped rows, final checks."""
import sys
from datetime import date

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import pandas as pd
from budget import categorize, importer, prepare
from budget.model import BudgetConfig
from budget_fixtures import messy_budget_bytes

KM = categorize.default_keyword_map()
raw = importer.read_raw_sheets(messy_budget_bytes())
name, layout = importer.best_sheet(raw)
IMPORT = importer.import_transactions(raw[name], layout, KM, "PK", date(2026, 6, 1))

print("TEST: starting categories - keyword-map categories plus the file's own; existing settings win")
cats = prepare.starting_categories(KM, IMPORT.rows)
assert list(cats)[:len(KM["categories"])] == list(KM["categories"]) and cats["Pets"] == 0 and "pets" not in {c.casefold() for c in KM["categories"]}
assert all(budget == 0 for budget in cats.values())
existing = prepare.starting_categories(KM, IMPORT.rows, {"Food": 300})
assert existing["Food"] == 300 and "Pets" in existing and "Transport" in existing, existing   # categories the rows use are added
assert "Health" not in existing and "Shopping" not in existing, "unused keyword-map categories are not forced on existing settings"
print("PASSED")

print("TEST: restoring dropped rows - back in source order, dated from the row above, flagged")
restored = prepare.restore_dropped(IMPORT.rows, IMPORT.dropped, [7, 17], KM)
assert len(restored) == 14 and list(restored["Source row"]) == sorted(restored["Source row"])
total = restored[restored["Source row"] == 7].iloc[0]
assert total["Description"] == "Total" and total["Amount"] == 175 and total["Date"] == date(2026, 6, 1)
assert "restored" in total["Flags"] and total["Type"] == "Expense"
note = restored[restored["Source row"] == 17].iloc[0]
assert note["Amount"] is None or pd.isna(note["Amount"])
assert len(IMPORT.rows) == 12, "the original is untouched"
assert prepare.restore_dropped(IMPORT.rows, IMPORT.dropped, [999], KM).equals(IMPORT.rows)
print("PASSED")

print("TEST: finalize - columns, added categories / payment methods, empty lines removed")
config = BudgetConfig(month_start=date(2026, 6, 1), categories={"Food": 100}, payment_methods=["Cash"])
rows, final, problems, notes = prepare.finalize(IMPORT.rows, config)
assert list(rows.columns) == ["Date", "Description", "Type", "Category", "Amount", "Payment method"] and not problems
assert "Transport" in final.categories and "Pets" in final.categories and final.categories["Food"] == 100
assert "Card" in final.payment_methods and "Bank transfer" in final.payment_methods and config.categories == {"Food": 100}
assert any(n.startswith("Added to your categories") for n in notes) and any("payment methods" in n for n in notes)
blank_line = pd.DataFrame([{c: None for c in IMPORT.rows.columns}])
assert len(prepare.finalize(pd.concat([IMPORT.rows, blank_line]), config)[0]) == 12
print("PASSED")

print("TEST: finalize - problems the user must fix")
bad = IMPORT.rows.copy()
bad.loc[0, "Type"] = "Spending"
bad.loc[1, "Amount"] = -5
_, _, problems, _ = prepare.finalize(bad, config)
assert any("Unknown Type: Spending" in p for p in problems) and any("positive" in p for p in problems)
bad.loc[2, "Amount"] = "abc"
assert any("not numbers" in p for p in prepare.finalize(bad, config)[2])
many = pd.DataFrame([{"Date": date(2026, 6, 1), "Description": "x", "Type": "Expense", "Category": f"C{i}", "Amount": 1.0,
                      "Payment method": f"M{i}"} for i in range(40)])
problems = prepare.finalize(many, config)[2]
assert any("categories" in p and "40" in p or "41" in p for p in problems) and any("payment methods" in p for p in problems)
assert prepare.finalize(IMPORT.rows, BudgetConfig(month_start=date(2026, 6, 4)))[2][0] == "The month must start on the 1st."
print("PASSED")
