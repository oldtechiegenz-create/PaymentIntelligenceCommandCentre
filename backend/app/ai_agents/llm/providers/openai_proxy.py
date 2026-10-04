"""Generic OpenAI-compatible provider \u2014 any internal gateway or third-party endpoint
that speaks the OpenAI chat-completions API shape (base_url + api_key). Use this for a
proxy that isn't Vertex/Gemini-shaped (the vertex_proxy/db_ai_access_layer providers
cover those)."""
from __future__ import annotations

import os

from langchain_core.language_models.chat_models import BaseChatModel

DEFAULT_MODEL = os.environ.get("OPENAI_PROXY_MODEL", "gpt-4o-mini")


def get_llm(model: str | None = None, temperature: float = 0.2, timeout: int = 120, **kwargs) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    base_url = os.environ.get("OPENAI_PROXY_BASE_URL")
    if not base_url:
        raise RuntimeError("OPENAI_PROXY_BASE_URL is not set")
    # Some internal gateways authenticate the caller some other way (mTLS, network
    # policy) and don't check this key at all — default to a placeholder so the SDK's
    # own "api key required" validation doesn't block a genuinely keyless gateway.
    api_key = os.environ.get("OPENAI_PROXY_API_KEY", "unused")

    return ChatOpenAI(
        model_name=model or DEFAULT_MODEL,
        openai_api_key=api_key,
        openai_api_base=base_url,
        temperature=temperature,
        request_timeout=timeout,
        **kwargs,
    )
