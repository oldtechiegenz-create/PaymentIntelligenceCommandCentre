"""LLM provider factory \u2014 verifies provider selection/fallback and that each provider
fails fast with a clear error when its required config is missing, without needing
real network access to Ollama/Vertex/Anthropic/AAL."""
from __future__ import annotations

import pytest

from app.ai_agents.llm.factory import get_llm


@pytest.fixture(autouse=True)
def _clean_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "LLM_PROVIDER", "OLLAMA_MODEL", "OLLAMA_BASE_URL",
        "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "ANTHROPIC_API_URL",
        "OPENAI_PROXY_BASE_URL", "OPENAI_PROXY_API_KEY", "OPENAI_PROXY_MODEL",
        "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION", "VERTEX_PROXY_URL", "VERTEX_MODEL",
        "AAL_GATEWAY_URL", "AAL_QUOTA_PROJECT_ID", "AAL_ROOT_CA_PATH", "AAL_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


def test_unknown_provider_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        get_llm(provider="not-a-real-provider")


def test_default_provider_is_ollama_and_needs_no_credentials() -> None:
    from langchain_ollama import ChatOllama

    llm = get_llm()
    assert isinstance(llm, ChatOllama)
    assert llm.model == "llama3.2"


def test_llm_provider_env_var_is_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5")
    from app.ai_agents.llm.providers import ollama as ollama_provider
    import importlib
    importlib.reload(ollama_provider)  # re-read the env-var default at import time

    llm = get_llm()
    assert llm.model == "qwen2.5"
    importlib.reload(ollama_provider)  # restore for other tests


def test_explicit_provider_arg_overrides_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")  # would fail (no API key) if honored
    from langchain_ollama import ChatOllama

    llm = get_llm(provider="ollama", model="mistral")
    assert isinstance(llm, ChatOllama)
    assert llm.model == "mistral"


def test_anthropic_missing_api_key_raises() -> None:
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        get_llm(provider="anthropic")


def test_anthropic_constructs_with_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from langchain_anthropic import ChatAnthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    llm = get_llm(provider="anthropic", model="claude-sonnet-4-5-20250929")
    assert isinstance(llm, ChatAnthropic)
    assert llm.model == "claude-sonnet-4-5-20250929"


def test_openai_proxy_missing_base_url_raises() -> None:
    with pytest.raises(RuntimeError, match="OPENAI_PROXY_BASE_URL"):
        get_llm(provider="openai_proxy")


def test_openai_proxy_constructs_with_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from langchain_openai import ChatOpenAI

    monkeypatch.setenv("OPENAI_PROXY_BASE_URL", "http://internal-gateway.example/v1")
    llm = get_llm(provider="openai_proxy")
    assert isinstance(llm, ChatOpenAI)


def test_vertex_proxy_missing_project_env_raises() -> None:
    with pytest.raises(KeyError):
        get_llm(provider="vertex_proxy")


def test_vertex_proxy_constructs_with_local_proxy_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from langchain_google_genai import ChatGoogleGenerativeAI

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "europe-west1")
    monkeypatch.setenv("VERTEX_PROXY_URL", "http://localhost:8081/gemini")
    llm = get_llm(provider="vertex_proxy")
    assert isinstance(llm, ChatGoogleGenerativeAI)


def test_db_ai_access_layer_missing_gateway_url_raises() -> None:
    with pytest.raises(KeyError):
        get_llm(provider="db_ai_access_layer")


def test_anthropic_structured_output_defaults_to_native_json_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    from langchain_anthropic import ChatAnthropic

    seen: dict = {}

    def fake_super(self, schema, *, method="function_calling", **kwargs):
        seen["method"] = method
        return "ok"

    monkeypatch.setattr(ChatAnthropic, "with_structured_output", fake_super)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    llm = get_llm(provider="anthropic")
    assert llm.with_structured_output(dict) == "ok"
    assert seen["method"] == "json_schema"


def test_anthropic_does_not_send_temperature_unless_opted_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    assert get_llm(provider="anthropic").temperature is None
    monkeypatch.setenv("ANTHROPIC_TEMPERATURE", "0.3")
    assert get_llm(provider="anthropic").temperature == 0.3
