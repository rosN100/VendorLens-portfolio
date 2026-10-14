"""Coordinate filesystem, metadata and audit writes without claiming cross-store atomicity."""

from dataclasses import dataclass
from datetime import datetime
import hashlib
import logging
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.components.observability.diagnostics import configure_diagnostics, diagnostic_event
from app.components.observability.recorder import record_event
from app.contracts import new_id, utc_now, validate_actor
from app.storage.errors import MissingFile, PersistenceError, RequestConflict, StorageError
from app.storage.local import LocalFiles, as_uuid, validate_name
from app.storage.models import StoredFile

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FileReceipt:
    file_id: UUID
    request_id: UUID
    original_name: str
    actor: str
    byte_size: int
    checksum_algorithm: str
    sha256: str
    created_at: datetime


class FileStore:
    def __init__(self, engine: Engine, files: LocalFiles):
        configure_diagnostics()
        self.engine = engine
        self.files = files

    def _failure_event(self, action: str, actor: str, request_id: UUID, file_id: UUID, code: str) -> None:
        try:
            with self.engine.begin() as connection:
                record_event(connection, action=action, outcome="failure", actor=actor,
                             correlation_id=request_id, file_id=file_id, error_code=code)
        except SQLAlchemyError:
            # Database may be unavailable. Preserve original error; never log its raw SQL/params.
            diagnostic_event(logger, "audit.write_failed", correlation_id=request_id, file_id=file_id,
                             error_code=code, outcome="failure")

    def save(self, data: bytes, filename: str, request_id: UUID | str, actor: str = "pilot_analyst") -> FileReceipt:
        validate_name(filename)
        validate_actor(actor)
        request_id = as_uuid(request_id)
        if not isinstance(data, bytes):
            raise TypeError("File content must be bytes.")
        digest = hashlib.sha256(data).hexdigest()
        file_id = new_id()
        values = dict(file_id=file_id, request_id=request_id, original_name=filename, actor=actor,
                      byte_size=len(data), checksum_algorithm="sha256", sha256=digest,
                      created_at=utc_now())
        try:
            with self.engine.begin() as connection:
                # Unique request constraint serializes concurrent identical requests before disk writes.
                inserted = connection.execute(
                    insert(StoredFile).values(**values)
                    .on_conflict_do_nothing(constraint="uq_stored_files_request_id")
                    .returning(StoredFile.file_id)
                ).scalar_one_or_none()
                if inserted is None:
                    row = connection.execute(select(StoredFile.__table__).where(StoredFile.request_id == request_id)).mappings().one()
                    file_id = row["file_id"]
                    if any(row[key] != values[key] for key in ("original_name", "actor", "byte_size", "sha256")):
                        raise RequestConflict("Request ID was already used with different inputs.")
                    # The digest is an integrity check, not the definition of request equality.
                    if self.files.read(file_id, row["sha256"], row["byte_size"]) != data:
                        raise RequestConflict("Request ID was already used with different inputs.")
                    receipt = FileReceipt(**row)
                else:
                    self.files.write(file_id, data)
                    record_event(connection, action="file.saved", outcome="success", actor=actor,
                                 correlation_id=request_id, file_id=file_id)
                    receipt = FileReceipt(**values)
            # Return only after commit succeeds.
            return receipt
        except SQLAlchemyError:
            diagnostic_event(logger, "storage.save_persistence_failed", correlation_id=request_id,
                             file_id=file_id, outcome="failure", inspect_retained_files="true")
            raise PersistenceError(
                "Database/audit commit failed or is uncertain. Retained files may need inspection; retry with the same request ID."
            ) from None
        except StorageError as error:
            self._failure_event("file.save_failed", actor, request_id, file_id, error.code)
            raise

    def read(self, file_id: UUID | str, actor: str = "pilot_analyst") -> bytes:
        file_id = as_uuid(file_id)
        validate_actor(actor)
        correlation_id = new_id()
        try:
            with self.engine.begin() as connection:
                row = connection.execute(select(StoredFile.__table__).where(StoredFile.file_id == file_id)).mappings().one_or_none()
                if row is None:
                    raise MissingFile("No metadata exists for this file ID.")
                data = self.files.read(file_id, row["sha256"], row["byte_size"])
                record_event(connection, action="file.read", outcome="success", actor=actor,
                             correlation_id=correlation_id, file_id=file_id)
            return data
        except SQLAlchemyError:
            diagnostic_event(logger, "storage.read_persistence_failed", correlation_id=correlation_id,
                             file_id=file_id, outcome="failure")
            raise PersistenceError("Database/audit write failed; file contents were not returned.") from None
        except StorageError as error:
            self._failure_event("file.read_failed", actor, correlation_id, file_id, error.code)
            raise

    def inspect_orphans(self) -> list[str]:
        """Read-only candidates, never automatic deletion. Run with writers stopped."""
        try:
            with self.engine.connect() as connection:
                known = {f"{value}.bin" for value in connection.scalars(select(StoredFile.file_id))}
        except SQLAlchemyError:
            raise PersistenceError("Cannot inspect orphans while the database is unavailable.") from None
        return sorted(path.name for path in self.files.root.glob("*.bin") if path.name not in known)
