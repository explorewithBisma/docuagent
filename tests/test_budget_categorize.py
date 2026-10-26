from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""budget/categorize.py: keyword matching, priorities, validation, round trips."""
import json
import sys
from datetime import date

sys.path.insert(0, str(ROOT))

from budget import categorize as kw
from budget.model import BudgetConfig, TYPES

KM = kw.default_keyword_map()

print("TEST: the shipped default map is valid and uses only known types")
assert kw.validate_keyword_map(KM) == "" and set(KM["type_keywords"]) <= set(TYPES)
assert "Card bill payment" in KM["type_keywords"] and len(TYPES) == 6
print("PASSED")

print("TEST: category matching - case, spaces, substrings, blanks")
cases = {"Taxi to office": "Transport", "  METRO card top-up ": "Transport", "Weekly Grocery shopping": "Food",
         "Lunch at Cafe Nero": "Food", "Netflix": "Entertainment", "random thing": None, "": None, None: None,
         float("nan"): None}
for text, expected in cases.items():
    assert kw.match_category(text, KM) == expected, (text, kw.match_category(text, KM), expected)
print("PASSED")

print("TEST: short keywords need a whole word ('bus' is not 'business'); long ones match inside words")
assert kw.match_category("business lunch", KM) == "Food"          # via 'lunch', not 'bus'
assert kw.match_category("business cards", KM) is None
assert kw.match_category("bus fare", KM) == "Transport" and kw.match_category("bus-fare", KM) == "Transport"
assert kw.match_category("supermarkets", KM) == "Food"
print("PASSED")

print("TEST: the longest keyword wins; a tie goes to the first category")
custom = {"categories": {"Home": ["card"], "Fun": ["credit card bonus"], "A": ["pizza"], "B": ["pizza"]}}
assert kw.match_category("credit card bonus points", custom) == "Fun"
assert kw.match_category("card", custom) == "Home"
assert kw.match_category("pizza", custom) == "A"
print("PASSED")

print("TEST: type keywords - salary, EMI, savings, credit card bill; plain spending has no type")
assert kw.match_type("Salary June", KM) == "Income"
assert kw.match_type("Car EMI", KM) == "Loan payment"
assert kw.match_type("Credit card bill - Visa", KM) == "Card bill payment"
assert kw.match_type("Transfer to savings", KM) == "Saving"
assert kw.match_type("taxi", KM) is None and kw.match_type(None, KM) is None
print("PASSED")

print("TEST: validation errors are readable")
for bad, text in [({}, "categories"), ({"categories": {"A": "x"}}, "list"), ({"categories": {"A": [""]}}, "non-empty"),
                  ({"categories": {"A": ["x"], "a": ["y"]}}, "twice"),
                  ({"categories": {}, "type_keywords": {"Nope": ["x"]}}, "Unknown type")]:
    assert text in kw.validate_keyword_map(bad), (bad, kw.validate_keyword_map(bad))
for raw in (b"not json", b"[]"):
    try:
        kw.load_keyword_map(raw)
        raise AssertionError("should have failed")
    except ValueError:
        pass
print("PASSED")

print("TEST: round trips - file bytes, editable table rows, add_keyword (original untouched)")
assert kw.load_keyword_map(kw.keyword_map_bytes(KM)) == KM
rows = kw.keyword_map_to_rows(KM)
assert kw.keyword_map_from_rows(rows, KM["type_keywords"]) == KM
rows.append({"Category": "Pets", "Keywords": "vet, dog food ,"})
rows.append({"Category": "  ", "Keywords": "ignored"})
rebuilt = kw.keyword_map_from_rows(rows)
assert rebuilt["categories"]["Pets"] == ["vet", "dog food"] and "  " not in rebuilt["categories"]
snapshot = json.dumps(KM)
bigger = kw.add_keyword(KM, "Food", "Shawarma")
assert "Shawarma" in bigger["categories"]["Food"] and json.dumps(KM) == snapshot
assert kw.add_keyword(bigger, "Food", " shawarma ")["categories"]["Food"].count("Shawarma") == 1
assert kw.match_category("chicken shawarma", bigger) == "Food"
print("PASSED")

print("TEST: BudgetConfig validation")
assert BudgetConfig(month_start=date(2026, 6, 1)).validate() == []
bad = BudgetConfig(month_start=date(2026, 6, 15), categories={"A": 1, "a": 2, "": 3}, payment_methods=["Cash", "cash"])
problems = " ".join(bad.validate())
assert "1st" in problems and "unique" in problems and "no name" in problems
assert BudgetConfig(categories={"A": -5}).validate()
print("PASSED")


print("TEST: Housing, Family / Remittance and Other are default categories; rent moved from Bills to Housing")
assert {"Housing", "Family / Remittance", "Other"} <= set(KM["categories"])
assert "rent" not in KM["categories"]["Bills"] and {"rent", "room", "villa", "flat"} == set(KM["categories"]["Housing"])
assert KM["categories"]["Family / Remittance"] == ["remittance", "send money", "transfer home", "taptap send", "western union", "exchange"]
assert KM["categories"]["Other"] == [] and kw.validate_keyword_map(KM) == ""
for text, expected in {"Room rent": "Housing", "Villa advance": "Housing", "Flat 3 deposit": "Housing", "Monthly rents": "Housing",
                       "Send money to mom": "Family / Remittance", "TapTap Send": "Family / Remittance",
                       "Western Union": "Family / Remittance", "currency EXCHANGE": "Family / Remittance",
                       "Internet bill": "Bills", "electricity": "Bills"}.items():
    assert kw.match_category(text, KM) == expected, (text, kw.match_category(text, KM))
print("PASSED")

print("TEST: 4-letter keywords are whole words (with an optional plural): 'room' is not 'mushroom', 'rent' is not 'current'")
for text in ("mushroom soup", "bathroom cleaner", "current account", "parent visit", "different shop", "inflation"):
    assert kw.match_category(text, KM) is None, (text, kw.match_category(text, KM))
assert kw.match_category("taxis", KM) == "Transport" and kw.match_category("buses", KM) == "Transport"
assert kw.match_category("TapTap", KM) is None, "a bare brand name is not guessed: it goes to the AI step / the Please-check list"
assert kw.match_category("rent-a-car", KM) == "Housing"          # a hyphen separates words
print("PASSED")

print("TEST: the empty 'Other' category survives the table round trip")
assert kw.keyword_map_from_rows(kw.keyword_map_to_rows(KM), KM["type_keywords"])["categories"]["Other"] == []
print("PASSED")
