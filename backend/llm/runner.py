"""Calls the providers in order with retry: a rate-limit (429) or timeout is retried up to N times with exponential backoff, then the
next provider is tried. Other failures skip straight to the next provider. If every provider fails, ExtractionFailed says why in plain words."""

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

from backend.config import Settings
from backend.llm.base import RETRYABLE, LLMBadOutput, LLMClient, LLMError, LLMNotConfigured, LLMTruncatedOutput
from backend.llm.gemini import GeminiClient
from backend.llm.groq_client import GroqClient
from backend.schemas import Invoice
from backend.services.files import SourceFile

log = logging.getLogger(__name__)


@dataclass
class ExtractionResult:
    invoice: Invoice
    provider: str


class ExtractionFailed(Exception):
    """No provider could read the document. `errors` lists what each one said; `message` is safe to show to the user."""

    def __init__(self, message: str, errors: list[str], bad_output: bool = False, truncated: bool = False):
        super().__init__(message)
        self.message, self.errors, self.bad_output, self.truncated = message, errors, bad_output, truncated


TOO_LONG_MESSAGE = "Document too long for the free tier, try fewer pages."


@dataclass
class ResilientExtractor:
    clients: list[LLMClient]
    max_tries: int = 3
    backoff_seconds: float = 1.0
    max_wait_seconds: float = 30.0                                       # never wait longer than this for a provider's 'try again in ...' hint
    sleep: Callable[[float], None] = field(default=time.sleep)             # replaced in tests so they do not wait

    def extract(self, source: SourceFile) -> ExtractionResult:
        errors: list[str] = []
        bad_output = False
        configured = [c for c in self.clients if c.is_configured()]
        for client in self.clients:
            if client not in configured:
                errors.append(f"{client.name}: no API key set (skipped).")
        if not configured:
            raise ExtractionFailed("No AI provider is configured. Set GEMINI_API_KEY or GROQ_API_KEY in the .env file.", errors)

        for client in configured:
            for attempt in range(1, self.max_tries + 1):
                try:
                    return ExtractionResult(client.extract(source), client.name)
                except RETRYABLE as error:
                    errors.append(f"{client.name}: {error} (try {attempt} of {self.max_tries})")
                    if attempt < self.max_tries:
                        hint = min(getattr(error, "retry_after", None) or 0.0, self.max_wait_seconds)           # the provider's own advice, if it gave one
                        self.sleep(max(self.backoff_seconds * 2 ** (attempt - 1), hint))      # 1 s, 2 s, 4 s ... (or longer if asked)
                except LLMNotConfigured as error:
                    errors.append(f"{client.name}: {error}")
                    break
                except LLMTruncatedOutput as error:       # cut off: partial data is never used, and the next provider is not asked (the document is the problem)
                    errors.append(f"{client.name}: {error}")
                    raise ExtractionFailed(TOO_LONG_MESSAGE, errors, bad_output=True, truncated=True) from error
                except LLMError as error:                                                 # bad output, bad key, server error: not retried
                    bad_output = bad_output or isinstance(error, LLMBadOutput)
                    errors.append(f"{client.name}: {error}")
                    break
            log.warning("LLM provider %s failed; trying the next one", client.name)
        names = " and ".join(c.name.capitalize() for c in configured)
        raise ExtractionFailed(f"Could not read the document: {names} both failed or are busy. Please try again in a minute." if len(configured) > 1
                               else f"Could not read the document: {names} failed or is busy. Please try again in a minute.", errors, bad_output)


def build_extractor(settings: Settings) -> ResilientExtractor:
    """Gemini first, Groq as the fallback (providers without a key are skipped at call time)."""
    return ResilientExtractor(
        clients=[GeminiClient(settings.gemini_api_key, settings.gemini_model, settings.llm_timeout_seconds),
                 GroqClient(settings.groq_api_key, settings.groq_model, settings.llm_timeout_seconds, max_tokens=settings.groq_max_tokens)],
        max_tries=settings.llm_max_tries, backoff_seconds=settings.llm_backoff_seconds)
