from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError

from app.components.observability.models import AuditEvent
from app.storage.errors import IntegrityError, MissingFile, PersistenceError, RequestConflict, StorageError
from app.storage.local import LocalFiles
from app.storage.models import StoredFile
from app.storage.service import FileStore

pytestmark = pytest.mark.integration


@pytest.fixture
def store(pg_engine, tmp_path):
    return FileStore(pg_engine, LocalFiles(tmp_path))


def events(engine, file_id):
    with engine.connect() as connection:
        return connection.execute(select(AuditEvent.__table__).where(AuditEvent.file_id == file_id)).mappings().all()


def test_round_trip_records_metadata_and_audit(store):
    receipt = store.save(b"hello", "demo.txt", uuid4())
    assert receipt.checksum_algorithm == "sha256"
    assert store.read(receipt.file_id) == b"hello"
    audit = events(store.engine, receipt.file_id)
    assert {(e["action"], e["outcome"]) for e in audit} == {("file.saved", "success"), ("file.read", "success")}
    assert all(e["case_id"] is None and e["schema_version"] == 1 for e in audit)
    saved = next(e for e in audit if e["action"] == "file.saved")
    assert saved["correlation_id"] == receipt.request_id
    assert saved["actor"] == "pilot_analyst"
    assert saved["occurred_at"].tzinfo is not None


def test_identical_retry_returns_same_file_without_duplicate_save_event(store):
    request_id = uuid4()
    first = store.save(b"hello", "demo.txt", request_id)
    second = store.save(b"hello", "demo.txt", request_id)
    assert first.file_id == second.file_id
    assert len(list(store.files.root.glob("*.bin"))) == 1
    assert len(events(store.engine, first.file_id)) == 1


def test_identical_bytes_with_new_request_id_create_new_operation(store):
    first = store.save(b"same", "same.txt", uuid4())
    second = store.save(b"same", "same.txt", uuid4())
    assert first.sha256 == second.sha256
    assert first.request_id != second.request_id
    assert first.file_id != second.file_id
    assert len(events(store.engine, first.file_id)) == 1
    assert len(events(store.engine, second.file_id)) == 1


def test_retry_checks_exact_bytes_even_if_digest_comparison_matches(store, monkeypatch):
    request_id = uuid4()
    first = store.save(b"start", "demo.txt", request_id)
    fake_hash = SimpleNamespace(sha256=lambda _: SimpleNamespace(hexdigest=lambda: first.sha256))
    monkeypatch.setattr("app.storage.service.hashlib", fake_hash)
    with pytest.raises(RequestConflict):
        store.save(b"other", "demo.txt", request_id)
    actions = [event["action"] for event in events(store.engine, first.file_id)]
    assert actions.count("file.saved") == 1
    assert actions.count("file.save_failed") == 1


def test_identical_retry_from_new_service_instance_returns_original(store):
    request_id = uuid4()
    first = store.save(b"hello", "demo.txt", request_id)
    fresh_engine = create_engine(store.engine.url, hide_parameters=True)
    try:
        reopened = FileStore(fresh_engine, LocalFiles(store.files.root))
        second = reopened.save(b"hello", "demo.txt", request_id)
        assert second.file_id == first.file_id
        assert len(events(fresh_engine, first.file_id)) == 1
    finally:
        fresh_engine.dispose()


@pytest.mark.parametrize("content,name,actor", [(b"changed", "demo.txt", "pilot_analyst"), (b"hello", "new.txt", "pilot_analyst"), (b"hello", "demo.txt", "other_actor")])
def test_changed_retry_conflicts(store, content, name, actor):
    request_id = uuid4()
    first = store.save(b"hello", "demo.txt", request_id)
    with pytest.raises(RequestConflict):
        store.save(content, name, request_id, actor)
    assert store.read(first.file_id) == b"hello"


def test_same_original_filename_does_not_overwrite(store):
    one = store.save(b"one", "same.txt", uuid4())
    two = store.save(b"two", "same.txt", uuid4())
    assert one.file_id != two.file_id
    assert store.read(one.file_id) == b"one"
    assert store.read(two.file_id) == b"two"


def test_empty_bytes_are_valid_storage_not_pdf_admission(store):
    result = store.save(b"", "empty.txt", uuid4())
    assert store.read(result.file_id) == b""


def test_concurrent_same_request_writes_once(store):
    request_id = uuid4()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.save(b"hello", "demo.txt", request_id), range(2)))
    assert results[0].file_id == results[1].file_id
    assert len(events(store.engine, results[0].file_id)) == 1


def test_tampered_file_is_not_returned_and_failure_is_audited(store):
    result = store.save(b"original", "demo.txt", uuid4())
    store.files.path_for(result.file_id).write_bytes(b"modified")
    with pytest.raises(IntegrityError):
        store.read(result.file_id)
    with pytest.raises(IntegrityError):
        store.save(b"original", "demo.txt", result.request_id)
    assert any(e["error_code"] == "integrity_mismatch" for e in events(store.engine, result.file_id))


def test_missing_disk_file_and_unknown_id_are_audited(store):
    result = store.save(b"original", "demo.txt", uuid4())
    store.files.path_for(result.file_id).unlink()  # disposable test file only
    for file_id in [result.file_id, uuid4()]:
        with pytest.raises(MissingFile):
            store.read(file_id)
        assert any(e["error_code"] == "missing_file" for e in events(store.engine, file_id))


def test_disk_failure_rolls_back_metadata(store, monkeypatch):
    request_id = uuid4()

    def fail_write(*_):
        raise StorageError("simulated disk failure")

    monkeypatch.setattr(store.files, "write", fail_write)
    with pytest.raises(StorageError):
        store.save(b"hello", "demo.txt", request_id)
    with store.engine.connect() as connection:
        assert connection.scalar(select(StoredFile.file_id).where(StoredFile.request_id == request_id)) is None
        assert connection.scalar(select(AuditEvent.outcome).where(AuditEvent.correlation_id == request_id)) == "failure"


def test_audit_failure_rolls_back_metadata_and_preserves_orphan_for_inspection(store, monkeypatch, caplog):
    request_id = uuid4()
    import logging
    app_logger = logging.getLogger("app")
    app_logger.addHandler(caplog.handler)

    def fail_event(*_, **__):
        raise OperationalError("secret SQL", {"secret": "never-log-me"}, Exception("secret"))

    try:
        with monkeypatch.context() as patch:
            patch.setattr("app.storage.service.record_event", fail_event)
            with pytest.raises(PersistenceError):
                store.save(b"private bytes", "sensitive-name.txt", request_id)
    finally:
        app_logger.removeHandler(caplog.handler)
    with store.engine.connect() as connection:
        assert connection.scalar(select(StoredFile.file_id).where(StoredFile.request_id == request_id)) is None
    candidates = store.inspect_orphans()
    assert len(candidates) == 1
    assert (store.files.root / candidates[0]).read_bytes() == b"private bytes"
    assert "storage.save_persistence_failed" in caplog.text
    assert "never-log-me" not in caplog.text
    assert "private bytes" not in caplog.text
    assert "sensitive-name" not in caplog.text
    result = store.save(b"private bytes", "sensitive-name.txt", request_id)
    assert store.read(result.file_id) == b"private bytes"
    assert store.inspect_orphans() == candidates  # no unsafe automated deletion


def test_read_audit_failure_does_not_return_bytes(store, monkeypatch):
    result = store.save(b"hello", "demo.txt", uuid4())

    def fail_event(*_, **__):
        raise OperationalError("audit failed", {}, Exception())

    monkeypatch.setattr("app.storage.service.record_event", fail_event)
    with pytest.raises(PersistenceError):
        store.read(result.file_id)


def test_database_unavailable_does_not_write_file(store):
    unreachable = create_engine("postgresql+psycopg://vendorlens@127.0.0.1:1/vendorlens_test_unreachable", connect_args={"connect_timeout": 1})
    try:
        unavailable = FileStore(unreachable, store.files)
        with pytest.raises(PersistenceError):
            unavailable.save(b"hello", "demo.txt", uuid4())
        assert list(store.files.root.iterdir()) == []
        with pytest.raises(PersistenceError):
            unavailable.inspect_orphans()
    finally:
        unreachable.dispose()


def test_commit_acknowledgment_loss_is_safe_to_retry(store):
    engine = store.engine
    request_id = uuid4()

    class LostAcknowledgment:
        @contextmanager
        def begin(self):
            with engine.begin() as connection:
                yield connection
            raise OperationalError("commit acknowledgment lost", {}, Exception())

    uncertain = FileStore(LostAcknowledgment(), store.files)
    with pytest.raises(PersistenceError):
        uncertain.save(b"hello", "demo.txt", request_id)
    recovered = store.save(b"hello", "demo.txt", request_id)
    assert len(events(engine, recovered.file_id)) == 1
    assert store.inspect_orphans() == []


def test_new_engine_and_service_retrieve_persisted_file(store):
    result = store.save(b"persisted", "demo.txt", uuid4())
    fresh_engine = create_engine(store.engine.url, hide_parameters=True)
    try:
        reopened = FileStore(fresh_engine, LocalFiles(store.files.root))
        assert reopened.read(result.file_id) == b"persisted"
    finally:
        fresh_engine.dispose()
