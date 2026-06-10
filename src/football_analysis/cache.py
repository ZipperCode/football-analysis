from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from football_analysis.settings import CacheSettings, QuotaSettings, SourceQuotaSettings


SECRET_FIELD_HINTS = ("key", "token", "secret", "password", "authorization")


def sanitize_mapping(values: dict[str, Any] | None) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in (values or {}).items():
        lowered = key.lower()
        if any(hint in lowered for hint in SECRET_FIELD_HINTS):
            sanitized[key] = "<redacted>" if value else ""
        else:
            sanitized[key] = value
    return sanitized


def make_request_key(provider: str, endpoint: str, params: dict[str, Any] | None) -> str:
    payload = {
        "provider": provider,
        "endpoint": endpoint,
        "params": sanitize_mapping(params),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def ttl_for_endpoint(cache: CacheSettings, endpoint: str) -> int:
    lowered = endpoint.lower()
    if "odd" in lowered:
        return cache.odds_ttl_seconds
    if "fixture" in lowered or "match" in lowered:
        return cache.fixtures_ttl_seconds
    if "historical" in lowered or "csv" in lowered:
        return cache.historical_ttl_seconds
    return cache.default_ttl_seconds


def quota_window_keys(now: datetime) -> dict[str, str]:
    return {
        "minute": now.strftime("minute:%Y%m%d%H%M"),
        "hour": now.strftime("hour:%Y%m%d%H"),
        "day": now.strftime("day:%Y%m%d"),
    }


def quota_limits(quota: QuotaSettings, provider: str) -> dict[str, int]:
    provider_quota: SourceQuotaSettings | None = quota.providers.get(provider)
    if provider_quota is None:
        return {}
    limits: dict[str, int] = {}
    if provider_quota.requests_per_minute is not None:
        limits["minute"] = provider_quota.requests_per_minute
    if provider_quota.requests_per_hour is not None:
        limits["hour"] = provider_quota.requests_per_hour
    if provider_quota.requests_per_day is not None:
        limits["day"] = provider_quota.requests_per_day
    return limits
