import hashlib
import os
from uuid import uuid4

import pytest

from app.storage.errors import IntegrityError, InvalidReference, MissingFile, StorageError
from app.storage.local import LocalFiles, validate_name


def test_round_trip_and_exclusive_write(tmp_path):
    files = LocalFiles(tmp_path)
    file_id = uuid4()
    data = b"original bytes\x00\xff"
    files.write(file_id, data)
    assert files.read(file_id, hashlib.sha256(data).hexdigest(), len(data)) == data
    with pytest.raises(StorageError):
        files.write(file_id, b"replacement")
    assert files.path_for(file_id).read_bytes() == data


@pytest.mark.parametrize("name", ["../outside.txt", "/absolute", "a/b", "a\\b", "..", "", "bad\x00name", "bad\nname"])
def test_reject_unsafe_names(name):
    with pytest.raises(InvalidReference):
        validate_name(name)


def test_traversal_id_cannot_be_used(tmp_path):
    with pytest.raises(InvalidReference):
        LocalFiles(tmp_path).path_for("../../outside")


def test_missing_file(tmp_path):
    with pytest.raises(MissingFile):
        LocalFiles(tmp_path).read(uuid4(), "0" * 64, 0)


@pytest.mark.parametrize("changed", [b"changed", b"short"])
def test_checksum_and_size_mismatch(tmp_path, changed):
    files = LocalFiles(tmp_path)
    file_id = uuid4()
    files.write(file_id, b"initial")
    files.path_for(file_id).write_bytes(changed)  # only a disposable fixture
    with pytest.raises(IntegrityError):
        files.read(file_id, hashlib.sha256(b"initial").hexdigest(), 7)


def test_symlink_is_not_followed_or_overwritten(tmp_path):
    files = LocalFiles(tmp_path / "store")
    target = tmp_path / "outside.txt"
    target.write_bytes(b"outside")
    file_id = uuid4()
    files.path_for(file_id).symlink_to(target)
    with pytest.raises(StorageError):
        files.write(file_id, b"overwrite")
    with pytest.raises(StorageError):
        files.read(file_id, hashlib.sha256(b"outside").hexdigest(), 7)
    assert target.read_bytes() == b"outside"


def test_partial_write_is_cleaned_up(tmp_path, monkeypatch):
    files = LocalFiles(tmp_path)
    file_id = uuid4()

    def fail_sync(_):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(os, "fsync", fail_sync)
    with pytest.raises(StorageError):
        files.write(file_id, b"partial")
    assert not files.path_for(file_id).exists()


def test_root_is_independent_of_current_directory(tmp_path, monkeypatch):
    monkeypatch.setattr("app.storage.local.REPOSITORY_ROOT", tmp_path)
    monkeypatch.delenv("STORAGE_ROOT", raising=False)
    monkeypatch.chdir(tmp_path.parent)
    assert LocalFiles().root == tmp_path / "data" / "originals"


def test_symlink_root_is_rejected(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(InvalidReference):
        LocalFiles(link)
