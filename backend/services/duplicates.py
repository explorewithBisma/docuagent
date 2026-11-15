"""Duplicate check against the documents the user has already CONFIRMED.

  exact     same normalised vendor name + same normalised invoice number
  possible  a similar vendor name + the same total + dates within N days (all three needed)
"""

from difflib import SequenceMatcher
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.models import Document
from backend.schemas import DuplicateKind, DuplicateMatch, Invoice
from backend.services.normalize import normalize_invoice_number, normalize_vendor, parse_iso_date

TOTAL_TOLERANCE = 0.005


def vendors_similar(a: Optional[str], b: Optional[str], threshold: float) -> bool:
    """Normalised vendor names that are equal, one inside the other (acme / acme trading), or alike enough (typos, spacing)."""
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = sorted((a, b), key=len)
    if len(shorter) >= 4 and shorter in longer:
        return True
    return SequenceMatcher(None, a, b).ratio() >= threshold


def find_duplicate(session: Session, invoice: Invoice, settings: Settings, exclude_id: Optional[int] = None) -> Optional[DuplicateMatch]:
    """The first confirmed document that duplicates `invoice` (exact matches win over possible ones), or None."""
    vendor = normalize_vendor(invoice.vendor_name)
    number = normalize_invoice_number(invoice.invoice_number)
    query = select(Document).where(Document.status == "confirmed")
    if exclude_id is not None:
        query = query.where(Document.id != exclude_id)
    saved = list(session.scalars(query.order_by(Document.id)))

    if vendor and number:
        for doc in saved:
            if doc.vendor_key == vendor and doc.invoice_number_key == number:
                return DuplicateMatch(kind=DuplicateKind.exact, document_id=doc.id,
                                      reason=f"Document #{doc.id} is already saved with the same vendor ({invoice.vendor_name}) "
                                             f"and invoice number ({invoice.invoice_number}).")

    date = parse_iso_date(invoice.invoice_date)
    if vendor and invoice.total is not None and date:
        for doc in saved:
            other_date = parse_iso_date(doc.invoice_date)
            if (doc.total is not None and other_date and abs(doc.total - invoice.total) <= TOTAL_TOLERANCE
                    and abs((other_date - date).days) <= settings.duplicate_date_window_days
                    and vendors_similar(vendor, doc.vendor_key, settings.vendor_similarity)):
                gap = abs((other_date - date).days)
                when = "the same day" if gap == 0 else f"{gap} day{'s' if gap != 1 else ''} apart"
                return DuplicateMatch(kind=DuplicateKind.possible, document_id=doc.id,
                                      reason=f"Document #{doc.id} has a similar vendor, the same total ({invoice.total:,.2f}) and a date {when}.")
    return None
