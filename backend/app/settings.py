"""Validate local configuration without echoing secret values in errors."""

import os
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class ConfigurationError(RuntimeError):
    """A required or unsafe local setting needs correction."""


def database_url() -> str:
    """Return the configured PostgreSQL URL; never include its value in errors."""
    raw = os.environ.get("DATABASE_URL")
    if not raw or not raw.strip():
        raise ConfigurationError(
            "DATABASE_URL is missing. Load the implementation-root .env in this terminal; see F2_SETTINGS.md."
        )
    try:
        parsed = make_url(raw)
        if parsed.drivername != "postgresql+psycopg" or not parsed.database:
            raise ValueError("unsupported or incomplete URL")
    except (ArgumentError, ValueError, TypeError):
        raise ConfigurationError(
            "DATABASE_URL must be a postgresql+psycopg URL with a database name. Check .env; its value is not shown."
        ) from None
    return raw


def storage_root_path(repository_root: Path, explicit: Path | None = None) -> Path:
    """Choose a dedicated local directory, not the repository or filesystem root."""
    if explicit is None:
        raw = os.environ.get("STORAGE_ROOT")
        if raw is not None and not raw.strip():
            raise ConfigurationError(
                "STORAGE_ROOT is empty. Unset it for data/originals or choose a dedicated directory."
            )
        configured = Path(raw) if raw is not None else Path("data/originals")
    else:
        configured = Path(explicit)
    if not configured.is_absolute():
        configured = repository_root / configured
    resolved = configured.resolve(strict=False)
    if resolved in {repository_root.resolve(), Path(resolved.anchor)}:
        raise ConfigurationError("STORAGE_ROOT must be a dedicated directory, not the repository or filesystem root.")
    return configured
