from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
"""budget/names.py: emoji / punctuation / typo tolerant matching of category names and descriptions."""
import sys

sys.path.insert(0, str(ROOT))

from budget import categorize
from budget import names

KM = categorize.default_keyword_map()
OURS = list(KM["categories"])

print("TEST: canonical / display names ignore emojis, capitals and punctuation")
assert names.canonical("🍔 Food") == "food" and names.canonical("  FOOD!! ") == "food" and names.canonical("Food/Grocery") == "food grocery"
assert names.canonical(None) == "" and names.canonical(float("nan")) == "" and names.canonical("🍔") == ""
assert names.display_name("🍔 Food") == "Food" and names.display_name("🏠 Housing") == "Housing" and names.display_name("Food/Grocery") == "Food/Grocery"
assert names.display_name("💊  Health  ") == "Health"
print("PASSED")

print("TEST: match_name - exact (ignoring decoration), typo, and no match")
assert names.match_name("🍔 Food", OURS) == ("Food", "exact")
assert names.match_name("transport", OURS) == ("Transport", "exact")
assert names.match_name("Transprt", OURS) == ("Transport", "typo")
assert names.match_name("Entertainmnt", OURS) == ("Entertainment", "typo")
assert names.match_name("Housing", OURS) == (None, "") and names.match_name("", OURS) == (None, "") and names.match_name(None, OURS) == (None, "")
assert names.match_name("Bil", OURS) == (None, ""), "short words are never matched loosely"
print("PASSED")

print("TEST: merge_map - obvious merges only")
merged = names.merge_map(["Food/Grocery", "🍔 Food", "Food", "Transprt", "Housing", "Other", "Health & Fitness", "Food & Shopping"], OURS)
assert merged == {"Food/Grocery": "Food", "🍔 Food": "Food", "Transprt": "Transport", "Health & Fitness": "Health"}, merged
assert "Food & Shopping" not in merged, "two different categories in one name is ambiguous - left alone"
assert names.merge_map(["Housing"], OURS) == {} and names.merge_map([None, ""], OURS) == {}
print("PASSED")

print("TEST: split_parts")
assert names.split_parts("Food/Grocery") == ["Food", "Grocery"] and names.split_parts("Eating out and drinks") == ["Eating out", "drinks"]
assert names.split_parts("Rent") == ["Rent"]
print("PASSED")

print("TEST: fuzzy_category - typos of single-word keywords match; short words and unrelated words do not")
assert names.fuzzy_category("Resturant", KM) == ("Food", "restaurant")
assert names.fuzzy_category("groceries at day to day", KM)[0] == "Food"       # exact words also match; the keyword map is simply tried first
assert names.fuzzy_category("Supermarkt", KM) == ("Food", "supermarket")
assert names.fuzzy_category("Taksi", KM) == (None, None), "'taksi' is only 5 letters from 'taxi' (4): too short to guess"
for unrelated in ("malabar", "Nesto", "Aster", "Grand barsha", "viva", "bus", "", None):
    assert names.fuzzy_category(unrelated, KM) == (None, None), unrelated
print("PASSED")
