"""Provider-agnostic LLM factory \u2014 every provider returns a LangChain BaseChatModel,
so agent/graph code never branches on which provider is active (same shape as
LangGraph's model wiring in the PAE reference project). Select via the `provider`
argument, the LLM_PROVIDER env var, or fall back to "ollama" \u2014 the only provider
that needs zero credentials, so a fresh checkout works offline by default.

Providers:
    vertex_proxy       Vertex AI/Gemini via a local dev reverse-proxy (PAE pattern)
    db_ai_access_layer Vertex AI/Gemini via DB's internal AI Access Layer gateway
                       (OAuth ADC + custom CA bundle)
    ollama             local Ollama server, zero-auth
    anthropic          Anthropic API (Claude), requires ANTHROPIC_API_KEY
    openai_proxy       any OpenAI-compatible endpoint (internal gateway or otherwise)

Usage:
    from app.ai_agents.llm.factory import get_llm
    llm = get_llm()                              # LLM_PROVIDER env var, or "ollama"
    llm = get_llm(provider="anthropic", model="claude-sonnet-4-5-20250929")
"""
from __future__ import annotations

import importlib
import os
from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel

_PROVIDER_MODULES = {
    "vertex_proxy": "app.ai_agents.llm.providers.vertex_proxy",
    "db_ai_access_layer": "app.ai_agents.llm.providers.db_ai_access_layer",
    "ollama": "app.ai_agents.llm.providers.ollama",
    "anthropic": "app.ai_agents.llm.providers.anthropic_provider",
    "openai_proxy": "app.ai_agents.llm.providers.openai_proxy",
}


def get_llm(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.2,
    **kwargs,
) -> BaseChatModel:
    resolved = (provider or os.environ.get("LLM_PROVIDER") or "ollama").lower()
    if resolved not in _PROVIDER_MODULES:
        raise ValueError(f"Unknown LLM provider {resolved!r}; choose one of {sorted(_PROVIDER_MODULES)}")

    module = importlib.import_module(_PROVIDER_MODULES[resolved])
    return module.get_llm(model=model, temperature=temperature, **kwargs)
