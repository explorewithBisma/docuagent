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

import copy
import json
from datetime import date


def build_recipe(recipe_name: str, locale_code: str, rules: list) -> dict:
    """Build the recipe dict that gets saved to a .json file. Only rules the
    user actually approved are included - rejected suggestions are dropped."""
    return {
        "recipe_name": recipe_name,
        "locale": locale_code,
        "rules": [rule for rule in rules if rule.get("approved")],
    }


def build_suggestions_file(suggested_rules_by_sheet: dict, provider: str, model: str, locale_code: str,
                           file_name: str, created: str = None) -> dict:
    """Everything the AI suggested, in the SAME format as a recipe, so it can be
    loaded back later through the same screen. Unlike a recipe it keeps ALL
    suggestions - ticked or not - and every rule is saved with approved = false,
    so loading the file pre-ticks nothing. Also records who suggested them
    (provider, model), the locale, the file they were made for and the date.
    The same rule suggested for several sheets is saved once."""
    seen = set()
    rules = []
    for sheet_rules in suggested_rules_by_sheet.values():
        for rule in sheet_rules:
            key = (rule["column"], rule["rule_type"], json.dumps(rule["params"], sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            rules.append({**copy.deepcopy(rule), "approved": False})
    return {
        "recipe_name": f"AI suggestions for {file_name}",
        "locale": locale_code,
        "provider": provider,
        "model": model,
        "file_name": file_name,
        "date": created or date.today().isoformat(),
        "rules": rules,
    }


def is_ticked(rule: dict) -> bool:
    """Should a loaded rule start out ticked on the review screen? Follows the rule's own
    'approved' flag: recipes (approved rules) are pre-ticked, a saved AI-suggestions file
    (approved = false) is not. A hand-written rule with no flag counts as ticked."""
    return rule.get("approved", True) is True


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
