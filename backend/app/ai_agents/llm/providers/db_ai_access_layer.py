"""Vertex AI / Gemini via an AI Access Layer (AAL) gateway.
Authenticates with an OAuth token from Application Default Credentials (gcloud ADC:
`gcloud auth application-default login --account you@example.com`) and, if configured,
trusts a custom internal root CA alongside certifi's public bundle.

Env vars:
    AAL_GATEWAY_URL     required \u2014 e.g. https://aal-gateway.example.com
    AAL_QUOTA_PROJECT_ID optional \u2014 GCP project pinned on the ADC token (avoids the
                          "no quota project" ADC warning)
    AAL_ROOT_CA_PATH    optional \u2014 path to an internal root CA cert (.cer/.pem) to merge
                          with certifi's bundle; skipped if unset (e.g. gateway already
                          has a publicly-trusted cert in that environment)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel

DEFAULT_MODEL = os.environ.get("AAL_MODEL", "gemini-2.5-pro-europe-west1")
_CERT_CACHE_DIR = Path(__file__).resolve().parent / ".cache"


def _configure_certificate(custom_cert_path: str) -> str:
    """Merges certifi's CA bundle with `custom_cert_path` into one combined PEM (cached
    on disk after the first run) and points REQUESTS_CA_BUNDLE/SSL_CERT_FILE/
    GRPC_DEFAULT_SSL_ROOTS_FILE_PATH at it, so TLS trusts the AAL gateway's cert."""
    import certifi

    _CERT_CACHE_DIR.mkdir(exist_ok=True)
    combined_path = _CERT_CACHE_DIR / "combined-ca-bundle.pem"
    if not combined_path.exists():
        ca_bundle = Path(certifi.where()).read_text(encoding="utf-8")
        custom_cert = Path(custom_cert_path).read_text(encoding="utf-8")
        combined_path.write_text(ca_bundle + "\n" + custom_cert, encoding="utf-8")

    for var in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"):
        os.environ[var] = str(combined_path)
    return str(combined_path)


class _GoogleOAuthTokenProvider:
    """Wraps Application Default Credentials, refreshing the token as needed."""

    def __init__(self, quota_project_id: Optional[str] = None):
        from google.auth import default
        import google.auth.transport.requests

        self._credentials, _ = default(
            scopes=[
                "https://www.googleapis.com/auth/cloud-platform",
                "https://www.googleapis.com/auth/userinfo.email",
            ],
            quota_project_id=quota_project_id,
        )
        self._request = google.auth.transport.requests.Request()

    @property
    def token(self) -> str:
        if not self._credentials.valid:
            self._credentials.refresh(self._request)
        return self._credentials.token


def get_llm(model: str | None = None, temperature: float = 0.2, timeout: int = 120, **kwargs) -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI

    gateway_url = os.environ["AAL_GATEWAY_URL"]
    quota_project_id = os.environ.get("AAL_QUOTA_PROJECT_ID")
    custom_cert_path = os.environ.get("AAL_ROOT_CA_PATH")
    if custom_cert_path:
        _configure_certificate(custom_cert_path)

    # A fresh token is minted on every get_llm() call — the underlying genai.Client
    # bakes the token in at construction and never refreshes it itself, so a
    # long-lived caller should re-call get_llm() rather than hold one instance across
    # an ADC token's ~1h expiry.
    token = _GoogleOAuthTokenProvider(quota_project_id).token

    return ChatGoogleGenerativeAI(
        model=model or DEFAULT_MODEL,
        vertexai=False,
        google_api_key=token,
        base_url=gateway_url,
        temperature=temperature,
        timeout=timeout,
        **kwargs,
    )
