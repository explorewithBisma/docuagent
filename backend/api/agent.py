"""Agent endpoints. Thin: they call backend.agent.chat and backend.services.agent_sessions.

The agent can only REQUEST a save. The document is confirmed by POST /agent/sessions/{id}/approve, which the UI calls when the user clicks
"Approve & Save": it checks that the fields did not change since the request, then runs the same confirm step as POST /documents/{id}/confirm."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.agent.chat import handle_chat
from backend.agent.llm import AgentLLM, build_agent_llm
from backend.agent.schemas import ApproveResponse, ChatMessageOut, ChatRequest, ChatResponse, PendingAction, RejectResponse, SessionOut
from backend.api.documents import get_extractor
from backend.config import Settings, get_settings
from backend.database import get_db
from backend.llm.base import LLMError, LLMNotConfigured
from backend.llm.runner import ResilientExtractor
from backend.services import agent_sessions
from backend.services import documents as docs

router = APIRouter(prefix="/agent", tags=["agent"])


def get_agent_llm(settings: Settings = Depends(get_settings)) -> AgentLLM:
    """The agent's model. Tests replace this dependency with a scripted fake."""
    return build_agent_llm(settings)


def _raise(error: docs.WorkflowError):
    raise HTTPException(status_code=error.status_code, detail=error.detail)


def _session_out(chat) -> SessionOut:
    return SessionOut(session_id=chat.id, document_id=chat.document_id, pending_action=PendingAction.model_validate(chat.pending_action) if chat.pending_action else None,
                      messages=[ChatMessageOut(index=i, role=m["role"], content=m["content"], tool_trace=m.get("tool_trace", [])) for i, m in enumerate(chat.messages)])


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
         extractor: ResilientExtractor = Depends(get_extractor), llm: AgentLLM = Depends(get_agent_llm)) -> ChatResponse:
    try:
        return handle_chat(db, settings, extractor, llm, request)
    except LLMNotConfigured as error:
        raise HTTPException(503, {"message": str(error)})
    except LLMError as error:
        raise HTTPException(503, {"message": "The agent's AI model is busy or unavailable right now. Please try again in a minute.", "errors": [str(error)]})


@router.get("/sessions/{session_id}", response_model=SessionOut)
def get_session(session_id: str, db: Session = Depends(get_db)) -> SessionOut:
    try:
        return _session_out(agent_sessions.get_or_404(db, session_id))
    except docs.WorkflowError as error:
        _raise(error)


@router.post("/sessions/{session_id}/approve", response_model=ApproveResponse)
def approve(session_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> ApproveResponse:
    """The user clicked Approve & Save."""
    try:
        chat = agent_sessions.get_or_404(db, session_id)
        doc = agent_sessions.approve(db, settings, chat)
        return ApproveResponse(document=docs.to_out(doc), message=f"Document {doc.id} saved.")
    except docs.WorkflowError as error:
        _raise(error)


@router.post("/sessions/{session_id}/reject", response_model=RejectResponse)
def reject(session_id: str, db: Session = Depends(get_db)) -> RejectResponse:
    """The user clicked Reject."""
    try:
        return RejectResponse(message=agent_sessions.reject(db, agent_sessions.get_or_404(db, session_id)))
    except docs.WorkflowError as error:
        _raise(error)
