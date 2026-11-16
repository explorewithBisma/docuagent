"""SQLAlchemy table for an uploaded document. The extracted data, the issues and the duplicate result are stored as JSON;
the few fields the duplicate check searches on (normalised vendor and invoice number, total, date) are also real columns."""

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)       # draft | confirmed
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(64))
    file_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)        # SHA-256 of the uploaded bytes
    file_path: Mapped[str] = mapped_column(String(512))
    provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)         # which LLM read it

    data: Mapped[dict[str, Any]] = mapped_column(JSON)                                 # Invoice as JSON
    issues: Mapped[list[Any]] = mapped_column(JSON, default=list)
    duplicate: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    # Searchable copies used by the duplicate check
    vendor_key: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    invoice_number_key: Mapped[Optional[str]] = mapped_column(String(128), index=True, nullable=True)
    total: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    invoice_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ChatSession(Base):
    """One agent conversation. `messages` is a list of {role: user|assistant|note, content, at, tool_trace?}; `pending_action` is a save request
    waiting for the user's Approve click (the agent can only create it, never approve it)."""
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    messages: Mapped[list[Any]] = mapped_column(JSON, default=list)
    pending_action: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
