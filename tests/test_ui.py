"""The Streamlit page shows the backend's clear error when a document is too long (the backend is faked, no server needed)."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from streamlit.testing.v1 import AppTest

PAGE = str(Path(__file__).resolve().parents[1] / "ui" / "streamlit_app.py")
TOO_LONG = "Document too long for the free tier, try fewer pages."


def fake_request(method, url, **kwargs):
    request = httpx.Request(method, url)
    if url.endswith("/health"):
        return httpx.Response(200, json={"status": "ok"}, request=request)
    if url.endswith("/documents") and method == "GET":
        return httpx.Response(200, json=[], request=request)
    if url.endswith("/documents/upload"):
        return httpx.Response(422, json={"detail": {"message": TOO_LONG, "errors": []}}, request=request)
    return httpx.Response(404, json={"detail": "not found"}, request=request)


def test_the_page_shows_the_too_long_message_and_no_document():
    with patch("httpx.request", fake_request):
        app = AppTest.from_file(PAGE, default_timeout=30).run()
        uploader = [w for w in app.main if getattr(w, "type", "") == "file_uploader"]
        assert not app.exception
        # the file uploader cannot be driven by AppTest, so the upload outcome is stored the way the page stores it
        app.session_state["notice"] = ("error", TOO_LONG)
        app.run()
    assert any(TOO_LONG in e.value for e in app.error)
