"""Vertex AI / Gemini via a local dev reverse-proxy \u2014 ported from the PAE reference
project's common/llm.py::get_llm() (hack-fobo/pae_actual_code/BE). Used when running
locally without direct network access to Vertex AI; VERTEX_PROXY_URL tunnels the
request through a local proxy (e.g. http://localhost:8081/gemini). In a deployed
environment, leave VERTEX_PROXY_URL unset to hit Vertex directly, or use the
db_ai_access_layer provider instead."""
from __future__ import annotations

import os

from langchain_core.language_models.chat_models import BaseChatModel

DEFAULT_MODEL = os.environ.get("VERTEX_MODEL", "gemini-2.5-pro")


def get_llm(model: str | None = None, temperature: float = 0.2, timeout: int = 120, **kwargs) -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI

    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    location = os.environ["GOOGLE_CLOUD_LOCATION"]
    proxy_url = os.environ.get("VERTEX_PROXY_URL")

    llm_kwargs: dict = dict(
        model=model or DEFAULT_MODEL,
        vertexai=True,
        project=project,
        location=location,
        temperature=temperature,
        timeout=timeout,
    )
    if proxy_url:
        llm_kwargs["base_url"] = proxy_url
    return ChatGoogleGenerativeAI(**llm_kwargs, **kwargs)
