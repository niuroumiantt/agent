from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path


def now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        lock_path = directory / "instance.lock"
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600)
        self._lock = os.fdopen(descriptor, "a+")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._lock.close()
            message = "该运行数据目录已有 agent 实例，请停止它或使用不同 data-dir。"
            raise ValueError(message) from exc
        self.path = directory / "tasks.sqlite3"
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, instruction TEXT NOT NULL, actor TEXT NOT NULL,
                payload TEXT NOT NULL, result TEXT, artifacts TEXT, error TEXT
            )""")
            db.execute(
                "UPDATE jobs SET status='interrupted', error=?, updated_at=? "
                "WHERE status IN ('queued','running')",
                ("服务已重启，任务已中断；请核对后重新提交。", now()),
            )
        self.path.chmod(0o600)

    def close(self):
        if not self._lock.closed:
            self._lock.close()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def create(self, instruction: str, actor: str, sources: list[dict]) -> str:
        job_id = uuid.uuid4().hex
        time = now()
        with self.connect() as db:
            db.execute(
                "INSERT INTO jobs VALUES (?, 'queued', ?, ?, ?, ?, ?, NULL, NULL, NULL)",
                (job_id, time, time, instruction, actor, json.dumps(sources, ensure_ascii=False)),
            )
        return job_id

    def update(self, job_id: str, status: str, result=None, artifacts=None, error=None):
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET status=?, updated_at=?, result=?, artifacts=?, error=? WHERE id=?",
                (
                    status, now(),
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    json.dumps(artifacts, ensure_ascii=False) if artifacts is not None else None,
                    error, job_id,
                ),
            )

    def get(self, job_id: str, include_payload: bool = False):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["result"] = json.loads(item["result"]) if item["result"] else None
        item["artifacts"] = json.loads(item["artifacts"]) if item["artifacts"] else []
        if include_payload:
            item["payload"] = json.loads(item["payload"])
        else:
            item.pop("payload")
        return item

    def list(self):
        with self.connect() as db:
            ids = db.execute("SELECT id FROM jobs ORDER BY created_at DESC LIMIT 100").fetchall()
        return [self.get(row["id"]) for row in ids]
