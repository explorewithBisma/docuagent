"""Timestamps are stored and sent as UTC. SQLite has no time zones (it gives back naive datetimes) and Postgres gives them back in the
connection's zone, so the same row would otherwise come out differently on the two databases."""

from datetime import datetime, timezone


def as_utc(value: datetime) -> datetime:
    """A timezone-aware datetime in UTC. A naive one is taken to already be UTC (that is what this app has always stored)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
