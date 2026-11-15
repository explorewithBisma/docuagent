"""Normalisation used by the duplicate check: the same vendor or invoice number written differently must compare equal."""

import re
import unicodedata
from datetime import date
from typing import Optional

COMPANY_WORDS = {"ltd", "limited", "llc", "inc", "incorporated", "co", "corp", "corporation", "company", "gmbh", "pvt", "private", "fze", "fzc", "llp", "plc",
                 "sa", "pty", "the", "and"}


def normalize_invoice_number(value: Optional[str]) -> Optional[str]:
    """Uppercase, with spaces, dashes, slashes and dots removed: 'INV-001', 'inv 001' and 'INV/001.' are all 'INV001'."""
    if not value:
        return None
    text = re.sub(r"[\s\-/.]+", "", value.upper())
    return text or None


def normalize_vendor(value: Optional[str]) -> Optional[str]:
    """Lower case, no accents or punctuation, company suffixes dropped: 'Acme Trading L.L.C.' and 'ACME trading' are 'acme trading'."""
    if not value:
        return None
    text = unicodedata.normalize("NFKD", value.casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text.replace(".", ""))             # "L.L.C." -> "llc"
    words = [w for w in text.split() if w not in COMPANY_WORDS]
    return " ".join(words) or None


def parse_iso_date(value: Optional[str]) -> Optional[date]:
    """A YYYY-MM-DD string as a date, or None when it is missing or not a real date (e.g. 2024-02-30)."""
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None
