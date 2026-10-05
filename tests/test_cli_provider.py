from __future__ import annotations

import copy
import json
import os
import sys
import time
from pathlib import Path

import pytest

from glocal_agent import cli_provider as cli

SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}}}
ANSWER = {"summary": "整理完成"}


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    """Only execute temporary Python scripts, never a real installed model CLI."""
    counter = 0

    def create(provider, body):
        nonlocal counter
        counter += 1
        executable = tmp_path / f"fake-model-{counter}"
        executable.write_text(
            f"#!{sys.executable}\nimport json, os, pathlib, subprocess, sys, time\n" + body,
            encoding="utf-8",
        )
        executable.chmod(0o700)
        variable = cli._COMMANDS[provider][0]
        monkeypatch.setenv(variable, str(executable))
        return executable

    return create


def codex_output(answer=ANSWER):
    return "\n".join(json.dumps(event) for event in [
        {"type": "thread.started", "thread_id": "fake"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(answer)}},
        {"type": "turn.completed", "usage": {}},
    ])


def claude_output(answer=ANSWER):
    return json.dumps({
        "type": "result", "subtype": "success", "is_error": False,
        "structured_output": answer,
    })


@pytest.mark.parametrize("provider", ["codex_cli", "claude_code_cli"])
def test_fixed_argv_stdin_isolation_and_cleanup(provider, fake_cli, tmp_path, monkeypatch):
    captured = tmp_path / "capture.json"
    marker = tmp_path / "must-not-exist"
    injection = f"$(touch {marker}); `touch {marker}`"
    schema = copy.deepcopy(SCHEMA)
    schema["properties"]["summary"]["description"] = injection
    original = copy.deepcopy(schema)
    output = codex_output() if provider == "codex_cli" else claude_output()
    monkeypatch.setenv("FAKE_CAPTURE", str(captured))
    monkeypatch.setenv("FAKE_EXISTING_LOGIN_MARKER", "inherited")
    executable = fake_cli(provider, (
        "args = sys.argv[1:]\n"
        "record = {'argv': args, 'stdin': sys.stdin.read(), 'cwd': os.getcwd(), "
        "'login_env': os.environ['FAKE_EXISTING_LOGIN_MARKER']}\n"
        "if '--output-schema' in args:\n"
        "    record['schema'] = json.loads(pathlib.Path(args[args.index('--output-schema')+1])"
        ".read_text())\n"
        "pathlib.Path(os.environ['FAKE_CAPTURE']).write_text(json.dumps(record))\n"
        f"print({output!r})\n"
    ))
    result = cli.complete(provider, "fake-model", "固定任务 " + injection, injection, schema)
    assert json.loads(result) == ANSWER
    record = json.loads(captured.read_text())
    arguments = record["argv"]
    assert arguments[arguments.index("--model") + 1] == "fake-model"
    assert injection in record["stdin"]
    assert all(injection not in value for value in arguments if value != json.dumps(
        schema, ensure_ascii=False
    ))
    assert record["cwd"] != str(tmp_path)
    assert not Path(record["cwd"]).exists()
    assert record["login_env"] == "inherited"
    assert not marker.exists()
    assert schema == original
    assert executable.exists()
    assert "--dangerously-skip-permissions" not in arguments
    assert "--dangerously-bypass-approvals-and-sandbox" not in arguments
    if provider == "codex_cli":
        assert "--ignore-user-config" in arguments and "--ignore-rules" in arguments
        assert arguments[arguments.index("--sandbox") + 1] == "read-only"
        assert arguments.count("--disable") == 20
        assert record["schema"]["required"] == ["summary"]
        assert record["schema"]["additionalProperties"] is False
    else:
        assert arguments[arguments.index("--tools") + 1] == ""
        assert arguments[arguments.index("--setting-sources") + 1] == ""
        assert arguments[arguments.index("--mcp-config") + 1] == '{"mcpServers":{}}'
        assert arguments[arguments.index("--settings") + 1] == '{"disableAllHooks":true}'


def test_ready_does_not_execute_or_claim_authentication(fake_cli, tmp_path):
    marker = tmp_path / "executed"
    fake_cli("codex_cli", f"pathlib.Path({str(marker)!r}).touch()\n")
    assert cli.ready("codex_cli", "fake-model") is True
    assert not marker.exists()
    for model in ("", "-model", "abc\nxyz", "model; echo input", "model name", "x" * 201):
        assert cli.ready("codex_cli", model) is False
    assert cli.ready("unknown-provider", "fake-model") is False
    assert cli.ready(None, "fake-model") is False


def test_command_is_a_single_executable_not_shell_arguments(monkeypatch, tmp_path):
    marker = tmp_path / "executed"
    monkeypatch.setenv("CODEX_CLI_COMMAND", f"touch {marker}")
    assert cli.ready("codex_cli", "fake-model") is False
    with pytest.raises(cli.CLIError) as error:
        cli.complete("codex_cli", "fake-model", "", "", SCHEMA)
    assert error.value.reason_code == "config_missing"
    assert str(marker) not in str(error.value)
    assert not marker.exists()


def test_codex_schema_recurses_preserves_named_default_property():
    schema = {
        "type": "object", "properties": {
            "default": {"type": "string", "default": "source"},
            "items": {"type": "array", "items": {
                "type": "object", "properties": {"value": {"type": "string"}},
            }},
        },
        "$defs": {"Child": {"type": "object", "properties": {"name": {"type": "string"}}}},
    }
    original = copy.deepcopy(schema)
    result = cli._codex_schema(schema)
    assert result["required"] == ["default", "items"]
    assert "default" not in result["properties"]["default"]
    assert result["properties"]["items"]["items"]["additionalProperties"] is False
    assert result["$defs"]["Child"]["required"] == ["name"]
    assert schema == original


@pytest.mark.parametrize(
    "tool", ["command_execution", "mcp_tool_call", "web_search", "file_change"]
)
def test_codex_rejects_tool_events(tool, fake_cli):
    output = json.dumps({"type": "item.started", "item": {"type": tool}})
    fake_cli("codex_cli", f"print({output!r})\n")
    with pytest.raises(cli.CLIError) as error:
        cli.complete("codex_cli", "fake-model", "", "", SCHEMA)
    assert error.value.reason_code == "tool_operation"


@pytest.mark.parametrize("output", [
    "not json",
    json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}}),
    json.dumps({"type": "turn.completed"}),
    codex_output() + "\n" + json.dumps({"type": "turn.completed"}),
    codex_output({"value": float("nan")}),
])
def test_codex_rejects_invalid_or_incomplete_outputs(output, fake_cli):
    fake_cli("codex_cli", f"print({output!r})\n")
    with pytest.raises(cli.CLIError):
        cli.complete("codex_cli", "fake-model", "", "", SCHEMA)


@pytest.mark.parametrize("envelope", [
    {},
    {"type": "result", "subtype": "success", "is_error": True, "structured_output": ANSWER},
    {"type": "result", "subtype": "error_max_turns", "is_error": False},
    {"type": "result", "subtype": "success", "is_error": False, "result": json.dumps(ANSWER)},
    {"type": "result", "subtype": "success", "is_error": False, "structured_output": "{}"},
    {"type": "result", "subtype": "success", "structured_output": ANSWER},
])
def test_claude_requires_successful_structured_output(envelope, fake_cli):
    fake_cli("claude_code_cli", f"print({json.dumps(envelope)!r})\n")
    with pytest.raises(cli.CLIError):
        cli.complete("claude_code_cli", "fake-model", "", "", SCHEMA)


def test_nonzero_exit_hides_stderr_and_source(fake_cli):
    secret = "source-private-test-text"
    fake_cli("codex_cli", f"sys.stderr.write({secret!r}); sys.exit(2)\n")
    with pytest.raises(cli.CLIError) as error:
        cli.complete("codex_cli", "fake-model", secret, secret, SCHEMA)
    assert error.value.reason_code == "request_failed"
    assert secret not in str(error.value)
    assert error.value.__cause__ is None


def test_stdout_and_stderr_share_two_mib_limit(fake_cli):
    fake_cli("codex_cli", (
        "sys.stdout.write('x' * (1024 * 1024)); sys.stdout.flush()\n"
        "sys.stderr.write('y' * (1024 * 1024 + 1)); sys.stderr.flush()\n"
    ))
    with pytest.raises(cli.CLIError) as error:
        cli.complete("codex_cli", "fake-model", "", "", SCHEMA)
    assert error.value.reason_code == "output_limit"


@pytest.mark.parametrize("failure", ["timeout", "output_limit"])
def test_timeout_or_output_limit_kills_process_group(failure, fake_cli, tmp_path, monkeypatch):
    pidfile = tmp_path / "pids.json"
    monkeypatch.setenv("FAKE_PIDS", str(pidfile))
    monkeypatch.setattr(cli, "_TIMEOUT", 0.5)
    monkeypatch.setattr(cli, "_OUTPUT_LIMIT", 1024)
    body = (
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "pathlib.Path(os.environ['FAKE_PIDS']).write_text(json.dumps([os.getpid(), child.pid]))\n"
    )
    if failure == "output_limit":
        body += "sys.stderr.write('x' * 2048); sys.stderr.flush()\n"
    body += "time.sleep(30)\n"
    fake_cli("codex_cli", body)
    started = time.monotonic()
    with pytest.raises(cli.CLIError) as error:
        cli.complete("codex_cli", "fake-model", "", "", SCHEMA)
    assert error.value.reason_code == failure
    assert time.monotonic() - started < 3
    parent, child = json.loads(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(parent, 0)
    # A killed orphan can remain a zombie until PID 1 reaps it in CI containers.
    stat = Path(f"/proc/{child}/stat")
    if stat.exists():
        assert stat.read_text().split()[2] == "Z"
    else:
        with pytest.raises(ProcessLookupError):
            os.kill(child, 0)
