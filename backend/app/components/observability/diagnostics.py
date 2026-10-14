"""Small local diagnostic sink, separate from the PostgreSQL audit trail."""

import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
from uuid import UUID

from app.contracts import utc_now

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_LOG_DIR = REPOSITORY_ROOT / "data" / "logs"
LOG_NAME = "diagnostics.jsonl"
MAX_BYTES = 1_000_000
BACKUP_COUNT = 3
_OWNED = "_vendorlens_diagnostic_handler"


class _PrivateRotatingFileHandler(RotatingFileHandler):
    def _open(self):
        # New active files remain owner-readable/writable after each rotation.
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        return os.fdopen(fd, "a", encoding=self.encoding or "utf-8")


def configure_diagnostics(log_dir: Path | None = None) -> Path:
    """Use terminal plus bounded local files for app.* logs; safe to call twice."""
    directory = Path(log_dir) if log_dir is not None else DEFAULT_LOG_DIR
    if directory.is_symlink():
        raise OSError("Diagnostic log directory must not be a symlink")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = directory / LOG_NAME
    if destination.is_symlink():
        raise OSError("Diagnostic log file must not be a symlink")
    target = logging.getLogger("app")
    if any(
        getattr(handler, _OWNED, False) and isinstance(handler, RotatingFileHandler)
        and Path(handler.baseFilename) == destination
        for handler in target.handlers
    ):
        return destination
    for handler in list(target.handlers):
        if getattr(handler, _OWNED, False):
            target.removeHandler(handler)
            handler.close()
    formatter = logging.Formatter("%(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    setattr(console, _OWNED, True)
    target.addHandler(console)
    target.setLevel(logging.INFO)
    target.propagate = False
    file_handler = None
    try:
        file_handler = _PrivateRotatingFileHandler(
            destination, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
        )
        os.chmod(destination, 0o600)
    except OSError:
        if file_handler is not None:
            file_handler.close()
        diagnostic_event(target, "diagnostic.file_unavailable", outcome="failure")
        raise RuntimeError("Diagnostic log file is unavailable; see terminal output.") from None
    file_handler.setFormatter(formatter)
    setattr(file_handler, _OWNED, True)
    target.addHandler(file_handler)
    return destination


def diagnostic_event(logger: logging.Logger, event: str, **fields: str | UUID) -> None:
    """Callers supply only predefined safe fields, never exception text or user input."""
    payload = {"event": event, "occurred_at": utc_now().isoformat()}
    payload.update({key: str(value) for key, value in fields.items()})
    logger.error(json.dumps(payload, separators=(",", ":")))
