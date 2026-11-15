"""File-type detection by content, page counting, page images for Groq, and the normalisation used by the duplicate check."""

import pytest

from backend.services import files
from backend.services.normalize import normalize_invoice_number, normalize_vendor, parse_iso_date

PNG = b"\x89PNG\r\n\x1a\n" + b"rest of a png"
JPEG = b"\xff\xd8\xff\xe0" + b"rest of a jpeg"


def test_file_type_comes_from_the_first_bytes_not_the_name(sample_files):
    assert files.detect_file_type(sample_files["1_clean_invoice"]).mime == "application/pdf"
    assert files.detect_file_type(PNG) == files.FileType("png", "image/png")
    assert files.detect_file_type(JPEG) == files.FileType("jpg", "image/jpeg")
    assert files.detect_file_type(b"MZ\x90\x00 an exe renamed invoice.pdf") is None
    assert files.detect_file_type(b"just some text") is None and files.detect_file_type(b"") is None


def test_hash_is_sha256():
    assert files.sha256_hex(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_page_count_and_damaged_pdf(sample_files, tmp_path):
    import make_samples
    pdf = files.detect_file_type(sample_files["1_clean_invoice"])
    assert files.count_pages(sample_files["1_clean_invoice"], pdf) == 1
    assert files.count_pages(make_samples.build_pdf_with_pages(tmp_path / "six.pdf", 6), pdf) == 6
    assert files.count_pages(PNG, files.FileType("png", "image/png")) == 1
    with pytest.raises(files.UnreadableFile):
        files.count_pages(b"%PDF-1.4 this is not really a pdf", pdf)


def test_pdf_pages_become_png_images_for_vision_models(sample_files, tmp_path):
    import make_samples
    source = files.SourceFile(make_samples.build_pdf_with_pages(tmp_path / "three.pdf", 3), "application/pdf", 3)
    images = source.page_images()
    assert len(images) == 3 and all(mime == "image/png" and data.startswith(b"\x89PNG") for data, mime in images)
    image_source = files.SourceFile(JPEG, "image/jpeg")
    assert image_source.page_images() == [(JPEG, "image/jpeg")]                 # an uploaded image is sent as it is


@pytest.mark.parametrize("a, b", [("INV-001", "inv 001"), ("INV/001.", "INV 001"), ("  inv-0 0 1 ", "INV001"), ("A.B-C/D E", "abcde")])
def test_invoice_numbers_compare_equal_when_only_case_and_separators_differ(a, b):
    assert normalize_invoice_number(a) == normalize_invoice_number(b)


def test_invoice_numbers_that_differ_stay_different():
    assert normalize_invoice_number("INV-001") != normalize_invoice_number("INV-002")
    assert normalize_invoice_number(None) is None and normalize_invoice_number(" - / . ") is None


def test_vendor_names_drop_case_punctuation_accents_and_company_suffixes():
    assert normalize_vendor("Northwind Office Supplies Ltd") == normalize_vendor("NORTHWIND OFFICE SUPPLIES LTD.") == "northwind office supplies"
    assert normalize_vendor("Acme Trading L.L.C.") == normalize_vendor("ACME trading") == "acme trading"
    assert normalize_vendor("Café Zürich GmbH") == "cafe zurich"
    assert normalize_vendor("") is None and normalize_vendor("Ltd") is None


def test_dates():
    assert str(parse_iso_date("2024-03-01")) == "2024-03-01"
    assert parse_iso_date("2024-02-30") is None and parse_iso_date("1 March 2024") is None and parse_iso_date(None) is None
