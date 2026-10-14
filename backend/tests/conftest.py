"""PostgreSQL tests require an explicitly separate database; never erase test rows."""

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


@pytest.fixture(scope="session")
def pg_engine():
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Set TEST_DATABASE_URL to an isolated vendorlens_test_* PostgreSQL database")
    url = make_url(raw)
    if url.get_backend_name() != "postgresql" or not (url.database or "").startswith("vendorlens_test_"):
        pytest.fail("Refusing integration tests: database must be PostgreSQL named vendorlens_test_*")
    production = os.environ.get("DATABASE_URL")
    if production and make_url(production).database == url.database:
        pytest.fail("TEST_DATABASE_URL must not target the application database")
    engine = create_engine(url, hide_parameters=True)
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT current_database()")) == url.database
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    yield engine
    engine.dispose()
