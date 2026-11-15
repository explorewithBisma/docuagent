"""Validation of extracted data: plain Python, no LLM. Returns a list of issues, each with the field, a severity and a plain-English message.

Errors mean the data is probably wrong or incomplete; warnings are worth a look but do not stop the user saving."""

from decimal import Decimal
from typing import Optional

from backend.schemas import DocType, Invoice, Issue, Severity
from backend.services.normalize import parse_iso_date

LOW_CONFIDENCE = 0.6
CHECKED_FOR_CONFIDENCE = ("vendor_name", "invoice_number", "invoice_date", "due_date", "currency", "subtotal", "tax", "total")


def _dec(value: Optional[float]) -> Optional[Decimal]:
    return None if value is None else Decimal(str(value))


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


def validate_invoice(invoice: Invoice, tolerance: float = 0.01) -> list[Issue]:
    tol = Decimal(str(tolerance))
    issues: list[Issue] = []

    def add(code: str, field: str, severity: Severity, message: str) -> None:
        issues.append(Issue(code=code, field=field, severity=severity, message=message))

    # --- required fields -----------------------------------------------------------------------------------------------
    if not invoice.vendor_name:
        add("missing_vendor_name", "vendor_name", Severity.error, "The vendor name is missing.")
    if not invoice.invoice_number:
        # a till receipt often has no invoice number: that is only worth a warning
        severity = Severity.warning if invoice.doc_type == DocType.receipt else Severity.error
        add("missing_invoice_number", "invoice_number", severity, "The invoice number is missing.")
    if not invoice.invoice_date:
        add("missing_invoice_date", "invoice_date", Severity.error, "The invoice date is missing.")
    if invoice.total is None:
        add("missing_total", "total", Severity.error, "The total amount is missing.")
    if not invoice.currency:
        add("missing_currency", "currency", Severity.warning, "The currency is missing.")
    if invoice.doc_type is None:
        add("missing_doc_type", "doc_type", Severity.warning, "It is not clear whether this is an invoice or a receipt.")
    if not invoice.line_items:
        add("no_line_items", "line_items", Severity.warning, "No line items were found.")

    # --- dates ---------------------------------------------------------------------------------------------------------
    invoice_date = parse_iso_date(invoice.invoice_date)
    due_date = parse_iso_date(invoice.due_date)
    if invoice.invoice_date and invoice_date is None:
        add("invalid_invoice_date", "invoice_date", Severity.error, f"The invoice date '{invoice.invoice_date}' is not a valid date (use YYYY-MM-DD).")
    if invoice.due_date and due_date is None:
        add("invalid_due_date", "due_date", Severity.error, f"The due date '{invoice.due_date}' is not a valid date (use YYYY-MM-DD).")
    if invoice_date and due_date and due_date < invoice_date:
        add("due_before_invoice", "due_date", Severity.error, f"The due date ({due_date}) is before the invoice date ({invoice_date}).")

    # --- line items ----------------------------------------------------------------------------------------------------
    amounts = [_dec(item.amount) for item in invoice.line_items]
    for number, item in enumerate(invoice.line_items, start=1):
        quantity, unit_price, amount = _dec(item.quantity), _dec(item.unit_price), _dec(item.amount)
        if quantity is not None and unit_price is not None and amount is not None and abs(quantity * unit_price - amount) > tol:
            add("line_amount_mismatch", "line_items", Severity.warning,
                f"Line {number}: {quantity:g} x {_money(unit_price)} = {_money(quantity * unit_price)}, but the amount says {_money(amount)}.")
    subtotal, tax, total = _dec(invoice.subtotal), _dec(invoice.tax), _dec(invoice.total)
    if invoice.line_items and subtotal is not None:
        if any(a is None for a in amounts):
            add("line_amount_missing", "line_items", Severity.warning, "Some line items have no amount, so they cannot be added up to check the subtotal.")
        else:
            line_sum = sum(amounts, Decimal(0))
            if abs(line_sum - subtotal) > tol:
                add("line_items_sum_mismatch", "subtotal", Severity.error,
                    f"The line items add up to {_money(line_sum)}, but the subtotal says {_money(subtotal)} (difference {_money(abs(line_sum - subtotal))}).")

    # --- totals --------------------------------------------------------------------------------------------------------
    if subtotal is not None and total is not None:
        if tax is None:
            if abs(subtotal - total) > tol:
                add("tax_missing", "tax", Severity.warning, f"The total ({_money(total)}) differs from the subtotal ({_money(subtotal)}) but no tax is stated.")
        elif abs(subtotal + tax - total) > tol:
            add("total_mismatch", "total", Severity.error,
                f"Subtotal {_money(subtotal)} + tax {_money(tax)} = {_money(subtotal + tax)}, but the total says {_money(total)} "
                f"(difference {_money(abs(subtotal + tax - total))}).")
    for name, value in (("subtotal", subtotal), ("tax", tax), ("total", total)):
        if value is not None and value < 0:
            add("negative_amount", name, Severity.warning, f"The {name} is negative ({_money(value)}). Is this a credit note?")

    # --- how sure the model was ----------------------------------------------------------------------------------------
    for name in CHECKED_FOR_CONFIDENCE:
        confidence = getattr(invoice.confidence, name)
        if getattr(invoice, name) is not None and confidence is not None and confidence < LOW_CONFIDENCE:
            add("low_confidence", name, Severity.warning, f"The {name.replace('_', ' ')} was hard to read (confidence {confidence:.0%}). Please check it.")
    return issues


def has_errors(issues: list[Issue]) -> bool:
    return any(issue.severity == Severity.error for issue in issues)
