"""Request and response models of the agent API."""

from typing import Any, Optional

from pydantic import BaseModel, Field

from backend.schemas import DocumentOut


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=2000)
    document_id: Optional[int] = None


class ToolTraceEntry(BaseModel):
    step: int
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    ok: bool
    summary: str = ""
    source_message: Optional[dict[str, Any]] = None      # update_fields: which user message the values came from {index, text}


class PendingAction(BaseModel):
    type: str = "confirm_document"
    document_id: int
    save_anyway: bool = False
    fields_hash: str                                      # hash of the document's fields when approval was requested
    summary: dict[str, Any] = Field(default_factory=dict)
    requested_at: str


class ChatResponse(BaseModel):
    reply: str
    tool_trace: list[ToolTraceEntry]
    pending_action: Optional[PendingAction] = None


class ChatMessageOut(BaseModel):
    index: int
    role: str                                             # user | assistant | note
    content: str
    tool_trace: list[ToolTraceEntry] = Field(default_factory=list)


class SessionOut(BaseModel):
    session_id: str
    document_id: Optional[int] = None
    messages: list[ChatMessageOut]
    pending_action: Optional[PendingAction] = None


class ApproveResponse(BaseModel):
    document: DocumentOut
    message: str


class RejectResponse(BaseModel):
    message: str
