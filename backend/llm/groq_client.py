"""Groq vision models. They read IMAGES only, so a PDF is rendered to one PNG per page (PyMuPDF) before it is sent. The answer is requested
as a JSON object and validated against the Invoice schema."""

import base64
import json
import re
from typing import Any, Optional

from pydantic import ValidationError

from backend.llm.base import (PROMPT, LLMBadOutput, LLMClient, LLMNotConfigured, LLMProviderError, LLMRateLimitError, LLMTimeoutError, LLMTruncatedOutput)
from backend.schemas import Invoice
from backend.services.files import SourceFile


def retry_hint(text: str) -> Optional[float]:
    """Seconds from Groq's 'Please try again in 14.3s' / 'in 250ms' / 'in 1m5s' hint, or None."""
    match = re.search(r"try again in (?:(\d+)m(?!s))?(?:(\d+(?:\.\d+)?)(ms|s))?", text)
    if not match or not (match.group(1) or match.group(2)):
        return None
    seconds = float(match.group(1) or 0) * 60
    if match.group(2):
        seconds += float(match.group(2)) / (1000 if match.group(3) == "ms" else 1)
    return seconds


class GroqClient(LLMClient):
    name = "groq"

    def __init__(self, api_key: str, model: str, timeout_seconds: float = 60.0, sdk_client: Optional[Any] = None, max_tokens: int = 1000):
        self.api_key, self.model, self.timeout_seconds, self.max_tokens = api_key, model, timeout_seconds, max_tokens
        self._client = sdk_client                          # tests pass a fake SDK client

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _sdk(self):
        if self._client is None:
            import groq
            self._client = groq.Groq(api_key=self.api_key, timeout=self.timeout_seconds, max_retries=0)    # retries are done by our own runner
        return self._client

    def build_messages(self, source: SourceFile) -> list[dict]:
        """One user message: the prompt, the schema, and every page as a base64 image (never a PDF)."""
        content: list[dict] = [{"type": "text", "text": PROMPT}]
        for image, mime in source.page_images(dpi=120):
            encoded = base64.b64encode(image).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
        return [{"role": "user", "content": content}]

    def extract(self, source: SourceFile) -> Invoice:
        if not self.is_configured():
            raise LLMNotConfigured("GROQ_API_KEY is not set.")
        import groq
        try:
            messages = self.build_messages(source)
        except Exception as error:                          # a PDF that cannot be rendered to images
            raise LLMProviderError(f"Could not turn the document into images for Groq: {error}") from error
        try:
            response = self._sdk().chat.completions.create(model=self.model, messages=messages, response_format={"type": "json_object"}, temperature=0,
                                                           max_tokens=self.max_tokens)
        except groq.RateLimitError as error:
            raise LLMRateLimitError(f"Groq rate limit: {error}", retry_after=retry_hint(str(error))) from error
        except (groq.APITimeoutError, groq.APIConnectionError) as error:
            raise LLMTimeoutError(f"Groq timed out or could not connect: {error}") from error
        except groq.APIStatusError as error:
            raise LLMProviderError(f"Groq error {error.status_code}: {error}") from error
        try:
            choice = response.choices[0]
        except (AttributeError, IndexError, TypeError) as error:
            raise LLMBadOutput("Groq returned no answer.") from error
        if getattr(choice, "finish_reason", None) == "length":      # hit max_tokens: the JSON is cut off, never use it
            raise LLMTruncatedOutput(f"Groq stopped at the {self.max_tokens}-token output limit (the answer is cut off).")
        try:
            return Invoice.model_validate(json.loads(choice.message.content))
        except json.JSONDecodeError as error:
            raise LLMTruncatedOutput(f"Groq did not return complete JSON (it may have been cut off): {error}") from error
        except (ValidationError, TypeError, AttributeError) as error:
            raise LLMBadOutput(f"Groq did not return a valid invoice JSON: {error}") from error
