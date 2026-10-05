from __future__ import annotations

import getpass
import hashlib
import hmac
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .batch import run_batch
from .config import Settings
from .extract import extract
from .files import MAX_FILES, CatalogError, FileCatalog
from .model import analyze
from .reports import write_reports
from .store import Store

WEB = Path(__file__).parent / "web"


class ScanRequest(BaseModel):
    recursive: bool = False


class JobRequest(BaseModel):
    file_ids: list[str] = Field(min_length=1, max_length=MAX_FILES)
    instruction: str = Field(min_length=1, max_length=2000)


def create_app(settings: Settings, analyzer=analyze) -> FastAPI:
    settings.validate()
    catalog = FileCatalog(settings.root)
    store = Store(settings.data_dir)
    session_token = secrets.token_urlsafe(32)
    catalog_lock = threading.Lock()
    job_gate = threading.Lock()
    cancellation_lock = threading.Lock()
    cancellations: dict[str, threading.Event] = {}
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="office-agent")

    @asynccontextmanager
    async def lifespan(app):
        yield
        with cancellation_lock:
            for event in cancellations.values():
                event.set()
        pool.shutdown(wait=True, cancel_futures=True)
        store.close()

    app = FastAPI(title="Glocal Agent 本机试点", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.session_token = session_token
    app.state.store = store
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"]
    )

    @app.middleware("http")
    async def protect_local_session(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            token = request.headers.get("x-agent-session", "")
            if not hmac.compare_digest(token, session_token):
                return JSONResponse({"detail": "请从本机工作台访问。"}, status_code=403)
            origin = request.headers.get("origin")
            if origin:
                parsed = urlsplit(origin)
                if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                    return JSONResponse({"detail": "不允许跨站请求。"}, status_code=403)
                if parsed.netloc != request.headers.get("host"):
                    return JSONResponse({"detail": "不允许跨站请求。"}, status_code=403)
            if request.method == "POST" and not request.headers.get(
                "content-type", ""
            ).startswith("application/json"):
                return JSONResponse({"detail": "请求必须为 JSON。"}, status_code=415)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'"
        )
        return response

    @app.exception_handler(CatalogError)
    async def handle_catalog_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return WEB.joinpath("index.html").read_text(encoding="utf-8").replace(
            "__SESSION_TOKEN__", session_token
        )

    app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.get("/api/config")
    def config():
        return settings.public()

    @app.post("/api/scan")
    def scan(body: ScanRequest):
        with catalog_lock:
            return catalog.scan(recursive=body.recursive)

    def read_source(file_id: str):
        with catalog_lock:
            metadata, content = catalog.read(file_id)
        parsed = extract(metadata["name"], content)
        return {
            "file": metadata,
            "extraction": asdict(parsed),
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    @app.get("/api/files/{file_id}/preview")
    def preview(file_id: str):
        return read_source(file_id)

    def execute(job_id: str, snapshot: FileCatalog, ids: list[str], cancelled: threading.Event):
        try:
            task = store.get(job_id, include_payload=True)
            store.update(job_id, "running")

            def progress_update(progress, result):
                store.update(job_id, "running", progress=progress, result=result)

            status, result, progress, error = run_batch(
                settings, task["instruction"], snapshot, ids, analyzer, cancelled, progress_update
            )
            artifacts = write_reports(settings.data_dir, job_id, result) if result else []
            store.update(job_id, status, result=result, artifacts=artifacts,
                         progress=progress, error=error)
        except Exception:
            # Preserve already analysed sources if a later read or export fails.
            task = store.get(job_id)
            store.update(job_id, "partial" if task and task["result"] else "failed",
                         result=task["result"] if task else None,
                         error="文件分析或报告生成失败，未修改原件。")
        finally:
            with cancellation_lock:
                cancellations.pop(job_id, None)
            job_gate.release()

    @app.post("/api/jobs", status_code=202)
    def create_job(body: JobRequest):
        if not settings.configured:
            raise HTTPException(409, "模型后端尚未配置，请先在本机运行 configure。")
        if not body.instruction.strip():
            raise HTTPException(422, "请填写分析任务。")
        if len(set(body.file_ids)) != len(body.file_ids):
            raise HTTPException(422, "选择中有重复文件。")
        if not job_gate.acquire(blocking=False):
            raise HTTPException(409, "已有分析任务正在运行，请等待它结束。")
        try:
            with catalog_lock:
                snapshot = catalog.freeze(body.file_ids)
            references = [snapshot.get(file_id) for file_id in body.file_ids]
            if not any(item["supported"] for item in references):
                raise HTTPException(422, "所选文件没有可处理的格式；凭据文件默认禁止读取。")
            job_id = store.create(body.instruction, getpass.getuser(), references)
            cancelled = threading.Event()
            with cancellation_lock:
                cancellations[job_id] = cancelled
            pool.submit(execute, job_id, snapshot, body.file_ids, cancelled)
            return {"id": job_id, "status": "queued"}
        except Exception:
            job_gate.release()
            raise

    @app.post("/api/jobs/{job_id}/cancel", status_code=202)
    def cancel_job(job_id: str):
        with cancellation_lock:
            cancelled = cancellations.get(job_id)
            if cancelled is None:
                if store.get(job_id) is None:
                    raise HTTPException(404, "任务不存在。")
                raise HTTPException(409, "任务已经结束。")
            cancelled.set()
        return {"id": job_id, "message": "已请求停止，当前模型调用结束后保留已有结果。"}

    @app.get("/api/jobs")
    def jobs():
        return {"jobs": store.list()}

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        task = store.get(job_id)
        if task is None:
            raise HTTPException(404, "任务不存在。")
        return task

    @app.get("/api/jobs/{job_id}/artifacts/{name}")
    def artifact(job_id: str, name: str):
        task = store.get(job_id)
        if task is None or task["status"] not in {"completed", "partial", "cancelled"}:
            raise HTTPException(404, "报告尚未生成。")
        if name not in {item["name"] for item in task["artifacts"]}:
            raise HTTPException(404, "报告不存在。")
        return FileResponse(settings.data_dir / "artifacts" / job_id / name, filename=name)

    return app
