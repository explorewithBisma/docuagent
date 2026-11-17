"""Timestamps: stored as UTC and sent as timezone-aware UTC ISO strings ("...Z"), the same on SQLite and Postgres."""

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from backend.models import Document, UTCDateTime
from backend.schemas import DocumentSummary
from backend.timeutil import as_utc

UTC_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
PLUS_FIVE = timezone(timedelta(hours=5))


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# --- the helper and the column type ---------------------------------------------------------------------------------------------------
def test_as_utc_makes_every_datetime_aware_utc():
    assert as_utc(datetime(2026, 11, 17, 18, 32)) == datetime(2026, 11, 17, 18, 32, tzinfo=timezone.utc)                 # naive: already UTC
    assert as_utc(datetime(2026, 11, 17, 18, 32, tzinfo=PLUS_FIVE)) == datetime(2026, 11, 17, 13, 32, tzinfo=timezone.utc)   # converted, not just relabelled
    assert as_utc(datetime(2026, 11, 17, 18, 32, tzinfo=timezone.utc)).utcoffset() == timedelta(0)


def test_the_column_type_returns_utc_even_when_the_database_returns_another_zone():
    column = UTCDateTime()
    postgres_style = datetime(2026, 11, 17, 18, 32, tzinfo=PLUS_FIVE)             # Postgres answers in the connection's time zone
    result = column.process_result_value(postgres_style, None)
    assert result == datetime(2026, 11, 17, 13, 32, tzinfo=timezone.utc) and result.utcoffset() == timedelta(0)
    assert column.process_result_value(datetime(2026, 11, 17, 18, 32), None).tzinfo is timezone.utc                     # SQLite: naive in, UTC out
    assert column.process_bind_param(postgres_style, None) == datetime(2026, 11, 17, 13, 32, tzinfo=timezone.utc)
    assert column.process_bind_param(None, None) is None and column.process_result_value(None, None) is None


def new_document(**fields) -> Document:
    return Document(filename="x.pdf", content_type="application/pdf", file_hash=fields.pop("file_hash", "h" * 64), file_path="x", data={}, **fields)


def test_a_value_written_with_an_offset_is_stored_as_utc_and_read_back_as_utc(session):
    doc = new_document(created_at=datetime(2026, 11, 17, 18, 32, tzinfo=PLUS_FIVE))      # 13:32 UTC
    session.add(doc)
    session.commit()
    stored = session.execute(text("SELECT created_at FROM documents WHERE id = :id"), {"id": doc.id}).scalar_one()
    assert str(stored).startswith("2026-11-17 13:32")                                    # SQLite would have kept 18:32 and dropped the offset
    session.expire_all()
    loaded = session.scalars(select(Document)).one()
    assert loaded.created_at == datetime(2026, 11, 17, 13, 32, tzinfo=timezone.utc) and loaded.created_at.tzinfo is not None


def test_the_default_timestamps_are_utc_and_unset_ones_stay_none(session):
    doc = new_document()
    session.add(doc)
    session.commit()
    session.expire_all()
    loaded = session.scalars(select(Document)).one()
    assert loaded.created_at.utcoffset() == timedelta(0) and abs(datetime.now(timezone.utc) - loaded.created_at) < timedelta(minutes=2)
    assert loaded.confirmed_at is None


# --- what the API sends ------------------------------------------------------------------------------------------------------------------
def test_the_api_sends_utc_timestamps_ending_in_z_from_every_endpoint(client, session, sample_files):
    uploaded = client.post("/documents/upload", files={"file": ("a.pdf", sample_files["1_clean_invoice"], "application/pdf")}).json()["document"]
    session.expire_all()                                                                  # force a real re-read from the database
    listed = client.get("/documents").json()[0]
    detail = client.get(f"/documents/{uploaded['id']}").json()
    for stamp in (uploaded["created_at"], listed["created_at"], detail["created_at"]):
        assert UTC_ISO.match(stamp), stamp
        assert abs(datetime.now(timezone.utc) - parse(stamp)) < timedelta(minutes=2)
    assert detail["confirmed_at"] is None

    session.expire_all()
    confirmed = client.post(f"/documents/{uploaded['id']}/confirm").json()
    assert UTC_ISO.match(confirmed["confirmed_at"]) and UTC_ISO.match(confirmed["created_at"])
    session.expire_all()
    assert UTC_ISO.match(client.get(f"/documents/{uploaded['id']}").json()["confirmed_at"])


def test_rows_saved_before_this_fix_are_read_as_utc(client, session, sample_files):
    """Existing SQLite rows hold naive UTC clock times; they must come out as UTC, not shifted."""
    doc_id = client.post("/documents/upload", files={"file": ("a.pdf", sample_files["1_clean_invoice"], "application/pdf")}).json()["document"]["id"]
    session.execute(text("UPDATE documents SET created_at = '2024-03-01 10:00:00.000000', confirmed_at = '2024-03-02 11:30:00.000000' WHERE id = :id"), {"id": doc_id})
    session.commit()
    session.expire_all()
    assert client.get("/documents").json()[0]["created_at"] == "2024-03-01T10:00:00Z"
    detail = client.get(f"/documents/{doc_id}").json()
    assert detail["created_at"] == "2024-03-01T10:00:00Z" and detail["confirmed_at"] == "2024-03-02T11:30:00Z"


def test_the_response_schemas_themselves_always_write_utc():
    summary = DocumentSummary(id=1, status="draft", filename="x.pdf", created_at=datetime(2026, 11, 17, 18, 32))                  # naive
    assert '"created_at":"2026-11-17T18:32:00Z"' in summary.model_dump_json()
    other_zone = DocumentSummary(id=1, status="draft", filename="x.pdf", created_at=datetime(2026, 11, 17, 18, 32, tzinfo=PLUS_FIVE))
    assert '"created_at":"2026-11-17T13:32:00Z"' in other_zone.model_dump_json()
