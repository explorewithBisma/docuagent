"""Shared test setup: a temporary database and upload folder, the 5 sample PDFs, and a FAKE LLM (no API calls anywhere)."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

import make_samples  # noqa: E402
from backend.api.documents import get_extractor  # noqa: E402
from backend.config import Settings, get_settings  # noqa: E402
from backend.database import Base, get_db, make_engine  # noqa: E402
from backend.llm.base import LLMClient  # noqa: E402
from backend.llm.runner import ResilientExtractor  # noqa: E402
from backend.main import app  # noqa: E402
from backend.schemas import Invoice  # noqa: E402


class FakeLLM(LLMClient):
    """Returns the Invoice a perfect extraction of each sample file would return, and counts the calls."""
    name = "fake"

    def __init__(self, answers: dict[bytes, Invoice]):
        self.answers, self.calls = answers, 0

    def is_configured(self) -> bool:
        return True

    def extract(self, source):
        self.calls += 1
        return self.answers[source.data].model_copy(deep=True)


@pytest.fixture(scope="session")
def sample_files(tmp_path_factory) -> dict[str, bytes]:
    return make_samples.build_all(tmp_path_factory.mktemp("samples"))


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'test.db'}", upload_dir=tmp_path / "uploads", gemini_api_key="", groq_api_key="")


@pytest.fixture
def session(settings):
    engine = make_engine(settings.sqlalchemy_url)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        yield db


@pytest.fixture
def fake_llm(sample_files) -> FakeLLM:
    return FakeLLM({sample_files[name]: invoice for name, invoice in make_samples.SAMPLES.items()})


@pytest.fixture
def extractor(fake_llm) -> ResilientExtractor:
    return ResilientExtractor(clients=[fake_llm], sleep=lambda seconds: None)


@pytest.fixture
def client(settings, session, extractor):
    """The real FastAPI app with the database, settings and LLM swapped for the test ones."""
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_extractor] = lambda: extractor
    yield TestClient(app)
    app.dependency_overrides.clear()
