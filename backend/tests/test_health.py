from uuid import UUID

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert UUID(response.headers["X-Correlation-ID"]).version == 4


def test_ready_reports_missing_database(monkeypatch) -> None:
    def unavailable():
        raise RuntimeError("private-database-url-and-password")

    monkeypatch.setattr("app.main.get_engine", unavailable)
    response = client.get("/api/ready")
    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "database_unavailable",
            "message": "Database unavailable.",
            "correlation_id": response.headers["X-Correlation-ID"],
        }
    }
    assert "private-database-url-and-password" not in response.text
