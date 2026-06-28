from __future__ import annotations

import time
from datetime import datetime
from functools import lru_cache
from ssl import SSLContext
from typing import Any

import httpx
import truststore

from football_analysis.cache import make_request_key, quota_limits, quota_window_keys, sanitize_mapping, ttl_for_endpoint
from football_analysis.contracts import SourceResponse
from football_analysis.db import StructuredRepository
from football_analysis.settings import Settings


class QuotaExceeded(RuntimeError):
    pass


class ProviderHttpClient:
    def __init__(self, settings: Settings, repository: StructuredRepository):
        self.settings = settings
        self.repository = repository

    def get_json(
        self,
        provider: str,
        url: str,
        endpoint: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> SourceResponse:
        return self._request(
            provider=provider,
            url=url,
            endpoint=endpoint,
            headers=headers,
            params=params,
            ttl_seconds=ttl_seconds,
            response_kind="json",
        )

    def get_text(
        self,
        provider: str,
        url: str,
        endpoint: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> SourceResponse:
        return self._request(
            provider=provider,
            url=url,
            endpoint=endpoint,
            headers=headers,
            params=params,
            ttl_seconds=ttl_seconds,
            response_kind="text",
        )

    def _request(
        self,
        provider: str,
        url: str,
        endpoint: str,
        headers: dict[str, str] | None,
        params: dict[str, Any] | None,
        ttl_seconds: int | None,
        response_kind: str,
    ) -> SourceResponse:
        params = params or {}
        request_key = make_request_key(provider, endpoint, params)
        sanitized_params = sanitize_mapping(params)
        ttl = ttl_seconds if ttl_seconds is not None else ttl_for_endpoint(self.settings.cache, endpoint)

        if self.settings.cache.enabled:
            cached_payload = self.repository.get_cached_payload(provider, endpoint, request_key)
            if cached_payload is not None:
                self.repository.record_source_request(
                    provider=provider,
                    endpoint=endpoint,
                    request_key=request_key,
                    status_code=200,
                    cached=True,
                    duration_ms=0,
                    sanitized_params=sanitized_params,
                )
                return SourceResponse(
                    provider=provider,
                    endpoint=endpoint,
                    request_key=request_key,
                    status_code=200,
                    payload=cached_payload,
                    cached=True,
                )

        try:
            self._consume_quota(provider)
        except QuotaExceeded as exc:
            self.repository.record_source_request(
                provider=provider,
                endpoint=endpoint,
                request_key=request_key,
                status_code=None,
                cached=False,
                duration_ms=0,
                sanitized_params=sanitized_params,
                error=str(exc),
            )
            return SourceResponse(provider=provider, endpoint=endpoint, request_key=request_key, error=str(exc))

        attempts = self.settings.ingestion.max_retries + 1
        last_error: str | None = None
        for attempt in range(attempts):
            started = time.perf_counter()
            try:
                with httpx.Client(timeout=self.settings.ingestion.request_timeout_seconds, verify=_ssl_context()) as client:
                    response = client.get(url, headers=headers, params=params)
                duration_ms = int((time.perf_counter() - started) * 1000)
                payload: Any = response.json() if response_kind == "json" else response.text
                if self.settings.ingestion.store_raw_payloads:
                    self.repository.save_raw_payload(provider, endpoint, request_key, response.status_code, payload, ttl)
                self.repository.record_source_request(
                    provider=provider,
                    endpoint=endpoint,
                    request_key=request_key,
                    status_code=response.status_code,
                    cached=False,
                    duration_ms=duration_ms,
                    sanitized_params=sanitized_params,
                    error=None if response.status_code < 400 else f"HTTP {response.status_code}",
                )
                return SourceResponse(
                    provider=provider,
                    endpoint=endpoint,
                    request_key=request_key,
                    status_code=response.status_code,
                    payload=payload,
                    duration_ms=duration_ms,
                    error=None if response.status_code < 400 else f"HTTP {response.status_code}",
                )
            except (httpx.HTTPError, ValueError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt + 1 < attempts:
                    time.sleep(0.25 * (attempt + 1))

        self.repository.record_source_request(
            provider=provider,
            endpoint=endpoint,
            request_key=request_key,
            status_code=None,
            cached=False,
            duration_ms=0,
            sanitized_params=sanitized_params,
            error=last_error,
        )
        return SourceResponse(provider=provider, endpoint=endpoint, request_key=request_key, error=last_error)

    def _consume_quota(self, provider: str) -> None:
        limits = quota_limits(self.settings.quota, provider)
        if not limits:
            return
        windows = quota_window_keys(datetime.utcnow())
        for scope, limit in limits.items():
            window_key = windows[scope]
            current = self.repository.quota_count(provider, window_key)
            if current >= limit:
                raise QuotaExceeded(f"quota_exceeded:{provider}:{scope}:{current}/{limit}")
        for scope in limits:
            self.repository.increment_quota(provider, windows[scope])


@lru_cache(maxsize=1)
def _ssl_context() -> SSLContext:
    return truststore.SSLContext()
