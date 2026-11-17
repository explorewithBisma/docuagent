"""The Agent page, with the backend faked (no server, no model): it shows the chat, the tool trace and the approval box, and Approve / Reject
call the right endpoints. Nothing is sent to the model unless a message is typed."""

from pathlib import Path

import sys

import httpx
from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ui"))
from api_client import session_for_document  # noqa: E402

PAGE = str(Path(__file__).resolve().parents[1] / "ui" / "pages" / "2_Agent.py")

PENDING = {"type": "confirm_document", "document_id": 7, "save_anyway": False, "fields_hash": "x", "requested_at": "now",
           "summary": {"vendor_name": "Acme", "invoice_number": "INV-1", "total": 10.0, "currency": "USD", "errors": 0, "warnings": 0}}
TRACE = [{"step": 1, "tool": "update_fields", "args": {}, "ok": True, "summary": "updated invoice_number; 0 issue(s) left",
          "source_message": {"index": 0, "text": "invoice number is INV-2231"}}]


class FakeBackend:
    def __init__(self, pending=True, approve_status=200):
        self.calls, self.pending, self.approve_status = [], pending, approve_status
        self.issues = []                                       # the document's issues; the agent's reply (validate_document) adds one

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url.split("8000")[-1], kwargs.get("json")))
        request = httpx.Request(method, url)
        path = url.split("8000")[-1]
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"}, request=request)
        if path.startswith("/documents/"):
            return httpx.Response(200, json={"id": 7, "status": "draft", "issues": self.issues, "data": {"vendor_name": "Acme", "invoice_number": "INV-1", "total": 10.0, "currency": "USD"}}, request=request)
        if path.endswith("/approve"):
            body = {"document": {}, "message": "Document 7 saved."} if self.approve_status == 200 else {"detail": {"message": "Document 7 changed after the save was requested. Approval must be requested again.", "stale": True}}
            return httpx.Response(self.approve_status, json=body, request=request)
        if path.endswith("/reject"):
            return httpx.Response(200, json={"message": "Save request for document 7 rejected. Nothing was saved."}, request=request)
        if path == "/agent/chat":
            self.issues = [{"code": "looks_like_instruction", "field": "vendor_name", "severity": "warning", "message": "x"}]
            return httpx.Response(200, json={"reply": "ok", "tool_trace": [], "pending_action": None}, request=request)
        if path.startswith("/agent/sessions/"):
            return httpx.Response(200, json={"session_id": "s", "document_id": 7, "pending_action": PENDING if self.pending else None, "messages": [
                {"index": 0, "role": "user", "content": "invoice number is INV-2231", "tool_trace": []},
                {"index": 1, "role": "assistant", "content": "Updated. Shall I ask you to approve?", "tool_trace": TRACE},
                {"index": 2, "role": "note", "content": "The user REJECTED the save request for document 7. It was not saved.", "tool_trace": []}]}, request=request)
        return httpx.Response(404, json={"detail": "not found"}, request=request)


def run(backend, monkeypatch):
    monkeypatch.setattr(httpx, "request", backend)
    app = AppTest.from_file(PAGE, default_timeout=30)
    app.session_state["agent_doc_id"] = 7
    return app.run()


def buttons(app):
    return {b.label: b for b in app.button}


def test_chat_trace_and_approval_box_are_shown(monkeypatch):
    app = run(FakeBackend(), monkeypatch)
    assert not app.exception
    assert any("Updated. Shall I ask you" in m.value for m in app.markdown)
    assert any("from your message #0" in m.value and "update_fields" in m.value for m in app.markdown)          # the tool trace with its source message
    assert any("REJECTED" in c.value for c in app.caption)                                                       # notes are shown
    assert any("asks you to save document #7" in w.value for w in app.warning)
    assert {"Approve & Save", "Reject"} <= set(buttons(app))


def test_no_approval_box_without_a_pending_action(monkeypatch):
    app = run(FakeBackend(pending=False), monkeypatch)
    assert not any("asks you to save" in w.value for w in app.warning) and "Approve & Save" not in buttons(app)


def test_approve_calls_the_approve_endpoint_and_nothing_else_saves(monkeypatch):
    backend = FakeBackend()
    app = run(backend, monkeypatch)
    buttons(app)["Approve & Save"].click().run()
    posts = [(m, p) for m, p, _ in backend.calls if m == "POST"]
    assert posts == [("POST", "/agent/sessions/" + app.session_state["agent_session_id"] + "/approve")]
    assert not any(p == "/agent/chat" for _, p, _ in backend.calls)                       # clicking costs no model tokens


def test_a_stale_approval_shows_the_refusal(monkeypatch):
    app = run(FakeBackend(approve_status=409), monkeypatch)
    buttons(app)["Approve & Save"].click().run()
    assert any("must be requested again" in e.value for e in app.error)


def test_reject_calls_the_reject_endpoint(monkeypatch):
    backend = FakeBackend()
    app = run(backend, monkeypatch)
    buttons(app)["Reject"].click().run()
    assert [p.split("/")[-1] for m, p, _ in backend.calls if m == "POST"] == ["reject"]


def banner(app):
    return next(i.value for i in app.info if i.value.startswith("**Document #7**"))


def test_the_banner_issue_count_is_refreshed_after_the_agents_reply(monkeypatch):
    app = run(FakeBackend(pending=False), monkeypatch)
    assert "0 issue(s)" in banner(app)
    app.chat_input[0].set_value("review it").run()                                         # the agent's validate_document finds 1 issue
    assert "1 issue(s)" in banner(app) and "0 issue(s)" not in banner(app)


def test_the_review_button_also_refreshes_the_banner(monkeypatch):
    app = run(FakeBackend(pending=False), monkeypatch)
    buttons(app)["Ask the agent to review it"].click().run()
    assert "1 issue(s)" in banner(app)


def test_a_new_document_starts_a_new_chat_session_and_the_same_document_keeps_it():
    fresh = iter(["new-1", "new-2"])
    make = lambda: next(fresh)
    assert session_for_document("old", 7, 8, make) == "new-1"            # another document: new session, so no history is shared
    assert session_for_document("old", None, 8, make) == "new-2"         # the first document of the page
    assert session_for_document("old", 7, 7, make) == "old"              # the same file uploaded again: same document, same chat


def test_typing_a_message_sends_it_to_the_agent_and_loading_the_page_does_not(monkeypatch):
    backend = FakeBackend(pending=False)
    app = run(backend, monkeypatch)
    assert not any(p == "/agent/chat" for _, p, _ in backend.calls)                       # opening the page spends nothing
    app.chat_input[0].set_value("invoice number is INV-2231").run()
    sent = [body for _, p, body in backend.calls if p == "/agent/chat"]
    assert len(sent) == 1 and sent[0]["message"] == "invoice number is INV-2231" and sent[0]["document_id"] == 7
