"""Shared identity and time helpers; business version rules stay with their owners."""

from datetime import UTC, datetime
from uuid import UUID, uuid4


def new_id() -> UUID:
    """Return a new opaque UUIDv4 for an application-generated identity."""
    return uuid4()


def utc_now() -> datetime:
    """Return a timezone-aware UTC instant for stored records and diagnostics."""
    return datetime.now(UTC)


def validate_actor(actor: str) -> str:
    """Validate a trace label without pretending it is authenticated identity."""
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 100 or any(ord(c) < 32 for c in actor):
        raise ValueError("Actor must be a nonempty identifier of at most 100 characters.")
    return actor.strip()
