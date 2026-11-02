"""
budget/categorize.py
---------------------
The keyword map: guesses a transaction's Category (and, for things like salary or loan
payments, its Type) from the description. Pure text matching - no AI.

Map format (JSON, see budget/default_keywords.json):
  {"version": 1,
   "categories":    {"Food": ["grocery", "restaurant"], "Transport": ["taxi", "metro"]},
   "type_keywords": {"Income": ["salary"], "Loan payment": ["emi"], ...}}

Matching: ignores capitals and surrounding spaces. A keyword of 4 characters or fewer must
match a whole word, optionally in the plural ("rent" matches "Room rent" and "rents" but not
"current"; "room" does not match "mushroom"); longer keywords match anywhere in the text. When several categories match, the longest keyword wins; on a tie the category that
comes first in the map wins.
"""

import json
import re
from pathlib import Path

from budget.model import TYPES
from quality import is_blank

DEFAULT_MAP_PATH = Path(__file__).with_name("default_keywords.json")
SHORT_KEYWORD = 4
# What a bank puts in front of the merchant: POS 1234 CARREFOUR, ECOM AMAZON, ATM WITHDRAWAL, CHQ 000123, IBFT TO ALI KHAN. Not part of the merchant's name.
_PREFIX = re.compile(r"^(?:(?:pos|ecom|e-com|atm|chq|cheque|ibft)\b[\s\-:/*#]*(?:\d+[\s\-:/*#]*)?)+", re.IGNORECASE)
# Keywords of the Card bill payment type that only say "my own account": when the text also names a savings / deposit / investment account, it is Saving.
OWN_ACCOUNT_WORDS = ("own account", "between accounts", "between my accounts", "internal transfer", "self transfer", "transfer to self", "own transfer")


def default_keyword_map() -> dict:
    return json.loads(DEFAULT_MAP_PATH.read_text(encoding="utf-8"))


def _keyword_list_problem(label: str, keywords) -> str:
    if not isinstance(keywords, list) or any(not isinstance(k, str) or not k.strip() for k in keywords):
        return f"{label} needs a list of non-empty keywords."
    return ""


def validate_keyword_map(data) -> str:
    """'' if the map is usable, otherwise what is wrong with it."""
    if not isinstance(data, dict) or not isinstance(data.get("categories"), dict):
        return "The keyword map needs a 'categories' object."
    seen = set()
    for name, keywords in data["categories"].items():
        if not isinstance(name, str) or not name.strip():
            return "A category name is empty."
        if name.casefold() in seen:
            return f"Category '{name}' appears twice (ignoring capitals)."
        seen.add(name.casefold())
        problem = _keyword_list_problem(f"Category '{name}'", keywords)
        if problem:
            return problem
    type_keywords = data.get("type_keywords", {})
    if not isinstance(type_keywords, dict):
        return "'type_keywords' must be an object."
    for type_name, keywords in type_keywords.items():
        if type_name not in TYPES:
            return f"Unknown type '{type_name}' in type_keywords (use one of: {', '.join(TYPES)})."
        problem = _keyword_list_problem(f"Type '{type_name}'", keywords)
        if problem:
            return problem
    return ""


def load_keyword_map(file_like_or_bytes) -> dict:
    """Read an uploaded keyword-map JSON. Raises ValueError with a readable message."""
    raw = file_like_or_bytes if isinstance(file_like_or_bytes, (bytes, str)) else file_like_or_bytes.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"This doesn't look like a valid JSON file: {error}")
    problem = validate_keyword_map(data)
    if problem:
        raise ValueError(problem)
    data.setdefault("version", 1)
    data.setdefault("type_keywords", {})
    return data


def keyword_map_bytes(keyword_map: dict) -> bytes:
    return json.dumps(keyword_map, indent=2, ensure_ascii=False).encode("utf-8")


def _normalize(text) -> str:
    return re.sub(r"\s+", " ", str(text).strip().casefold())


def strip_prefix(description) -> str:
    """The description without a leading bank code (POS, ECOM, ATM, CHQ, IBFT and the reference number after it)."""
    text = str(description).strip()
    stripped = _PREFIX.sub("", text).strip()
    return stripped or text


def _hits(text: str, keyword: str) -> bool:
    keyword = _normalize(keyword)
    if not keyword:
        return False
    if len(keyword) <= SHORT_KEYWORD:
        return re.search(rf"(?<!\w){re.escape(keyword)}(?:e?s)?(?!\w)", text) is not None
    return keyword in text


def _best(text: str, groups: dict):
    """Name of the group whose longest matching keyword is longest; the first group wins a tie."""
    best_name, best_length = None, 0
    for name, keywords in groups.items():
        for keyword in keywords:
            length = len(_normalize(keyword))
            if length > best_length and _hits(text, keyword):
                best_name, best_length = name, length
    return best_name


def match_category(description, keyword_map: dict):
    """The category a description belongs to, or None when no keyword matches."""
    if is_blank(description):
        return None
    return _best(_normalize(strip_prefix(description)), keyword_map["categories"])


def match_type(description, keyword_map: dict):
    """A Type implied by the description (salary -> Income, EMI -> Loan payment ...), or None."""
    if is_blank(description):
        return None
    text = _normalize(strip_prefix(description))
    groups = keyword_map.get("type_keywords", {})
    best = _best(text, groups)
    if best == "Card bill payment" and "Saving" in groups:
        # "TRANSFER TO OWN ACCOUNT SAVINGS": the card-bill keyword that matched is only "own account" -> it is a Saving
        own_only = not any(_hits(text, k) and _normalize(k) not in OWN_ACCOUNT_WORDS for k in groups["Card bill payment"])
        if own_only and any(_hits(text, k) for k in groups["Saving"]):
            return "Saving"
    return best


def add_keyword(keyword_map: dict, category: str, keyword: str) -> dict:
    """A copy of the map with `keyword` added to `category` (created if new)."""
    updated = json.loads(json.dumps(keyword_map))
    keywords = updated["categories"].setdefault(category, [])
    if _normalize(keyword) not in {_normalize(k) for k in keywords}:
        keywords.append(keyword.strip())
    return updated


def keyword_map_to_rows(keyword_map: dict) -> list:
    """[{'Category': 'Food', 'Keywords': 'grocery, restaurant'}, ...] for the editable table."""
    return [{"Category": name, "Keywords": ", ".join(keywords)} for name, keywords in keyword_map["categories"].items()]


def keyword_map_from_rows(rows: list, type_keywords: dict = None) -> dict:
    """Build a map back from the editable table (blank rows ignored). Raises ValueError if invalid."""
    categories = {}
    for row in rows:
        name = str(row.get("Category") or "").strip()
        if not name:
            continue
        categories[name] = [k.strip() for k in str(row.get("Keywords") or "").split(",") if k.strip()]
    data = {"version": 1, "categories": categories, "type_keywords": type_keywords or {}}
    problem = validate_keyword_map(data)
    if problem:
        raise ValueError(problem)
    return data
