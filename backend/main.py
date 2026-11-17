"""DocuAgent API. Run with:  uvicorn backend.main:app --reload"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api import agent, documents
from backend.config import Settings, get_settings
from backend.database import init_db
from backend.schemas import HealthResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()                       # create the tables on first start
    yield


app = FastAPI(title="DocuAgent", version="0.1.0", description="Reads invoices and receipts, validates them, blocks duplicates, saves after you confirm.",
              lifespan=lifespan)
def add_cors(application: FastAPI, settings: Settings) -> None:
    """Lets the web pages named in CORS_ORIGINS call the API from a browser. Only those exact origins; no wildcard, no cookies."""
    application.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
                               allow_headers=["Content-Type"])


add_cors(app, get_settings())
app.include_router(documents.router)
app.include_router(agent.router)


@app.get("/health", tags=["meta"], response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")
