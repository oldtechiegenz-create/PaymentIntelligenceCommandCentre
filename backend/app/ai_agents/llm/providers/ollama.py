"""Local Ollama provider \u2014 zero-auth, offline-friendly. Default provider for local
dev so a fresh checkout works without any credentials configured.

Requires Ollama running locally (https://ollama.com/download) with the target model
pulled: `ollama pull llama3.2`.
"""
from __future__ import annotations

import os

from langchain_core.language_models.chat_models import BaseChatModel

DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")
DEFAULT_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")


def get_llm(model: str | None = None, temperature: float = 0.2, timeout: int = 120, **kwargs) -> BaseChatModel:
    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=model or DEFAULT_MODEL,
        base_url=DEFAULT_BASE_URL,
        temperature=temperature,
        timeout=timeout,
        **kwargs,
    )
