"""update_fields may only use values the USER gave. A value is accepted when it appears in the user message the agent cites (`source_message`),
or when that message is a short "yes" and the value appears in the assistant message just before it (the user confirmed a proposed value).
Everything else is refused, so the model cannot invent or guess a value."""

import re
from typing import Any, Optional

AFFIRMATION = re.compile(r"^\s*(yes|yep|yeah|yup|y|ok|okay|sure|correct|right|confirmed|that is right|that's right|go ahead|please do|do it)\b[\s.,!]*", re.I)
NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def _numbers_in(text: str) -> list[float]:
    found = []
    for match in NUMBER.findall(text):
        try:
            found.append(float(match.replace(",", "")))
        except ValueError:
            continue
    return found


def _leaves(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from _leaves(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _leaves(item)
    elif value is not None:
        yield value


def _leaf_in_text(leaf: Any, text: str) -> bool:
    haystack = _norm(text)
    if isinstance(leaf, bool):
        return False
    if isinstance(leaf, (int, float)):
        return any(abs(number - float(leaf)) < 1e-9 for number in _numbers_in(text)) or _norm(f"{leaf:g}") in haystack
    needle = _norm(str(leaf))
    return bool(needle) and needle in haystack


def values_found_in(value: Any, text: str) -> Optional[str]:
    """None when every value in `value` appears in `text`; otherwise the first one that does not (as text)."""
    for leaf in _leaves(value):
        if not _leaf_in_text(leaf, text):
            return str(leaf)
    return None


def is_affirmation(text: str) -> bool:
    return len(text) <= 60 and bool(AFFIRMATION.match(text))


def check_sources(fields: dict[str, Any], messages: list[dict], source_index: int) -> tuple[bool, str]:
    """(ok, reason). `messages` is the stored chat history; `source_index` is the position of the cited message in it."""
    if not isinstance(source_index, int) or not 0 <= source_index < len(messages):
        return False, f"source_message {source_index} does not exist."
    source = messages[source_index]
    if source.get("role") != "user":
        return False, f"message {source_index} is not a user message. Cite the user message that gave the value."
    texts = [source["content"]]
    if is_affirmation(source["content"]) and source_index > 0 and messages[source_index - 1].get("role") == "assistant":
        texts.append(messages[source_index - 1]["content"])         # the user said yes to a value the assistant proposed
    for name, value in fields.items():
        if value is None:                                            # clearing a field still needs a user message to cite
            continue
        if not any(values_found_in(value, text) is None for text in texts):
            return False, (f"Refused: the value for {name} was not stated or confirmed by the user in message {source_index}. "
                           "Ask the user for it; never guess or invent a value.")
    return True, ""
