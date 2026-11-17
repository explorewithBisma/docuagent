"""Chat sessions and the approval step. The agent can only REQUEST a save (it writes a pending_action); the document is confirmed here,
and only when the user clicks Approve. Approval is refused if the document's fields changed after the request (stale approval)."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from backend.config import Settings
from backend.models import ChatSession, Document
from backend.schemas import Invoice, Severity
from backend.services import documents as docs


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_or_create(db: Session, session_id: str, document_id: Optional[int] = None) -> ChatSession:
    chat = db.get(ChatSession, session_id)
    if chat is None:
        chat = ChatSession(id=session_id, document_id=document_id, messages=[], pending_action=None)
        db.add(chat)
    elif document_id is not None and document_id != chat.document_id:      # the user switched to another document
        if chat.document_id is not None:                                    # (a chat that had no document yet has nothing to mix up)
            chat.messages = []                                              # fresh history: one document's chat is never sent to the model as history about another
        chat.document_id = document_id
        chat.pending_action = None
    db.flush()
    return chat


def get_or_404(db: Session, session_id: str) -> ChatSession:
    chat = db.get(ChatSession, session_id)
    if chat is None:
        raise docs.WorkflowError(404, f"Chat session '{session_id}' was not found.")
    return chat


def append(chat: ChatSession, role: str, content: str, **extra: Any) -> int:
    """Adds a message and returns its index. The list is replaced (not mutated) so SQLAlchemy sees the change."""
    chat.messages = [*chat.messages, {"role": role, "content": content, "at": _now(), **extra}]
    return len(chat.messages) - 1


def fields_hash(doc: Document) -> str:
    """A fingerprint of the document's fields (not of the status or the issues)."""
    data = Invoice.model_validate(doc.data).model_dump(mode="json")
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def make_pending(doc: Document, save_anyway: bool) -> dict[str, Any]:
    data = doc.data or {}
    issues = doc.issues or []
    return {"type": "confirm_document", "document_id": doc.id, "save_anyway": save_anyway, "fields_hash": fields_hash(doc), "requested_at": _now(),
            "summary": {"vendor_name": data.get("vendor_name"), "invoice_number": data.get("invoice_number"), "total": data.get("total"),
                        "currency": data.get("currency"), "errors": sum(1 for i in issues if i["severity"] == Severity.error.value),
                        "warnings": sum(1 for i in issues if i["severity"] == Severity.warning.value)}}


def _clear(db: Session, chat: ChatSession, note: str) -> None:
    chat.pending_action = None
    append(chat, "note", note)
    db.commit()


def approve(db: Session, settings: Settings, chat: ChatSession) -> Document:
    """The user clicked Approve & Save. Re-checks the fields hash, then runs the normal confirm step (same code as POST /documents/{id}/confirm)."""
    pending = chat.pending_action
    if not pending:
        raise docs.WorkflowError(409, "Nothing is waiting for approval.")
    number = pending["document_id"]
    try:
        doc = docs.get_or_404(db, number)
    except docs.WorkflowError:
        _clear(db, chat, f"Approval refused: document {number} no longer exists.")
        raise
    if doc.status == "confirmed":
        _clear(db, chat, f"Approval not needed: document {number} is already saved.")
        raise docs.WorkflowError(409, f"Document {number} is already saved.")
    if fields_hash(doc) != pending["fields_hash"]:
        _clear(db, chat, f"Approval refused: document {number} changed after the save request. Approval must be requested again.")
        raise docs.WorkflowError(409, {"message": f"Document {number} changed after the save was requested, so the approval was refused. "
                                                  "Approval must be requested again: ask the agent to request it.", "stale": True})
    try:
        docs.confirm(db, settings, doc, override_issues=bool(pending.get("save_anyway")))
    except docs.WorkflowError as error:
        detail = error.detail.get("message") if isinstance(error.detail, dict) else str(error.detail)
        _clear(db, chat, f"Approval failed: {detail}")
        raise
    chat.pending_action = None
    append(chat, "note", f"The user approved the request. Document {number} is now saved.")
    db.commit()
    return doc


def reject(db: Session, chat: ChatSession) -> str:
    pending = chat.pending_action
    if not pending:
        raise docs.WorkflowError(409, "Nothing is waiting for approval.")
    number = pending["document_id"]
    _clear(db, chat, f"The user REJECTED the save request for document {number}. It was not saved.")
    return f"Save request for document {number} rejected. Nothing was saved."


def model_history(chat: ChatSession, limit: int) -> list[dict[str, str]]:
    """The last `limit` messages in the model's format. User messages carry their number so the agent can cite them; notes are marked as app text."""
    start = max(0, len(chat.messages) - limit)
    out: list[dict[str, str]] = []
    for index in range(start, len(chat.messages)):
        message = chat.messages[index]
        if message["role"] == "user":
            out.append({"role": "user", "content": f"[msg {index}] {message['content']}"})
        elif message["role"] == "assistant":
            out.append({"role": "assistant", "content": message["content"]})
        else:
            out.append({"role": "user", "content": f"[app note, not from the user] {message['content']}"})
    return out
