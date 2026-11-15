"""The document workflow, kept out of the API layer so it can be tested without HTTP:

  upload  -> check type / size / pages -> same file before? (return it, no LLM call) -> LLM extraction -> validation -> duplicate check -> draft
  patch   -> the user fixes fields -> validation and duplicate check run again
  confirm -> blocked by an exact duplicate (always) or by error-level issues (unless overridden) -> status "confirmed"
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.llm.runner import ExtractionFailed, ResilientExtractor
from backend.models import Document, utcnow
from backend.schemas import (DocumentOut, DocumentStatus, DocumentSummary, DuplicateKind, DuplicateMatch, Invoice, Issue)
from backend.services.duplicates import find_duplicate
from backend.services.files import SourceFile, UnreadableFile, count_pages, detect_file_type, sha256_hex
from backend.services.normalize import normalize_invoice_number, normalize_vendor
from backend.services.validation import has_errors, validate_invoice


class WorkflowError(Exception):
    """A problem the user can act on. `status_code` is the HTTP status the API answers with; `detail` is the message (or a dict)."""

    def __init__(self, status_code: int, detail: Any):
        super().__init__(str(detail))
        self.status_code, self.detail = status_code, detail


@dataclass
class UploadResult:
    document: Document
    file_duplicate: bool
    message: str


# --- conversions ---------------------------------------------------------------------------------------------------------
def to_out(doc: Document) -> DocumentOut:
    return DocumentOut(id=doc.id, status=DocumentStatus(doc.status), filename=doc.filename, content_type=doc.content_type, file_hash=doc.file_hash,
                       provider=doc.provider, data=Invoice.model_validate(doc.data), issues=[Issue.model_validate(i) for i in doc.issues],
                       duplicate=DuplicateMatch.model_validate(doc.duplicate) if doc.duplicate else None,
                       created_at=doc.created_at, confirmed_at=doc.confirmed_at)


def to_summary(doc: Document) -> DocumentSummary:
    data = doc.data or {}
    return DocumentSummary(id=doc.id, status=DocumentStatus(doc.status), filename=doc.filename, vendor_name=data.get("vendor_name"),
                           invoice_number=data.get("invoice_number"), invoice_date=data.get("invoice_date"), currency=data.get("currency"),
                           total=data.get("total"), issue_count=len(doc.issues or []), created_at=doc.created_at)


def _analyse(session: Session, settings: Settings, doc: Document, invoice: Invoice) -> None:
    """(Re)compute everything derived from the data: issues, duplicate result and the searchable columns."""
    doc.data = invoice.model_dump(mode="json")
    doc.issues = [issue.model_dump(mode="json") for issue in validate_invoice(invoice, settings.amount_tolerance)]
    match = find_duplicate(session, invoice, settings, exclude_id=doc.id)
    doc.duplicate = match.model_dump(mode="json") if match else None
    doc.vendor_key = normalize_vendor(invoice.vendor_name)
    doc.invoice_number_key = normalize_invoice_number(invoice.invoice_number)
    doc.total = invoice.total
    doc.invoice_date = invoice.invoice_date


# --- upload ------------------------------------------------------------------------------------------------------------------
def create_from_upload(session: Session, settings: Settings, extractor: ResilientExtractor, filename: str, data: bytes) -> UploadResult:
    if not data:
        raise WorkflowError(400, "The file is empty.")
    if len(data) > settings.max_upload_bytes:
        raise WorkflowError(413, f"The file is larger than {settings.max_upload_mb} MB.")
    file_type = detect_file_type(data)              # by the first bytes: a renamed .txt or .exe is refused whatever it is called
    if file_type is None:
        raise WorkflowError(415, "Only PDF, PNG and JPG files are accepted (the file content is not one of them).")

    file_hash = sha256_hex(data)
    existing = session.scalar(select(Document).where(Document.file_hash == file_hash))
    if existing is not None:                         # the same file again: no LLM call, no new record
        return UploadResult(existing, True, f"This exact file was already uploaded (document #{existing.id}). Showing that record.")

    try:
        pages = count_pages(data, file_type)
    except UnreadableFile as error:
        raise WorkflowError(422, str(error))
    if pages > settings.max_pages:
        raise WorkflowError(422, f"The PDF has {pages} pages; the limit is {settings.max_pages}.")

    try:
        result = extractor.extract(SourceFile(data, file_type.mime, pages))
    except ExtractionFailed as failure:
        # nothing is saved here: no record and no file. A cut-off answer is a 422 (the document is the problem, not the service).
        status = 422 if failure.truncated else 502 if failure.bad_output else 503
        raise WorkflowError(status, {"message": failure.message, "errors": [] if failure.truncated else failure.errors})

    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    path = Path(settings.upload_dir) / f"{file_hash}.{file_type.extension}"
    path.write_bytes(data)
    doc = Document(status="draft", filename=Path(filename or "upload").name[:255], content_type=file_type.mime, file_hash=file_hash, file_path=str(path),
                   provider=result.provider, data={}, issues=[])
    session.add(doc)
    session.flush()                                  # gives the document its id (the duplicate check excludes it)
    _analyse(session, settings, doc, result.invoice)
    session.commit()
    message = ""
    if doc.duplicate:
        message = ("Duplicate: " if doc.duplicate["kind"] == "exact" else "Possible duplicate: ") + doc.duplicate["reason"]
    return UploadResult(doc, False, message)


# --- edit and confirm ------------------------------------------------------------------------------------------------------------
def get_or_404(session: Session, document_id: int) -> Document:
    doc = session.get(Document, document_id)
    if doc is None:
        raise WorkflowError(404, f"Document {document_id} was not found.")
    return doc


def apply_patch(session: Session, settings: Settings, doc: Document, patch: dict[str, Any]) -> Document:
    """The user fixes fields. Only the fields in `patch` change (null clears one); a fixed field counts as fully confident."""
    if doc.status == "confirmed":
        raise WorkflowError(409, "This document is already confirmed and can no longer be edited.")
    allowed = set(Invoice.model_fields)
    unknown = sorted(set(patch) - allowed)
    if unknown:
        raise WorkflowError(422, f"Unknown field(s): {', '.join(unknown)}.")
    merged = {**Invoice.model_validate(doc.data).model_dump(), **patch}
    if "low_confidence_fields" not in patch:         # a field the user has just fixed is no longer in doubt
        merged["low_confidence_fields"] = [f for f in merged["low_confidence_fields"] if f not in patch]
    try:
        invoice = Invoice.model_validate(merged)
    except ValidationError as error:
        raise WorkflowError(422, "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()))
    _analyse(session, settings, doc, invoice)
    session.commit()
    return doc


def confirm(session: Session, settings: Settings, doc: Document, override_issues: bool = False) -> Document:
    if doc.status == "confirmed":
        raise WorkflowError(409, "This document is already confirmed.")
    invoice = Invoice.model_validate(doc.data)
    _analyse(session, settings, doc, invoice)         # the saved records may have changed since the upload
    issues = [Issue.model_validate(i) for i in doc.issues]
    duplicate = DuplicateMatch.model_validate(doc.duplicate) if doc.duplicate else None
    if duplicate and duplicate.kind == DuplicateKind.exact:
        session.commit()
        raise WorkflowError(409, {"message": "This is a duplicate of a saved document, so it cannot be saved again.", "duplicate": duplicate.model_dump(mode="json")})
    if has_errors(issues) and not override_issues:
        session.commit()
        raise WorkflowError(422, {"message": "The document still has errors. Fix them, or confirm anyway.", "issues": [i.model_dump(mode="json") for i in issues]})
    doc.status, doc.confirmed_at = "confirmed", utcnow()
    session.commit()
    return doc


def list_documents(session: Session, status: Optional[str] = None) -> list[Document]:
    query = select(Document).order_by(Document.id.desc())
    if status:
        query = query.where(Document.status == status)
    return list(session.scalars(query))
