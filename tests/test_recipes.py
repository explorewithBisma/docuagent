from pathlib import Path as _Path
ROOT = _Path(__file__).resolve().parents[1]
FIXTURES = _Path(__file__).resolve().parent / "fixtures"
"""Offline unit tests for recipes.py."""
import sys
sys.path.insert(0, str(ROOT))

import io
import recipes

print("=" * 70)
print("TEST: build_recipe keeps only approved rules")
print("=" * 70)
rules = [
    {"id": "rule_1", "column": "Phone", "rule_type": "normalize_phone",
     "params": {"country": "PK"}, "description": "x", "confidence": "high", "approved": True},
    {"id": "rule_2", "column": "Status", "rule_type": "unify_casing",
     "params": {"style": "title"}, "description": "y", "confidence": "medium", "approved": False},
]
recipe = recipes.build_recipe("Test Recipe", "PK", rules)
print(recipe)
assert recipe["recipe_name"] == "Test Recipe"
assert recipe["locale"] == "PK"
assert len(recipe["rules"]) == 1
assert recipe["rules"][0]["id"] == "rule_1"
print("PASSED")

print()
print("=" * 70)
print("TEST: save -> load round trip")
print("=" * 70)
recipe_bytes = recipes.save_recipe_bytes(recipe)
print(recipe_bytes.decode("utf-8"))
loaded = recipes.load_recipe(io.BytesIO(recipe_bytes))
assert loaded == recipe
print("PASSED")

print()
print("=" * 70)
print("TEST: load_recipe rejects garbage JSON")
print("=" * 70)
try:
    recipes.load_recipe(io.BytesIO(b"not json at all {{{"))
    raise AssertionError("expected ValueError, got none")
except ValueError as error:
    print("Correctly raised:", error)
print("PASSED")

print()
print("=" * 70)
print("TEST: load_recipe rejects valid JSON that isn't a recipe")
print("=" * 70)
try:
    recipes.load_recipe(io.BytesIO(b'{"hello": "world"}'))
    raise AssertionError("expected ValueError, got none")
except ValueError as error:
    print("Correctly raised:", error)
print("PASSED")

print()
print("ALL RECIPES TESTS PASSED")
