"""Looking up one record, and the time now: the ways every backend module reads a
row and stamps a time."""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import Connection, Table, select


def now() -> str:
    """Return the time now in UTC, to the microsecond, so that records sort in the
    order they were made."""
    return datetime.now(UTC).isoformat(timespec="microseconds")


def lookup(conn: Connection, table: Table, record_id: str | int) -> dict | None:
    """Return one row by its id, or None."""
    [key] = table.primary_key.columns
    row = conn.execute(select(table).where(key == record_id)).mappings().first()
    return dict(row) if row else None


def find(conn: Connection, table: Table, record_id: str | int) -> dict:
    """Return one row by its id, or raise a 404."""
    row = lookup(conn, table, record_id)
    if row is None:
        raise HTTPException(404, f"There is no record {record_id}.")
    return row
