"""Google Gemini via the google-genai SDK. PDFs and images are sent as they are (Gemini reads PDFs natively) with the Invoice
schema as the response schema, so the answer is structured JSON."""

import json
from typing import Any, Optional

from pydantic import ValidationError

from backend.llm.base import (PROMPT, LLMBadOutput, LLMClient, LLMNotConfigured, LLMProviderError, LLMRateLimitError, LLMTimeoutError, LLMTruncatedOutput)
from backend.schemas import Invoice
from backend.services.files import SourceFile


class GeminiClient(LLMClient):
    name = "gemini"

    def __init__(self, api_key: str, model: str, timeout_seconds: float = 60.0, sdk_client: Optional[Any] = None):
        self.api_key, self.model, self.timeout_seconds = api_key, model, timeout_seconds
        self._client = sdk_client                          # tests pass a fake SDK client

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _sdk(self):
        if self._client is None:
            from google import genai
            from google.genai import types
            self._client = genai.Client(api_key=self.api_key, http_options=types.HttpOptions(timeout=int(self.timeout_seconds * 1000)))
        return self._client

    def extract(self, source: SourceFile) -> Invoice:
        if not self.is_configured():
            raise LLMNotConfigured("GEMINI_API_KEY is not set.")
        from google.genai import errors, types
        try:
            response = self._sdk().models.generate_content(
                model=self.model,
                contents=[types.Part.from_bytes(data=source.data, mime_type=source.mime), PROMPT],
                config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=Invoice, temperature=0),
            )
        except errors.APIError as error:
            code = getattr(error, "code", None)
            if code == 429:
                raise LLMRateLimitError(f"Gemini rate limit: {error}") from error
            if code in (408, 504):
                raise LLMTimeoutError(f"Gemini timed out: {error}") from error
            raise LLMProviderError(f"Gemini error {code}: {error}") from error
        except Exception as error:                         # network trouble and SDK timeouts surface as httpx errors
            name = type(error).__name__.lower()
            if "timeout" in name or "connect" in name:
                raise LLMTimeoutError(f"Gemini connection problem: {error}") from error
            raise LLMProviderError(f"Gemini failed: {error}") from error
        return self._parse(response)

    @staticmethod
    def _finish_reason(response: Any) -> str:
        try:
            reason = response.candidates[0].finish_reason
            return str(getattr(reason, "name", reason) or "")
        except (AttributeError, IndexError, TypeError):
            return ""

    @classmethod
    def _parse(cls, response: Any) -> Invoice:
        if cls._finish_reason(response).upper() in ("MAX_TOKENS", "LENGTH"):     # cut off: never use partial data
            raise LLMTruncatedOutput("Gemini stopped at its output limit (the answer is cut off).")
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, Invoice):
            return parsed
        try:
            return Invoice.model_validate(json.loads(response.text))
        except json.JSONDecodeError as error:
            raise LLMTruncatedOutput(f"Gemini did not return complete JSON (it may have been cut off): {error}") from error
        except (ValidationError, TypeError, AttributeError) as error:
            raise LLMBadOutput(f"Gemini did not return a valid invoice JSON: {error}") from error
