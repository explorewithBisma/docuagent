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
    llm_provider_order: str = "groq,gemini"      # who is asked first; the next one is the fallback (names: groq, gemini)
    groq_max_tokens: int = 1000                 # the free tier allows 1,000 output tokens per minute; asking for more is refused
    llm_timeout_seconds: float = 60.0
    llm_max_tries: int = 3                      # per provider, for rate-limit (429) and timeout errors
    llm_backoff_seconds: float = 1.0            # 1 s, then 2 s, then 4 s ... between tries

    # --- Agent (a Groq TEXT model with tool calling; separate from the vision model above) -------------------------
    agent_model: str = "openai/gpt-oss-20b"
    agent_reasoning_effort: str = "low"          # these are reasoning models; "low" keeps the output tokens (1,000/min on the free tier) down
    agent_max_tokens: int = 700                  # output cap per model call
    agent_max_tool_calls: int = 6                # per user message
    agent_history_messages: int = 12             # how many past chat messages are sent to the model

    # --- Database --------------------------------------------------------------------------------------------------
    database_url: str = "sqlite:///./docuagent.db"

    # --- CORS (which web pages may call the API from a browser) ----------------------------------------------------
    cors_origins: str = "http://localhost:5173"  # comma-separated origins, e.g. "http://localhost:5173,https://app.example.com". Never "*".

    # --- Uploads ---------------------------------------------------------------------------------------------------
    upload_dir: Path = Field(default=PROJECT_ROOT / "uploads")
    max_upload_mb: int = 10
    max_pages: int = 5                          # PDFs with more pages are rejected (both providers)

    # --- Validation and duplicate rules ----------------------------------------------------------------------------
    amount_tolerance: float = 0.01              # money differences up to this are rounding, not errors
    duplicate_date_window_days: int = 3         # "possible duplicate": similar vendor + same total + dates this close
    vendor_similarity: float = 0.85             # 0..1, how alike two normalised vendor names must be

    @property
    def cors_origin_list(self) -> list[str]:
        """The allowed origins as a list. A wildcard is refused: browsers would then let any website call the API."""
        origins = [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]
        if "*" in origins:
            raise ValueError('CORS_ORIGINS must list exact origins (e.g. http://localhost:5173); "*" is not allowed.')
        return origins

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
