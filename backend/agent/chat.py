"""One chat turn: store the user's message, run the agent loop, store the reply. No model call happens unless the user sends a message."""

from sqlalchemy.orm import Session

from backend.agent import prompts
from backend.agent.llm import AgentLLM
from backend.agent.loop import run_turn
from backend.agent.schemas import ChatRequest, ChatResponse, ToolTraceEntry
from backend.agent.tools import ToolContext
from backend.config import Settings
from backend.llm.runner import ResilientExtractor
from backend.services import agent_sessions


def handle_chat(db: Session, settings: Settings, extractor: ResilientExtractor, llm: AgentLLM, request: ChatRequest) -> ChatResponse:
    chat = agent_sessions.get_or_create(db, request.session_id, request.document_id)
    agent_sessions.append(chat, "user", request.message)
    db.commit()                                                  # the message is kept even if the model is unavailable

    messages = [{"role": "system", "content": prompts.system_prompt(chat.document_id)},
                *agent_sessions.model_history(chat, settings.agent_history_messages)]
    result = run_turn(llm, ToolContext(db, settings, extractor, chat), messages, settings.agent_max_tool_calls)

    trace = [ToolTraceEntry.model_validate(entry) for entry in result.trace]
    agent_sessions.append(chat, "assistant", result.reply, tool_trace=[t.model_dump(mode="json") for t in trace])
    db.commit()
    return ChatResponse(reply=result.reply, tool_trace=trace, pending_action=chat.pending_action)
