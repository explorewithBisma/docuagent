"""The HTTP endpoints. Thin: they read the request, call backend.services.documents and shape the response."""

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database import get_db
from backend.llm.runner import ResilientExtractor, build_extractor
from backend.schemas import ConfirmRequest, DocumentOut, DocumentStatus, DocumentSummary, UploadResponse
from backend.services import documents as service

router = APIRouter(prefix="/documents", tags=["documents"])


def get_extractor(settings: Settings = Depends(get_settings)) -> ResilientExtractor:
    """The LLM runner (order from LLM_PROVIDER_ORDER, default Groq then Gemini). Tests replace this dependency with a fake."""
    return build_extractor(settings)


def _raise(error: service.WorkflowError):
    raise HTTPException(status_code=error.status_code, detail=error.detail)


@router.post("/upload", response_model=UploadResponse)
async def upload_document(file: UploadFile = File(...), session: Session = Depends(get_db), settings: Settings = Depends(get_settings),
                          extractor: ResilientExtractor = Depends(get_extractor)) -> UploadResponse:
    """Upload a PDF, PNG or JPG (max 10 MB, max 5 pages). Returns a draft with the extracted data, issues and any duplicate."""
    data = await file.read(settings.max_upload_bytes + 1)          # one byte more than the limit is enough to know it is too big
    try:
        result = service.create_from_upload(session, settings, extractor, file.filename or "upload", data)
    except service.WorkflowError as error:
        _raise(error)
    return UploadResponse(document=service.to_out(result.document), file_duplicate=result.file_duplicate, message=result.message)


@router.get("", response_model=list[DocumentSummary])
def list_documents(status: Optional[DocumentStatus] = Query(None), session: Session = Depends(get_db)) -> list[DocumentSummary]:
    return [service.to_summary(d) for d in service.list_documents(session, status.value if status else None)]


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(document_id: int, session: Session = Depends(get_db)) -> DocumentOut:
    try:
        return service.to_out(service.get_or_404(session, document_id))
    except service.WorkflowError as error:
        _raise(error)


@router.get("/{document_id}/file")
def get_file(document_id: int, session: Session = Depends(get_db)) -> FileResponse:
    """The original uploaded file (so the user can compare it with the extracted fields)."""
    try:
        doc = service.get_or_404(session, document_id)
    except service.WorkflowError as error:
        _raise(error)
    return FileResponse(doc.file_path, media_type=doc.content_type, filename=doc.filename)


@router.patch("/{document_id}", response_model=DocumentOut)
def update_document(document_id: int, patch: dict[str, Any] = Body(...), session: Session = Depends(get_db),
                    settings: Settings = Depends(get_settings)) -> DocumentOut:
    """The user fixes fields: send only the fields to change. Validation and the duplicate check run again."""
    try:
        doc = service.get_or_404(session, document_id)
        return service.to_out(service.apply_patch(session, settings, doc, patch))
    except service.WorkflowError as error:
        _raise(error)


@router.post("/{document_id}/confirm", response_model=DocumentOut)
def confirm_document(document_id: int, body: ConfirmRequest = ConfirmRequest(), session: Session = Depends(get_db),
                     settings: Settings = Depends(get_settings)) -> DocumentOut:
    """Save the document (draft -> confirmed). Blocked for an exact duplicate; blocked by errors unless override_issues is true."""
    try:
        doc = service.get_or_404(session, document_id)
        return service.to_out(service.confirm(session, settings, doc, body.override_issues))
    except service.WorkflowError as error:
        _raise(error)
