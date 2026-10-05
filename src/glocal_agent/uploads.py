"""Bounded uploads into an authenticated user's private input directory."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import HTTPException, Request

from .file_policy import SUPPORTED_EXTENSIONS, sensitive_filename
from .files import MAX_FILE_BYTES, MAX_FILES

USER_QUOTA = 512 * 1024 * 1024
TOTAL_QUOTA = 2 * 1024 * 1024 * 1024


def validate_name(name: str) -> None:
    if (not name or len(name.encode("utf-8")) > 200 or name.startswith(".")
            or any(char in name for char in "/\\:")
            or any(ord(char) < 32 or ord(char) == 127 for char in name)):
        raise HTTPException(422, "文件名无效，请使用不含路径的普通文件名。")
    if sensitive_filename(name):
        raise HTTPException(422, "凭据文件禁止上传。")
    if Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise HTTPException(422, "当前不支持该格式；图片和扫描件尚未接入 OCR。")


async def receive_upload(request: Request, directory: Path, name: str, lock) -> None:
    validate_name(name)
    declared = request.headers.get("content-length")
    if declared:
        try:
            if int(declared) < 0 or int(declared) > MAX_FILE_BYTES:
                raise HTTPException(413, "每份文件最多 15 MiB。")
        except ValueError as exc:
            raise HTTPException(400, "文件长度无效。") from exc
    temporary = directory / (".upload-" + secrets.token_hex(16))
    size = 0
    try:
        with temporary.open("xb") as stream:
            temporary.chmod(0o600)
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise HTTPException(413, "每份文件最多 15 MiB。")
                stream.write(chunk)
        if not size:
            raise HTTPException(422, "文件为空。")
        with lock:
            files = [p for p in directory.iterdir() if not p.name.startswith(".")]
            if len(files) >= MAX_FILES or sum(p.stat().st_size for p in files) + size > USER_QUOTA:
                raise HTTPException(413, "个人文件空间已达到 2000 份或 512 MiB 上限。")
            total = sum(p.stat().st_size for p in directory.parent.glob("*/*") if p.is_file())
            if total > TOTAL_QUOTA:
                raise HTTPException(413, "工作台上传空间已满，请联系管理员。")
            try:
                # link is atomic and refuses existing names; never overwrite an original.
                os.link(temporary, directory / name, follow_symlinks=False)
            except FileExistsError as exc:
                raise HTTPException(409, "同名文件已存在，原件未覆盖。") from exc
    finally:
        temporary.unlink(missing_ok=True)
