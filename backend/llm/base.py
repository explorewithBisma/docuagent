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


class LLMTruncatedOutput(LLMBadOutput):
    """The answer was cut off (finish_reason "length" / max tokens) or is not complete JSON. The partial data is never used."""


RETRYABLE = (LLMRateLimitError, LLMTimeoutError)

# Compact on purpose: the free tiers limit tokens per minute, so a short prompt and a short answer (null fields left out, minified JSON) matter.
PROMPT = (
    "Read this invoice/receipt and reply with JSON only. Copy values exactly as printed; NEVER invent or calculate a value. "
    "Leave out (do not write) every field that is missing or unreadable.\n"
    "Fields: doc_type (invoice|receipt), vendor_name, vendor_tax_id, invoice_number, invoice_date and due_date (YYYY-MM-DD), currency (ISO code), "
    "line_items [{description, quantity, unit_price, amount}] one per printed line, subtotal, tax, total (plain numbers as printed), "
    "overall_confidence (0-1 for the whole document), low_confidence_fields (names of fields you are unsure about; usually []).\n"
    "Minified JSON on one line, no explanation."
)


class LLMClient(ABC):
    """Reads a document and returns an Invoice. Implementations map their SDK's exceptions to the LLMError classes above."""

    name: str

    @abstractmethod
    def is_configured(self) -> bool:
        """True when this provider has an API key."""

    @abstractmethod
    def extract(self, source: SourceFile) -> Invoice:
        """Send the document to the model and return the structured result. Raises an LLMError on failure."""
