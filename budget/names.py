"""
budget/names.py
----------------
Matching of category names and descriptions that are *almost* the same:

  canonical("🍔 Food")            -> "food"          (emojis, capitals and punctuation are ignored)
  match_name("Resturant", [...])  -> typo-tolerant match against a list of names
  merge_map(file_categories, ours) -> {"Food/Grocery": "Food"}  only when the match is obvious
  fuzzy_category(description, keyword_map) -> typo-tolerant keyword match ("Resturant" -> Food)
"""

import difflib
import re

from quality import is_blank

TYPO_CUTOFF = 0.85           # similarity (0-1) two words need to count as the same word with a typo
MIN_TYPO_LENGTH = 5          # shorter words are never matched loosely ("bus" is not "bust")
_SEPARATORS = re.compile(r"\s*(?:/|&|,|\+|;|\band\b)\s*", re.IGNORECASE)


def canonical(name) -> str:
    """Lower-case letters and digits only, single spaces: emojis, symbols and punctuation are removed."""
    if is_blank(name):
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]|_", " ", str(name)).casefold()).strip()


def display_name(name) -> str:
    """The name as a person would write it: emojis and stray symbols removed, spaces tidied ("🍔 Food" -> "Food")."""
    if is_blank(name):
        return ""
    cleaned = re.sub(r"[^\w\s/&,+\-().]", " ", str(name))
    return re.sub(r"\s+", " ", cleaned).strip()


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def match_name(name, candidates) -> tuple:
    """(candidate, how) for the candidate that `name` equals ("exact") or nearly equals ("typo"), else (None, "")."""
    wanted = canonical(name)
    if not wanted:
        return None, ""
    by_canonical = {canonical(c): c for c in candidates}
    if wanted in by_canonical:
        return by_canonical[wanted], "exact"
    if len(wanted) >= MIN_TYPO_LENGTH:
        close = difflib.get_close_matches(wanted, [k for k in by_canonical if len(k) >= MIN_TYPO_LENGTH], n=1, cutoff=TYPO_CUTOFF)
        if close:
            return by_canonical[close[0]], "typo"
    return None, ""


def split_parts(name) -> list:
    """"Food/Grocery" -> ["Food", "Grocery"]; "Eating out & drinks" -> ["Eating out", "drinks"]."""
    return [part for part in _SEPARATORS.split(display_name(name)) if part.strip()]


def merge_map(file_categories, our_categories) -> dict:
    """{file category: our category} for the categories of the file that obviously mean one of ours:
    the same name ignoring emojis, capitals and punctuation, a typo of it, or a combined name
    ("Food/Grocery") with exactly one part that matches one of ours. Anything ambiguous is left out."""
    ours = list(our_categories)
    merged = {}
    for name in file_categories:
        if is_blank(name):
            continue
        target, _ = match_name(name, ours)
        if target is None:
            parts = split_parts(name)
            hits = {match_name(part, ours)[0] for part in parts} - {None} if len(parts) > 1 else set()
            target = next(iter(hits)) if len(hits) == 1 else None
        if target is not None and str(name) != target:
            merged[str(name)] = target
    return merged


def fuzzy_category(description, keyword_map: dict):
    """(category, keyword) when a word of the description is a typo of a single-word keyword
    ("Resturant" ~ "restaurant"), else (None, None). Short words are never matched loosely."""
    if is_blank(description):
        return None, None
    from budget.categorize import strip_prefix
    words = [w for w in canonical(strip_prefix(description)).split() if len(w) >= MIN_TYPO_LENGTH]
    best = (0.0, None, None)
    for category, keywords in keyword_map["categories"].items():
        for keyword in keywords:
            keyword_c = canonical(keyword)
            if " " in keyword_c or len(keyword_c) < MIN_TYPO_LENGTH:
                continue
            for word in words:
                score = _similar(word, keyword_c)
                if score >= TYPO_CUTOFF and score > best[0]:
                    best = (score, category, keyword)
    return best[1], best[2]
