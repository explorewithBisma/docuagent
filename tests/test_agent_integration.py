"""Real Groq agent model (AGENT_MODEL). Skipped without GROQ_API_KEY. Run with:  pytest -m integration tests/test_agent_integration.py -s
It spends free-tier tokens, so it is deselected by default."""

import time

import pytest

from backend.agent.chat import handle_chat
from backend.agent.llm import build_agent_llm
from backend.agent.schemas import ChatRequest
from backend.config import Settings
from backend.llm.runner import ResilientExtractor
from backend.models import Document
from backend.services import documents as docs

REAL = Settings()                                    # reads .env and the environment (AGENT_MODEL can be overridden there)

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not REAL.groq_api_key, reason="GROQ_API_KEY is not set")]


def test_the_real_agent_model_calls_tools_and_updates_a_field_from_the_users_answer(session, settings, sample_files, fake_llm):
    settings = settings.model_copy(update={"groq_api_key": REAL.groq_api_key, "agent_model": REAL.agent_model, "agent_reasoning_effort": REAL.agent_reasoning_effort})
    document = docs.create_from_upload(session, settings, ResilientExtractor([fake_llm], sleep=lambda s: None), "missing-number.pdf",
                                       sample_files["4_missing_invoice_number"]).document
    llm = build_agent_llm(settings)
    totals = {"prompt": 0, "completion": 0, "calls": 0}
    original = llm._once

    def counting(messages, tools):
        reply = original(messages, tools)
        totals["calls"] += 1
        totals["prompt"] += llm.last_usage["prompt_tokens"]
        totals["completion"] += llm.last_usage["completion_tokens"]
        return reply
    llm._once = counting

    first = handle_chat(session, settings, ResilientExtractor([fake_llm]), llm, ChatRequest(session_id="it-1", document_id=document.id, message="Please check this document and tell me what is wrong."))
    print(f"\nmodel={settings.agent_model}\nturn 1 tools={[t.tool for t in first.tool_trace]}\nreply={first.reply}")
    assert first.tool_trace and first.tool_trace[0].tool in ("get_document", "validate_document")
    assert first.pending_action is None and session.get(Document, document.id).status == "draft"

    time.sleep(20)                                    # stay under the free tier's per-minute token limits
    second = handle_chat(session, settings, ResilientExtractor([fake_llm]), llm, ChatRequest(session_id="it-1", message="The invoice number is INV-2231"))
    print(f"turn 2 tools={[(t.tool, t.ok) for t in second.tool_trace]}\nreply={second.reply}\ntokens={totals}")
    assert any(t.tool == "update_fields" and t.ok for t in second.tool_trace)
    session.refresh(document)
    assert document.data["invoice_number"] == "INV-2231" and document.status == "draft"
