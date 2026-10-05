import json

import httpx
import pytest

from glocal_agent.config import Settings
from glocal_agent.model import ModelError, analyze


def sources():
    return [{"source_id": "S1", "blocks": [{"locator": "page 1", "text": "Quantity: 32"}]}]


def result(quote="Quantity: 32", source_id="S1", locator="page 1"):
    return {
        "summary": "待核对的摘要",
        "documents": [{"source_id": "S1", "category": "PO", "summary": "采购材料"}],
        "facts": [{"source_id": source_id, "locator": locator, "quote": quote, "claim": "32台"}],
        "recommendations": ["核对订单身份"],
    }


@pytest.mark.parametrize("provider", ["ollama", "gateway"])
def test_api_contract_and_source_verification(tmp_path, provider):
    requests = []

    def respond(request):
        requests.append(request)
        data = json.loads(request.content)
        assert data["model"] == "test-model"
        assert data["stream"] is False
        content = json.dumps(result())
        envelope = (
            {"message": {"content": content}}
            if provider == "ollama" else {"choices": [{"message": {"content": content}}]}
        )
        return httpx.Response(200, json=envelope)

    settings = Settings(
        tmp_path, tmp_path / "other", provider, "https://model.example", "test-model"
    )
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        data = analyze(settings, "归纳", sources(), client)
    assert data["facts"][0]["verified"] is True
    assert data["provider"] == provider
    assert requests[0].url.path == (
        "/api/chat" if provider == "ollama" else "/v1/chat/completions"
    )


@pytest.mark.parametrize("quote,source_id,locator", [
    ("Quantity: 99", "S1", "page 1"),
    ("Quantity: 32", "S2", "page 1"),
    ("Quantity: 32", "S1", "page 9"),
])
def test_hallucinated_fact_remains_unverified(tmp_path, quote, source_id, locator):
    output = result(quote, source_id, locator)
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, json={"message": {"content": json.dumps(output)}}
    ))
    with httpx.Client(transport=transport) as client:
        data = analyze(Settings(tmp_path, tmp_path / "data", base_url="https://model.example"),
                       "归纳", sources(), client)
    assert data["facts"][0]["verified"] is False
    assert data["warnings"]


def test_auth_failure_does_not_leak_response_or_fallback(tmp_path):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(403, text="secret-provider-response")

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ModelError) as exc:
            analyze(Settings(tmp_path, tmp_path / "data", base_url="https://model.example"),
                    "private-material", sources(), client)
    assert "secret" not in str(exc.value)
    assert "private-material" not in str(exc.value)
    assert len(calls) == 1


def test_non_json_result_is_visible_failure(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, json={"message": {"content": "invented prose"}}
    ))) as client:
        with pytest.raises(ModelError, match="结构化"):
            analyze(Settings(tmp_path, tmp_path / "data", base_url="https://model.example"),
                    "归纳", sources(), client)


@pytest.mark.parametrize("provider", ["codex_cli", "claude_code_cli"])
def test_cli_provider_shares_output_and_evidence_contract(tmp_path, monkeypatch, provider):
    captured = []

    def complete(name, model, system, user, schema):
        captured.append((name, model, json.loads(user), schema))
        return json.dumps(result())

    monkeypatch.setattr("glocal_agent.cli_provider.complete", complete)
    settings = Settings(tmp_path, tmp_path / "data", provider=provider, model="explicit-model")
    data = analyze(settings, "核对采购资料", sources())
    assert data["facts"][0]["verified"] is True
    assert data["provider"] == provider
    assert captured[0][0:2] == (provider, "explicit-model")
    assert captured[0][2]["instruction"] == "核对采购资料"
    assert "facts" in captured[0][3]["properties"]
