"""DocuAgent - Agent page: upload a document, then chat with the agent about it.

The agent can only REQUEST a save. When it does, a yellow box appears here and the document is saved only if YOU click "Approve & Save".
No model tokens are spent unless you send a message (or click the review button). This page only calls the API; there is no business logic here.
"""

import sys
import uuid
from pathlib import Path

import httpx
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))          # ui/ (for api_client)
from api_client import API_URL, backend_is_up, call_api, error_text, session_for_document  # noqa: E402

REVIEW_PROMPT = "Please review this document and tell me what is missing or wrong."

st.set_page_config(page_title="DocuAgent - Agent", page_icon="🤖", layout="wide")
st.title("🤖 DocuAgent - Agent")
st.caption("Upload an invoice or receipt and chat with the agent. It checks the document, asks you for anything missing, and asks for your approval before saving.")

if not backend_is_up():
    st.error(f"The backend is not running at {API_URL}. Start it with:  `uvicorn backend.main:app --reload --timeout-graceful-shutdown 2`")
    st.stop()

st.session_state.setdefault("agent_session_id", uuid.uuid4().hex)
st.session_state.setdefault("agent_doc_id", None)
session_id = st.session_state.agent_session_id


def notify(kind: str, text: str) -> None:
    st.session_state.agent_notice = (kind, text)


def send(message: str) -> None:
    body = {"session_id": session_id, "message": message}
    if st.session_state.agent_doc_id is not None:
        body["document_id"] = st.session_state.agent_doc_id
    with st.spinner("The agent is working ..."):
        try:
            response = call_api("POST", "/agent/chat", json=body)
        except httpx.HTTPError as error:
            notify("error", f"Could not reach the backend: {error}")
            return
    if response.status_code != 200:
        notify("error", error_text(response))


def describe_step(step: dict) -> str:
    mark = "✅" if step["ok"] else "❌"
    line = f"{mark} **{step['step']}. {step['tool']}** - {step['summary']}"
    source = step.get("source_message")
    if source:
        line += f"  \n&nbsp;&nbsp;&nbsp;&nbsp;↳ from your message #{source['index']}: \"{source['text']}\""
    return line


# --- upload -----------------------------------------------------------------------------------------------------------------------------
with st.expander("1. Upload a document", expanded=st.session_state.agent_doc_id is None):
    uploaded = st.file_uploader("Invoice or receipt (PDF, PNG or JPG, up to 10 MB)", type=["pdf", "png", "jpg", "jpeg"], key="agent_upload")
    if uploaded is not None and st.button("Upload and read", type="primary"):
        with st.spinner("Reading the document ..."):
            try:
                response = call_api("POST", "/documents/upload", files={"file": (uploaded.name, uploaded.getvalue(), uploaded.type or "application/octet-stream")})
            except httpx.HTTPError as error:
                notify("error", f"Could not reach the backend: {error}")
            else:
                if response.status_code == 200:
                    body = response.json()
                    st.session_state.agent_session_id = session_for_document(
                        st.session_state.agent_session_id, st.session_state.agent_doc_id, body["document"]["id"], lambda: uuid.uuid4().hex)
                    st.session_state.agent_doc_id = body["document"]["id"]
                    session_id = st.session_state.agent_session_id          # the chat below must use the new session in this same run
                    notify("info" if body["file_duplicate"] else "success", body["message"] or f"Document #{body['document']['id']} is ready. Ask the agent to review it.")
                else:
                    notify("error", error_text(response))

notice = st.session_state.pop("agent_notice", None)
if notice:
    getattr(st, notice[0])(notice[1])

doc_id = st.session_state.agent_doc_id
if doc_id is not None:
    info = call_api("GET", f"/documents/{doc_id}")
    if info.status_code == 200:
        doc = info.json()
        data = doc["data"]
        st.info(f"**Document #{doc_id}** ({doc['status']}) · {data.get('vendor_name') or 'vendor unknown'} · invoice {data.get('invoice_number') or '?'} · "
                f"total {data.get('total') if data.get('total') is not None else '?'} {data.get('currency') or ''} · {len(doc['issues'])} issue(s)")
    if st.button("Ask the agent to review it"):
        send(REVIEW_PROMPT)
        st.rerun()                                          # the banner above was drawn before the agent ran: redraw it from the backend

# --- chat -------------------------------------------------------------------------------------------------------------------------------
prompt = st.chat_input("Tell the agent what to do, or answer its questions (e.g. 'invoice number is INV-2231')")
if prompt:
    send(prompt)
    st.rerun()                                              # redraw the document banner (its issue count may have changed); any error notice is shown on the rerun

history = call_api("GET", f"/agent/sessions/{session_id}")
session = history.json() if history.status_code == 200 else {"messages": [], "pending_action": None}

for message in session["messages"]:
    if message["role"] == "note":
        st.caption(f"ℹ️ {message['content']}")
        continue
    with st.chat_message("user" if message["role"] == "user" else "assistant"):
        st.markdown(message["content"])
        if message["tool_trace"]:
            with st.expander(f"What the agent did ({len(message['tool_trace'])} step{'s' if len(message['tool_trace']) != 1 else ''})"):
                for step in message["tool_trace"]:
                    st.markdown(describe_step(step))

# --- approval ---------------------------------------------------------------------------------------------------------------------------
pending = session.get("pending_action")
if pending:
    summary = pending["summary"]
    with st.container(border=True):
        st.warning(f"**The agent asks you to save document #{pending['document_id']}.** Nothing is saved until you approve.")
        st.write(f"Vendor: {summary.get('vendor_name') or '?'} · Invoice: {summary.get('invoice_number') or '?'} · "
                 f"Total: {summary.get('total') if summary.get('total') is not None else '?'} {summary.get('currency') or ''}")
        if pending["save_anyway"] or summary.get("errors"):
            st.error(f"This will save the document even though it has {summary.get('errors', 0)} error(s).")
        approve_col, reject_col, _ = st.columns([1, 1, 3])
        if approve_col.button("Approve & Save", type="primary"):
            response = call_api("POST", f"/agent/sessions/{session_id}/approve")
            notify("success" if response.status_code == 200 else "error", response.json().get("message", "Saved.") if response.status_code == 200 else error_text(response))
            st.rerun()
        if reject_col.button("Reject"):
            response = call_api("POST", f"/agent/sessions/{session_id}/reject")
            notify("info" if response.status_code == 200 else "error", response.json().get("message", "Rejected.") if response.status_code == 200 else error_text(response))
            st.rerun()
