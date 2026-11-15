"""Settings, read from environment variables (and a local .env file). Nothing secret is hard-coded."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # --- LLM providers ---------------------------------------------------------------------------------------------
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    groq_api_key: str = ""
    groq_model: str = "qwen/qwen3.8-27b"
    groq_max_tokens: int = 1000                 # the free tier allows 1,000 output tokens per minute; asking for more is refused
    llm_timeout_seconds: float = 60.0
    llm_max_tries: int = 3                      # per provider, for rate-limit (429) and timeout errors
    llm_backoff_seconds: float = 1.0            # 1 s, then 2 s, then 4 s ... between tries

    # --- Database --------------------------------------------------------------------------------------------------
    database_url: str = "sqlite:///./docuagent.db"

    # --- Uploads ---------------------------------------------------------------------------------------------------
    upload_dir: Path = Field(default=PROJECT_ROOT / "uploads")
    max_upload_mb: int = 10
    max_pages: int = 5                          # PDFs with more pages are rejected (both providers)

    # --- Validation and duplicate rules ----------------------------------------------------------------------------
    amount_tolerance: float = 0.01              # money differences up to this are rounding, not errors
    duplicate_date_window_days: int = 3         # "possible duplicate": similar vendor + same total + dates this close
    vendor_similarity: float = 0.85             # 0..1, how alike two normalised vendor names must be

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def sqlalchemy_url(self) -> str:
        """The URL with the driver SQLAlchemy needs: Neon / Heroku style postgres:// URLs use psycopg 3."""
        url = self.database_url
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
