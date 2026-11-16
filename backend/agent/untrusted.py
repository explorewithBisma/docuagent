"""Text read from a document is DATA, never instructions. Every extracted string that goes back to the model is wrapped in <data>...</data>
and cleaned so it cannot close the wrapper early or fake a new message (angle brackets and line breaks are removed, length is capped)."""

from typing import Any

MAX_VALUE_CHARS = 120


def wrap(value: Any) -> str:
    text = " ".join(str(value).split()).replace("<", "‹").replace(">", "›")
    if len(text) > MAX_VALUE_CHARS:
        text = text[:MAX_VALUE_CHARS - 1] + "…"
    return f"<data>{text}</data>"


def wrap_tree(value: Any) -> Any:
    """Wraps every string inside a dict / list; numbers, booleans and None are left alone."""
    if isinstance(value, str):
        return wrap(value)
    if isinstance(value, dict):
        return {key: wrap_tree(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [wrap_tree(item) for item in value]
    return value
