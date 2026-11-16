"""The agent's tools: thin wrappers around backend/services (no business logic here). Arguments are validated with Pydantic before anything runs,
results are compact, and every string taken from a document is wrapped as untrusted data.

There is deliberately NO tool that confirms a document: request_confirmation only writes a pending_action; the user approves it in the UI."""

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from backend.agent import grounding
from backend.agent.untrusted import wrap, wrap_tree
from backend.config import Settings
from backend.llm.runner import ResilientExtractor
from backend.models import ChatSession, Document
from backend.schemas import DocType, DuplicateKind, LineItem, Severity
from backend.services import agent_sessions
from backend.services import documents as docs


class ToolError(Exception):
    """A problem the model can understand and act on; the message is short and goes back to the model."""


@dataclass
class ToolContext:
    db: Session
    settings: Settings
    extractor: ResilientExtractor
    chat: ChatSession


@dataclass
class ToolResult:
    data: dict[str, Any]                       # what the model sees (compact)
    summary: str                               # one line for the "What the agent did" panel
    source_message: Optional[dict[str, Any]] = None


# --- arguments (Pydantic-validated; unknown arguments are refused) ------------------------------------------------------------------
class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentArgs(_Args):
    document_id: int


class FieldUpdates(_Args):
    doc_type: Optional[DocType] = None
    vendor_name: Optional[str] = None
    vendor_tax_id: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = Field(None, description="YYYY-MM-DD")
    due_date: Optional[str] = Field(None, description="YYYY-MM-DD")
    currency: Optional[str] = None
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    total: Optional[float] = None
    line_items: Optional[list[LineItem]] = None


class UpdateFieldsArgs(_Args):
    document_id: int
    fields: FieldUpdates
    source_message: int = Field(description="Number N of the user message [msg N] that gave these values")


class ConfirmationArgs(_Args):
    document_id: int
    save_anyway: bool = False


class SearchArgs(_Args):
    vendor: Optional[str] = None
    date_from: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD")
    date_to: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD")
    status: Optional[Literal["draft", "confirmed"]] = None

    @field_validator("vendor", "date_from", "date_to", "status", mode="before")
    @classmethod
    def _blank_is_none(cls, value):
        return None if isinstance(value, str) and not value.strip() else value


# --- compact views --------------------------------------------------------------------------------------------------------------------
def _issues_view(doc: Document) -> list[dict[str, Any]]:
    return [{"field": i["field"], "severity": i["severity"], "message": wrap(i["message"])} for i in doc.issues or []]


def _duplicate_view(doc: Document) -> Optional[dict[str, Any]]:
    d = doc.duplicate
    return None if not d else {"kind": d["kind"], "document_id": d["document_id"], "reason": wrap(d["reason"])}


def _document_view(doc: Document) -> dict[str, Any]:
    data = {k: v for k, v in (doc.data or {}).items() if v not in (None, [], "")}
    items = data.pop("line_items", [])
    return {"id": doc.id, "status": doc.status, "filename": wrap(doc.filename), "data": wrap_tree(data),
            "line_items": [wrap_tree({k: v for k, v in item.items() if v is not None}) for item in items[:8]], "line_item_count": len(items),
            "issues": _issues_view(doc), "duplicate": _duplicate_view(doc)}


def _own_document(ctx: ToolContext, document_id: int, *, change: bool) -> Document:
    """Loads a document. Tools that change something may only touch the session's own document."""
    if change and ctx.chat.document_id is not None and document_id != ctx.chat.document_id:
        raise ToolError(f"This chat is about document {ctx.chat.document_id}; it cannot change document {document_id}.")
    try:
        return docs.get_or_404(ctx.db, document_id)
    except docs.WorkflowError as error:
        raise ToolError(str(error.detail)) from error


def _workflow(error: docs.WorkflowError) -> ToolError:
    detail = error.detail
    text = detail.get("message", "The request failed.") if isinstance(detail, dict) else str(detail)
    return ToolError(text)


# --- the tools -------------------------------------------------------------------------------------------------------------------------
def get_document(args: DocumentArgs, ctx: ToolContext) -> ToolResult:
    doc = _own_document(ctx, args.document_id, change=False)
    return ToolResult(_document_view(doc), f"document {doc.id} ({doc.status}), {len(doc.issues or [])} issue(s)")


def extract_document(args: DocumentArgs, ctx: ToolContext) -> ToolResult:
    doc = _own_document(ctx, args.document_id, change=True)
    try:
        docs.reextract(ctx.db, ctx.settings, ctx.extractor, doc)
    except docs.WorkflowError as error:
        raise _workflow(error) from error
    return ToolResult(_document_view(doc), f"read again with {doc.provider}; {len(doc.issues or [])} issue(s)")


def validate_document(args: DocumentArgs, ctx: ToolContext) -> ToolResult:
    doc = docs.refresh(ctx.db, ctx.settings, _own_document(ctx, args.document_id, change=False))
    errors = sum(1 for i in doc.issues if i["severity"] == Severity.error.value)
    return ToolResult({"issues": _issues_view(doc)}, f"{len(doc.issues)} issue(s), {errors} error(s)")


def check_duplicates(args: DocumentArgs, ctx: ToolContext) -> ToolResult:
    doc = docs.refresh(ctx.db, ctx.settings, _own_document(ctx, args.document_id, change=False))
    view = _duplicate_view(doc)
    return ToolResult({"duplicate": view}, "no duplicate found" if view is None else f"{view['kind']} duplicate of document {view['document_id']}")


def update_fields(args: UpdateFieldsArgs, ctx: ToolContext) -> ToolResult:
    doc = _own_document(ctx, args.document_id, change=True)
    patch = args.fields.model_dump(mode="json", exclude_unset=True)
    if not patch:
        raise ToolError("No fields were given to update.")
    index = args.source_message
    ok, reason = grounding.check_sources(patch, ctx.chat.messages, index)
    if not ok:
        raise ToolError(reason)
    source = {"index": index, "text": ctx.chat.messages[index]["content"][:120]}
    try:
        docs.apply_patch(ctx.db, ctx.settings, doc, patch)
    except docs.WorkflowError as error:
        raise _workflow(error) from error
    return ToolResult({"updated": sorted(patch), "issues": _issues_view(doc), "duplicate": _duplicate_view(doc)},
                      f"updated {', '.join(sorted(patch))}; {len(doc.issues)} issue(s) left", source_message=source)


def request_confirmation(args: ConfirmationArgs, ctx: ToolContext) -> ToolResult:
    """Only records a request. Nothing is saved until the user clicks Approve in the UI."""
    doc = _own_document(ctx, args.document_id, change=True)
    if doc.status == "confirmed":
        raise ToolError("This document is already saved.")
    duplicate = doc.duplicate
    if duplicate and duplicate["kind"] == DuplicateKind.exact.value:
        raise ToolError(f"This is an exact duplicate of saved document {duplicate['document_id']}, so it can never be saved. Tell the user.")
    errors = [i for i in doc.issues or [] if i["severity"] == Severity.error.value]
    if errors and not args.save_anyway:
        raise ToolError(f"The document still has {len(errors)} error(s). Ask the user to fix them, or whether to save anyway.")
    ctx.chat.pending_action = agent_sessions.make_pending(doc, args.save_anyway)
    return ToolResult({"status": "waiting_for_user_approval", "note": "Tell the user to click Approve & Save. It is NOT saved yet."},
                      "asked the user to approve saving" + (" (save anyway)" if args.save_anyway else ""))


def search_documents(args: SearchArgs, ctx: ToolContext) -> ToolResult:
    rows = docs.search_documents(ctx.db, args.vendor, args.date_from, args.date_to, args.status)
    found = [wrap_tree({"id": d.id, "status": d.status, "vendor": (d.data or {}).get("vendor_name"), "number": (d.data or {}).get("invoice_number"),
                        "date": d.invoice_date, "total": d.total, "currency": (d.data or {}).get("currency")}) for d in rows]
    return ToolResult({"count": len(found), "documents": found}, f"{len(found)} document(s) found")


# --- registry --------------------------------------------------------------------------------------------------------------------------
@dataclass
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    run: Callable[[Any, ToolContext], ToolResult]
    mutating: bool = False


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("get_document", "Show a document's fields, issues and duplicate result.", DocumentArgs, get_document),
    Tool("extract_document", "Read the uploaded file again with the AI (replaces the draft's data).", DocumentArgs, extract_document, mutating=True),
    Tool("validate_document", "Re-run the checks (maths, dates, required fields). Returns the issues.", DocumentArgs, validate_document),
    Tool("check_duplicates", "Check whether the document duplicates a saved one.", DocumentArgs, check_duplicates),
    Tool("update_fields", "Fix fields with values the USER stated or confirmed. Never guess a value.", UpdateFieldsArgs, update_fields, mutating=True),
    Tool("request_confirmation", "Ask the user to approve saving. Does NOT save.", ConfirmationArgs, request_confirmation, mutating=True),
    Tool("search_documents", "Find saved documents by vendor, invoice date range or status.", SearchArgs, search_documents),
]}


def compact_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The model's JSON schema with $refs inlined and titles removed (fewer tokens, and no $defs for the API to trip over)."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return clean(defs[node["$ref"].split("/")[-1]])
            options = node.get("anyOf")
            if options and len(options) == 2 and {"type": "null"} in options:      # Optional[X] -> X (fewer tokens; null is still accepted)
                inner = clean(next(o for o in options if o != {"type": "null"}))
                return {**inner, **{k: v for k, v in clean({k: v for k, v in node.items() if k != "anyOf"}).items()}}
            return {k: clean(v) for k, v in node.items() if k not in ("title", "default", "additionalProperties")}
        if isinstance(node, list):
            return [clean(v) for v in node]
        return node
    return clean(schema)


def tool_specs() -> list[dict[str, Any]]:
    return [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": compact_schema(t.args_model)}} for t in TOOLS.values()]
