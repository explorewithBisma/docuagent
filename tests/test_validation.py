"""Validation rules (plain Python, no LLM)."""

import pytest

from backend.schemas import Confidence, DocType, Invoice, LineItem, Severity
from backend.services.validation import has_errors, validate_invoice


def good() -> Invoice:
    return Invoice(doc_type=DocType.invoice, vendor_name="Acme", invoice_number="A-1", invoice_date="2024-03-01", due_date="2024-03-31", currency="usd",
                   line_items=[LineItem(description="x", quantity=2, unit_price=10.0, amount=20.0), LineItem(description="y", quantity=1, unit_price=5.0, amount=5.0)],
                   subtotal=25.0, tax=2.5, total=27.5)


def codes(invoice: Invoice, **kwargs) -> dict[str, Severity]:
    return {i.code: i.severity for i in validate_invoice(invoice, **kwargs)}


def test_a_correct_invoice_has_no_issues():
    assert validate_invoice(good()) == []
    assert good().currency == "USD"                                           # currency codes are upper-cased


def test_line_items_must_add_up_to_the_subtotal():
    invoice = good()
    invoice.subtotal = 30.0
    issues = validate_invoice(invoice)
    mismatch = next(i for i in issues if i.code == "line_items_sum_mismatch")
    assert mismatch.field == "subtotal" and mismatch.severity == Severity.error and "25.00" in mismatch.message and "30.00" in mismatch.message


def test_subtotal_plus_tax_must_equal_the_total_within_a_tolerance():
    invoice = good()
    invoice.total = 27.505                                                    # half a cent off: rounding
    assert validate_invoice(invoice) == []
    invoice.total = 30.0
    issue = next(i for i in validate_invoice(invoice) if i.code == "total_mismatch")
    assert issue.field == "total" and issue.severity == Severity.error and "27.50" in issue.message
    assert "total_mismatch" not in codes(invoice, tolerance=5.0)              # the tolerance is configurable


def test_missing_tax_is_a_warning_only_when_the_total_differs_from_the_subtotal():
    invoice = good()
    invoice.tax = None
    assert codes(invoice) == {"tax_missing": Severity.warning}
    invoice.total = 25.0
    assert codes(invoice) == {}


def test_required_fields():
    empty = validate_invoice(Invoice())
    assert {i.code for i in empty} >= {"missing_vendor_name", "missing_invoice_number", "missing_invoice_date", "missing_total", "missing_currency", "missing_doc_type", "no_line_items"}
    assert has_errors(empty)
    receipt = Invoice(doc_type=DocType.receipt, vendor_name="Cafe", invoice_date="2024-03-01", currency="USD", total=5.0)
    assert codes(receipt)["missing_invoice_number"] == Severity.warning       # a till receipt often has no number


def test_blank_strings_from_the_model_are_null_not_values():
    invoice = Invoice(vendor_name="  ", invoice_number="", invoice_date=" ")
    assert invoice.vendor_name is None and invoice.invoice_number is None and invoice.invoice_date is None


def test_dates_must_be_real_and_due_date_not_before_invoice_date():
    invoice = good()
    invoice.invoice_date = "2024-02-30"
    assert codes(invoice)["invalid_invoice_date"] == Severity.error
    invoice = good()
    invoice.due_date = "2024-02-15"
    issue = next(i for i in validate_invoice(invoice) if i.code == "due_before_invoice")
    assert issue.field == "due_date" and issue.severity == Severity.error
    invoice.due_date = "2024-03-01"                                           # the same day is fine
    assert validate_invoice(invoice) == []
    invoice.due_date = "next month"
    assert codes(invoice)["invalid_due_date"] == Severity.error


def test_a_line_that_does_not_multiply_out_is_a_warning():
    invoice = good()
    invoice.line_items[0].amount = 21.0
    invoice.subtotal, invoice.total = 26.0, 28.5
    found = codes(invoice)
    assert found == {"line_amount_mismatch": Severity.warning}


def test_lines_without_amounts_cannot_be_checked():
    invoice = good()
    invoice.line_items[1].amount = None
    assert codes(invoice)["line_amount_missing"] == Severity.warning


def test_negative_amounts_and_low_confidence_are_warnings():
    invoice = good()
    invoice.subtotal, invoice.tax, invoice.total = -25.0, -2.5, -27.5
    invoice.line_items = []
    assert codes(invoice)["negative_amount"] == Severity.warning
    invoice = good()
    invoice.confidence = Confidence(total=0.3, vendor_name=0.9)
    low = [i for i in validate_invoice(invoice) if i.code == "low_confidence"]
    assert [i.field for i in low] == ["total"] and low[0].severity == Severity.warning


def test_confidence_values_are_kept_between_0_and_1():
    assert Confidence(total=1.7, tax=-0.2).total == 1.0 and Confidence(total=1.7, tax=-0.2).tax == 0.0


def test_the_sample_documents_have_exactly_the_planted_problems(sample_files):
    import make_samples
    found = {name: {i.code for i in validate_invoice(invoice)} for name, invoice in make_samples.SAMPLES.items()}
    assert found["1_clean_invoice"] == set() and found["5_duplicate_invoice"] == set()
    assert found["2_receipt"] == set()
    assert found["3_math_error"] == {"total_mismatch"}
    assert found["4_missing_invoice_number"] == {"missing_invoice_number"}
