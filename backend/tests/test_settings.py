import pytest
from fastapi.testclient import TestClient

from app.db import get_engine
from app.main import app
from app.settings import ConfigurationError, database_url, storage_root_path
from app.storage.local import LocalFiles


def test_missing_database_url_has_actionable_safe_error(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ConfigurationError) as error:
        database_url()
    assert "DATABASE_URL is missing" in str(error.value)
    assert "F2_SETTINGS.md" in str(error.value)


@pytest.mark.parametrize(
    "value",
    [
        "not-a-url-private-password",
        "sqlite:///private-password.db",
        "postgresql+psycopg://pilot:private-password@127.0.0.1/",
    ],
)
def test_invalid_database_url_never_echoes_its_value(monkeypatch, value):
    monkeypatch.setenv("DATABASE_URL", value)
    with pytest.raises(ConfigurationError) as error:
        database_url()
    assert "postgresql+psycopg" in str(error.value)
    assert "private-password" not in str(error.value)


def test_valid_database_url_is_returned_without_connecting(monkeypatch):
    value = "postgresql+psycopg://pilot:private-password@127.0.0.1:5432/vendorlens"
    monkeypatch.setenv("DATABASE_URL", value)
    assert database_url() == value


def test_missing_configuration_is_not_exposed_by_ready_api(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_engine.cache_clear()
    try:
        response = TestClient(app).get("/api/ready")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "database_unavailable"
        assert "DATABASE_URL" not in str(response.json())
    finally:
        get_engine.cache_clear()


def test_storage_root_defaults_under_repository(monkeypatch, tmp_path):
    monkeypatch.delenv("STORAGE_ROOT", raising=False)
    assert storage_root_path(tmp_path) == tmp_path / "data" / "originals"


@pytest.mark.parametrize("value", ["", "  ", ".", "data/..", "/"])
def test_empty_or_broad_storage_root_is_rejected(monkeypatch, tmp_path, value):
    monkeypatch.setenv("STORAGE_ROOT", value)
    with pytest.raises(ConfigurationError) as error:
        storage_root_path(tmp_path)
    assert "STORAGE_ROOT" in str(error.value)
    assert list(tmp_path.iterdir()) == []


def test_relative_storage_root_is_anchored_to_repository(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_ROOT", "data/custom")
    assert storage_root_path(tmp_path) == tmp_path / "data" / "custom"


def test_explicit_test_root_ignores_development_setting(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_ROOT", "data/originals")
    test_root = tmp_path / "isolated"
    assert LocalFiles(test_root).root == test_root


def test_storage_setting_error_does_not_create_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_ROOT", "data/..")
    with pytest.raises(ConfigurationError):
        storage_root_path(tmp_path)
    assert list(tmp_path.iterdir()) == []
