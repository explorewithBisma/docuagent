"""The HTTP API end to end with a fake LLM: upload, validation, duplicates, edit, confirm, and every refusal."""

import pytest

import make_samples
from backend.llm.base import LLMRateLimitError
from backend.llm.runner import ResilientExtractor


def upload(client, name, data, filename=None, content_type="application/pdf"):
    return client.post("/documents/upload", files={"file": (filename or f"{name}.pdf", data, content_type)})


def issue_codes(document):
    return {i["code"] for i in document["issues"]}


def test_upload_returns_a_draft_with_data_and_no_issues_for_a_clean_invoice(client, sample_files, fake_llm):
    response = upload(client, "1", sample_files["1_clean_invoice"], "invoice-march.pdf")
    assert response.status_code == 200
    body = response.json()
    document = body["document"]
    assert body["file_duplicate"] is False and document["status"] == "draft" and document["filename"] == "invoice-march.pdf"
    assert document["provider"] == "fake" and document["issues"] == [] and document["duplicate"] is None
    data = document["data"]
    assert data["vendor_name"] == "Northwind Office Supplies Ltd" and data["invoice_number"] == "INV-2024-001" and data["total"] == 139.29
    assert len(data["line_items"]) == 3 and data["overall_confidence"] == 0.95 and data["low_confidence_fields"] == []
    assert fake_llm.calls == 1


def test_the_planted_math_error_and_missing_invoice_number_are_reported(client, sample_files):
    math = upload(client, "3", sample_files["3_math_error"]).json()["document"]
    assert issue_codes(math) == {"total_mismatch"}
    issue = math["issues"][0]
    assert issue["field"] == "total" and issue["severity"] == "error" and "540.00" in issue["message"] and "560.00" in issue["message"]
    missing = upload(client, "4", sample_files["4_missing_invoice_number"]).json()["document"]
    assert issue_codes(missing) == {"missing_invoice_number"} and missing["data"]["invoice_number"] is None      # null, never invented


def test_same_file_twice_returns_the_existing_document_without_calling_the_llm(client, sample_files, fake_llm):
    first = upload(client, "1", sample_files["1_clean_invoice"]).json()
    assert fake_llm.calls == 1
    second = upload(client, "1", sample_files["1_clean_invoice"], "renamed-copy.pdf").json()
    assert second["file_duplicate"] is True and second["document"]["id"] == first["document"]["id"] and "already uploaded" in second["message"]
    assert fake_llm.calls == 1                                                # the LLM was not called again
    assert len(client.get("/documents").json()) == 1


def test_a_second_copy_of_a_saved_invoice_is_an_exact_duplicate_and_cannot_be_confirmed(client, sample_files):
    first = upload(client, "1", sample_files["1_clean_invoice"]).json()["document"]
    assert client.post(f"/documents/{first['id']}/confirm").status_code == 200
    copy = upload(client, "5", sample_files["5_duplicate_invoice"]).json()
    assert copy["file_duplicate"] is False                                    # a different file, so the LLM read it ...
    document = copy["document"]
    assert document["duplicate"]["kind"] == "exact" and document["duplicate"]["document_id"] == first["id"]        # ... and the content matched
    assert "Possible" not in copy["message"]
    blocked = client.post(f"/documents/{document['id']}/confirm", json={"override_issues": True})
    assert blocked.status_code == 409 and blocked.json()["detail"]["duplicate"]["kind"] == "exact"
    assert client.get(f"/documents/{document['id']}").json()["status"] == "draft"


def test_possible_duplicate_when_the_vendor_is_similar_total_equal_and_dates_are_close(client, sample_files, session):
    first = upload(client, "1", sample_files["1_clean_invoice"]).json()["document"]
    client.post(f"/documents/{first['id']}/confirm")
    second = upload(client, "5", sample_files["5_duplicate_invoice"]).json()["document"]
    patched = client.patch(f"/documents/{second['id']}", json={"invoice_number": "OTHER-1", "invoice_date": "2024-03-03", "vendor_name": "Northwind Office Suplies"})
    duplicate = patched.json()["duplicate"]
    assert duplicate["kind"] == "possible" and duplicate["document_id"] == first["id"] and "similar vendor" in duplicate["reason"]
    # a possible duplicate can still be saved
    assert client.post(f"/documents/{second['id']}/confirm").status_code == 200


def test_patch_fixes_fields_and_revalidates(client, sample_files):
    document = upload(client, "3", sample_files["3_math_error"]).json()["document"]
    response = client.patch(f"/documents/{document['id']}", json={"total": 540.0})
    assert response.status_code == 200
    fixed = response.json()
    assert fixed["data"]["total"] == 540.0 and fixed["issues"] == [] and "total" not in fixed["data"]["low_confidence_fields"]          # an edited field is no longer in doubt
    assert fixed["data"]["vendor_name"] == "Blue Harbor Logistics"                                                             # other fields untouched
    cleared = client.patch(f"/documents/{document['id']}", json={"due_date": None}).json()
    assert cleared["data"]["due_date"] is None


def test_patch_rejects_unknown_fields_and_bad_values(client, sample_files):
    document = upload(client, "1", sample_files["1_clean_invoice"]).json()["document"]
    assert client.patch(f"/documents/{document['id']}", json={"colour": "red"}).status_code == 422
    assert client.patch(f"/documents/{document['id']}", json={"total": "lots"}).status_code == 422
    assert client.patch(f"/documents/{document['id']}", json={"doc_type": "contract"}).status_code == 422
    assert client.patch("/documents/999", json={"total": 1}).status_code == 404


def test_confirm_is_blocked_by_errors_unless_overridden(client, sample_files):
    document = upload(client, "3", sample_files["3_math_error"]).json()["document"]
    blocked = client.post(f"/documents/{document['id']}/confirm")
    assert blocked.status_code == 422 and blocked.json()["detail"]["issues"][0]["code"] == "total_mismatch"
    assert client.get(f"/documents/{document['id']}").json()["status"] == "draft"
    confirmed = client.post(f"/documents/{document['id']}/confirm", json={"override_issues": True})
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "confirmed" and confirmed.json()["confirmed_at"]


def test_confirmed_documents_cannot_be_edited_or_confirmed_twice(client, sample_files):
    document = upload(client, "1", sample_files["1_clean_invoice"]).json()["document"]
    assert client.post(f"/documents/{document['id']}/confirm").status_code == 200
    assert client.post(f"/documents/{document['id']}/confirm").status_code == 409
    assert client.patch(f"/documents/{document['id']}", json={"total": 1.0}).status_code == 409


def test_list_filter_get_and_original_file(client, sample_files):
    first = upload(client, "1", sample_files["1_clean_invoice"]).json()["document"]
    upload(client, "2", sample_files["2_receipt"])
    client.post(f"/documents/{first['id']}/confirm")
    everything = client.get("/documents").json()
    assert [d["id"] for d in everything] == [2, 1]                            # newest first
    summary = next(d for d in everything if d["id"] == 1)
    assert summary["vendor_name"] == "Northwind Office Supplies Ltd" and summary["total"] == 139.29 and summary["status"] == "confirmed" and summary["issue_count"] == 0
    assert [d["id"] for d in client.get("/documents", params={"status": "draft"}).json()] == [2]
    assert [d["id"] for d in client.get("/documents", params={"status": "confirmed"}).json()] == [1]
    assert client.get("/documents", params={"status": "nonsense"}).status_code == 422
    assert client.get("/documents/1").json()["data"]["invoice_number"] == "INV-2024-001"
    assert client.get("/documents/99").status_code == 404
    original = client.get("/documents/1/file")
    assert original.status_code == 200 and original.content == sample_files["1_clean_invoice"] and original.headers["content-type"] == "application/pdf"


def test_the_file_type_is_checked_by_content_not_by_name(client, sample_files, fake_llm):
    fake = upload(client, "x", b"MZ this is an executable", "invoice.pdf", "application/pdf")
    assert fake.status_code == 415 and "PDF, PNG and JPG" in fake.json()["detail"]
    assert upload(client, "x", b"plain text", "notes.png", "image/png").status_code == 415
    assert upload(client, "x", b"", "empty.pdf").status_code == 400
    assert fake_llm.calls == 0                                                          # refused before any AI call


def test_size_and_page_limits(client, sample_files, tmp_path, settings):
    big = b"%PDF-1.4 " + b"0" * (settings.max_upload_bytes + 1)
    response = upload(client, "big", big, "big.pdf")
    assert response.status_code == 413 and "10 MB" in response.json()["detail"]
    six_pages = make_samples.build_pdf_with_pages(tmp_path / "six.pdf", 6)
    response = upload(client, "six", six_pages, "six.pdf")
    assert response.status_code == 422 and "6 pages" in response.json()["detail"] and "limit is 5" in response.json()["detail"]
    assert upload(client, "bad", b"%PDF-1.4 damaged", "damaged.pdf").status_code == 422


def test_the_page_limit_is_configurable(client, settings, tmp_path, sample_files):
    settings.max_pages = 2
    three = make_samples.build_pdf_with_pages(tmp_path / "three.pdf", 3)
    assert upload(client, "three", three, "three.pdf").status_code == 422


def test_when_every_ai_provider_fails_the_user_gets_a_clear_503_and_nothing_is_saved(client, sample_files, fake_llm, settings):
    from backend.api.documents import get_extractor
    from backend.main import app

    class Busy(type(fake_llm)):
        def extract(self, source):
            raise LLMRateLimitError("quota exceeded")

    busy = ResilientExtractor([Busy({}), Busy({})], sleep=lambda seconds: None)
    app.dependency_overrides[get_extractor] = lambda: busy
    response = upload(client, "1", sample_files["1_clean_invoice"])
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "both failed or are busy" in detail["message"] and any("quota exceeded" in e for e in detail["errors"])
    assert client.get("/documents").json() == [] and not list(settings.upload_dir.glob("*"))                        # no record and no stray file


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_a_truncated_answer_gives_a_clear_error_and_saves_nothing(client, sample_files, settings, fake_llm, extractor):
    from backend.llm.base import LLMTruncatedOutput

    def cut_off(source):
        raise LLMTruncatedOutput("stopped at the output limit")
    fake_llm.extract = cut_off
    response = upload(client, "1", sample_files["1_clean_invoice"])
    assert response.status_code == 422
    assert response.json()["detail"]["message"] == "Document too long for the free tier, try fewer pages."
    assert client.get("/documents").json() == []                                            # no partial record
    assert not settings.upload_dir.exists() or list(settings.upload_dir.iterdir()) == []   # no stored file either
