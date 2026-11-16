# CLAUDE.md - DocuAgent

## What this is
DocuAgent reads invoices and receipts (PDF/PNG/JPG), extracts structured data with an LLM, validates it (maths, dates, missing fields),
flags missing/wrong fields, blocks duplicates, and saves a document only after the user confirms (draft -> confirmed).
It replaced the old "Spreadsheet Helper" project, which lives on branch `archive/spreadsheet-helper` / tag `spreadsheet-helper-final`. Never reuse it.
The folder and GitHub repo will be renamed to "docuagent" by the user. Do not rename or push unless asked.

## Folder structure
```
backend/   config.py (pydantic-settings, reads .env)   schemas.py (Pydantic v2, Invoice = LLM response schema)   models.py   database.py   main.py
           api/documents.py            thin FastAPI routes, no business logic
           services/                   documents.py (workflow)  files.py  normalize.py  validation.py  duplicates.py
           llm/                        base.py (LLMClient, errors, PROMPT)  groq_client.py  gemini.py  runner.py (retry + fallback, build_extractor)
ui/        streamlit_app.py            only calls the API over HTTP (httpx)
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
pytest -m integration                                   # real Groq/Gemini calls (spends free-tier quota; skipped without keys)
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
- Model names in `.env.example`: Groq `qwen/qwen3.8-27b` is verified; Gemini `gemini-3.8-flash` is UNVERIFIED (no Gemini key yet).

## Groq free-tier limits (these shaped the design)
- 1,000 output tokens per minute: `GROQ_MAX_TOKENS=1000`; the default 2048 is refused.
- 7,000 input tokens per minute: a one-page request is about 1,950 prompt tokens, so keep the prompt compact; PDF pages are rendered at dpi 120.
- Vision models read images only: PDF pages are rendered to PNG with PyMuPDF before sending. Max 5 pages per upload (`MAX_PAGES`).
- The extraction output is small on purpose: null fields are omitted, one `overall_confidence` + `low_confidence_fields` (no per-field confidence).
- 429 and timeouts are retried 3 times with exponential backoff (honouring "try again in Ns", capped at 30 s), then the next provider is used.
- A cut-off answer (`finish_reason == "length"` or invalid JSON) is NOT retried: the API returns 422 "Document too long for the free tier, try fewer pages."
- The model can answer 503 "over capacity" at times; just retry later.

## Phase 1 (done)
Upload (PDF/PNG/JPG, 10 MB, 5 pages, type checked by magic bytes), SHA-256 file hash (same file = existing record, no LLM call), extraction with retry and
provider fallback, validation, duplicate detection (same file / exact vendor + normalised invoice number / possible: similar vendor + same total + dates
within 3 days), edit with PATCH (re-validated), confirm, Streamlit UI, SQLite or Postgres via `DATABASE_URL`, pytest suite.
Endpoints: `POST /documents/upload`, `GET /documents`, `GET /documents/{id}`, `GET /documents/{id}/file`, `PATCH /documents/{id}`, `POST /documents/{id}/confirm`, `GET /health`.
Not built: migrations (tables are created on start-up), auth, deployment.

## Roadmap
1. Phase 2: agent loop with function calling (the model chooses tools such as extract, validate, check duplicates, ask the user).
2. Phase 3: React front end replacing Streamlit (the API stays the contract).
3. Phase 4: n8n automation, chat with invoices (RAG), MCP server.
4. Then deploy (Postgres/Neon, auth, migrations).
Out of scope until its phase: agent loop, React, n8n, RAG, chat, MCP, auth, deployment. Do not add them early.

## Working agreements
- Run `pytest` inside `.venv` before every commit; report failures honestly.
- Commit with a clear message; do not push or rename the folder/repo unless the user asks.
- Do not uninstall anything from the global Python.
