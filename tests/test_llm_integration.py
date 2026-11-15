"""OPTIONAL integration tests: they call the real Gemini / Groq APIs with a fake sample invoice, and are skipped when there is no key.

    pytest -m integration            (needs GEMINI_API_KEY and/or GROQ_API_KEY in .env)
"""

import pytest

import make_samples
from backend.config import Settings
from backend.llm.gemini import GeminiClient
from backend.llm.groq_client import GroqClient
from backend.services.files import SourceFile

SETTINGS = Settings()
pytestmark = pytest.mark.integration


def check_extraction(invoice):
    expected = make_samples.SAMPLES["1_clean_invoice"]
    assert invoice.vendor_name and "northwind" in invoice.vendor_name.lower()
    assert invoice.invoice_number and invoice.invoice_number.replace(" ", "").upper() == "INV-2024-001"
    assert invoice.invoice_date == expected.invoice_date and invoice.currency == "USD"
    assert invoice.total == pytest.approx(expected.total, abs=0.01) and invoice.subtotal == pytest.approx(expected.subtotal, abs=0.01)
    assert len(invoice.line_items) == 3


@pytest.fixture(scope="module")
def sample_pdf(tmp_path_factory) -> SourceFile:
    data = make_samples.build_all(tmp_path_factory.mktemp("integration"))["1_clean_invoice"]
    return SourceFile(data, "application/pdf", 1)


@pytest.mark.skipif(not SETTINGS.gemini_api_key, reason="GEMINI_API_KEY is not set")
def test_real_gemini_reads_a_sample_invoice(sample_pdf):
    check_extraction(GeminiClient(SETTINGS.gemini_api_key, SETTINGS.gemini_model, SETTINGS.llm_timeout_seconds).extract(sample_pdf))


@pytest.mark.skipif(not SETTINGS.groq_api_key, reason="GROQ_API_KEY is not set")
def test_real_groq_reads_a_sample_invoice(sample_pdf):
    check_extraction(GroqClient(SETTINGS.groq_api_key, SETTINGS.groq_model, SETTINGS.llm_timeout_seconds, max_tokens=SETTINGS.groq_max_tokens).extract(sample_pdf))
