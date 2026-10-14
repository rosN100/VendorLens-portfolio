"""Append events using the caller's transaction; never commit independently."""

from uuid import UUID

from sqlalchemy import Connection, insert

from app.components.observability.models import AuditEvent
from app.contracts import new_id, utc_now


def record_event(
    connection: Connection,
    *,
    action: str,
    outcome: str,
    actor: str,
    correlation_id: UUID,
    file_id: UUID | None = None,
    error_code: str | None = None,
    parent_event_id: UUID | None = None,
    component: str = "storage",
    case_id: UUID | None = None,
    document_id: UUID | None = None,
    submission_id: UUID | None = None,
    run_id: UUID | None = None,
    ledger_revision: int | None = None,
) -> UUID:
    """Append one component event inside the caller's transaction."""
    if outcome not in {"success", "failure"}:
        raise ValueError("Invalid event outcome")
    event_id = new_id()
    connection.execute(
        insert(AuditEvent).values(
            event_id=event_id,
            schema_version=1,
            occurred_at=utc_now(),
            component=component,
            action=action,
            outcome=outcome,
            actor=actor,
            correlation_id=correlation_id,
            file_id=file_id,
            error_code=error_code,
            parent_event_id=parent_event_id,
            case_id=case_id,
            document_id=document_id,
            submission_id=submission_id,
            run_id=run_id,
            ledger_revision=ledger_revision,
        )
    )
    return event_id
