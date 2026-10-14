"""Case persistence, retry and audit behavior on isolated PostgreSQL."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select

from app.components.case_intake.api import get_case_service
from app.components.case_intake.models import Case, CaseSubmission
from app.components.case_intake.service import CaseService
from app.components.observability.models import AuditEvent
from app.main import app

pytestmark = pytest.mark.integration


def payload(request_id, **changes):
    values = {
        "request_id": str(request_id),
        "vendor_name": "SupportFlow Technologies Ltd.",
        "service_name": "SupportFlow Cloud",
        "assessment_type": "new_vendor_onboarding",
        "risk_tier": "high",
        "assessment_date": "2026-09-30",
        "policy_id": "mr_mart_vendor_policy_store",
        "policy_version": "v1.0",
        "policy_status": "ACTIVE",
    }
    values.update(changes)
    return values


@pytest.fixture
def case_client(pg_engine):
    app.dependency_overrides[get_case_service] = lambda: CaseService(pg_engine)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_case_service, None)


def test_create_reload_list_and_audit_case(pg_engine, case_client):
    request_id = uuid4()
    create_correlation, read_correlation, list_correlation = uuid4(), uuid4(), uuid4()
    created = case_client.post(
        "/api/cases",
        json=payload(request_id),
        headers={"X-Pilot-Actor": "pilot_analyst", "X-Correlation-ID": str(create_correlation)},
    )
    assert created.status_code == 201
    case = created.json()
    assert "case_version" not in case
    assert "submission_version" not in case
    assert case["case_id"] != case["submission_id"]
    assert case["state"] == "intake_in_progress"

    reloaded = case_client.get(
        f"/api/cases/{case['case_id']}",
        headers={"X-Pilot-Actor": "pilot_analyst", "X-Correlation-ID": str(read_correlation)},
    )
    assert reloaded.status_code == 200
    assert reloaded.json() == case
    listed = case_client.get(
        "/api/cases?limit=100&offset=0",
        headers={"X-Pilot-Actor": "pilot_analyst", "X-Correlation-ID": str(list_correlation)},
    )
    assert listed.status_code == 200
    assert any(item["case_id"] == case["case_id"] for item in listed.json()["items"])

    with pg_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Case).where(Case.request_id == request_id)) == 1
        assert connection.scalar(
            select(func.count()).select_from(CaseSubmission).where(CaseSubmission.case_id == case["case_id"])
        ) == 1
        events = connection.execute(
            select(AuditEvent.action, AuditEvent.correlation_id)
            .where(AuditEvent.case_id == case["case_id"])
            .order_by(AuditEvent.occurred_at)
        ).all()
    assert events == [("case.created", create_correlation), ("case.read", read_correlation)]
    with pg_engine.connect() as connection:
        assert connection.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.action == "case.listed", AuditEvent.correlation_id == list_correlation
            )
        ) == 1


def test_exact_retry_returns_original_but_changed_retry_conflicts(pg_engine, case_client):
    request_id = uuid4()
    headers = {"X-Pilot-Actor": "pilot_analyst"}
    first = case_client.post("/api/cases", json=payload(request_id), headers=headers)
    replay = case_client.post("/api/cases", json=payload(request_id), headers=headers)
    conflict = case_client.post(
        "/api/cases", json=payload(request_id, vendor_name="Different Vendor"), headers=headers
    )
    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "request_conflict"
    case_id = first.json()["case_id"]
    with pg_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Case).where(Case.request_id == request_id)) == 1
        actions = connection.scalars(
            select(AuditEvent.action).where(AuditEvent.case_id == case_id).order_by(AuditEvent.occurred_at)
        ).all()
    assert actions == ["case.created", "case.create_failed"]


def test_missing_case_is_safe_and_audited(pg_engine, case_client):
    missing_id, correlation_id = uuid4(), uuid4()
    response = case_client.get(
        f"/api/cases/{missing_id}",
        headers={"X-Pilot-Actor": "pilot_analyst", "X-Correlation-ID": str(correlation_id)},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "case_not_found"
    with pg_engine.connect() as connection:
        event = connection.execute(
            select(AuditEvent.outcome, AuditEvent.error_code).where(
                AuditEvent.case_id == missing_id, AuditEvent.correlation_id == correlation_id
            )
        ).one()
    assert event == ("failure", "case_not_found")


def test_migrated_schema_uses_ids_without_entity_versions(pg_engine):
    schema = inspect(pg_engine)
    assert "case_version" not in {column["name"] for column in schema.get_columns("cases")}
    assert "submission_version" not in {column["name"] for column in schema.get_columns("case_submissions")}
    assert "document_version" not in {column["name"] for column in schema.get_columns("audit_events")}
    assert "policy_version" in {column["name"] for column in schema.get_columns("cases")}
    assert "schema_version" in {column["name"] for column in schema.get_columns("audit_events")}
