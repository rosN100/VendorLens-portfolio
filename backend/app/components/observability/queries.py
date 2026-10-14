"""Bounded, read-only audit lookup by file or operation correlation ID."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine, select

from app.components.observability.models import AuditEvent

MAX_EVENTS = 100


def find_events(
    engine: Engine, *, file_id: UUID | None = None, correlation_id: UUID | None = None, limit: int = 50
) -> list[dict[str, str | None]]:
    if file_id is None and correlation_id is None:
        raise ValueError("Provide a file ID or correlation ID.")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_EVENTS:
        raise ValueError("Limit must be between 1 and 100.")
    if file_id is not None and not isinstance(file_id, UUID):
        raise ValueError("File ID must be a UUID.")
    if correlation_id is not None and not isinstance(correlation_id, UUID):
        raise ValueError("Correlation ID must be a UUID.")
    query = select(
        AuditEvent.event_id, AuditEvent.occurred_at, AuditEvent.action, AuditEvent.outcome,
        AuditEvent.actor, AuditEvent.file_id, AuditEvent.correlation_id, AuditEvent.error_code,
    )
    if file_id is not None:
        query = query.where(AuditEvent.file_id == file_id)
    if correlation_id is not None:
        query = query.where(AuditEvent.correlation_id == correlation_id)
    query = query.order_by(AuditEvent.occurred_at.desc(), AuditEvent.event_id.desc()).limit(limit)
    with engine.connect() as connection:
        rows = connection.execute(query).mappings().all()
    return [
        {key: value.isoformat() if isinstance(value, datetime) else str(value) if isinstance(value, UUID) else value
         for key, value in row.items()}
        for row in rows
    ]
