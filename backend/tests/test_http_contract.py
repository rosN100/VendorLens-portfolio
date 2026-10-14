import json
import logging
from datetime import UTC
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.contracts import new_id, utc_now
from app.http import install_http_contract
from app.main import app

client = TestClient(app)


def test_shared_generated_ids_and_times():
    generated = new_id()
    assert isinstance(generated, UUID)
    assert generated.version == 4
    assert utc_now().tzinfo == UTC


def test_client_correlation_id_is_echoed():
    supplied = str(uuid4())
    response = client.get("/api/health", headers={"X-Correlation-ID": supplied})
    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == supplied


def test_invalid_correlation_id_gets_safe_error_and_new_id():
    response = client.get("/api/health", headers={"X-Correlation-ID": "not-a-uuid"})
    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "invalid_correlation_id",
            "message": "X-Correlation-ID must be a UUIDv4.",
            "correlation_id": response.headers["X-Correlation-ID"],
        }
    }
    assert UUID(response.headers["X-Correlation-ID"]).version == 4


def test_noncanonical_correlation_id_is_rejected_and_logged_with_new_id(caplog):
    supplied = str(uuid4()).upper()
    http_logger = logging.getLogger("app.http")
    http_logger.addHandler(caplog.handler)
    try:
        response = client.get("/api/health?secret=private-query-value", headers={"X-Correlation-ID": supplied})
    finally:
        http_logger.removeHandler(caplog.handler)
    assert response.status_code == 400
    replacement = response.headers["X-Correlation-ID"]
    assert replacement != supplied
    assert UUID(replacement).version == 4
    assert response.json()["error"]["correlation_id"] == replacement
    records = [json.loads(record.message) for record in caplog.records if record.name == "app.http"]
    assert records[-1]["correlation_id"] == replacement
    assert records[-1]["route"] == "<unmatched>"
    assert supplied not in caplog.text
    assert "private-query-value" not in caplog.text


def test_not_found_and_method_not_allowed_have_same_error_shape():
    not_found = client.get("/api/missing")
    assert not_found.status_code == 404
    assert not_found.json()["error"]["code"] == "not_found"
    assert not_found.json()["error"]["correlation_id"] == not_found.headers["X-Correlation-ID"]

    wrong_method = client.post("/api/health")
    assert wrong_method.status_code == 405
    assert wrong_method.json()["error"]["code"] == "method_not_allowed"
    assert wrong_method.json()["error"]["correlation_id"] == wrong_method.headers["X-Correlation-ID"]


def test_validation_and_unhandled_errors_hide_supplied_and_internal_values(caplog):
    isolated = FastAPI()
    install_http_contract(isolated)

    @isolated.get("/number/{number}")
    def number(number: int):
        return {"number": number}

    @isolated.get("/explode")
    def explode():
        raise RuntimeError("private-internal-value")

    isolated_client = TestClient(isolated)
    http_logger = logging.getLogger("app.http")
    http_logger.addHandler(caplog.handler)
    try:
        invalid = isolated_client.get("/number/private-supplied-value?secret=private-query-value")
        failure = isolated_client.get("/explode?secret=private-query-value")
    finally:
        http_logger.removeHandler(caplog.handler)
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_request"
    assert failure.status_code == 500
    assert failure.json()["error"]["code"] == "internal_error"
    assert "private-internal-value" not in caplog.text
    assert "private-supplied-value" not in caplog.text
    assert "private-query-value" not in caplog.text
    assert "private-internal-value" not in str(failure.json())
    assert "private-supplied-value" not in str(invalid.json())


def test_structured_request_log_has_safe_fields(caplog):
    http_logger = logging.getLogger("app.http")
    http_logger.addHandler(caplog.handler)
    try:
        response = client.get("/api/health?secret=private-query-value")
    finally:
        http_logger.removeHandler(caplog.handler)
    records = [json.loads(record.message) for record in caplog.records if record.name == "app.http"]
    request = next(item for item in records if item["event"] == "http.request")
    assert request["correlation_id"] == response.headers["X-Correlation-ID"]
    assert request["method"] == "GET"
    assert request["route"] == "/api/health"
    assert request["status_code"] == 200
    assert request["duration_ms"] >= 0
    assert "private-query-value" not in caplog.text
