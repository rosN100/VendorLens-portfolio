"""Read-only, bounded inspection of durable audit events on the isolated database."""

from uuid import uuid4

import pytest
from sqlalchemy import select, func

from app.components.observability.models import AuditEvent
from app.components.observability.queries import find_events
from app.components.observability.recorder import record_event

pytestmark = pytest.mark.integration


def test_query_by_file_and_correlation_does_not_change_history(pg_engine):
    file_id, correlation_id = uuid4(), uuid4()
    with pg_engine.begin() as connection:
        record_event(connection, action="file.saved", outcome="success", actor="pilot_analyst",
                     correlation_id=correlation_id, file_id=file_id)
        record_event(connection, action="file.read", outcome="success", actor="pilot_analyst",
                     correlation_id=uuid4(), file_id=file_id)
    with pg_engine.connect() as connection:
        before = connection.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.file_id == file_id))
    by_file = find_events(pg_engine, file_id=file_id, limit=1)
    by_operation = find_events(pg_engine, correlation_id=correlation_id)
    assert len(by_file) == 1
    assert len(by_operation) == 1
    assert by_operation[0]["action"] == "file.saved"
    assert by_operation[0]["file_id"] == str(file_id)
    with pg_engine.connect() as connection:
        after = connection.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.file_id == file_id))
    assert before == after == 2


def test_query_requires_selector_and_bounded_limit(pg_engine):
    with pytest.raises(ValueError, match="Provide"):
        find_events(pg_engine)
    with pytest.raises(ValueError, match="Limit"):
        find_events(pg_engine, file_id=uuid4(), limit=101)
