"""Exclusive local writes and verified reads. Storage root is trusted configuration."""

import hashlib
import logging
import os
from pathlib import Path
import stat
from uuid import UUID

from app.components.observability.diagnostics import diagnostic_event
from app.settings import storage_root_path
from app.storage.errors import IntegrityError, InvalidReference, MissingFile, StorageError

logger = logging.getLogger(__name__)
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def as_uuid(value: UUID | str) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise InvalidReference("File and request IDs must be UUIDs.") from None


def validate_name(name: str) -> None:
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name) > 255
        or name in {".", ".."}
        or any(c in name for c in ("/", "\\", "\x00"))
        or any(ord(c) < 32 for c in name)
    ):
        raise InvalidReference("Use a plain filename, not a path.")


class LocalFiles:
    def __init__(self, root: Path | None = None):
        configured = storage_root_path(REPOSITORY_ROOT, root)
        if configured.is_symlink():
            raise InvalidReference("Storage root must not be a symlink.")
        try:
            configured.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.root = configured.resolve(strict=True)
        except OSError:
            raise StorageError("Storage directory is unavailable.") from None

    def path_for(self, file_id: UUID | str) -> Path:
        return self.root / f"{as_uuid(file_id)}.bin"

    def write(self, file_id: UUID, data: bytes) -> None:
        path = self.path_for(file_id)
        created = False
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            created = True
            with os.fdopen(fd, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            # Persist directory entry as well as contents on the local filesystem.
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            if created:
                try:
                    path.unlink()
                except OSError:
                    diagnostic_event(logger, "storage.partial_file_cleanup_failed", file_id=file_id,
                                     outcome="failure")
            raise StorageError("File write failed; no existing file was overwritten.") from None

    def read(self, file_id: UUID, expected_sha256: str, expected_size: int) -> bytes:
        path = self.path_for(file_id)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise InvalidReference("Stored reference is not a regular file.")
                data = source.read()
        except FileNotFoundError:
            raise MissingFile("Stored file is missing from disk.") from None
        except OSError:
            raise StorageError("Stored file cannot be safely read.") from None
        if len(data) != expected_size or hashlib.sha256(data).hexdigest() != expected_sha256:
            raise IntegrityError("Stored bytes do not match their saved checksum/size.")
        return data
