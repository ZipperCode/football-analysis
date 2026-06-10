from __future__ import annotations

import os
from datetime import datetime

import httpx

from football_analysis.cache import quota_limits, quota_window_keys
from football_analysis.db import StructuredRepository
from football_analysis.models import SourceHealth, SourceState
from football_analysis.settings import Settings, remote_validation_enabled


class SourceHealthChecker:
    def __init__(self, settings: Settings, repository: StructuredRepository | None = None):
        self.settings = settings
        self.repository = repository

    async def check_all(self) -> list[SourceHealth]:
        return [await self.check(source_id) for source_id in self.settings.data_sources]

    async def check(self, source_id: str) -> SourceHealth:
        source = self.settings.data_sources[source_id]
        credential_present = bool(source.api_key_env and os.getenv(source.api_key_env))

        if not source.enabled:
            return SourceHealth(
                source_id=source_id,
                name=source.name,
                state=SourceState.disabled,
                enabled=False,
                credential_present=credential_present,
                detail="Source disabled in config.",
                quota=self._quota(source_id),
                cache=self._cache(source_id),
            )

        if source.api_key_env and not credential_present:
            return SourceHealth(
                source_id=source_id,
                name=source.name,
                state=SourceState.missing_credentials,
                enabled=True,
                credential_present=False,
                detail=f"Missing env credential: {source.api_key_env}.",
                quota=self._quota(source_id),
                cache=self._cache(source_id),
            )

        if not remote_validation_enabled():
            return SourceHealth(
                source_id=source_id,
                name=source.name,
                state=SourceState.remote_check_skipped,
                enabled=True,
                credential_present=credential_present,
                detail="Remote validation skipped; set FOOTBALL_VALIDATE_REMOTE=1 to probe endpoints.",
                quota=self._quota(source_id),
                cache=self._cache(source_id),
            )

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(source.base_url)
            state = SourceState.ok if response.status_code < 500 else SourceState.error
            detail = f"HTTP {response.status_code} from {source.base_url}."
        except httpx.HTTPError as exc:
            state = SourceState.error
            detail = f"{type(exc).__name__}: {exc}"

        return SourceHealth(
            source_id=source_id,
            name=source.name,
            state=state,
            enabled=True,
            credential_present=credential_present,
            checked_at=datetime.utcnow(),
            detail=detail,
            quota=self._quota(source_id),
            cache=self._cache(source_id),
        )

    def _quota(self, source_id: str) -> dict:
        if self.repository is None:
            return {}
        now_keys = quota_window_keys(datetime.utcnow())
        limits = quota_limits(self.settings.quota, source_id)
        return {
            scope: {
                "used": self.repository.quota_count(source_id, now_keys[scope]),
                "limit": limit,
                "window": now_keys[scope],
            }
            for scope, limit in limits.items()
        }

    def _cache(self, source_id: str) -> dict:
        if self.repository is None:
            return {}
        return {"entries": self.repository.cache_count(source_id)}
