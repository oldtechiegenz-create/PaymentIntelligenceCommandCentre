"""Anthropic (Claude) provider \u2014 hosted API, requires ANTHROPIC_API_KEY."""
from __future__ import annotations

import os

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")


class _ClaudeChat(ChatAnthropic):
    """ChatAnthropic that defaults `with_structured_output` to Anthropic's native JSON-schema
    mode. The library's default (forced tool calling) isn't supported on newer Claude models
    and can fail when the model doesn't emit the tool call; the router and summarizer both
    depend on structured output, so they must not rely on that path."""

    def with_structured_output(self, schema, *, method="json_schema", **kwargs):
        return super().with_structured_output(schema, method=method, **kwargs)


def get_llm(model: str | None = None, temperature: float = 0.2, timeout: int = 120, **kwargs) -> BaseChatModel:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    # Newer Claude models reject any non-default temperature, so the factory's generic
    # `temperature` argument is deliberately NOT forwarded; set ANTHROPIC_TEMPERATURE to
    # opt in for a model that still supports it.
    if os.environ.get("ANTHROPIC_TEMPERATURE"):
        kwargs["temperature"] = float(os.environ["ANTHROPIC_TEMPERATURE"])

    return _ClaudeChat(
        model=model or DEFAULT_MODEL,
        anthropic_api_key=api_key,
        anthropic_api_url=os.environ.get("ANTHROPIC_API_URL"),  # None -> library default
        default_request_timeout=timeout,
        **kwargs,
    )
