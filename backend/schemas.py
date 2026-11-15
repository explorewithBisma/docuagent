"""Pydantic v2 models: the data extracted from a document, and what the API sends back.

`Invoice` is also the schema given to the LLM, so every field is optional: a field the model cannot read is null, never invented.
Dates are ISO strings (YYYY-MM-DD) so a badly read date is reported as an issue by the validator instead of failing the whole extraction.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class DocType(str, Enum):
    invoice = "invoice"
    receipt = "receipt"


class LineItem(BaseModel):
    description: Optional[str] = Field(None, description="What was bought, as written on the document")
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    amount: Optional[float] = Field(None, description="Line total as printed (quantity x unit price)")


DATA_FIELDS = ("doc_type", "vendor_name", "vendor_tax_id", "invoice_number", "invoice_date", "due_date", "currency", "line_items", "subtotal", "tax", "total")


class Invoice(BaseModel):
    doc_type: Optional[DocType] = None
    vendor_name: Optional[str] = None
    vendor_tax_id: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = Field(None, description="ISO date YYYY-MM-DD")
    due_date: Optional[str] = Field(None, description="ISO date YYYY-MM-DD")
    currency: Optional[str] = Field(None, description="ISO 4217 code such as USD, EUR, AED")
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    total: Optional[float] = None
    overall_confidence: Optional[float] = Field(None, description="How sure the reader is about the whole document, 0 to 1")
    low_confidence_fields: list[str] = Field(default_factory=list, description="Names of the fields it was unsure about (usually empty)")

    @field_validator("vendor_name", "vendor_tax_id", "invoice_number", "invoice_date", "due_date", "currency", mode="before")
    @classmethod
    def _blank_is_null(cls, value):
        """An empty string from the model means 'not on the document': null, not an invented value."""
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("overall_confidence")
    @classmethod
    def _clamp(cls, value: Optional[float]) -> Optional[float]:
        return None if value is None else max(0.0, min(1.0, float(value)))

    @field_validator("low_confidence_fields")
    @classmethod
    def _known_fields_only(cls, names: list[str]) -> list[str]:
        """Only real field names (the model may write anything); no repeats, order kept."""
        known = set(DATA_FIELDS)
        return list(dict.fromkeys(name for name in names if name in known))

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, value: Optional[str]) -> Optional[str]:
        return value.upper() if value else value


class Severity(str, Enum):
    error = "error"
    warning = "warning"


class Issue(BaseModel):
    code: str                       # machine name, e.g. "total_mismatch"
    field: str                      # which field it is about, e.g. "total" or "line_items"
    severity: Severity
    message: str                    # plain English, shown to the user


class DuplicateKind(str, Enum):
    exact = "exact"
    possible = "possible"


class DuplicateMatch(BaseModel):
    kind: DuplicateKind
    document_id: int                # the saved record it matches
    reason: str


class DocumentStatus(str, Enum):
    draft = "draft"
    confirmed = "confirmed"


class DocumentOut(BaseModel):
    id: int
    status: DocumentStatus
    filename: str
    content_type: str
    file_hash: str
    provider: Optional[str] = None
    data: Invoice
    issues: list[Issue]
    duplicate: Optional[DuplicateMatch] = None
    created_at: datetime
    confirmed_at: Optional[datetime] = None


class DocumentSummary(BaseModel):
    id: int
    status: DocumentStatus
    filename: str
    vendor_name: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    currency: Optional[str] = None
    total: Optional[float] = None
    issue_count: int = 0
    created_at: datetime


class UploadResponse(BaseModel):
    document: DocumentOut
    file_duplicate: bool = False    # True: this exact file was uploaded before; `document` is the existing record, the LLM was not called
    message: str = ""


class ConfirmRequest(BaseModel):
    override_issues: bool = False   # confirm even though the document still has error-level issues
