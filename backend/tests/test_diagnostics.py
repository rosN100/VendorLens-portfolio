"""Local diagnostic retention, rotation, and safe database-outage summaries."""

from contextlib import contextmanager
import json
import logging
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import OperationalError

from app.components.observability import diagnostics
from app.main import app
from app.storage.local import LocalFiles
from app.storage.service import FileStore


def test_http_diagnostic_is_retained_without_request_secret(tmp_path):
    path = diagnostics.configure_diagnostics(tmp_path)
    try:
        response = TestClient(app).get("/api/health?secret=do-not-store")
        assert response.status_code == 200
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert rows[-1]["event"] == "http.request"
        assert rows[-1]["correlation_id"] == response.headers["X-Correlation-ID"]
        assert "do-not-store" not in path.read_text()
        assert path.stat().st_mode & 0o077 == 0
    finally:
        diagnostics.configure_diagnostics()


def test_unavailable_diagnostic_file_is_visible_in_terminal(tmp_path, capsys):
    (tmp_path / "diagnostics.jsonl").mkdir()
    try:
        with pytest.raises(RuntimeError, match="Diagnostic log file is unavailable"):
            diagnostics.configure_diagnostics(tmp_path)
        assert '"event":"diagnostic.file_unavailable"' in capsys.readouterr().err
    finally:
        diagnostics.configure_diagnostics()


def test_reconfiguration_is_idempotent_and_rotation_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "MAX_BYTES", 300)
    monkeypatch.setattr(diagnostics, "BACKUP_COUNT", 2)
    path = diagnostics.configure_diagnostics(tmp_path)
    try:
        assert diagnostics.configure_diagnostics(tmp_path) == path
        app_logger = logging.getLogger("app")
        assert sum(isinstance(handler, diagnostics.RotatingFileHandler) for handler in app_logger.handlers) == 1
        for _ in range(20):
            diagnostics.diagnostic_event(app_logger, "test.heartbeat", outcome="failure")
        files = list(tmp_path.glob("diagnostics.jsonl*"))
        assert 1 < len(files) <= 3
        assert all(file.stat().st_size <= 300 for file in files)
    finally:
        diagnostics.configure_diagnostics()


def test_audit_recording_failure_is_visible_when_database_is_down(tmp_path):
    class BrokenEngine:
        @contextmanager
        def begin(self):
            raise OperationalError("private SQL", {"password": "never-log-me"}, Exception("private"))
            yield

    store = FileStore(BrokenEngine(), LocalFiles(tmp_path / "files"))
    path = diagnostics.configure_diagnostics(tmp_path / "logs")
    try:
        correlation_id = uuid4()
        store._failure_event("file.read_failed", "pilot_analyst", correlation_id, uuid4(), "missing_file")
        row = json.loads(path.read_text().splitlines()[-1])
        assert row["event"] == "audit.write_failed"
        assert row["correlation_id"] == str(correlation_id)
        assert row["error_code"] == "missing_file"
        assert "never-log-me" not in path.read_text()
        assert "private SQL" not in path.read_text()
    finally:
        diagnostics.configure_diagnostics()
