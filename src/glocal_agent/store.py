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
            if "progress" not in {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}:
                db.execute("ALTER TABLE jobs ADD COLUMN progress TEXT")
            db.executescript("""CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY, actor TEXT NOT NULL, title TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                conversation_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                file_ids TEXT NOT NULL, job_id TEXT, metadata TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS conversation_messages ON messages(conversation_id);
            """)
            db.execute("UPDATE messages SET status='interrupted', content=?, updated_at=? "
                       "WHERE role='assistant' AND status IN ('queued','running')",
                       ("服务已重启，这次回答已中断。你可以继续发送消息。", now()))
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
                "INSERT INTO jobs (id,status,created_at,updated_at,instruction,actor,payload) "
                "VALUES (?, 'queued', ?, ?, ?, ?, ?)",
                (job_id, time, time, instruction, actor, json.dumps(sources, ensure_ascii=False)),
            )
        return job_id

    def update(self, job_id: str, status: str, result=None, artifacts=None, error=None,
               progress=None):
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET status=?, updated_at=?, result=?, artifacts=?, error=?, "
                "progress=COALESCE(?,progress) WHERE id=?",
                (
                    status, now(),
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    json.dumps(artifacts, ensure_ascii=False) if artifacts is not None else None,
                    error, json.dumps(progress) if progress is not None else None, job_id,
                ),
            )

    def get(self, job_id: str, include_payload: bool = False, actor: str | None = None):
        with self.connect() as db:
            query = "SELECT * FROM jobs WHERE id=?"
            args = [job_id]
            if actor is not None:
                query += " AND actor=?"
                args.append(actor)
            row = db.execute(query, args).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["result"] = json.loads(item["result"]) if item["result"] else None
        item["artifacts"] = json.loads(item["artifacts"]) if item["artifacts"] else []
        item["progress"] = json.loads(item["progress"]) if item["progress"] else {}
        if include_payload:
            item["payload"] = json.loads(item["payload"])
        else:
            item.pop("payload")
        return item

    def list(self, actor: str | None = None):
        with self.connect() as db:
            query = "SELECT id FROM jobs"
            args = []
            if actor is not None:
                query += " WHERE actor=?"
                args.append(actor)
            ids = db.execute(query + " ORDER BY created_at DESC LIMIT 100", args).fetchall()
        return [self.get(row["id"], actor=actor) for row in ids]

    def create_conversation(self, actor: str, title="新对话"):
        identifier, timestamp = uuid.uuid4().hex, now()
        with self.connect() as db:
            db.execute("INSERT INTO conversations VALUES (?,?,?,?,?)",
                       (identifier, actor, title[:80], timestamp, timestamp))
        return identifier

    def conversations(self, actor: str):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM conversations WHERE actor=? ORDER BY updated_at DESC LIMIT 100",
                (actor,))]

    def conversation(self, identifier: str, actor: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM conversations WHERE id=? AND actor=?",
                             (identifier, actor)).fetchone()
            if row is None:
                return None
            messages = db.execute("SELECT * FROM messages WHERE conversation_id=? "
                                  "ORDER BY sequence", (identifier,)).fetchall()
        item = dict(row)
        item["messages"] = []
        for row in messages:
            message = dict(row)
            message.pop("sequence")
            message["file_ids"] = json.loads(message["file_ids"])
            message["metadata"] = json.loads(message["metadata"])
            message["job"] = self.get(message["job_id"], actor=actor) if message["job_id"] else None
            item["messages"].append(message)
        return item

    def append_turn(self, conversation_id, actor, content, file_ids, *, control=False):
        timestamp = now()
        user_id, assistant_id = uuid.uuid4().hex, uuid.uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM conversations WHERE id=? AND actor=?",
                             (conversation_id, actor)).fetchone()
            if row is None:
                raise ValueError("对话不存在。")
            pending = db.execute("SELECT count(*) FROM messages JOIN conversations "
                                 "ON conversations.id=messages.conversation_id WHERE "
                                 "messages.role='assistant' AND messages.status IN "
                                 "('queued','running') AND conversations.actor=?",
                                 (actor,)).fetchone()[0]
            if not control and pending >= 3:
                raise ValueError("已有三条消息等待处理，请稍后发送，或让我停止当前任务。")
            total = db.execute("SELECT count(*) FROM messages WHERE role='assistant' "
                               "AND status IN ('queued','running')").fetchone()[0]
            if not control and total >= 30:
                raise ValueError("工作台正忙，请稍后发送。")
            for identifier, role, text, status in (
                (user_id, "user", content, "completed"),
                (assistant_id, "assistant", "", "queued"),
            ):
                db.execute("INSERT INTO messages (id,conversation_id,role,content,status,"
                           "created_at,updated_at,file_ids,metadata) VALUES (?,?,?,?,?,?,?,?,?)",
                           (identifier, conversation_id, role, text, status, timestamp, timestamp,
                            json.dumps(file_ids), "{}"))
            title = content[:60] if row["title"] == "新对话" else row["title"]
            db.execute("UPDATE conversations SET title=?,updated_at=? WHERE id=?",
                       (title, timestamp, conversation_id))
        return assistant_id

    def answer(self, message_id, content, *, status="completed", job_id=None, metadata=None):
        timestamp = now()
        with self.connect() as db:
            db.execute("UPDATE messages SET content=?,status=?,updated_at=?,job_id=COALESCE(?,"
                       "job_id),metadata=? WHERE id=? AND role='assistant'",
                       (content, status, timestamp, job_id,
                        json.dumps(metadata or {}, ensure_ascii=False), message_id))
            db.execute("UPDATE conversations SET updated_at=? WHERE id=(SELECT conversation_id "
                       "FROM messages WHERE id=?)", (timestamp, message_id))
