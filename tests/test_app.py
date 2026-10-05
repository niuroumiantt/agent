import hashlib
import time

from fastapi.testclient import TestClient

from glocal_agent.app import create_app
from glocal_agent.config import Settings
from glocal_agent.model import ModelError


def setup(tmp_path, analyzer):
    root = tmp_path / "Downloads"
    root.mkdir()
    (root / "po.txt").write_text("Quantity: 32", encoding="utf-8")
    app = create_app(
        Settings(root, tmp_path / "private", base_url="https://unused.example"), analyzer
    )
    return app, root, {"X-Agent-Session": app.state.session_token}


def finished(client, job_id, headers):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        task = client.get(f"/api/jobs/{job_id}", headers=headers).json()
        if task["status"] not in {"queued", "running"}:
            return task
        time.sleep(0.01)
    raise AssertionError("Synthetic job did not finish")


def fake_analysis(settings, instruction, sources):
    return {
        "summary": "需要核对的采购材料", "documents": [],
        "facts": [{"source_id": "S1", "locator": "line 1", "quote": "Quantity: 32",
                   "claim": "32", "verified": True}],
        "recommendations": ["核对后另存"], "warnings": [], "model": settings.model,
    }


def test_selected_file_to_persisted_reports_without_changing_original(tmp_path):
    app, root, headers = setup(tmp_path, fake_analysis)
    original = (root / "po.txt").read_bytes()
    with TestClient(app) as client:
        assert "__SESSION_TOKEN__" not in client.get("/").text
        scan = client.post("/api/scan", json={}, headers=headers).json()
        file_id = scan["files"][0]["id"]
        preview = client.get(f"/api/files/{file_id}/preview", headers=headers).json()
        assert preview["sha256"] == hashlib.sha256(original).hexdigest()
        response = client.post("/api/jobs", json={
            "file_ids": [file_id], "instruction": "识别采购资料"
        }, headers=headers)
        assert response.status_code == 202
        task = finished(client, response.json()["id"], headers)
        assert task["status"] == "completed"
        assert task["result"]["sources"][0]["sha256"] == preview["sha256"]
        assert len(task["artifacts"]) == 4
        artifact = client.get(task["artifacts"][2]["url"], headers=headers)
        assert artifact.status_code == 200
        assert artifact.content.startswith(b"PK")
        assert client.get(task["artifacts"][2]["url"]).status_code == 403
        assert client.get(f'/api/jobs/{task["id"]}/artifacts/not-a-report',
                          headers=headers).status_code == 404
        history = client.get("/api/jobs", headers=headers).json()
        assert history["jobs"][0]["status"] == "completed"
    assert (root / "po.txt").read_bytes() == original
    assert list(root.iterdir()) == [root / "po.txt"]


def test_loopback_session_and_origin_boundaries(tmp_path):
    app, _, headers = setup(tmp_path, fake_analysis)
    with TestClient(app) as client:
        assert client.get("/api/config").status_code == 403
        assert client.get("/api/config", headers={"X-Agent-Session": "wrong"}).status_code == 403
        for origin in ("https://evil.example", "http://localhost:9999"):
            assert client.post("/api/scan", json={}, headers={
                **headers, "Origin": origin
            }).status_code == 403
        assert client.get("/api/config", headers={
            **headers, "Host": "evil.example"
        }).status_code == 400
        assert client.post("/api/scan", content="{}", headers=headers).status_code == 415
        assert client.get("/api/config", headers=headers).status_code == 200
        assert "api_key" not in client.get("/api/config", headers=headers).json()


def test_model_failure_is_visible_and_gate_is_released(tmp_path):
    def fail(settings, instruction, sources):
        raise ModelError("模型拒绝访问")

    app, _, headers = setup(tmp_path, fail)
    with TestClient(app) as client:
        scan = client.post("/api/scan", json={}, headers=headers).json()
        payload = {"file_ids": [scan["files"][0]["id"]], "instruction": "阅读"}
        for _ in range(2):
            response = client.post("/api/jobs", json=payload, headers=headers)
            assert response.status_code == 202
            task = finished(client, response.json()["id"], headers)
            assert task["status"] == "failed"
            assert task["error"] == "模型拒绝访问"
            assert task["result"] is None


def test_unsupported_material_never_sent_to_model(tmp_path):
    calls = []

    def should_not_call(*args):
        calls.append(args)
        raise AssertionError

    app, root, headers = setup(tmp_path, should_not_call)
    (root / "scan.png").write_bytes(b"image")
    with TestClient(app) as client:
        files = client.post("/api/scan", json={}, headers=headers).json()["files"]
        image = next(item for item in files if item["name"] == "scan.png")
        response = client.post("/api/jobs", json={
            "file_ids": [image["id"]], "instruction": "读图"
        }, headers=headers)
        assert response.status_code == 422
    assert not calls


def test_clipped_model_input_is_marked_partial(tmp_path):
    captured = []

    def capture(settings, instruction, sources):
        captured.extend(sources)
        return fake_analysis(settings, instruction, sources)

    app, root, headers = setup(tmp_path, capture)
    (root / "po.txt").write_text("a" * 18000)
    with TestClient(app) as client:
        file_id = client.post("/api/scan", json={}, headers=headers).json()["files"][0]["id"]
        response = client.post("/api/jobs", json={
            "file_ids": [file_id], "instruction": "阅读"
        }, headers=headers)
        assert finished(client, response.json()["id"], headers)["status"] == "completed"
    assert captured[0]["status"] == "partial"
    assert sum(len(block["text"]) for block in captured[0]["blocks"]) == 8000
    assert captured[0]["warnings"]
