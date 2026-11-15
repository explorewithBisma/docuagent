"""Duplicate logic: exact (normalised vendor + invoice number) and possible (similar vendor + same total + dates within N days)."""

from backend.models import Document
from backend.schemas import DuplicateKind, Invoice
from backend.services.duplicates import find_duplicate, vendors_similar
from backend.services.normalize import normalize_invoice_number, normalize_vendor


def save(session, vendor="Northwind Office Supplies Ltd", number="INV-001", total=100.0, date="2024-03-01", status="confirmed") -> Document:
    invoice = Invoice(vendor_name=vendor, invoice_number=number, invoice_date=date, total=total)
    doc = Document(status=status, filename="x.pdf", content_type="application/pdf", file_hash=f"hash{session.query(Document).count()}", file_path="x",
                   data=invoice.model_dump(mode="json"), issues=[], vendor_key=normalize_vendor(vendor), invoice_number_key=normalize_invoice_number(number),
                   total=total, invoice_date=date)
    session.add(doc)
    session.commit()
    return doc


def check(session, settings, **fields):
    return find_duplicate(session, Invoice(**fields), settings)


def test_exact_duplicate_ignores_case_punctuation_company_suffix_and_number_separators(session, settings):
    saved = save(session)
    match = check(session, settings, vendor_name="NORTHWIND OFFICE SUPPLIES LTD.", invoice_number="inv 001", total=999.0, invoice_date="2030-01-01")
    assert match.kind == DuplicateKind.exact and match.document_id == saved.id
    assert "already saved" in match.reason and "inv 001" in match.reason


def test_same_invoice_number_from_another_vendor_is_not_a_duplicate(session, settings):
    save(session)
    assert check(session, settings, vendor_name="Blue Harbor Logistics", invoice_number="INV-001", total=100.0, invoice_date="2024-03-01") is None


def test_same_vendor_other_number_and_other_total_is_not_a_duplicate(session, settings):
    save(session)
    assert check(session, settings, vendor_name="Northwind Office Supplies", invoice_number="INV-002", total=250.0, invoice_date="2024-03-01") is None


def test_possible_duplicate_needs_similar_vendor_same_total_and_dates_within_the_window(session, settings):
    saved = save(session)
    same_day = check(session, settings, vendor_name="Northwind Office Suplies", invoice_number="OTHER-9", total=100.0, invoice_date="2024-03-01")      # typo in the vendor
    assert same_day.kind == DuplicateKind.possible and same_day.document_id == saved.id and "the same day" in same_day.reason
    three_days = check(session, settings, vendor_name="Northwind Office Supplies", invoice_number=None, total=100.0, invoice_date="2024-03-04")
    assert three_days.kind == DuplicateKind.possible and "3 days apart" in three_days.reason
    assert check(session, settings, vendor_name="Northwind Office Supplies", invoice_number=None, total=100.0, invoice_date="2024-03-05") is None   # 4 days: outside the window
    assert check(session, settings, vendor_name="Northwind Office Supplies", invoice_number=None, total=100.01, invoice_date="2024-03-01") is None  # another total
    assert check(session, settings, vendor_name="Totally Different Ltd", invoice_number=None, total=100.0, invoice_date="2024-03-01") is None       # another vendor
    assert check(session, settings, vendor_name="Northwind Office Supplies", invoice_number=None, total=100.0, invoice_date=None) is None            # no date: not enough evidence


def test_the_date_window_is_configurable(session, settings):
    save(session)
    wide = settings.model_copy(update={"duplicate_date_window_days": 10})
    assert find_duplicate(session, Invoice(vendor_name="Northwind Office Supplies", total=100.0, invoice_date="2024-03-09"), wide).kind == DuplicateKind.possible
    narrow = settings.model_copy(update={"duplicate_date_window_days": 0})
    assert find_duplicate(session, Invoice(vendor_name="Northwind Office Supplies", total=100.0, invoice_date="2024-03-02"), narrow) is None


def test_exact_wins_over_possible_and_only_confirmed_documents_count(session, settings):
    draft = save(session, status="draft")
    assert check(session, settings, vendor_name="Northwind Office Supplies Ltd", invoice_number="INV-001", total=100.0, invoice_date="2024-03-01") is None
    possible = save(session, number="OTHER", total=50.0, date="2024-03-02")
    exact = save(session, number="INV-777", total=50.0, date="2024-03-02")
    match = check(session, settings, vendor_name="Northwind Office Supplies Ltd", invoice_number="INV 777", total=50.0, invoice_date="2024-03-02")
    assert match.kind == DuplicateKind.exact and match.document_id == exact.id
    assert draft.id not in (possible.id, exact.id)


def test_a_document_is_never_a_duplicate_of_itself(session, settings):
    saved = save(session)
    assert find_duplicate(session, Invoice.model_validate(saved.data), settings, exclude_id=saved.id) is None


def test_vendor_similarity_helper(settings):
    assert vendors_similar("acme", "acme trading", 0.85) and vendors_similar("northwind office supplies", "northwind office suplies", 0.85)
    assert not vendors_similar("acme", "zeta", 0.85) and not vendors_similar(None, "acme", 0.85)
    assert not vendors_similar("abc", "abcd", 0.99)                  # too short to count as 'inside'
