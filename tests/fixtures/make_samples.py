"""Generates 5 FAKE sample invoices / receipts as PDFs (invented companies and numbers only), each with a known, planted property:

  1_clean_invoice.pdf              everything adds up
  2_receipt.pdf                    a till receipt (no due date)
  3_math_error.pdf                 subtotal + tax does not equal the total
  4_missing_invoice_number.pdf     no invoice number
  5_duplicate_invoice.pdf          the same invoice as #1 written differently (vendor and number spelled another way) -> exact duplicate

Run it to write the files:   python tests/fixtures/make_samples.py [output folder]
The tests use the same definitions: `SAMPLES` holds the Invoice each PDF contains (what a perfect extraction returns)."""

import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.schemas import DocType, Invoice, LineItem  # noqa: E402


def _invoice(**fields) -> Invoice:
    return Invoice(overall_confidence=0.95, **fields)


SAMPLES: dict[str, Invoice] = {
    "1_clean_invoice": _invoice(
        doc_type=DocType.invoice, vendor_name="Northwind Office Supplies Ltd", vendor_tax_id="TAX-100200", invoice_number="INV-2024-001",
        invoice_date="2024-03-01", due_date="2024-03-31", currency="USD",
        line_items=[LineItem(description="Paper A4 (box)", quantity=10, unit_price=4.50, amount=45.00),
                    LineItem(description="Stapler", quantity=2, unit_price=12.00, amount=24.00),
                    LineItem(description="Printer ink", quantity=3, unit_price=19.99, amount=59.97)],
        subtotal=128.97, tax=10.32, total=139.29),
    "2_receipt": _invoice(
        doc_type=DocType.receipt, vendor_name="Corner Cafe", invoice_number="R-5521", invoice_date="2024-03-05", currency="USD",
        line_items=[LineItem(description="Coffee", quantity=2, unit_price=3.50, amount=7.00),
                    LineItem(description="Muffin", quantity=1, unit_price=2.75, amount=2.75)],
        subtotal=9.75, tax=0.78, total=10.53),
    "3_math_error": _invoice(
        doc_type=DocType.invoice, vendor_name="Blue Harbor Logistics", invoice_number="INV-77", invoice_date="2024-03-10", due_date="2024-04-09", currency="EUR",
        line_items=[LineItem(description="Freight Hamburg - Rotterdam", quantity=2, unit_price=250.00, amount=500.00)],
        subtotal=500.00, tax=40.00, total=560.00),                       # 500 + 40 = 540, not 560: the planted error
    "4_missing_invoice_number": _invoice(
        doc_type=DocType.invoice, vendor_name="Sunrise Printing Co", invoice_date="2024-03-12", due_date="2024-04-11", currency="GBP",
        line_items=[LineItem(description="Flyers (500)", quantity=1, unit_price=100.00, amount=100.00)],
        subtotal=100.00, tax=8.00, total=108.00),                        # no invoice number: the planted gap
    "5_duplicate_invoice": _invoice(
        doc_type=DocType.invoice, vendor_name="NORTHWIND OFFICE SUPPLIES LTD.", vendor_tax_id="TAX-100200", invoice_number="inv 2024 001",
        invoice_date="2024-03-01", due_date="2024-03-31", currency="USD",
        line_items=[LineItem(description="Paper A4 (box)", quantity=10, unit_price=4.50, amount=45.00),
                    LineItem(description="Stapler", quantity=2, unit_price=12.00, amount=24.00),
                    LineItem(description="Printer ink", quantity=3, unit_price=19.99, amount=59.97)],
        subtotal=128.97, tax=10.32, total=139.29),
}


def _text(value) -> str:
    return "" if value is None else str(value)


def render_pdf(invoice: Invoice, path: Path, footer: str = "") -> None:
    """Draw the invoice as a simple one-page PDF (printed text, like a real document)."""
    pdf = canvas.Canvas(str(path), pagesize=A4)
    y = 800
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(50, y, (invoice.doc_type.value if invoice.doc_type else "document").upper())
    pdf.setFont("Helvetica", 11)
    for label, value in (("Vendor", invoice.vendor_name), ("Tax ID", invoice.vendor_tax_id), ("Invoice No", invoice.invoice_number),
                         ("Date", invoice.invoice_date), ("Due date", invoice.due_date), ("Currency", invoice.currency)):
        if value:
            y -= 18
            pdf.drawString(50, y, f"{label}: {value}")
    y -= 36
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(50, y, "Description")
    pdf.drawString(300, y, "Qty")
    pdf.drawString(360, y, "Unit price")
    pdf.drawString(460, y, "Amount")
    pdf.setFont("Helvetica", 11)
    for item in invoice.line_items:
        y -= 18
        pdf.drawString(50, y, _text(item.description))
        pdf.drawString(300, y, f"{item.quantity:g}" if item.quantity is not None else "")
        pdf.drawString(360, y, f"{item.unit_price:.2f}" if item.unit_price is not None else "")
        pdf.drawString(460, y, f"{item.amount:.2f}" if item.amount is not None else "")
    y -= 36
    for label, value in (("Subtotal", invoice.subtotal), ("Tax", invoice.tax), ("Total", invoice.total)):
        if value is not None:
            pdf.drawString(360, y, f"{label}: {value:.2f}")
            y -= 18
    if footer:
        pdf.drawString(50, 60, footer)
    pdf.save()


def build_all(folder: Path) -> dict[str, bytes]:
    """Write the 5 sample PDFs into `folder` and return {sample name: file bytes}."""
    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, invoice in SAMPLES.items():
        path = folder / f"{name}.pdf"
        render_pdf(invoice, path, footer="Copy of the invoice sent on 2024-03-02" if name == "5_duplicate_invoice" else "")
        out[name] = path.read_bytes()
    return out


def build_pdf_with_pages(path: Path, pages: int) -> bytes:
    """A PDF with `pages` blank-ish pages (for the page-limit test)."""
    pdf = canvas.Canvas(str(path), pagesize=A4)
    for number in range(1, pages + 1):
        pdf.drawString(50, 800, f"Fake page {number}")
        pdf.showPage()
    pdf.save()
    return path.read_bytes()


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "samples"
    files = build_all(target)
    print(f"Wrote {len(files)} sample PDFs to {target}")
