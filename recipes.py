"""
recipes.py
-----------
Save and load a "recipe" - a reusable list of approved AI-suggested rules -
as a JSON file. Saving only keeps rules the user actually approved, so
loading a recipe later skips straight back to a short list of known-good
rules instead of needing a fresh (and billed) AI suggestion call. Loaded
rules still go through the same approve/reject review screen as fresh AI
suggestions - just pre-checked - so reusing a recipe is never fully silent.
"""

import json


def build_recipe(recipe_name: str, locale_code: str, rules: list) -> dict:
    """Build the recipe dict that gets saved to a .json file. Only rules the
    user actually approved are included - rejected suggestions are dropped."""
    return {
        "recipe_name": recipe_name,
        "locale": locale_code,
        "rules": [rule for rule in rules if rule.get("approved")],
    }


def save_recipe_bytes(recipe: dict) -> bytes:
    """Turn a recipe dict into downloadable JSON file bytes."""
    return json.dumps(recipe, indent=2).encode("utf-8")


def load_recipe(file_like) -> dict:
    """Read an uploaded recipe .json file back into a dict. Raises
    ValueError with a human-readable message if the file doesn't look like a
    recipe - the caller (the UI) should display that message, not crash."""
    try:
        data = json.load(file_like)
    except json.JSONDecodeError as error:
        raise ValueError(f"This doesn't look like a valid JSON file: {error}")

    if not isinstance(data, dict) or "rules" not in data or not isinstance(data["rules"], list):
        raise ValueError("This JSON file doesn't look like a cleaning recipe (missing a 'rules' list).")

    return data
