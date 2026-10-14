"""Case intake HTTP contract without opening PostgreSQL."""

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.components.case_intake.api import get_case_service
from app.components.case_intake.errors import CaseRequestConflict
from app.main import app


def payload(**changes):
    values = {
        "request_id": str(uuid4()),
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


def view(body):
    return {
        **body,
        "request_id": str(body["request_id"]),
        "case_id": str(uuid4()),
        "submission_id": str(uuid4()),
        "state": "intake_in_progress",
        "created_by": "pilot_analyst",
        "created_at": datetime(2026, 9, 30, tzinfo=UTC),
        "assessment_date": date.fromisoformat(str(body["assessment_date"])),
    }


class FakeCases:
    def __init__(self):
        self.called = False

    def create(self, body, *, actor, correlation_id):
        self.called = True
        assert actor == "pilot_analyst"
        return view(body.model_dump()), False

    def list(self, *, actor, correlation_id, limit, offset):
        self.called = True
        return [], 0

    def get(self, case_id, *, actor, correlation_id):
        self.called = True
        return view({**payload(), "request_id": uuid4()})


@pytest.fixture
def fake_client():
    fake = FakeCases()
    app.dependency_overrides[get_case_service] = lambda: fake
    try:
        yield TestClient(app), fake
    finally:
        app.dependency_overrides.pop(get_case_service, None)


def test_valid_case_contract_and_empty_list(fake_client):
    client, fake = fake_client
    created = client.post("/api/cases", json=payload(), headers={"X-Pilot-Actor": "pilot_analyst"})
    assert created.status_code == 201
    assert created.json()["risk_tier"] == "high"
    assert created.json()["policy_version"] == "v1.0"
    assert "case_version" not in created.json()
    assert "submission_version" not in created.json()
    listed = client.get("/api/cases", headers={"X-Pilot-Actor": "pilot_analyst"})
    assert listed.status_code == 200
    assert listed.json() == {"items": [], "count": 0}
    assert fake.called


@pytest.mark.parametrize(
    "body",
    [
        {key: value for key, value in payload().items() if key != "service_name"},
        payload(policy_version="v0.9"),
        payload(risk_tier="medium"),
        payload(vendor_name="   "),
    ],
)
def test_missing_or_invalid_case_fields_never_reach_service(fake_client, body):
    client, fake = fake_client
    response = client.post("/api/cases", json=body, headers={"X-Pilot-Actor": "pilot_analyst"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert not fake.called


@pytest.mark.parametrize("field", ["case_version", "session_version", "submission_version", "document_version"])
def test_obsolete_entity_version_fields_are_rejected(fake_client, field):
    client, fake = fake_client
    response = client.post(
        "/api/cases", json=payload(**{field: 1}), headers={"X-Pilot-Actor": "pilot_analyst"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert not fake.called


def test_explicit_pilot_actor_is_required_and_validated(fake_client):
    client, fake = fake_client
    missing = client.post("/api/cases", json=payload())
    assert missing.status_code == 422
    invalid = client.post("/api/cases", json=payload(), headers={"X-Pilot-Actor": "   "})
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_actor"
    assert not fake.called


def test_request_conflict_has_stable_safe_error(fake_client):
    client, _ = fake_client

    class ConflictCases(FakeCases):
        def create(self, body, *, actor, correlation_id):
            raise CaseRequestConflict("Request ID was already used with different case inputs or actor.")

    app.dependency_overrides[get_case_service] = ConflictCases
    response = client.post("/api/cases", json=payload(), headers={"X-Pilot-Actor": "pilot_analyst"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "request_conflict"
    assert response.json()["error"]["correlation_id"] == response.headers["X-Correlation-ID"]
