import dataclasses
import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient
from test_app import fake_analysis, finished

from glocal_agent import uploads
from glocal_agent.app import create_app
from glocal_agent.config import Settings

DOMAIN = "https://agent.glocalstorage.cn"
KEY = "synthetic-proxy-key-" + "x" * 48


def setup_server(tmp_path):
    root = tmp_path / "uploads"
    root.mkdir()
    settings = Settings(root, tmp_path / "jobs", base_url="https://unused.example",
                        mode="server", public_url=DOMAIN, proxy_key=KEY)
    app = create_app(settings, fake_analysis)

    def headers(subject):
        return {"X-Agent-Proxy-Key": KEY, "X-Agent-Subject": subject,
                "X-Agent-Session": app.state.session_token, "Origin": DOMAIN}

    return app, settings, headers(str(uuid.uuid4())), headers(str(uuid.uuid4()))


def upload(client, headers, name="po.txt", content=b"Quantity: 32"):
    return client.post("/api/uploads", params={"name": name}, content=content,
                       headers={**headers, "Content-Type": "application/octet-stream"})


def test_auth_is_required_for_page_static_api_and_reports(tmp_path):
    app, settings, a, _ = setup_server(tmp_path)
    with TestClient(app, base_url=DOMAIN) as client:
        assert client.get("/healthz").json() == {"status": "ok", "mode": "server"}
        for path in ("/", "/static/app.js", "/api/config", "/api/jobs"):
            assert client.get(path).status_code == 403
            assert client.get(path, headers={**a, "X-Agent-Proxy-Key": "forged"}).status_code == 403
            bad = client.get(path, headers={**a, "X-Agent-Subject": "../someone"})
            assert bad.status_code == 403
        assert client.get("/", headers=a).status_code == 200
        public = client.get("/api/config", headers=a).json()
        assert public["root"] == "我的上传文件"
        assert str(settings.root) not in str(public)
        assert KEY not in str(public)
        assert upload(client, {**a, "Origin": "https://mail.glocalstorage.cn"}).status_code == 403
        assert upload(client, {**a, "X-Agent-Session": "forged"}).status_code == 403
        wrong_host = client.get("/api/config", headers={**a, "Host": "evil.example"})
        assert wrong_host.status_code in {400, 403}


def test_user_files_tasks_cancellation_and_downloads_are_isolated(tmp_path):
    app, _, a, b = setup_server(tmp_path)
    with TestClient(app, base_url=DOMAIN) as client:
        source = upload(client, a).json()["files"][0]
        client.post("/api/scan", json={}, headers=b)
        assert client.get(f'/api/files/{source["id"]}/preview', headers=b).status_code == 400
        assert client.post("/api/jobs", json={"file_ids": [source["id"]],
                                               "instruction": "阅读"}, headers=b).status_code == 400
        response = client.post("/api/jobs", json={"file_ids": [source["id"]],
                                                  "instruction": "阅读"}, headers=a)
        task = finished(client, response.json()["id"], a)
        assert task["status"] == "completed"
        assert task["result"]["sources"][0]["sha256"] == hashlib.sha256(b"Quantity: 32").hexdigest()
        assert client.get("/api/jobs", headers=b).json() == {"jobs": []}
        assert client.get(f'/api/jobs/{task["id"]}', headers=b).status_code == 404
        assert client.post(f'/api/jobs/{task["id"]}/cancel', json={}, headers=b).status_code == 404
        for artifact in task["artifacts"]:
            assert client.get(artifact["url"], headers=b).status_code == 404
            assert client.get(artifact["url"], headers=a).status_code == 200
        assert upload(client, b, content=b"Private B text").status_code == 201
        preview = client.get(f'/api/files/{source["id"]}/preview', headers=a).json()
        assert preview["sha256"] == hashlib.sha256(b"Quantity: 32").hexdigest()
        preview_b = client.get(f'/api/files/{source["id"]}/preview', headers=b).json()
        assert preview_b["sha256"] == hashlib.sha256(b"Private B text").hexdigest()


@pytest.mark.parametrize("name", ["../po.txt", "sub/po.txt", "sub\\po.txt", ".env",
                                 "credentials.txt",
                                 "key.pem", "a\n.txt", "a.exe", "a" * 201 + ".txt"])
def test_invalid_upload_names_never_create_files(tmp_path, name):
    app, settings, a, _ = setup_server(tmp_path)
    with TestClient(app, base_url=DOMAIN) as client:
        assert upload(client, a, name).status_code == 422
    assert not list(settings.root.glob("*/*"))


def test_upload_never_overwrites_and_cleans_partial_files(tmp_path, monkeypatch):
    app, settings, a, _ = setup_server(tmp_path)
    with TestClient(app, base_url=DOMAIN) as client:
        assert upload(client, a).status_code == 201
        assert upload(client, a, content=b"replacement").status_code == 409
        assert upload(client, a, "empty.txt", b"").status_code == 422
        monkeypatch.setattr(uploads, "MAX_FILE_BYTES", 8)
        assert upload(client, a, "too-large.txt", b"123456789").status_code == 413
        monkeypatch.setattr(uploads, "USER_QUOTA", 1)
        assert upload(client, a, "quota.txt", b"123").status_code == 413
    files = list(settings.root.glob("*/*"))
    assert len(files) == 1 and files[0].read_bytes() == b"Quantity: 32"


def test_public_mode_refuses_missing_boundaries(tmp_path):
    app, settings, _, _ = setup_server(tmp_path)
    app.state.store.close()
    for change in ({"proxy_key": ""}, {"public_url": "http://agent.glocalstorage.cn"},
                   {"provider": "codex_cli"}):
        with pytest.raises(ValueError):
            dataclasses.replace(settings, **change).validate()
