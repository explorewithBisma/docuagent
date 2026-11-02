"""
budget/ai_categories.py
------------------------
The optional AI step of the budget builder: suggest a category for descriptions the keyword map could not place.

Privacy rules (the same spirit as the cleaner's "the AI never receives the full file"):
  * only the DISTINCT unmatched description texts are sent - never amounts, dates, payment methods, notes or any other column;
  * plus our own category names, so the answer can only be one of them;
  * every answer is validated locally: a category that is not in our list, or a description we did not send, is dropped;
  * no API key -> the step is skipped without an error.

The provider code (Groq / Anthropic, key handling, friendly errors) is reused from ai_rules.py.
"""

import json
import os

import ai_rules
from ai_rules import AISuggestionError, PROVIDER_LABELS, model_for, select_provider
from quality import is_blank

OTHER = "Other"             # always an allowed answer: "none of your categories / I can't tell"
MAX_CONFIDENCE = "medium"   # every description sent here already failed the keyword map, so the AI is never "high"
MAX_DESCRIPTIONS = 150
MAX_DESCRIPTION_LENGTH = 80
CONFIDENCES = ("high", "medium", "low")

CATEGORY_SCHEMA = {
    "type": "object",
    "properties": {"categories": {"type": "array", "items": {
        "type": "object",
        "properties": {"description": {"type": "string"}, "category": {"type": "string"},
                       "confidence": {"type": "string", "enum": list(CONFIDENCES)}},
        "required": ["description", "category", "confidence"], "additionalProperties": False}}},
    "required": ["categories"], "additionalProperties": False,
}


ai_available = ai_rules.ai_available          # one check for every page (defined in ai_rules.py)


def clean_descriptions(descriptions) -> list:
    """The distinct, trimmed description texts that would be sent (order kept, capped in number and length)."""
    seen, result = set(), []
    for text in descriptions:
        if is_blank(text):
            continue
        text = " ".join(str(text).split())[:MAX_DESCRIPTION_LENGTH]
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            result.append(text)
    return result[:MAX_DESCRIPTIONS]


def allowed_categories(categories) -> list:
    """Our categories plus 'Other' (the answer for 'none of these / I don't know')."""
    names = list(categories)
    return names if any(str(n).casefold() == OTHER.casefold() for n in names) else names + [OTHER]


def build_prompt(descriptions: list, categories: list) -> str:
    """The whole text sent to the AI: instructions, our category names and the description texts. Nothing else."""
    return (
        "You help sort personal spending into budget categories.\n"
        "Below are short descriptions of purchases (shop names, items, sometimes in a mix of languages or with typos), "
        "and the allowed categories. For each description choose the single best category from the list. Never invent "
        "a category that is not in the list.\n"
        "Be honest about how sure you are. Confidence 'high' is only for a description whose own words name the kind "
        "of purchase. A brand or shop name that you do not clearly recognise, or that could belong to more than one "
        "category (for example an app that might be a taxi service or a money-transfer service), must get confidence "
        "'low' - and category 'Other' if you have no good guess. A wrong confident answer is worse than an honest 'low'.\n\n"
        f"Allowed categories: {json.dumps(list(categories), ensure_ascii=False)}\n\n"
        f"Descriptions: {json.dumps(list(descriptions), ensure_ascii=False)}\n\n"
        'Respond with JSON only: {"categories": [{"description": "...", "category": "...", "confidence": "high|medium|low"}]} '
        "with one entry per description, using the description text exactly as given."
    )


def validate_answer(answer, sent: list, categories: list):
    """({description: (category, confidence)}, dropped) from the parsed AI answer. A description we did not send,
    a category outside our list, an empty category or a repeated description is dropped with a reason."""
    sent_by_key = {text.casefold(): text for text in sent}
    allowed = {name.casefold(): name for name in allowed_categories(categories)}
    assigned, dropped = {}, []
    items = answer.get("categories") if isinstance(answer, dict) else None
    if not isinstance(items, list):
        raise AISuggestionError("The AI answer didn't have the expected shape (a 'categories' list).")
    for item in items:
        if not isinstance(item, dict):
            dropped.append({"description": str(item)[:40], "reason": "not an object"})
            continue
        description, category = str(item.get("description", "")).strip(), str(item.get("category", "")).strip()
        confidence = str(item.get("confidence", "")).strip().lower()
        original = sent_by_key.get(" ".join(description.split()).casefold())
        if original is None:
            dropped.append({"description": description[:40], "reason": "not one of the descriptions that were sent"})
        elif not category:
            dropped.append({"description": original, "reason": "the AI was unsure"})
        elif category.casefold() not in allowed:
            dropped.append({"description": original, "reason": f"'{category}' is not one of your categories"})
        elif original in assigned:
            dropped.append({"description": original, "reason": "answered twice"})
        else:
            confidence = confidence if confidence in CONFIDENCES else "low"
            if CONFIDENCES.index(confidence) < CONFIDENCES.index(MAX_CONFIDENCE):
                confidence = MAX_CONFIDENCE
            assigned[original] = (allowed[category.casefold()], confidence)
    return assigned, dropped


def suggest_categories(descriptions, categories: list, provider: str = None) -> dict:
    """Ask the configured provider. Returns {"assignments": {description: (category, confidence)}, "dropped": [...],
    "provider", "model", "sent": [descriptions that were sent]}. Raises AISuggestionError with a readable message."""
    sent = clean_descriptions(descriptions)
    provider = provider or select_provider()
    prompt = build_prompt(sent, allowed_categories(categories))
    text = ai_rules._call_groq(prompt) if provider == "groq" else ai_rules._call_anthropic(prompt, CATEGORY_SCHEMA)
    try:
        answer = json.loads(ai_rules._final_answer_text(text))
    except ValueError as error:
        raise AISuggestionError(f"{PROVIDER_LABELS[provider]} returned an answer that couldn't be read ({error}). Try again.")
    assigned, dropped = validate_answer(answer, sent, categories)
    return {"assignments": assigned, "dropped": dropped, "provider": provider, "model": model_for(provider), "sent": sent}
