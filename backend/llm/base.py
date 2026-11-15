"""The one interface every LLM provider implements, the errors they raise, and the prompt they share."""

from abc import ABC, abstractmethod

from backend.schemas import Invoice
from backend.services.files import SourceFile


class LLMError(Exception):
    """Base class for everything that can go wrong when asking a model."""


class LLMRateLimitError(LLMError):
    """HTTP 429 / quota: worth retrying after a pause. `retry_after` is how long the provider said to wait, in seconds (when it said)."""

    def __init__(self, message: str = "", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class LLMTimeoutError(LLMError):
    """The provider did not answer in time (or the connection dropped): worth retrying."""


class LLMNotConfigured(LLMError):
    """No API key for this provider."""


class LLMBadOutput(LLMError):
    """The model answered, but not with valid JSON for the Invoice schema. Retrying the same call rarely helps."""


class LLMProviderError(LLMError):
    """Any other provider failure (bad key, unsupported model, server error): not retried, the next provider is tried."""


RETRYABLE = (LLMRateLimitError, LLMTimeoutError)

PROMPT = """You read invoices and receipts and return their data as JSON that follows the given schema.

Rules:
- Copy values exactly as printed. NEVER invent, guess or calculate a value that is not on the document: use null for anything missing or unreadable.
- doc_type is "invoice" or "receipt" (a till / shop receipt). Use null if you cannot tell.
- Dates must be ISO format YYYY-MM-DD. If a date is ambiguous or unreadable, use null.
- currency is the ISO 4217 code (USD, EUR, GBP, AED ...). Use null if no currency is shown.
- Amounts are plain numbers without currency symbols or thousands separators (1234.50).
- line_items has one entry per printed line: description, quantity, unit_price, amount (null for any part that is not printed).
- subtotal, tax and total are the amounts printed on the document. Do not recompute them.
- confidence: for every field a number from 0 (a guess) to 1 (clearly printed), or null when the field itself is null.
Return only the JSON object."""


class LLMClient(ABC):
    """Reads a document and returns an Invoice. Implementations map their SDK's exceptions to the LLMError classes above."""

    name: str

    @abstractmethod
    def is_configured(self) -> bool:
        """True when this provider has an API key."""

    @abstractmethod
    def extract(self, source: SourceFile) -> Invoice:
        """Send the document to the model and return the structured result. Raises an LLMError on failure."""
