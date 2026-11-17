# CLAUDE.md - DocuAgent

## What this is
DocuAgent reads invoices and receipts (PDF/PNG/JPG), extracts structured data with an LLM, validates it (maths, dates, missing fields),
flags missing/wrong fields, blocks duplicates, and saves a document only after the user confirms (draft -> confirmed).
It replaced the old "Spreadsheet Helper" project, which lives on branch `archive/spreadsheet-helper` / tag `spreadsheet-helper-final`. Never reuse it.
The GitHub repo is now "docuagent": https://github.com/explorewithBisma/docuagent (`origin`, branch `main`). The old excel-cleaner-webapp repo was deleted.
The local folder may still be renamed by the user. Do not rename the folder or push unless asked.

## Folder structure
```
backend/   config.py (pydantic-settings, reads .env)   schemas.py (Pydantic v2, Invoice = LLM response schema)   models.py   database.py   main.py
           api/documents.py            thin FastAPI routes, no business logic
           services/                   documents.py (workflow)  files.py  normalize.py  validation.py  duplicates.py
           llm/                        base.py (LLMClient, errors, PROMPT)  groq_client.py  gemini.py  runner.py (retry + fallback, build_extractor)
           agent/                      loop.py  tools.py  llm.py (GroqAgentLLM)  chat.py  prompts.py  grounding.py  untrusted.py  schemas.py
           api/agent.py                /agent/chat, /agent/sessions/{id}[/approve|/reject]      services/agent_sessions.py (sessions, approval)
ui/        streamlit_app.py (Phase 1 page)  pages/2_Agent.py (agent chat)  api_client.py   only call the API over HTTP (httpx)
tests/     conftest.py (FakeLLM, tmp DB)   fixtures/make_samples.py (5 fake PDFs)   test_*.py
```

## Run (from the repo root, inside .venv)
```
python -m venv .venv && .venv\Scripts\activate         # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
copy .env.example .env                                  # then add your own keys
uvicorn backend.main:app --reload                       # API + docs at http://localhost:8000/docs
streamlit run ui/streamlit_app.py                       # UI at http://localhost:8501
pytest                                                  # unit + API tests, AI mocked, no network
pytest -m integration                                   # real Groq/Gemini/agent-model calls (spends free-tier quota; skipped without keys)
python tests/fixtures/make_samples.py                   # writes the 5 fake sample PDFs to tests/fixtures/samples/
```
On the Windows bash tool, heredocs mangle backslashes and apostrophes: use the Write/Edit tools for such content.

## Key design rules
- **Groq is the PRIMARY provider, Gemini the fallback.** Order comes from `LLM_PROVIDER_ORDER` (default `groq,gemini`); a test guards the default.
- All business logic lives in `backend/services/`. API routes stay thin; the UI never contains logic and only calls the API.
- Every LLM provider sits behind the `LLMClient` interface and maps SDK errors to our `LLMError` classes. Tests use a fake client, never the network.
- Missing data is `null`, never invented. Validation is plain Python (Decimal maths), not AI.
- Nothing is saved from a failed or truncated extraction (no DB row, no uploaded file).
- Duplicates are checked against CONFIRMED documents only. An exact duplicate can never be confirmed.
- No real data in fixtures: all sample invoices are fake (made by reportlab in `tests/fixtures/make_samples.py`).
- Secrets only in `.env` (git-ignored). `.env.example` has placeholders only. Never print or commit keys.
- Model names in `.env.example`: Groq `qwen/qwen3.8-27b` (vision) and `openai/gpt-oss-20b` (agent) are verified; Gemini `gemini-3.8-flash` is UNVERIFIED (no Gemini key yet).

## Groq free-tier limits (these shaped the design)
- 1,000 output tokens per minute: `GROQ_MAX_TOKENS=1000`; the default 2048 is refused.
- 7,000 input tokens per minute: a one-page request is about 1,950 prompt tokens, so keep the prompt compact; PDF pages are rendered at dpi 120.
- Vision models read images only: PDF pages are rendered to PNG with PyMuPDF before sending. Max 5 pages per upload (`MAX_PAGES`).
- The extraction output is small on purpose: null fields are omitted, one `overall_confidence` + `low_confidence_fields` (no per-field confidence).
- 429, 503 ("over capacity") and timeouts are retried 3 times with exponential backoff (honouring "try again in Ns", capped at 30 s), then the next provider is used.
- A cut-off answer (`finish_reason == "length"` or invalid JSON) is NOT retried: the API returns 422 "Document too long for the free tier, try fewer pages."
- The agent model (gpt-oss-20b) needs ~900 prompt tokens per call (system prompt + 7 tool schemas); a turn with 4-5 calls is ~4.5k input tokens, so avoid chaining many turns within one minute.

## Agent rules (Phase 2; code in `backend/agent/`)
- The agent model is a Groq TEXT model with tool calling: `AGENT_MODEL` (default `openai/gpt-oss-20b`, verified, `reasoning_effort=low`), separate from the vision `GROQ_MODEL`.
  Do not use `openai/gpt-oss-safeguard-20b`. Use `openai/gpt-oss-120b` only if 20b stops working.
- **The agent can NEVER save.** No tool confirms a document. `request_confirmation` only writes a `pending_action`; the user clicks "Approve & Save", which calls
  `POST /agent/sessions/{id}/approve` (re-checks the fields hash, then runs the normal `confirm` service). "Reject" clears it and leaves a note for the agent.
- Stale approval: `pending_action` stores a hash of the document's fields; if they changed, approval is refused (409), the request is cleared and a note says it must be requested again.
- Document text is DATA: every extracted string in a tool result is wrapped in `<data>...</data>` (`agent/untrusted.py`), and the system prompt says never to obey it.
- `update_fields` only takes values the user typed (or said "yes" to): the agent cites `source_message` (the `[msg N]` number) and `agent/grounding.py` checks the value is in that message. The tool trace shows the source.
- Loop (`agent/loop.py`): max 6 tool calls per user message, unknown tools / bad JSON / bad arguments are refused with a short message, tool errors go back to the model as short text.
- No model tokens unless the user sends a message or clicks a button. Keep prompts, tool results and history small (last 12 messages, file content is never in the chat).
- Tools are thin wrappers over `backend/services/*`: put new logic in services, not in tools. Sessions live in the `chat_sessions` table.

## Phase 1 (done)
Upload (PDF/PNG/JPG, 10 MB, 5 pages, type checked by magic bytes), SHA-256 file hash (same file = existing record, no LLM call), extraction with retry and
provider fallback, validation, duplicate detection (same file / exact vendor + normalised invoice number / possible: similar vendor + same total + dates
within 3 days), edit with PATCH (re-validated), confirm, Streamlit UI, SQLite or Postgres via `DATABASE_URL`, pytest suite.
Endpoints: `POST /documents/upload`, `GET /documents`, `GET /documents/{id}`, `GET /documents/{id}/file`, `PATCH /documents/{id}`, `POST /documents/{id}/confirm`, `GET /health`.
Not built: migrations (tables are created on start-up), auth, deployment.

## Phase 2 (done)
Agent loop with function calling (7 tools: get_document, extract_document, validate_document, check_duplicates, update_fields, request_confirmation, search_documents),
chat sessions in the DB, `POST /agent/chat`, `GET /agent/sessions/{id}`, `POST /agent/sessions/{id}/approve|reject`, Streamlit "Agent" page (`ui/pages/2_Agent.py`) with upload,
chat, Approve & Save / Reject and a "What the agent did" panel. Groq 503 "over capacity" is retried like 429.

## Roadmap
1. Phase 3: React front end replacing Streamlit (the API stays the contract).
2. Phase 4: n8n automation, chat with invoices (RAG / analytics), MCP server.
3. Then deploy (Postgres/Neon, auth, migrations).
Out of scope until its phase: React, n8n, RAG, invoice analytics, MCP, auth, deployment. Do not add them early.

## Working agreements
- Run `pytest` inside `.venv` before every commit; report failures honestly.
- Commit with a clear message; do not push or rename the folder/repo unless the user asks.
- Do not uninstall anything from the global Python.
