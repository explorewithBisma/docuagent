"""DocuAgent API. Run with:  uvicorn backend.main:app --reload"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.api import agent, documents
from backend.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()                       # create the tables on first start
    yield


app = FastAPI(title="DocuAgent", version="0.1.0", description="Reads invoices and receipts, validates them, blocks duplicates, saves after you confirm.",
              lifespan=lifespan)
app.include_router(documents.router)
app.include_router(agent.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}
