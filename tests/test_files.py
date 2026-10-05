import os
from pathlib import Path

import pytest

from glocal_agent import files
from glocal_agent.files import CatalogError, FileCatalog


def test_scan_is_scoped_read_only_and_skips_hidden_and_unsafe(tmp_path):
    root = tmp_path / "downloads"
    root.mkdir()
    (root / "invoice.txt").write_text("price 42")
    (root / ".private").write_text("secret")
    sub = root / "project"
    sub.mkdir()
    (sub / "notes.md").write_text("notes")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    (root / "link.txt").symlink_to(outside)
    (root / "linked-dir").symlink_to(tmp_path, target_is_directory=True)
    os.mkfifo(root / "pipe.txt")
    original = (root / "invoice.txt").stat()
    catalog = FileCatalog(root)
    flat = catalog.scan()
    assert [item["name"] for item in flat["files"]] == ["invoice.txt"]
    assert "unsafe_entries_skipped" in flat["warnings"]
    recursive = catalog.scan(recursive=True)
    assert {item["relative_path"] for item in recursive["files"]} == {
        "invoice.txt",
        "project/notes.md",
    }
    metadata, content = catalog.read(recursive["files"][0]["id"])
    assert content == b"price 42"
    assert metadata["supported"] is True
    assert "root" not in metadata
    assert (root / "invoice.txt").stat().st_mtime_ns == original.st_mtime_ns
    metadata["relative_path"] = "../outside.txt"
    assert catalog.get(metadata["id"])["relative_path"] == "invoice.txt"


def test_unknown_id_and_scan_required(tmp_path):
    catalog = FileCatalog(tmp_path)
    with pytest.raises(CatalogError, match="^scan_required$"):
        catalog.read("../secret")
    catalog.scan()
    with pytest.raises(CatalogError, match="^unknown_file$"):
        catalog.read("../secret")


def test_modified_file_requires_rescan(tmp_path):
    path = tmp_path / "file.txt"
    path.write_text("old value")
    catalog = FileCatalog(tmp_path)
    file_id = catalog.scan()["files"][0]["id"]
    path.write_text("new value")
    with pytest.raises(CatalogError, match="^rescan_required$"):
        catalog.read(file_id)
    assert catalog.scan()["files"][0]["id"] == file_id
    assert catalog.read(file_id)[1] == b"new value"


def test_file_replaced_by_symlink_is_refused(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    path = root / "file.txt"
    path.write_text("allowed")
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    catalog = FileCatalog(root)
    file_id = catalog.scan()["files"][0]["id"]
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(CatalogError, match="^unsafe_path$"):
        catalog.read(file_id)


def test_parent_directory_replaced_by_symlink_is_refused(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    directory = root / "sub"
    directory.mkdir()
    (directory / "file.txt").write_text("allowed")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file.txt").write_text("private")
    catalog = FileCatalog(root)
    file_id = catalog.scan(recursive=True)["files"][0]["id"]
    directory.rename(root / "moved")
    directory.symlink_to(outside, target_is_directory=True)
    with pytest.raises(CatalogError, match="^unsafe_path$"):
        catalog.read(file_id)


def test_root_replacement_is_refused(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "file.txt").write_text("allowed")
    catalog = FileCatalog(root)
    file_id = catalog.scan()["files"][0]["id"]
    root.rename(tmp_path / "original")
    root.mkdir()
    (root / "file.txt").write_text("other")
    with pytest.raises(CatalogError, match="^unsafe_path$"):
        catalog.read(file_id)


def test_change_during_read_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "file.txt"
    path.write_text("allowed")
    catalog = FileCatalog(tmp_path)
    file_id = catalog.scan()["files"][0]["id"]
    original_read = os.read
    changed = False

    def race_read(fd, count):
        nonlocal changed
        result = original_read(fd, count)
        if not changed:
            path.write_text("replaced")
            changed = True
        return result

    monkeypatch.setattr(files.os, "read", race_read)
    with pytest.raises(CatalogError, match="^rescan_required$"):
        catalog.read(file_id)


def test_file_replaced_after_open_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "file.txt"
    path.write_text("allowed")
    catalog = FileCatalog(tmp_path)
    file_id = catalog.scan()["files"][0]["id"]
    original_open = os.open

    def race_open(name, flags, **kwargs):
        fd = original_open(name, flags, **kwargs)
        if name == "file.txt":
            path.unlink()
            path.write_text("replacement")
        return fd

    monkeypatch.setattr(files.os, "open", race_open)
    with pytest.raises(CatalogError, match="^rescan_required$"):
        catalog.read(file_id)


def test_limits_are_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "MAX_FILES", 2)
    monkeypatch.setattr(files, "MAX_FILE_BYTES", 3)
    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_text("four")
    catalog = FileCatalog(tmp_path)
    result = catalog.scan()
    assert len(result["files"]) == 2
    assert "scan_limit_reached" in result["warnings"]
    with pytest.raises(CatalogError, match="^file_too_large$"):
        catalog.read(result["files"][0]["id"])


def test_invalid_root(tmp_path):
    with pytest.raises(CatalogError, match="^invalid_root$"):
        FileCatalog(tmp_path / "missing")
    path = tmp_path / "file"
    path.write_text("x")
    with pytest.raises(CatalogError, match="^invalid_root$"):
        FileCatalog(Path(path))
