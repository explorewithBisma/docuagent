# DocuAgent

DocuAgent reads **invoices and receipts** (PDF, PNG or JPG), extracts the data with an AI model, **checks it** (maths, dates, missing fields), **flags duplicates**, and saves it to a database **only after you confirm**.

**Phase 1** is the pipeline: upload, extract, validate, duplicate check, edit, confirm. **Phase 2** adds an **agent** you chat with (function calling) that does the same steps for you but can never save by itself.
**Phase 3** (in progress) replaces Streamlit with a React front end in `frontend/`; only the setup is done so far (see "The React front end" below).
Not built yet: the React screens, n8n, RAG / invoice analytics, an MCP server, authentication, deployment.

## What it does

- **Upload** a PDF, PNG or JPG (max 10 MB, max 5 pages). The file type is checked from the file content, not its name.
- **Extract** the data with **Groq** (a vision model; the primary provider, structured JSON). Anything not on the document is `null`, never invented. The answer is kept small (free-tier token limits): one `overall_confidence` (0-1) plus a `low_confidence_fields` list instead of a confidence per field.
  If the answer is cut off (output limit reached, or incomplete JSON) nothing is saved and the user sees "Document too long for the free tier, try fewer pages."
  If Groq is busy or has no key, **Google Gemini** is used instead (change the order with `LLM_PROVIDER_ORDER`). Groq reads images only, so PDF pages are turned into PNGs first.
- **Validate** (plain Python, no AI): line items add up to the subtotal, subtotal + tax = total (small tolerance), valid dates, due date not before the invoice date,
  required fields present, low-confidence fields. Each issue has a field, a severity (error or warning) and a plain-English message.
- **Detect duplicates**
  - *same file*: a SHA-256 hash of every upload. The same file again returns the existing record **without calling the AI**.
  - *exact*: same normalised vendor + same normalised invoice number (`INV-001` = `inv 001`).
  - *possible*: similar vendor + same total + dates within 3 days (configurable).
- **Review and confirm**: edit any field (it is re-validated), then **Confirm & Save** (draft -> confirmed). An exact duplicate can never be saved; error-level issues need an explicit "save anyway".
- **Resilient**: a rate limit (429) or timeout is retried up to 3 times with exponential backoff (it honours the provider's own "try again in 14 s" hint), then the other provider is tried. If both fail, the UI says so clearly.

## The agent (Phase 2)

Open the **Agent** page in the Streamlit sidebar: upload a document, then chat ("review this", "invoice number is INV-2231", "save it").

- **Model**: a Groq text model with tool calling, `AGENT_MODEL` (default `openai/gpt-oss-20b`, `reasoning_effort=low`), separate from the vision `GROQ_MODEL`.
- **Tools** (thin wrappers over `backend/services`): `get_document`, `extract_document`, `validate_document`, `check_duplicates`, `update_fields`, `request_confirmation`, `search_documents`.
  At most 6 tool calls per message; unknown tools and invalid arguments are refused safely.
- **Human approval**: the agent can never save. `request_confirmation` only creates a pending request; the document is saved only when **you** click **Approve & Save**.
  If the document's fields change after the request, the approval is refused and must be requested again. **Reject** clears it and tells the agent.
- **Only your values**: `update_fields` accepts only values you typed (or said "yes" to); the "What the agent did" panel shows which of your messages each update came from.
- **Document text is data**: extracted text is wrapped as `<data>...</data>` in tool results and the agent is told never to obey it.
- **No tokens unless you act**: the model is called only when you send a message or click "Ask the agent to review it".
- API: `POST /agent/chat` - `GET /agent/sessions/{id}` - `POST /agent/sessions/{id}/approve` - `POST /agent/sessions/{id}/reject`.

## Architecture

```
  Streamlit UI  (ui/streamlit_app.py)           only calls the API, no business logic
        |  HTTP (httpx)
        v
  FastAPI  (backend/main.py, backend/api/documents.py)
        |
        v
  services/documents.py        upload -> extract -> validate -> duplicate check -> draft ... confirm
        |
        +--> services/files.py        file type by magic bytes, SHA-256, page count, PDF -> PNG (PyMuPDF)
        +--> llm/runner.py            retry + backoff + provider fallback
        |        +--> llm/gemini.py       Gemini (reads the PDF directly)    } both implement
        |        +--> llm/groq_client.py  Groq vision (PNG pages only)       } llm/base.py: LLMClient
        +--> services/validation.py   maths / dates / required fields  -> list[Issue]
        +--> services/duplicates.py   exact and possible duplicates    -> DuplicateMatch
        |
        v
  SQLAlchemy 2  (backend/models.py, backend/database.py)
        v
  SQLite (local)  or  Postgres / Neon (DATABASE_URL)
```

Folders:

```
backend/   config.py  schemas.py (Pydantic)  models.py  database.py  main.py
           api/documents.py   llm/{base,gemini,groq_client,runner}.py   services/{documents,files,normalize,validation,duplicates}.py
ui/        streamlit_app.py (Phase 1 page)  pages/2_Agent.py (agent chat)  api_client.py
           (agent code: backend/agent/, backend/api/agent.py, backend/services/agent_sessions.py)
frontend/  Vite + React + TypeScript + Tailwind (Phase 3)   src/api/ (generated types + typed fetch client)   src/pages/   src/components/
tests/     conftest.py   fixtures/make_samples.py (5 fake PDFs)   test_*.py
```

## Setup

Python 3.12 or newer (developed and tested on 3.14).

```
python -m venv .venv
.venv\Scripts\activate                 # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt    # runtime + test packages (use requirements.txt for runtime only)
copy .env.example .env                 # macOS/Linux: cp .env.example .env   - then put your own keys in .env
```

`.env` (never commit it; it is git-ignored):

| Variable | Meaning | Default |
|---|---|---|
| `LLM_PROVIDER_ORDER` | Providers in the order they are asked (first = primary) | `groq,gemini` |
| `AGENT_MODEL` | Groq text model with tool calling for the Agent page | `openai/gpt-oss-20b` |
| `GEMINI_API_KEY` | Google Gemini key (free tier) | empty |
| `GEMINI_MODEL` | Gemini model name | `gemini-3.8-flash` |
| `GROQ_API_KEY` | Groq key (fallback) | empty |
| `GROQ_MODEL` | Groq vision model | `qwen/qwen3.8-27b` |
| `GROQ_MAX_TOKENS` | Output tokens per Groq call (the free tier allows 1,000 per minute) | `1000` |
| `DATABASE_URL` | SQLAlchemy URL: `sqlite:///./docuagent.db` or your Neon Postgres URL | SQLite file |
| `CORS_ORIGINS` | Web pages allowed to call the API from a browser: comma-separated exact origins. `*` is refused | `http://localhost:5173` |

Optional settings (same names in upper case): `AGENT_REASONING_EFFORT` (low), `AGENT_MAX_TOKENS` (700), `AGENT_MAX_TOOL_CALLS` (6), `AGENT_HISTORY_MESSAGES` (12), `MAX_UPLOAD_MB` (10), `MAX_PAGES` (5), `AMOUNT_TOLERANCE` (0.01), `DUPLICATE_DATE_WINDOW_DAYS` (3), `VENDOR_SIMILARITY` (0.85),
`LLM_MAX_TRIES` (3), `LLM_BACKOFF_SECONDS` (1), `LLM_TIMEOUT_SECONDS` (60), `UPLOAD_DIR`.

## Run it

Two terminals, from the project folder:

```
# 1. the backend (API docs at http://localhost:8000/docs)
#    --timeout-graceful-shutdown 2: a reload would otherwise hang waiting for open browser connections and keep serving the old code
uvicorn backend.main:app --reload --timeout-graceful-shutdown 2

# 2. the web page (http://localhost:8501)
streamlit run ui/streamlit_app.py
```

The page uses `API_URL` (default `http://localhost:8000`) to find the backend.

### The React front end (Phase 3, setup only)

Needs Node 20.19+ or 22.12+ (developed on Node 26). With the backend running as above, in a third terminal:

```
cd frontend
npm install
npm run dev          # http://localhost:5173
```

The home page calls `/health` and shows "Backend: ok" or a clear error. **`/documents`** lists the documents (status filter kept in the URL as `?status=draft|confirmed`, refresh button, loading / error / empty states; each row links to `/documents/:id`, a placeholder for now). `/upload` and `/agent` are empty placeholders. Streamlit keeps working alongside it.

| Command (in `frontend/`) | What it does |
|---|---|
| `npm run dev` | Dev server on port 5173 (fixed, because the backend's `CORS_ORIGINS` allows exactly that origin) |
| `npm run build` | Type-check (strict) and build to `frontend/dist/` |
| `npm run lint` | oxlint |
| `npm test` | Vitest + React Testing Library (jsdom) with a mocked `fetch`: no backend and no network needed. `npm run test:watch` re-runs on change |
| `npm run gen:types` | Regenerates `src/api/schema.d.ts` from `http://localhost:8000/openapi.json` (the backend must be running). Run it after changing an API route or schema, and commit the result |

The backend address comes from `VITE_API_URL` (default `http://localhost:8000`; see `frontend/.env.example`, copy it to `frontend/.env.local` to change it). Only `VITE_*` variables reach the browser, so never put keys there.
If the page says the backend cannot be reached, check that uvicorn is running and that `CORS_ORIGINS` in the backend's `.env` includes the address in your browser's address bar.
TypeScript is pinned to 5.9 because `openapi-typescript` does not support TypeScript 7 yet.

API: `POST /documents/upload` - `GET /documents` - `GET /documents/{id}` - `GET /documents/{id}/file` - `PATCH /documents/{id}` - `POST /documents/{id}/confirm` - `GET /health`.

## Tests

```
pytest                       # all tests, no API calls (the AI is mocked)
pytest -m integration        # also calls the real Gemini / Groq APIs; each is skipped when its key is missing
python tests/fixtures/make_samples.py   # writes the 5 fake sample PDFs to tests/fixtures/samples/ so you can try them in the UI
```

The sample PDFs are generated from fake data: a clean invoice, a receipt, an invoice with a maths error, an invoice with no invoice number, and a second copy of the clean invoice (an exact duplicate).

## Notes

- Uploaded files are kept in `uploads/` (git-ignored) so the original can be compared with the extracted fields.
- Phase 1 creates the tables on start-up and has no migrations.
- DocuAgent replaced an older project (Spreadsheet Helper), which is no longer kept.
