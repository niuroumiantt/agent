import json
import threading
import time
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from test_app import fake_analysis

from glocal_agent.app import create_app
from glocal_agent.config import Settings
from glocal_agent.conversation import direct_action, plan
from glocal_agent.model import ModelError
from glocal_agent.store import Store

DOMAIN = "https://agent.glocalstorage.cn"
KEY = "synthetic-trusted-proxy-" + "x" * 48


def setup(tmp_path, planner, analyzer=fake_analysis, configured=True):
    root = tmp_path / "uploads"
    root.mkdir()
    app = create_app(
        Settings(
            root,
            tmp_path / "state",
            base_url="https://unused.example" if configured else "",
            mode="server",
            public_url=DOMAIN,
            proxy_key=KEY,
        ),
        analyzer,
        planner,
    )

    def headers():
        return {
            "X-Agent-Proxy-Key": KEY,
            "X-Agent-Subject": str(uuid.uuid4()),
            "X-Agent-Session": app.state.session_token,
            "Origin": DOMAIN,
        }

    return app, headers(), headers()


def upload(client, headers, name="po.txt"):
    return client.post(
        "/api/uploads",
        params={"name": name},
        content=b"Quantity: 32",
        headers={**headers, "Content-Type": "application/octet-stream"},
    ).json()["files"][0]["id"]


def create(client, headers, job_id=None):
    response = client.post("/api/conversations", json={"job_id": job_id}, headers=headers)
    assert response.status_code == 201
    return response.json()["id"]


def send(client, headers, identifier, content, ids=None):
    return client.post(
        f"/api/conversations/{identifier}/messages",
        json={"content": content, "file_ids": ids},
        headers=headers,
    )


def done(client, headers, identifier):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        conversation = client.get(f"/api/conversations/{identifier}", headers=headers).json()
        if all(
            message["status"] not in {"queued", "running"} for message in conversation["messages"]
        ):
            return conversation
        time.sleep(0.01)
    raise AssertionError("synthetic conversation did not finish")


def test_conversation_analyzes_files_and_follows_up_with_history_and_prior_result(tmp_path):
    calls = []

    def planner(settings, content, history, files, result):
        calls.append((content, history, files, result))
        return {"action": "analyze" if len(calls) == 1 else "reply", "reply": "再核对交期。"}

    app, a, b = setup(tmp_path, planner)
    with TestClient(app, base_url=DOMAIN) as client:
        file_id = upload(client, a)
        identifier = create(client, a)
        assert send(client, a, identifier, "总结这份文件", [file_id]).status_code == 202
        first = done(client, a, identifier)
        task = first["messages"][1]["job"]
        assert task["status"] == "completed" and len(task["artifacts"]) == 4
        assert first["messages"][0]["file_ids"] == [file_id]
        assert send(client, a, identifier, "接下来该核对什么？").status_code == 202
        second = done(client, a, identifier)
        assert second["messages"][-1]["content"] == "再核对交期。"
        assert calls[1][1][0] == {"role": "user", "content": "总结这份文件"}
        assert calls[1][3]["facts"][0]["claim"] == "32"
        assert calls[1][2] == [{"name": "po.txt"}]
        for artifact in task["artifacts"]:
            assert client.get(artifact["url"], headers=a).status_code == 200
            assert client.get(artifact["url"], headers=b).status_code == 404


def test_conversations_messages_and_imported_jobs_are_owned_and_csrf_protected(tmp_path):
    app, a, b = setup(tmp_path, lambda *args: {"action": "reply", "reply": "你好"})
    with TestClient(app, base_url=DOMAIN) as client:
        file_id = upload(client, a)
        identifier = create(client, a)
        assert client.get("/api/conversations", headers=b).json() == {"conversations": []}
        assert client.get(f"/api/conversations/{identifier}", headers=b).status_code == 404
        assert send(client, b, identifier, "下载报告").status_code == 404
        other = create(client, b)
        assert send(client, b, other, "读取", [file_id]).status_code == 400
        for changed in (
            {"Origin": "https://evil.example"},
            {"X-Agent-Session": "forged"},
            {"X-Agent-Proxy-Key": "forged"},
        ):
            assert send(client, {**a, **changed}, identifier, "你好").status_code == 403
        task = client.post(
            "/api/jobs", json={"file_ids": [file_id], "instruction": "阅读"}, headers=a
        ).json()["id"]
        assert (
            client.post("/api/conversations", json={"job_id": task}, headers=b).status_code == 404
        )
        imported = create(client, a, task)
        conversation = client.get(f"/api/conversations/{imported}", headers=a).json()
        assert conversation["messages"][1]["job_id"] == task
        assert conversation["messages"][0]["file_ids"] == [file_id]


def test_stop_message_interrupts_running_analysis_and_keeps_partial_report(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def analyzer(*args):
        entered.set()
        assert release.wait(4)
        return fake_analysis(*args)

    app, a, _ = setup(
        tmp_path, lambda *args: {"action": "analyze", "reply": "开始阅读。"}, analyzer
    )
    with TestClient(app, base_url=DOMAIN) as client:
        file_id = upload(client, a)
        identifier = create(client, a)
        send(client, a, identifier, "读文件", [file_id])
        assert entered.wait(3)
        try:
            stopped = send(client, a, identifier, "请停止当前任务").json()
            assert "已请求停止" in stopped["messages"][-1]["content"]
        finally:
            release.set()
        conversation = done(client, a, identifier)
        assert conversation["messages"][1]["job"]["status"] == "cancelled"
        assert len(conversation["messages"][1]["job"]["artifacts"]) == 4
        report = send(client, a, identifier, "下载报告").json()["messages"][-1]
        assert len(report["metadata"]["artifacts"]) == 4


def test_preview_and_file_refresh_do_not_call_model_or_require_configuration(tmp_path):
    def no_model(*args):
        raise AssertionError("control called model")

    app, a, _ = setup(tmp_path, no_model, configured=False)
    with TestClient(app, base_url=DOMAIN) as client:
        file_id = upload(client, a)
        identifier = create(client, a)
        preview = send(client, a, identifier, "查看原文", [file_id]).json()["messages"][-1]
        assert (
            preview["metadata"]["previews"][0]["extraction"]["blocks"][0]["text"] == "Quantity: 32"
        )
        refreshed = send(client, a, identifier, "刷新文件").json()["messages"][-1]
        assert refreshed["metadata"]["catalog"]["files"][0]["id"] == file_id
        assert (
            "还没有生成"
            in send(client, a, identifier, "下载报告").json()["messages"][-1]["content"]
        )


def test_general_chat_without_file_selection_and_planner_failure_recover(tmp_path):
    calls = []

    def planner(*args):
        calls.append(args)
        if len(calls) == 1:
            raise RuntimeError("PRIVATE_SYNTHETIC_CREDENTIAL")
        return {"action": "reply", "reply": "我们可以先讨论你的工作目标。"}

    app, a, _ = setup(tmp_path, planner)
    with TestClient(app, base_url=DOMAIN) as client:
        identifier = create(client, a)
        send(client, a, identifier, "你好", [])
        first = done(client, a, identifier)
        assert first["messages"][-1]["status"] == "failed"
        assert "PRIVATE_SYNTHETIC" not in json.dumps(first)
        send(client, a, identifier, "你好", [])
        assert done(client, a, identifier)["messages"][-1]["status"] == "completed"
        assert calls[-1][3] == []


def test_queued_turns_only_receive_completed_earlier_context(tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def planner(settings, content, history, files, result):
        calls.append((content, history))
        if len(calls) == 1:
            entered.set()
            assert release.wait(4)
        return {"action": "reply", "reply": "回答：" + content}

    app, a, _ = setup(tmp_path, planner)
    with TestClient(app, base_url=DOMAIN) as client:
        identifier = create(client, a)
        assert send(client, a, identifier, "第一轮", []).status_code == 202
        assert entered.wait(3)
        try:
            assert send(client, a, identifier, "第二轮", []).status_code == 202
            assert send(client, a, identifier, "第三轮", []).status_code == 202
            assert send(client, a, identifier, "第四轮", []).status_code == 409
        finally:
            release.set()
        assert len(done(client, a, identifier)["messages"]) == 6
        assert calls[0][1] == []
        assert calls[1][1] == [
            {"role": "user", "content": "第一轮"},
            {"role": "assistant", "content": "回答：第一轮"},
        ]
        assert calls[2][1] == calls[1][1] + [
            {"role": "user", "content": "第二轮"},
            {"role": "assistant", "content": "回答：第二轮"},
        ]


def test_restart_preserves_conversation_and_marks_only_unfinished_answers(tmp_path):
    store = Store(tmp_path)
    identifier = store.create_conversation("employee-a")
    first = store.append_turn(identifier, "employee-a", "第一轮", ["file-a"])
    store.answer(first, "已完成的回答")
    store.append_turn(identifier, "employee-a", "第二轮", [])
    store.close()
    recovered = Store(tmp_path)
    try:
        conversation = recovered.conversation(identifier, "employee-a")
        assert conversation["messages"][1]["content"] == "已完成的回答"
        assert conversation["messages"][-1]["status"] == "interrupted"
        assert recovered.conversation(identifier, "employee-b") is None
    finally:
        recovered.close()


@pytest.mark.parametrize(
    "text,action",
    [
        ("请停止当前任务", "cancel"),
        ("下载 Word", "reports"),
        ("请查看原文", "preview"),
        ("分析完了吗", "status"),
        ("不要停止当前任务", None),
        ("解释合同里的停止付款条款", None),
        ("不要下载报告，继续分析", None),
    ],
)
def test_controls_respect_explicit_intent_and_negation(text, action):
    assert direct_action(text) == action


def test_planner_uses_strict_action_schema_and_existing_gateway_credentials(tmp_path):
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({"action": "reply", "reply": "继续核对交期。"})
                        }
                    }
                ]
            },
        )

    settings = Settings(
        tmp_path,
        tmp_path / "data",
        provider="gateway",
        base_url="https://gateway.example",
        api_key="synthetic-agent-key",
    )
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        reply = plan(
            settings,
            "接下来呢？",
            [{"role": "user", "content": "核对合同"}],
            [],
            {"summary": "合同仍需核对", "facts": [], "recommendations": []},
            client,
        )
    assert reply["action"] == "reply"
    assert seen[0].headers["authorization"] == "Bearer synthetic-agent-key"
    body = json.loads(seen[0].content)
    assert "previous_analysis" in body["messages"][-1]["content"]
    assert (
        "analyze"
        in body["response_format"]["json_schema"]["schema"]["properties"]["action"]["enum"]
    )


def test_planner_rejects_unimplemented_action(tmp_path):
    def respond(request):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"action":"send_email","reply":"sent"}'}}]},
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ModelError):
            plan(
                Settings(tmp_path, tmp_path / "data", base_url="https://gateway.example"),
                "发信",
                [],
                [],
                None,
                client,
            )
