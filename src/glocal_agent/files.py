"""A read-only, explicitly scoped catalogue of local files.

IDs refer to a scan snapshot. Every read reopens the file relative to the
authorised directory using non-following directory descriptors and verifies
the snapshot, so a replaced path cannot grant access to another file.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
import threading
from pathlib import Path
from typing import Any

MAX_FILES = 2_000
MAX_DIRECTORIES = 2_000
MAX_FILE_BYTES = 15 * 1024 * 1024
SUPPORTED_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".docx",
        ".xlsx",
        ".xlsm",
        ".pptx",
        ".txt",
        ".md",
        ".csv",
        ".tsv",
    }
)


class CatalogError(ValueError):
    """A fixed, non-sensitive reason for refusing a catalogue operation."""


def _fingerprint(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


class FileCatalog:
    def __init__(self, root: Path):
        try:
            self.root = Path(root).expanduser().resolve(strict=True)
            root_stat = self.root.stat()
            if not stat.S_ISDIR(root_stat.st_mode):
                raise CatalogError("invalid_root")
        except (OSError, RuntimeError) as error:
            raise CatalogError("invalid_root") from error
        self._root_identity = (root_stat.st_dev, root_stat.st_ino)
        self._entries: dict[str, tuple[dict[str, Any], tuple[int, ...]]] = {}
        self._scanned = False
        self._lock = threading.RLock()

    def _open_root(self) -> int:
        try:
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError as error:
            raise CatalogError("unsafe_path") from error
        current = os.fstat(fd)
        if (current.st_dev, current.st_ino) != self._root_identity:
            os.close(fd)
            raise CatalogError("unsafe_path")
        return fd

    def scan(self, recursive: bool = False) -> dict[str, Any]:
        with self._lock:
            return self._scan(recursive)

    def _scan(self, recursive: bool) -> dict[str, Any]:
        self._entries.clear()
        self._scanned = False
        warnings: list[str] = []
        visited_directories = 0
        stopped = False

        def warn(reason: str) -> None:
            if reason not in warnings:
                warnings.append(reason)

        def visit(fd: int, parent: tuple[str, ...]) -> None:
            nonlocal visited_directories, stopped
            visited_directories += 1
            try:
                with os.scandir(fd) as iterator:
                    # Bound directory listings as well as the resulting catalogue.
                    names = []
                    for entry in iterator:
                        if entry.name.startswith("."):
                            continue
                        names.append(entry.name)
                        if len(names) > MAX_FILES + MAX_DIRECTORIES:
                            warn("directory_entry_limit_reached")
                            break
            except OSError:
                warn("unreadable_entries_skipped")
                return
            for name in sorted(names):
                if stopped:
                    return
                try:
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode):
                        warn("unsafe_entries_skipped")
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        if not recursive:
                            continue
                        if visited_directories >= MAX_DIRECTORIES:
                            warn("directory_limit_reached")
                            continue
                        child = os.open(
                            name,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=fd,
                        )
                        try:
                            child_info = os.fstat(child)
                            if (child_info.st_dev, child_info.st_ino) != (info.st_dev, info.st_ino):
                                warn("unsafe_entries_skipped")
                                continue
                            visit(child, parent + (name,))
                        finally:
                            os.close(child)
                        continue
                    if not stat.S_ISREG(info.st_mode):
                        warn("unsafe_entries_skipped")
                        continue
                    if len(self._entries) >= MAX_FILES:
                        warn("scan_limit_reached")
                        stopped = True
                        return
                    relative = "/".join(parent + (name,))
                    file_id = hashlib.sha256(
                        relative.encode("utf-8", "surrogateescape")
                    ).hexdigest()
                    extension = Path(name).suffix.lower()
                    metadata = {
                        "id": file_id,
                        "name": name,
                        "relative_path": relative,
                        "extension": extension,
                        "size": info.st_size,
                        "modified_ns": info.st_mtime_ns,
                        "supported": extension in SUPPORTED_EXTENSIONS,
                    }
                    self._entries[file_id] = (metadata, _fingerprint(info))
                except OSError:
                    warn("unreadable_entries_skipped")

        root_fd = self._open_root()
        try:
            visit(root_fd, ())
        finally:
            os.close(root_fd)
        self._scanned = True
        return {"files": [dict(item[0]) for item in self._entries.values()], "warnings": warnings}

    def get(self, file_id: str) -> dict[str, Any]:
        with self._lock:
            if not self._scanned:
                raise CatalogError("scan_required")
            if file_id not in self._entries:
                raise CatalogError("unknown_file")
            return dict(self._entries[file_id][0])

    def read(self, file_id: str) -> tuple[dict[str, Any], bytes]:
        with self._lock:
            return self._read(file_id)

    def _read(self, file_id: str) -> tuple[dict[str, Any], bytes]:
        metadata = self.get(file_id)
        expected = self._entries[file_id][1]
        parts = metadata["relative_path"].split("/")
        if not parts or any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
            raise CatalogError("unsafe_path")
        descriptors: list[int] = []
        directory_snapshots: list[tuple[int, tuple[int, ...]]] = []
        try:
            descriptors.append(self._open_root())
            for part in parts[:-1]:
                directory = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=descriptors[-1],
                )
                descriptors.append(directory)
                directory_snapshots.append((directory, _fingerprint(os.fstat(directory))))
            # O_NONBLOCK prevents a concurrently substituted FIFO from hanging.
            file_fd = os.open(
                parts[-1],
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                dir_fd=descriptors[-1],
            )
            descriptors.append(file_fd)
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode):
                raise CatalogError("unsafe_path")
            if _fingerprint(before) != expected:
                raise CatalogError("rescan_required")
            if before.st_size > MAX_FILE_BYTES:
                raise CatalogError("file_too_large")
            chunks = []
            count = 0
            while True:
                chunk = os.read(file_fd, min(1024 * 1024, MAX_FILE_BYTES + 1 - count))
                if not chunk:
                    break
                chunks.append(chunk)
                count += len(chunk)
                if count > MAX_FILE_BYTES:
                    raise CatalogError("file_too_large")
            if count != before.st_size or _fingerprint(os.fstat(file_fd)) != expected:
                raise CatalogError("rescan_required")
            if any(_fingerprint(os.fstat(fd)) != snapshot for fd, snapshot in directory_snapshots):
                raise CatalogError("rescan_required")
            # Check the current path too, rather than returning an unlinked snapshot.
            current = os.stat(parts[-1], dir_fd=descriptors[-2], follow_symlinks=False)
            if not stat.S_ISREG(current.st_mode) or _fingerprint(current) != expected:
                raise CatalogError("rescan_required")
            return metadata, b"".join(chunks)
        except OSError as error:
            reason = (
                "unsafe_path" if error.errno in {errno.ELOOP, errno.ENOTDIR} else "rescan_required"
            )
            raise CatalogError(reason) from error
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
