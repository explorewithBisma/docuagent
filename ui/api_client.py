"""How the Streamlit pages talk to the FastAPI backend. No business logic here either."""

import os
from typing import Any, Callable, Optional

import httpx

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")


def call_api(method: str, path: str, **kwargs: Any) -> httpx.Response:
    return httpx.request(method, f"{API_URL}{path}", timeout=180, **kwargs)


def error_text(response: httpx.Response) -> str:
    """A readable message from an error response (the backend sends a string, a {message, errors} object or a list of validation errors)."""
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        return response.text or f"HTTP {response.status_code}"
    if isinstance(detail, dict):
        extra = detail.get("errors") or [i["message"] for i in detail.get("issues", [])]
        return detail.get("message", "Something went wrong.") + ("\n\n" + "\n".join(f"- {line}" for line in extra) if extra else "")
    if isinstance(detail, list):
        return "; ".join(f"{'.'.join(map(str, e.get('loc', [])))}: {e.get('msg')}" for e in detail)
    return str(detail)


def upload_notice(body: dict[str, Any]) -> tuple[str, str]:
    """(level, text) shown after an upload. `body` is the /documents/upload response; `file_duplicate` tells a new draft from a same-file hit."""
    number = body["document"]["id"]
    if body.get("file_duplicate"):
        return "info", f"You already uploaded this file. Showing document #{number}."
    text = f"Draft created as document #{number}. Nothing is saved until you click Confirm."
    if body.get("message"):                                           # e.g. a duplicate or validation warning from the backend
        return "warning", f"{text}\n\n{body['message']}"
    return "success", text


def session_for_document(current_session_id: str, current_doc_id: Optional[int], new_doc_id: int, new_session_id: Callable[[], str]) -> str:
    """The chat session to use after an upload: a different document gets a NEW session, so one document's chat is never sent as history about another."""
    return current_session_id if new_doc_id == current_doc_id else new_session_id()


def backend_is_up() -> bool:
    try:
        return call_api("GET", "/health").status_code == 200
    except httpx.HTTPError:
        return False
