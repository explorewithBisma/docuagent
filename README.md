# DocuAgent

DocuAgent reads **invoices and receipts** (PDF, PNG or JPG), extracts the data with an AI model, **checks it** (maths, dates, missing fields), **flags duplicates**, and saves it to a database **only after you confirm**.

This is **Phase 1 (MVP)**: upload, extract, validate, duplicate check, edit, confirm. Not built yet: an agent loop, a React front end, RAG / chat, an MCP server, authentication, deployment.

## What it does

- **Upload** a PDF, PNG or JPG (max 10 MB, max 5 pages). The file type is checked from the file content, not its name.
- **Extract** the data with Google Gemini (structured JSON). Anything not on the document is `null`, never invented, and every field has a confidence value.
  If Gemini is busy or has no key, **Groq** (a vision model) is used instead; Groq reads images only, so PDF pages are turned into PNGs first.
- **Validate** (plain Python, no AI): line items add up to the subtotal, subtotal + tax = total (small tolerance), valid dates, due date not before the invoice date,
  required fields present, low-confidence fields. Each issue has a field, a severity (error or warning) and a plain-English message.
- **Detect duplicates**
  - *same file*: a SHA-256 hash of every upload. The same file again returns the existing record **without calling the AI**.
  - *exact*: same normalised vendor + same normalised invoice number (`INV-001` = `inv 001`).
  - *possible*: similar vendor + same total + dates within 3 days (configurable).
- **Review and confirm**: edit any field (it is re-validated), then **Confirm & Save** (draft -> confirmed). An exact duplicate can never be saved; error-level issues need an explicit "save anyway".
- **Resilient**: a rate limit (429) or timeout is retried up to 3 times with exponential backoff (it honours the provider's own "try again in 14 s" hint), then the other provider is tried. If both fail, the UI says so clearly.

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
ui/        streamlit_app.py
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
| `GEMINI_API_KEY` | Google Gemini key (free tier) | empty |
| `GEMINI_MODEL` | Gemini model name | `gemini-3.8-flash` |
| `GROQ_API_KEY` | Groq key (fallback) | empty |
| `GROQ_MODEL` | Groq vision model | `qwen/qwen3.8-27b` |
| `GROQ_MAX_TOKENS` | Output tokens per Groq call (the free tier allows 1,000 per minute) | `1000` |
| `DATABASE_URL` | SQLAlchemy URL: `sqlite:///./docuagent.db` or your Neon Postgres URL | SQLite file |

Optional settings (same names in upper case): `MAX_UPLOAD_MB` (10), `MAX_PAGES` (5), `AMOUNT_TOLERANCE` (0.01), `DUPLICATE_DATE_WINDOW_DAYS` (3), `VENDOR_SIMILARITY` (0.85),
`LLM_MAX_TRIES` (3), `LLM_BACKOFF_SECONDS` (1), `LLM_TIMEOUT_SECONDS` (60), `UPLOAD_DIR`.

## Run it

Two terminals, from the project folder:

```
# 1. the backend (API docs at http://localhost:8000/docs)
uvicorn backend.main:app --reload

# 2. the web page (http://localhost:8501)
streamlit run ui/streamlit_app.py
```

The page uses `API_URL` (default `http://localhost:8000`) to find the backend.

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
- The old project (Spreadsheet Helper) is preserved on the `archive/spreadsheet-helper` branch and the `spreadsheet-helper-final` tag.
