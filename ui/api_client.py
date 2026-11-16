"""How the Streamlit pages talk to the FastAPI backend. No business logic here either."""

import os
from typing import Any

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


def backend_is_up() -> bool:
    try:
        return call_api("GET", "/health").status_code == 200
    except httpx.HTTPError:
        return False
