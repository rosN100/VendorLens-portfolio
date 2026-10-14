"""Safe, user-facing errors: do not include SQL, credentials or file contents."""


class StorageError(Exception):
    code = "storage_error"


class InvalidReference(StorageError):
    code = "invalid_reference"


class MissingFile(StorageError):
    code = "missing_file"


class IntegrityError(StorageError):
    code = "integrity_mismatch"


class RequestConflict(StorageError):
    code = "request_conflict"


class PersistenceError(StorageError):
    code = "persistence_error"
